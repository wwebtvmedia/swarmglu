package com.tree4five.swarmglu

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.InetSocketAddress
import java.util.concurrent.Executors

/**
 * Nœud SwarmGlu Kotlin — serveur HTTP (JDK), interopérable câble avec les
 * nœuds Python : /join, /heartbeat, /leader-beat, /leader-announce,
 * /registry, /state/digest, /state/sync, /health, /status, /ask (mock +
 * pare-feu OSP + savoir commun), /knowledge, /relay, /audit/verify.
 */
class NoeudKotlin(
    val nodeId: String,
    val port: Int,
    val ipAnnonce: String,
    val portAnnonce: Int,
    val mode: String = "full",
    val essaim: String = "salle-kotlin",
    val dossierDonnees: String,
    cheminWhitelist: String,
    val config: ConsensusConfig,
) {
    val whitelist = Whitelist(cheminWhitelist)
    val chaine = HashChain("$dossierDonnees/swarm_audit.jsonl")
    val etat = StateManager(nodeId, whitelist)
    val transport = HttpJson()
    val consensus = ConsensusManager(
        JSONObject().put("node_id", nodeId).put("ip", ipAnnonce)
            .put("port", portAnnonce).put("mode", mode),
        etat, transport, chaine, config)
    private var serveur: HttpServer? = null
    private val debut = System.currentTimeMillis()

    fun demarrer() {
        if (!whitelist.estAutorise(nodeId)) whitelist.ajouter(nodeId, "self-bootstrap")
        etat.setWhitelistNodes(whitelist.noeuds())
        val capacites = mutableListOf("node", "inference")
        if (mode in setOf("full", "ambassador")) capacites.add("harnessdroid")
        etat.declarerSoi(capacites, listOf("mock-qwen-0.5b"))
        val champs = HashMap<String, Any>()
        champs["ip"] = ipAnnonce; champs["port"] = portAnnonce; champs["mode"] = mode
        champs["capabilities"] = JSONArray(capacites)
        champs["models"] = JSONArray(listOf("mock-qwen-0.5b"))
        champs["api_flavor"] = "mock"
        etat.majPair(nodeId, champs)
        etat.toucher(nodeId)

        File(dossierDonnees).mkdirs()
        serveur = HttpServer.create(InetSocketAddress("0.0.0.0", port), 0)
        serveur!!.executor = Executors.newFixedThreadPool(16)
        val racines = LinkedHashMap<String, Pair<String, (JSONObject, String) -> Pair<Int, JSONObject>>>()
        fun route(chemin: String, methode: String, fn: (JSONObject, String) -> Pair<Int, JSONObject>) {
            racines[chemin] = methode to fn
        }
        route("/join", "POST", ::surJoin)
        route("/heartbeat", "POST", ::surBattement)
        route("/leader-beat", "POST", ::surLeaderBeat)
        route("/leader-announce", "POST", ::surProclamation)
        route("/state/sync", "POST", ::surSync)
        route("/ask", "POST", ::surAsk)
        route("/relay", "POST", ::surRelais)
        route("/health", "GET", ::surSante)
        route("/status", "GET", ::surStatut)
        route("/registry", "GET", ::surRegistre)
        route("/state/digest", "GET", ::surDigest)
        route("/knowledge", "GET", ::surSavoir)
        route("/audit/verify", "GET", ::surAudit)
        for (entree in racines.entries) {
            val chemin = entree.key
            val methode = entree.value.first
            val fn = entree.value.second
            serveur!!.createContext(chemin) { echange ->
                try {
                    if (echange.requestMethod == "OPTIONS") {
                        repondre(echange, 204, null); return@createContext
                    }
                    if (echange.requestMethod != methode) {
                        repondre(echange, 404, JSONObject().put("error", "route inconnue")); return@createContext
                    }
                    val ipClient = echange.remoteAddress.address.hostAddress ?: ""
                    val corps: JSONObject =
                        if (methode == "POST")
                            try { JSONObject(echange.requestBody.readBytes().toString(Charsets.UTF_8)) }
                            catch (_: Exception) { JSONObject() }
                        else JSONObject()
                    val (statut, reponse) = fn(corps, ipClient)
                    repondre(echange, statut, reponse)
                } catch (e: Exception) {
                    try { repondre(echange, 500, JSONObject().put("error", "erreur interne: $e")) }
                    catch (_: Exception) {}
                }
            }
        }
        serveur!!.start()
        chaine.ajouter("node_start", nodeId, JSONObject()
            .put("ip", ipAnnonce).put("port", portAnnonce).put("mode", mode)
            .put("swarm_id", essaim).put("implementation", "kotlin-jvm"))
        consensus.demarrer()
        threadSync()
    }

    private fun threadSync() {
        val fil = Thread {
            while (true) {
                Thread.sleep(5000)
                try {
                    val leader = consensus.leaderId
                    if (leader != null && leader != nodeId) {
                        etat.pairs().optJSONObject(leader)?.let { p ->
                            transport.get(p.optString("ip"), p.optInt("port"), "/registry", 3.0)
                        }?.second?.optJSONObject("state")?.let { distant ->
                            val rapport = etat.fusionner(distant)
                            if (rapport.optBoolean("change"))
                                chaine.ajouter("state_synced", nodeId, JSONObject()
                                    .put("source", leader).put("epoch", etat.doc.optInt("epoch")))
                        }
                    } else {
                        // anti-entropie légère avec un pair au hasard
                        val vivants = etat.pairsVivants(15.0).filter { it.first != nodeId }
                        if (vivants.isNotEmpty()) {
                            val (id, p) = vivants.random()
                            val (s, r) = transport.get(p.optString("ip"), p.optInt("port"), "/state/digest", 2.0)
                            if (s == 200 && r?.optString("digest") != etat.digest()) {
                                transport.get(p.optString("ip"), p.optInt("port"), "/registry", 3.0)
                                    .second?.optJSONObject("state")?.let { etat.fusionner(it) }
                            }
                        }
                    }
                } catch (_: Exception) {
                }
            }
        }
        fil.name = "sync"
        fil.isDaemon = true
        fil.start()
    }

    // -- handlers ----------------------------------------------------------

    fun surJoin(corps: JSONObject, ip: String) = consensus.surJoin(corps, ip)
    fun surBattement(corps: JSONObject, ip: String) = consensus.surBattement(corps, ip)
    fun surLeaderBeat(corps: JSONObject, ip: String) = consensus.surLeaderBeat(corps)
    fun surProclamation(corps: JSONObject, ip: String) = consensus.surProclamation(corps)

    fun surSync(corps: JSONObject, ip: String): Pair<Int, JSONObject> {
        val distant = corps.optJSONObject("state")
        val rapport = etat.fusionner(distant)
        return 200 to JSONObject().put("ok", true).put("digest", etat.digest())
            .put("epoch", etat.doc.optInt("epoch")).put("change", rapport.optBoolean("change"))
    }

    fun surSante(corps: JSONObject, ip: String) = 200 to JSONObject().put("ok", true)
        .put("node_id", nodeId).put("role", consensus.role.name.lowercase())
        .put("epoch", etat.doc.optInt("epoch"))

    fun surStatut(corps: JSONObject, ip: String): Pair<Int, JSONObject> {
        val pairs = JSONArray()
        for (id in etat.pairs().keySet().sorted()) {
            val p = etat.pairs().getJSONObject(id)
            pairs.put(JSONObject().put("node_id", id)
                .put("addr", "${p.optString("ip")}:${p.optInt("port")}")
                .put("mode", p.optString("mode", "full"))
                .put("capabilities", p.optJSONArray("capabilities") ?: JSONArray())
                .put("models", p.optJSONArray("models") ?: JSONArray())
                .put("alive", p.optBoolean("alive", false))
                .put("last_seen", p.opt("last_seen") ?: 0))
        }
        return 200 to JSONObject()
            .put("node_id", nodeId).put("addr", "$ipAnnonce:$portAnnonce")
            .put("swarm_id", essaim).put("mode", mode)
            .put("consensus", consensus.instantane())
            .put("state", JSONObject()
                .put("epoch", etat.doc.optInt("epoch"))
                .put("version", etat.doc.optInt("version"))
                .put("digest", etat.digest())
                .put("peer_count", pairs.length())
                .put("alive_count", (0 until pairs.length()).count {
                    pairs.getJSONObject(it).optBoolean("alive") }))
            .put("whitelist_digest", whitelist.digest())
            .put("whitelist_count", whitelist.noeuds().size)
            .put("crypto_mode", "kotlin-jvm")
            .put("implementation", "kotlin-jvm")
            .put("audit", chaine.verifier())
            .put("peers", pairs)
    }

    fun surRegistre(corps: JSONObject, ip: String) =
        200 to JSONObject().put("state", etat.versDict()).put("from", nodeId)

    fun surDigest(corps: JSONObject, ip: String) = 200 to JSONObject()
        .put("epoch", etat.doc.optInt("epoch")).put("digest", etat.digest())
        .put("node_id", nodeId)

    fun surSavoir(corps: JSONObject, ip: String): Pair<Int, JSONObject> {
        val entrees = mutableListOf<JSONObject>()
        for (cle in etat.doc.optJSONObject("meta")!!.keySet()) {
            if (cle.startsWith("knowledge:")) {
                entrees.add(JSONObject(etat.doc.optJSONObject("meta")!!.getJSONObject(cle).toString())
                    .put("key", cle))
            }
        }
        entrees.sortByDescending { it.optDouble("ts", 0.0) }
        return 200 to JSONObject().put("count", entrees.size)
            .put("digest", Sha.hex(Json.canonical(entrees)))
            .put("entries", JSONArray(entrees))
    }

    fun surAudit(corps: JSONObject, ip: String) = 200 to chaine.verifier()

    /** /ask — mock + pare-feu OSP v0.6 + savoir commun (comme le Python). */
    fun surAsk(corps: JSONObject, ip: String): Pair<Int, JSONObject> {
        val t0 = System.currentTimeMillis() / 1000.0
        val question = corps.optString("prompt")
        if (question.isEmpty()) return 400 to JSONObject().put("error", "champ 'prompt' requis")

        // Délégation au leader si follower (marqueur anti-boucle absent).
        if (!corps.has("_fwd") && consensus.role != Role.LEADER && consensus.leaderId != null) {
            etat.pairs().optJSONObject(consensus.leaderId!!)?.let { p ->
                val transfere = JSONObject(corps.toString()).put("_fwd", 1)
                val (s, r) = transport.post(p.optString("ip"), p.optInt("port"),
                                            "/ask", transfere, 95.0)
                if (s == 200 && r != null) return 200 to r.put("served_by", consensus.leaderId!!)
            }
        }

        // Sources ancrées : celles de la requête, sinon corpus local.
        val sources: JSONArray =
            if (corps.has("sources")) corps.optJSONArray("sources") ?: JSONArray()
            else JSONArray(listOf("osp://local/djerba-wiki", "osp://local/manuel-svt"))
        val n = sources.length()
        val g = when {
            n <= 0 -> 0.0
            else -> minOf(1.0, 0.4 + 0.2 * (n - 1))
        }
        val verifie = g >= 0.30
        val osp = JSONObject()
            .put("groundedness", Math.round(g * 1000.0) / 1000.0)
            .put("threshold", 0.30)
            .put("verdict", if (verifie) "VERIFIED" else "UNVERIFIED")
            .put("rag_eligible", verifie)
            .put("note_osp", if (verifie) "Réponse ancrée admise dans le RAG distribué."
                else "Pare-feu OSP : véracité ancrée insuffisante (g < 0.30). " +
                     "Réponse délivrée mais EXCLUE du RAG distribué.")

        val ecoule = System.currentTimeMillis() / 1000.0 - t0
        chaine.ajouter(
            "request_routed", corps.optString("student_id").ifEmpty { nodeId },
            JSONObject().put("student_id", corps.optString("student_id"))
                .put("prompt_sha256", Sha.hex(question))
                .put("provider", nodeId).put("model", "mock-qwen-0.5b")
                .put("osp", osp),
            processingTime = ecoule)

        // Savoir commun : toute réponse VERIFIED entre dans l'état distribué.
        var partage: JSONObject? = null
        if (verifie) {
            val cle = "knowledge:" + Sha.hex(question).take(12)
            etat.setMeta(cle, JSONObject()
                .put("q_sha256", Sha.hex(question))
                .put("student_id", corps.optString("student_id"))
                .put("sources", sources)
                .put("groundedness", osp.optDouble("groundedness"))
                .put("provider", nodeId).put("model", "mock-qwen-0.5b")
                .put("epoch", etat.doc.optInt("epoch"))
                .put("ts", java.math.BigDecimal(
                    Json.pythonFloat(Math.round(System.currentTimeMillis() / 10.0) / 1000.0))))
            chaine.ajouter("knowledge_shared", corps.optString("student_id").ifEmpty { nodeId },
                JSONObject().put("key", cle).put("groundedness", osp.optDouble("groundedness")))
            partage = JSONObject().put("knowledge_key", cle).put("shared", true)
        }

        return 200 to JSONObject()
            .put("answer", "[mock-kotlin] Voici une réponse ancrée de démonstration à : « " +
                question.take(120) + " ». Le nœud Kotlin SwarmGlu fonctionne hors-ligne.")
            .put("model", "mock-qwen-0.5b")
            .put("provider", nodeId)
            .put("sources", sources)
            .put("processing_time", Math.round(ecoule * 1000.0) / 1000.0)
            .put("request_time", t0)
            .put("osp", osp)
            .put("epoch", etat.doc.optInt("epoch"))
            .put("knowledge", partage ?: JSONObject.NULL)
    }

    fun surRelais(corps: JSONObject, ip: String): Pair<Int, JSONObject> {
        val hote = corps.optString("host")
        val port = corps.optInt("port", 80)
        if (hote.isEmpty()) return 400 to JSONObject().put("error", "host requis")
        if (!estPrivee(hote)) return 403 to JSONObject()
            .put("error", "relais limité aux réseaux privés (RFC1918/loopback)")
        val chemin = corps.optString("path", "/")
        val methode = corps.optString("method", "GET").uppercase()
        val t0 = System.currentTimeMillis() / 1000.0
        val (statut, reponse) = if (methode == "POST")
            transport.post(hote, port, chemin, corps.optJSONObject("body")
                ?: JSONObject(), 30.0)
        else if (methode == "GET") transport.get(hote, port, chemin, 30.0)
        else return 400 to JSONObject().put("error", "methode GET ou POST uniquement")
        chaine.ajouter("relayed", nodeId, JSONObject()
            .put("host", hote).put("port", port).put("path", chemin)
            .put("method", methode).put("status", statut),
            processingTime = System.currentTimeMillis() / 1000.0 - t0)
        return 200 to JSONObject().put("status", statut).put("data", reponse ?: JSONObject.NULL)
            .put("relayed_by", nodeId)
    }

    private fun estPrivee(hote: String): Boolean = try {
        val addr = java.net.InetAddress.getByName(hote)
        addr.isLoopbackAddress || addr.isSiteLocalAddress
    } catch (_: Exception) {
        false
    }

    private fun repondre(echange: HttpExchange, statut: Int, corps: JSONObject?) {
        // Fil CANONIQUE : garantit les flottants Python (« 0.0 » et non « 0 »)
        // sur le câble — les digest trans-langage en dépendent.
        val octets = corps?.let { Json.canonical(it).toByteArray(Charsets.UTF_8) } ?: ByteArray(0)
        echange.responseHeaders.add("Access-Control-Allow-Origin", "*")
        echange.sendResponseHeaders(statut, if (corps == null) -1 else octets.size.toLong())
        if (corps != null) echange.responseBody.use { it.write(octets) }
        echange.close()
    }

    fun adresse() = "$ipAnnonce:$portAnnonce"
}
