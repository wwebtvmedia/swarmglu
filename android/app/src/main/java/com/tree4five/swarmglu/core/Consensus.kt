package com.tree4five.swarmglu.core

import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import kotlin.concurrent.thread

/**
 * Moteur d'élection par bail — PORTAGE de consensus.ConsensusManager.
 * Mêmes règles que le Python : heartbeats au leader, bail renouvelé par
 * la réponse, silence > lease -> élection déterministe (clé la plus
 * petite gagne, délai proportionnel au rang), abdication à epoch égale
 * devant une clé plus petite, carnet d'adresses véhiculé par les
 * messages du leader. Interopérable CÂBLE avec les nœuds Python.
 */
enum class Role { FOLLOWER, CANDIDATE, LEADER }

class ConsensusConfig(
    val heartbeat: Double = 3.0,
    val bail: Double = 6.0,
    val delaiParRang: Double = 0.3,
    val tick: Double = 0.5,
) {
    val fenetrePairs: Double get() = heartbeat * 3
}

class ConsensusManager(
    val identite: JSONObject,          // {node_id, ip, port, mode}
    val etat: StateManager,
    val transport: HttpJson,
    val chaine: HashChain,
    val config: ConsensusConfig,
    var allowJoin: Boolean = true,
) {
    @Volatile var role = Role.FOLLOWER
    @Volatile var leaderId: String? = null
    @Volatile var epoch = 0
    private var bailEcheance = System.currentTimeMillis() / 1000.0 + config.bail
    private var prochainBattement = 0.0
    private var prochaineTentativeJoin = 0.0
    private var proclamationA: Double? = null
    private val cle = cleDeterministe(identite.getString("ip"), identite.getString("node_id"))

    val nodeId: String get() = identite.getString("node_id")

    fun cleStr(): String = "%010d|%s".format(cle.first, cle.second)

    fun demarrer() {
        thread(name = "consensus", isDaemon = true) {
            while (true) {
                Thread.sleep((config.tick * 1000).toLong())
                try {
                    val maintenant = System.currentTimeMillis() / 1000.0
                    if (role == Role.LEADER) tickLeader(maintenant) else tickFollower(maintenant)
                } catch (_: InterruptedException) {
                    return@thread
                } catch (_: Exception) {
                }
            }
        }
    }

    // ------------------------------------------------------------------

    private var prochaineSondeErmite = 0.0

    private fun tickLeader(maintenant: Double) {
        etat.toucher(nodeId)
        if (maintenant >= prochainBattement) {
            prochainBattement = maintenant + config.heartbeat
            val corps = messageLeader()
            for ((id, _) in candidatsVivants()) {
                if (id == nodeId) continue
                adresse(id)?.let { transport.post(it.first, it.second, "/leader-beat", corps, 2.0) }
            }
        }
        // ANTI-HERMITE : leader sans AUCUN follower -> re-probe des graines ;
        // un leader découvert est plus légitime -> abdication (guérison de
        // partition, miroir du correctif Python).
        if (prochaineSondeErmite <= 0.0) {
            prochaineSondeErmite = maintenant + maxOf(3.0, 3 * config.bail)
        }
        if (maintenant >= prochaineSondeErmite && candidatsVivants().none { it.first != nodeId }) {
            prochaineSondeErmite = maintenant + maxOf(3.0, 3 * config.bail)
            sonderCommeErmite()
        }
    }

    private fun sonderCommeErmite() {
        val corps = JSONObject()
            .put("node_id", nodeId).put("ip", identite.getString("ip"))
            .put("port", identite.getInt("port"))
            .put("mode", identite.optString("mode", "full"))
            .put("capabilities", JSONArray(etat.capacitesPropres()))
            .put("models", JSONArray(etat.modelesPropres()))
        for (idPaire in cles(etat.pairs())) {
            if (idPaire == nodeId) continue
            val p = etat.pairs().getJSONObject(idPaire)
            val ip = p.optString("ip"); val port = p.optInt("port", 0)
            if (ip.isEmpty() || port <= 0) continue
            val (s, rep) = transport.post(ip, port, "/join", corps, 2.0)
            if (s == 200 && rep != null && rep.optString("leader_id").isNotEmpty()
                && rep.optString("leader_id") != nodeId) {
                chaine.ajouter("hermit_rejoined", nodeId, JSONObject()
                    .put("discovered_leader", rep.getString("leader_id")))
                abdiquer(rep)
                return
            }
        }
    }

    private fun tickFollower(maintenant: Double) {
        etat.toucher(nodeId)

        if (role == Role.CANDIDATE && proclamationA != null && maintenant >= proclamationA!!) {
            proclamer("delai_rang")
            return
        }
        val leader = leaderId
        if (leader != null) {
            if (maintenant >= bailEcheance) {
                lancerElection("silence_leader_%.0fs".format(config.bail)); return
            }
            if (maintenant >= prochainBattement) {
                prochainBattement = maintenant + config.heartbeat
                envoyerBattement(leader)
            }
        } else {
            if (maintenant >= bailEcheance) {
                lancerElection("silence_bootstrap"); return
            }
            if (maintenant >= prochaineTentativeJoin) {
                prochaineTentativeJoin = maintenant + config.heartbeat
                tenterJoindre()
            }
        }
    }

    private fun candidatsVivants(): List<Pair<String, JSONObject>> =
        etat.pairsVivants(config.fenetrePairs)

    private fun adresse(id: String): Pair<String, Int>? {
        val p = etat.pairs().optJSONObject(id) ?: return null
        val ip = p.optString("ip"); val port = p.optInt("port", 0)
        return if (ip.isEmpty() || port == 0) null else ip to port
    }

    private fun envoyerBattement(leader: String) {
        val addr = adresse(leader) ?: run { leaderId = null; return }
        val corps = JSONObject()
            .put("node_id", nodeId).put("ip", identite.getString("ip"))
            .put("port", identite.getInt("port")).put("ts", System.currentTimeMillis() / 1000.0)
            .put("mode", identite.optString("mode", "full"))
            .put("capabilities", JSONArray(etat.capacitesPropres()))
            .put("models", JSONArray(etat.modelesPropres()))
        val (statut, reponse) = transport.post(addr.first, addr.second, "/heartbeat", corps, 2.0)
        if (statut == 200 && reponse != null) {
            apprendreCarnet(reponse.optJSONArray("peers"))
            if (reponse.optString("leader_id") == leader) {
                bailEcheance = System.currentTimeMillis() / 1000.0 + config.bail
                if (reponse.optInt("epoch", epoch) > epoch) epoch = reponse.optInt("epoch")
            } else if (reponse.optString("leader_id").isNotEmpty()) {
                adopterLeader(reponse)
            }
        }
    }

    private fun tenterJoindre() {
        for (entree in cles(etat.pairs())) {
            val id = entree
            val pair = etat.pairs().getJSONObject(id)
            if (id == nodeId) continue
            val ip = pair.optString("ip"); val port = pair.optInt("port", 0)
            if (ip.isEmpty() || port == 0) continue
            val corps = JSONObject()
                .put("node_id", nodeId).put("ip", identite.getString("ip"))
                .put("port", identite.getInt("port"))
                .put("mode", identite.optString("mode", "full"))
                .put("capabilities", JSONArray(etat.capacitesPropres()))
                .put("models", JSONArray(etat.modelesPropres()))
            val (statut, rep) = transport.post(ip, port, "/join", corps, 2.0)
            if (statut == 200 && rep != null) {
                etat.toucher(id, vivant = true)
                if (id.startsWith("seed-")) etat.pairs().remove(id)
                if (rep.optString("leader_id").isNotEmpty() &&
                    rep.optString("leader_id") != nodeId) {
                    adopterLeader(rep); return
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Élection
    // ------------------------------------------------------------------

    private fun lancerElection(raison: String) {
        val cles = candidatsTries()
        val rang = cles.indexOfFirst { it.second == nodeId }
        if (rang < 0) {   // mode non éligible : attendre
            role = Role.FOLLOWER
            bailEcheance = System.currentTimeMillis() / 1000.0 + config.bail
            return
        }
        role = Role.CANDIDATE
        leaderId = null
        proclamationA = System.currentTimeMillis() / 1000.0 + config.delaiParRang * rang
        bailEcheance = System.currentTimeMillis() / 1000.0 + config.bail
        chaine.ajouter("election_started", nodeId, JSONObject()
            .put("reason", raison).put("epoch", epoch).put("rank", rang)
            .put("candidates", JSONArray(cles.map { it.second })))
    }

    private fun candidatsTries(): List<Pair<Pair<Long, String>, String>> {
        val liste = mutableListOf(cle to nodeId)
        if (identite.optString("mode", "full") !in setOf("full", "ambassador")) liste.clear()
        for ((id, p) in candidatsVivants()) {
            if (id == nodeId) continue
            if (p.optString("mode", "full") !in setOf("full", "ambassador")) continue
            liste.add(cleDeterministe(p.optString("ip"), id) to id)
        }
        return liste.sortedWith(compareBy({ it.first.first }, { it.first.second }))
    }

    private fun proclamer(declencheur: String) {
        if (role == Role.LEADER) return
        epoch += 1
        role = Role.LEADER
        leaderId = nodeId
        proclamationA = null
        prochainBattement = 0.0
        bailEcheance = Double.MAX_VALUE
        etat.setLeader(nodeId, cleStr(), epoch)
        chaine.ajouter("leader_elected", nodeId, JSONObject()
            .put("leader", nodeId).put("epoch", epoch).put("trigger", declencheur))
        val corps = messageLeader()
        for (id in cles(etat.pairs())) {
            val p = etat.pairs().getJSONObject(id)
            if (id == nodeId) continue
            val ip = p.optString("ip"); val port = p.optInt("port", 0)
            if (ip.isEmpty() || port == 0) continue
            transport.post(ip, port, "/leader-announce", corps, 2.0)
        }
    }

    fun messageLeader(): JSONObject {
        val carnet = JSONArray()
        for (id in cles(etat.pairs())) {
            if (id == nodeId) continue
            val p = etat.pairs().getJSONObject(id)
            if (p.optString("ip").isNotEmpty() && p.optInt("port", 0) > 0) {
                carnet.put(JSONObject().put("node_id", id).put("ip", p.getString("ip"))
                    .put("port", p.getInt("port")).put("mode", p.optString("mode", "full")))
            }
        }
        return JSONObject()
            .put("leader_id", nodeId).put("ip", identite.getString("ip"))
            .put("port", identite.getInt("port")).put("epoch", epoch)
            .put("key", cleStr()).put("state_digest", etat.digest())
            .put("peers", carnet)
    }

    private fun adopterLeader(msg: JSONObject) {
        val id = msg.optString("leader_id")
        if (id.isEmpty()) return
        val ip = msg.optString("ip"); val port = msg.optInt("port", 0)
        val change = (leaderId != id) || (msg.optInt("epoch", 0) > epoch) || role != Role.FOLLOWER
        role = Role.FOLLOWER
        leaderId = id
        epoch = maxOf(epoch, msg.optInt("epoch", 0))
        proclamationA = null
        bailEcheance = System.currentTimeMillis() / 1000.0 + config.bail
        prochainBattement = 0.0
        etat.setLeader(id, msg.optString("key"), epoch)
        etat.majPair(id, mapOf("ip" to ip, "port" to port, "mode" to "full"))
        apprendreCarnet(msg.optJSONArray("peers"))
        if (change) chaine.ajouter("leader_accepted", nodeId, JSONObject()
            .put("leader", id).put("epoch", epoch))
    }

    private fun abdiquer(gagnant: JSONObject) {
        chaine.ajouter("leader_stepdown", nodeId, JSONObject()
            .put("winner", gagnant.optString("leader_id")))
        adopterLeader(gagnant)
    }

    private fun apprendreCarnet(carnet: JSONArray?) {
        if (carnet == null) return
        for (i in 0 until carnet.length()) {
            val e = carnet.getJSONObject(i)
            val id = e.optString("node_id")
            if (id.isEmpty() || id == nodeId) continue
            if (e.optString("ip").isEmpty() || e.optInt("port", 0) <= 0) continue
            if (!etat.pairs().has(id)) {
                etat.majPair(id, mapOf("ip" to e.getString("ip"),
                                       "port" to e.getInt("port"),
                                       "mode" to e.optString("mode", "full")))
                etat.toucher(id)
            }
        }
    }

    // ------------------------------------------------------------------
    // Handlers HTTP (mêmes réponses que le Python)
    // ------------------------------------------------------------------

    fun surJoin(corps: JSONObject, ipClient: String): Pair<Int, JSONObject> {
        val id = corps.optString("node_id")
        if (id.isEmpty() || corps.optInt("port", 0) <= 0)
            return 400 to JSONObject().put("error", "node_id et port requis")
        var enrôle = false
        if (etat.whitelist?.estAutorise(id) == false) {
            if (allowJoin && role == Role.LEADER) {
                etat.whitelist?.ajouter(id, "leader:$nodeId")
                etat.setWhitelistNodes(etat.whitelist!!.noeuds())
                chaine.ajouter("whitelist_approved", nodeId, JSONObject().put("new_node", id))
                enrôle = true
            } else {
                return 403 to JSONObject().put("error", "nœud non whitelisté — approbation requise")
                    .put("leader_id", leaderId ?: JSONObject.NULL)
                    .put("leader_addr", addrLeader())
            }
        }
        majPairDepuisBattement(id, corps, ipClient)
        etat.toucher(id)
        chaine.ajouter("peer_registered", id, JSONObject()
            .put("ip", corps.optString("ip").ifEmpty { ipClient })
            .put("port", corps.optInt("port")).put("enrolled_now", enrôle))
        return 200 to reponseJoin()
    }

    private fun reponseJoin(): JSONObject {
        val rep = JSONObject().put("ok", true)
            .put("leader_id", leaderId ?: JSONObject.NULL)
            .put("leader_addr", addrLeader())
            .put("epoch", epoch).put("node_id", nodeId)
            .put("role", role.name.lowercase())
        if (leaderId == nodeId) {
            rep.put("ip", identite.getString("ip")).put("port", identite.getInt("port"))
                .put("key", cleStr())
        } else if (leaderId != null) {
            adresse(leaderId!!)?.let { rep.put("ip", it.first).put("port", it.second) }
            rep.put("key", etat.doc.optJSONObject("meta")?.optString("leader_key") ?: "")
        }
        return rep
    }

    private fun addrLeader(): String? {
        if (leaderId == null) return null
        if (leaderId == nodeId) return "${identite.getString("ip")}:${identite.getInt("port")}"
        adresse(leaderId!!)?.let { return "${it.first}:${it.second}" }
        return null
    }

    fun surBattement(corps: JSONObject, ipClient: String): Pair<Int, JSONObject> {
        val id = corps.optString("node_id")
        if (id.isEmpty()) return 400 to JSONObject().put("error", "node_id requis")
        if (etat.whitelist?.estAutorise(id) == false)
            return 403 to JSONObject().put("error", "nœud non whitelisté")
        majPairDepuisBattement(id, corps, ipClient)
        etat.toucher(id)
        return if (role == Role.LEADER)
            200 to JSONObject().put("ok", true).put("leader_id", nodeId)
                .put("epoch", epoch).put("state_digest", etat.digest())
                .put("peers", carnet())
        else 200 to reponseJoin()
    }

    private fun majPairDepuisBattement(id: String, corps: JSONObject, ipClient: String) {
        val champs = HashMap<String, Any>()
        champs["ip"] = corps.optString("ip").ifEmpty { ipClient }
        champs["port"] = corps.optInt("port")
        champs["mode"] = corps.optString("mode", "full")
        corps.optJSONArray("capabilities")?.let { champs["capabilities"] = it }
        corps.optJSONArray("models")?.let { champs["models"] = it }
        etat.majPair(id, champs)
    }

    private fun carnet(): JSONArray {
        val carnet = JSONArray()
        for (id in cles(etat.pairs())) {
            if (id == nodeId) continue
            val p = etat.pairs().getJSONObject(id)
            if (p.optString("ip").isNotEmpty() && p.optInt("port", 0) > 0)
                carnet.put(JSONObject().put("node_id", id).put("ip", p.getString("ip"))
                    .put("port", p.getInt("port")).put("mode", p.optString("mode", "full")))
        }
        return carnet
    }

    fun surLeaderBeat(corps: JSONObject): Pair<Int, JSONObject> {
        val id = corps.optString("leader_id")
        if (id.isEmpty() || etat.whitelist?.estAutorise(id) == false)
            return 403 to JSONObject().put("error", "leader non whitelisté")
        if (role == Role.LEADER && id != nodeId) {
            val e = corps.optInt("epoch", 0)
            if (e > epoch || (e == epoch && corps.optString("key") < cleStr())) {
                abdiquer(corps); return 200 to JSONObject().put("ok", true).put("stood_down", true)
            }
            return 200 to JSONObject().put("ok", true).put("conflict", "ignoré")
        }
        apprendreCarnet(corps.optJSONArray("peers"))
        adopterLeader(corps)
        return 200 to JSONObject().put("ok", true).put("follower", nodeId)
    }

    fun surProclamation(corps: JSONObject): Pair<Int, JSONObject> {
        val id = corps.optString("leader_id")
        val e = corps.optInt("epoch", 0)
        if (id.isEmpty() || etat.whitelist?.estAutorise(id) == false)
            return 403 to JSONObject().put("error", "leader non whitelisté")
        if (id == nodeId) return 200 to JSONObject().put("ok", true).put("self", true)
        if (role == Role.LEADER) {
            if (e > epoch || (e == epoch && corps.optString("key") < cleStr())) {
                abdiquer(corps); return 200 to JSONObject().put("ok", true).put("stood_down", true)
            }
            return 200 to JSONObject().put("ok", true).put("conflict", "ignoré")
        }
        if (e < epoch) return 200 to JSONObject().put("ok", true).put("stale", true)
        adopterLeader(corps)
        return 200 to JSONObject().put("ok", true)
    }

    fun instantane(): JSONObject = JSONObject()
        .put("role", role.name.lowercase())
        .put("leader_id", leaderId ?: JSONObject.NULL)
        .put("leader_addr", addrLeader())
        .put("epoch", epoch)
        .put("heartbeat_interval", config.heartbeat)
        .put("lease_timeout", config.bail)
}

/** Client HTTP JSON minimal (java.net — aucun dépendance). */
class HttpJson {
    fun post(ip: String, port: Int, chemin: String, corps: JSONObject?,
             delai: Double = 5.0, enTetes: Map<String, String> = emptyMap()
    ): Pair<Int, JSONObject?> {
        return executer("POST", ip, port, chemin, corps, delai, enTetes)
    }

    fun get(ip: String, port: Int, chemin: String, delai: Double = 5.0): Pair<Int, JSONObject?> {
        return executer("GET", ip, port, chemin, null, delai)
    }

    private fun executer(methode: String, ip: String, port: Int, chemin: String,
                         corps: JSONObject?, delai: Double,
                         enTetes: Map<String, String> = emptyMap()
    ): Pair<Int, JSONObject?> {
        return try {
            val url = java.net.URL("http://$ip:$port$chemin")
            val cnx = url.openConnection() as java.net.HttpURLConnection
            cnx.requestMethod = methode
            cnx.connectTimeout = (delai * 1000).toInt()
            cnx.readTimeout = (delai * 1000).toInt()
            cnx.doOutput = corps != null
            cnx.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            for ((k, v) in enTetes) cnx.setRequestProperty(k, v)
            for ((k, v) in enTetes) cnx.setRequestProperty(k, v)
            if (corps != null) cnx.outputStream.use { it.write(corps.toString().toByteArray()) }
            val reponse = try {
                cnx.inputStream.bufferedReader().readText()
            } catch (e: Exception) {
                cnx.errorStream?.bufferedReader()?.readText() ?: ""
            }
            val parse = if (reponse.isEmpty()) null else try { JSONObject(reponse) } catch (_: Exception) { null }
            cnx.responseCode to parse
        } catch (_: Exception) {
            0 to null
        }
    }
}
