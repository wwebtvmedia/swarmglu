package com.tree4five.swarmglu.core

import fi.iki.elonen.NanoHTTPD
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Nœud SwarmGlu NATIF Android (phase 2) : le noyau Kotlin (core/) servi par
 * NanoHTTPD (Android n'a pas com.sun.net.httpserver). Mêmes routes que le
 * nœud Python/JVM — interopérable câble, avec en plus la crypto
 * post-quantique NATIVE (/secure/handshake, /secure/rpc).
 */
class NoeudAndroid(
    val nodeId: String,
    val port: Int,
    val ipAnnonce: String,
    val portAnnonce: Int,
    val mode: String = "full",
    val essaim: String = "salle-android",
    dossierDonnees: String,
    cheminWhitelist: String,
    heartbeat: Double = 3.0,
    bail: Double = 6.0,
    val graines: List<String> = emptyList(),
) {
    val whitelist = Whitelist(cheminWhitelist)
    val chaine = HashChain("$dossierDonnees/swarm_audit.jsonl")
    val etat = StateManager(nodeId, whitelist)
    val transport = HttpJson()
    val crypto = NodeCrypto(nodeId, dossierDonnees)
    val consensus = ConsensusManager(
        JSONObject().put("node_id", nodeId).put("ip", ipAnnonce)
            .put("port", portAnnonce).put("mode", mode),
        etat, transport, chaine, ConsensusConfig(heartbeat, bail))
    val sessionsSecures = HashMap<String, ByteArray>()
    private val taches = HashMap<String, JSONObject>()
    private val verrouTaches = Any()
    private val serveur: Serveur

    init {
        File(dossierDonnees).mkdirs()
        serveur = Serveur(port)
    }

    fun demarrer() {
        if (!whitelist.estAutorise(nodeId)) whitelist.ajouter(nodeId, "self-bootstrap")
        etat.setWhitelistNodes(whitelist.noeuds())
        val capacites = mutableListOf("node", "inference")
        if (mode in setOf("full", "ambassador")) capacetesAdd(capacites)
        etat.declarerSoi(capacites, listOf("mock-qwen-0.5b"))
        val champs = HashMap<String, Any>()
        champs["ip"] = ipAnnonce; champs["port"] = portAnnonce; champs["mode"] = mode
        champs["capabilities"] = JSONArray(capacites)
        champs["models"] = JSONArray(listOf("mock-qwen-0.5b"))
        champs["api_flavor"] = "mock"
        etat.majPair(nodeId, champs)
        etat.toucher(nodeId)
        for (g in graines) {
            val h = g.substringBeforeLast(":"); val p = g.substringAfterLast(":").toInt()
            etat.majPair("seed-$g", mapOf("ip" to h, "port" to p))
            etat.toucher("seed-$g")
        }
        serveur.start(NanoHTTPD.SOCKET_READ_TIMEOUT, false)
        chaine.ajouter("node_start", nodeId, JSONObject()
            .put("ip", ipAnnonce).put("port", portAnnonce).put("mode", mode)
            .put("swarm_id", essaim).put("implementation", "android-kotlin-native"))
        consensus.demarrer()
        filSync()
    }

    private fun capacetesAdd(c: MutableList<String>) { c.add("harnessdroid") }

    fun arreter() { serveur.stop() }

    private fun filSync() {
        Thread {
            Thread.sleep(3000)
            while (true) {
                Thread.sleep(5000)
                try {
                    val leader = consensus.leaderId
                    val cible: JSONObject? = if (leader != null && leader != nodeId)
                        etat.pairs().optJSONObject(leader) else null
                    if (cible != null) {
                        transport.get(cible.optString("ip"), cible.optInt("port"), "/registry", 3.0)
                            .second?.optJSONObject("state")?.let { etat.fusionner(it) }
                    } else {
                        val vivants = etat.pairsVivants(15.0).filter { it.first != nodeId }
                        if (vivants.isNotEmpty()) {
                            val p = vivants.random().second
                            val (s, r) = transport.get(p.optString("ip"), p.optInt("port"), "/state/digest", 2.0)
                            if (s == 200 && r?.optString("digest") != etat.digest()) {
                                transport.get(p.optString("ip"), p.optInt("port"), "/registry", 3.0)
                                    .second?.optJSONObject("state")?.let { etat.fusionner(it) }
                            }
                        }
                    }
                } catch (_: Exception) {}
            }
        }.apply { name = "sync"; isDaemon = true }.start()
    }

    // ------------------------------------------------------------------
    // Serveur HTTP (NanoHTTPD) — mêmes routes/réponses que le Python
    // ------------------------------------------------------------------
    private inner class Serveur(port: Int) : NanoHTTPD("0.0.0.0", port) {
        override fun serve(session: IHTTPSession): Response {
            val chemin = session.uri.trimEnd('/')
            val cheminNorm = if (chemin.isEmpty()) "/" else chemin
            // Route à motif : /agent/task/{id}/validate
            val correspondance = Regex("^/agent/task/([^/]+)/validate$").find(cheminNorm)
            if (correspondance != null && session.method == NanoHTTPD.Method.POST) {
                val corpsVal: JSONObject = try {
                    JSONObject(session.inputStream.readBytes().toString(Charsets.UTF_8))
                } catch (_: Exception) { JSONObject() }
                val (s, r) = surValidationTache(correspondance.groupValues[1], corpsVal)
                return newFixedLengthResponse(
                    Response.Status.lookup(s) ?: Response.Status.INTERNAL_ERROR,
                    "application/json", Json.canonical(r))
            }
            try {
                val corps: JSONObject = if (session.method == NanoHTTPD.Method.POST) {
                    val taille = Integer.parseInt(
                        session.headers.get("content-length") ?: "0")
                    val octets = ByteArray(taille)
                    var lus = 0
                    while (lus < taille) {
                        val n = session.inputStream.read(octets, lus, taille - lus)
                        if (n < 0) break
                        lus += n
                    }
                    if (lus == 0) JSONObject() else JSONObject(String(octets, 0, lus, Charsets.UTF_8))
                } else JSONObject()
                val ipClient = session.headers.get("http-client-ip")
                    ?: session.remoteIpAddress ?: ""
                val (statut, reponse) = router(cheminNorm, session.method, corps, ipClient)
                return newFixedLengthResponse(
                    Response.Status.lookup(statut) ?: Response.Status.OK,
                    "application/json",
                    Json.canonical(reponse))
            } catch (e: Exception) {
                return newFixedLengthResponse(Response.Status.INTERNAL_ERROR,
                    "application/json",
                    Json.canonical(JSONObject().put("error", "erreur interne: $e")))
            }
        }
    }

    private fun router(chemin: String, methode: NanoHTTPD.Method, corps: JSONObject, ip: String)
            : Pair<Int, JSONObject> = when (chemin to methode) {
        "/join" to NanoHTTPD.Method.POST -> consensus.surJoin(corps, ip)
        "/heartbeat" to NanoHTTPD.Method.POST -> consensus.surBattement(corps, ip)
        "/leader-beat" to NanoHTTPD.Method.POST -> consensus.surLeaderBeat(corps)
        "/leader-announce" to NanoHTTPD.Method.POST -> consensus.surProclamation(corps)
        "/state/sync" to NanoHTTPD.Method.POST -> 200 to JSONObject().put("ok", true)
            .put("digest", etat.digest()).put("epoch", etat.doc.optInt("epoch"))
            .also { etat.fusionner(corps.optJSONObject("state")) }
        "/ask" to NanoHTTPD.Method.POST -> surAsk(corps)
        "/relay" to NanoHTTPD.Method.POST -> surRelais(corps)
        "/agent/task" to NanoHTTPD.Method.POST -> surTache(corps)
        "/agent/dispatch" to NanoHTTPD.Method.POST -> surDispatch(corps)
        "/secure/handshake" to NanoHTTPD.Method.POST -> surHandshake(corps)
        "/secure/rpc" to NanoHTTPD.Method.POST -> surRpcSecurise(corps)
        "/health" to NanoHTTPD.Method.GET -> 200 to JSONObject().put("ok", true)
            .put("node_id", nodeId).put("role", consensus.role.name.lowercase())
            .put("epoch", etat.doc.optInt("epoch"))
        "/status" to NanoHTTPD.Method.GET -> surStatut()
        "/registry" to NanoHTTPD.Method.GET -> 200 to JSONObject()
            .put("state", etat.versDict()).put("from", nodeId)
        "/state/digest" to NanoHTTPD.Method.GET -> 200 to JSONObject()
            .put("epoch", etat.doc.optInt("epoch")).put("digest", etat.digest())
            .put("node_id", nodeId)
        "/knowledge" to NanoHTTPD.Method.GET -> surSavoir()
        "/audit/verify" to NanoHTTPD.Method.GET -> 200 to chaine.verifier()
        "/audit/tail" to NanoHTTPD.Method.GET -> 200 to JSONObject()
            .put("events", JSONArray(chaine.derniers(20)))
        "/agent/task" to NanoHTTPD.Method.GET ->
            (synchronized(verrouTaches) { taches[corps.optString("task_id")] }?.let {
                200 to JSONObject().put("task", JSONObject(it.toString()))
            } ?: 404 to JSONObject().put("error", "tâche inconnue"))
        else -> 404 to JSONObject().put("error", "route inconnue").put("path", chemin)
    }

    private fun surStatut(): Pair<Int, JSONObject> {
        val pairsJson = JSONArray()
        for (id in cles(etat.pairs()).sorted()) {
            val p = etat.pairs().getJSONObject(id)
            pairsJson.put(JSONObject().put("node_id", id)
                .put("addr", "${p.optString("ip")}:${p.optInt("port")}")
                .put("mode", p.optString("mode", "full"))
                .put("capabilities", p.optJSONArray("capabilities") ?: JSONArray())
                .put("models", p.optJSONArray("models") ?: JSONArray())
                .put("alive", p.optBoolean("alive", false))
                .put("last_seen", if (p.has("last_seen")) p.get("last_seen") else 0))
        }
        return 200 to JSONObject()
            .put("node_id", nodeId).put("addr", "$ipAnnonce:$portAnnonce")
            .put("swarm_id", essaim).put("mode", mode)
            .put("consensus", consensus.instantane())
            .put("state", JSONObject()
                .put("epoch", etat.doc.optInt("epoch"))
                .put("version", etat.doc.optInt("version"))
                .put("digest", etat.digest())
                .put("peer_count", pairsJson.length())
                .put("alive_count", (0 until pairsJson.length())
                    .count { pairsJson.getJSONObject(it).optBoolean("alive") }))
            .put("whitelist_digest", whitelist.digest())
            .put("whitelist_count", whitelist.noeuds().size)
            .put("crypto_mode", crypto.mode)
            .put("implementation", "android-kotlin-native")
            .put("audit", chaine.verifier())
            .put("peers", pairsJson)
    }

    private fun surSavoir(): Pair<Int, JSONObject> {
        val entrees = mutableListOf<JSONObject>()
        val meta = etat.doc.optJSONObject("meta") ?: JSONObject()
        for (cle in cles(meta))
            if (cle.startsWith("knowledge:"))
                entrees.add(JSONObject(meta.getJSONObject(cle).toString()).put("key", cle))
        entrees.sortByDescending { it.optDouble("ts", 0.0) }
        return 200 to JSONObject().put("count", entrees.size)
            .put("digest", Sha.hex(Json.canonical(entrees)))
            .put("entries", JSONArray(entrees))
    }

    private fun surAsk(corps: JSONObject): Pair<Int, JSONObject> {
        val t0 = System.currentTimeMillis() / 1000.0
        val question = corps.optString("prompt")
        if (question.isEmpty()) return 400 to JSONObject().put("error", "champ 'prompt' requis")
        if (!corps.has("_fwd") && consensus.role != Role.LEADER && consensus.leaderId != null) {
            etat.pairs().optJSONObject(consensus.leaderId!!)?.let { p ->
                val transfere = JSONObject(corps.toString()).put("_fwd", 1)
                val (s, r) = transport.post(p.optString("ip"), p.optInt("port"), "/ask", transfere, 95.0)
                if (s == 200 && r != null) return 200 to r.put("served_by", consensus.leaderId!!)
            }
        }
        val sources: JSONArray =
            if (corps.has("sources")) corps.optJSONArray("sources") ?: JSONArray()
            else JSONArray(listOf("osp://local/djerba-wiki", "osp://local/manuel-svt"))
        val n = sources.length()
        val g = if (n <= 0) 0.0 else minOf(1.0, 0.4 + 0.2 * (n - 1))
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
        chaine.ajouter("request_routed",
            corps.optString("student_id").ifEmpty { nodeId },
            JSONObject().put("student_id", corps.optString("student_id"))
                .put("prompt_sha256", Sha.hex(question))
                .put("provider", nodeId).put("model", "mock-qwen-0.5b")
                .put("osp", osp),
            processingTime = ecoule)
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
                .put("ts", Math.round(System.currentTimeMillis() / 10.0) / 1000.0))
            chaine.ajouter("knowledge_shared",
                corps.optString("student_id").ifEmpty { nodeId },
                JSONObject().put("key", cle)
                    .put("groundedness", osp.optDouble("groundedness")))
            partage = JSONObject().put("knowledge_key", cle).put("shared", true)
        }
        return 200 to JSONObject()
            .put("answer", "[android-kotlin] Réponse ancrée native : « " +
                question.take(120) + " » — nœud Kotlin pur, crypto PQ incluse.")
            .put("model", "mock-qwen-0.5b").put("provider", nodeId)
            .put("sources", sources)
            .put("processing_time", Math.round(ecoule * 1000.0) / 1000.0)
            .put("request_time", t0)
            .put("osp", osp).put("epoch", etat.doc.optInt("epoch"))
            .put("knowledge", partage ?: JSONObject.NULL)
    }

    /** Cycle HITL identique au Python : pending_validation -> done, unique. */
    private fun surTache(corps: JSONObject): Pair<Int, JSONObject> {
        val tid = Sha.hex("$nodeId|${corps.optString("student_id")}|${System.nanoTime()}").take(16)
        val tache = JSONObject()
            .put("task_id", tid)
            .put("student_id", corps.optString("student_id"))
            .put("mcp", corps.optJSONObject("mcp") ?: JSONObject())
            .put("status", "pending_validation")
            .put("created_at", System.currentTimeMillis() / 1000.0)
        synchronized(verrouTaches) { taches[tid] = tache }
        chaine.ajouter("agent_task_created",
            corps.optString("student_id").ifEmpty { nodeId },
            JSONObject().put("task_id", tid))
        return 200 to JSONObject().put("task", JSONObject(tache.toString()))
            .put("hint", "POST /agent/task/$tid/validate pour lever la barrière HITL")
    }

    private fun surDispatch(corps: JSONObject): Pair<Int, JSONObject> {
        val tache = corps.optJSONObject("task") ?: JSONObject()
        if (tache.optString("status") != "approved")
            return 409 to JSONObject().put("error", "tâche non approuvée (barrière HITL)")
        val tid = tache.optString("task_id")
        val resultat = JSONObject().put("status", "executed").put("mode", "echo")
            .put("transcript", JSONArray(listOf(
                "[HITL] validation humaine obtenue",
                "[MCP ] exécution native Kotlin (mock)")))
        synchronized(verrouTaches) {
            taches[tid]?.put("status", "done")?.put("result", resultat)
        }
        chaine.ajouter("agent_task_result", nodeId, JSONObject().put("task_id", tid))
        return 200 to JSONObject().put("ok", true).put("task_id", tid).put("result", resultat)
    }

    private fun surValidationTache(tid: String, corps: JSONObject): Pair<Int, JSONObject> {
        val tache = synchronized(verrouTaches) { taches[tid] }
            ?: return 404 to JSONObject().put("error", "tâche inconnue")
        if (tache.optString("status") != "pending_validation")
            return 409 to JSONObject().put("error", "tâche déjà traitée")
                .put("status", tache.optString("status"))
        tache.put("approved_by", corps.optString("by", "enseignant"))
        if (!corps.optBoolean("approved", false)) {
            tache.put("status", "rejected")
            chaine.ajouter("agent_task_validated", nodeId,
                JSONObject().put("task_id", tid).put("approved", false))
            return 200 to JSONObject().put("task_id", tid).put("status", "rejected")
        }
        tache.put("status", "approved")
        chaine.ajouter("agent_task_validated", nodeId,
            JSONObject().put("task_id", tid).put("approved", true))
        val (s, p) = surDispatch(JSONObject().put("task", JSONObject(tache.toString())))
        return s to p.put("task", JSONObject(taches[tid].toString()))
    }

    private fun surRelais(corps: JSONObject): Pair<Int, JSONObject> {
        val hote = corps.optString("host"); val portCible = corps.optInt("port", 80)
        if (hote.isEmpty()) return 400 to JSONObject().put("error", "host requis")
        val privee = try {
            val a = java.net.InetAddress.getByName(hote)
            a.isLoopbackAddress || a.isSiteLocalAddress
        } catch (_: Exception) { false }
        if (!privee) return 403 to JSONObject()
            .put("error", "relais limité aux réseaux privés (RFC1918/loopback)")
        val chemin = corps.optString("path", "/")
        val methode = corps.optString("method", "GET").uppercase()
        val (statut, reponse) = if (methode == "POST")
            transport.post(hote, portCible, chemin,
                corps.optJSONObject("body") ?: JSONObject(), 30.0)
        else if (methode == "GET") transport.get(hote, portCible, chemin, 30.0)
        else return 400 to JSONObject().put("error", "methode GET ou POST uniquement")
        chaine.ajouter("relayed", nodeId, JSONObject()
            .put("host", hote).put("port", portCible).put("path", chemin)
            .put("method", methode).put("status", statut))
        return 200 to JSONObject().put("status", statut)
            .put("data", reponse ?: JSONObject.NULL).put("relayed_by", nodeId)
    }

    // -- crypto PQ native -----------------------------------------------------

    private fun surHandshake(corps: JSONObject): Pair<Int, JSONObject> {
        if (corps.optString("type") != "handshake_init")
            return 400 to JSONObject().put("error", "Type de handshake invalide")
        return try {
            val (rep, session) = crypto.respondHandshake(corps)
            sessionsSecures[corps.optString("sender_id")] = session
            chaine.ajouter("secure_handshake_established", nodeId, JSONObject()
                .put("peer", corps.optString("sender_id"))
                .put("post_quantum", "ML-KEM-768").put("auth", "ECDSA-P256"))
            200 to rep
        } catch (e: Exception) {
            400 to JSONObject().put("error", "Handshake refusé : $e")
        }
    }

    private fun surRpcSecurise(corps: JSONObject): Pair<Int, JSONObject> {
        val emetteur = corps.optString("sender_id", "")
        val session = sessionsSecures[emetteur]
            ?: return 401 to JSONObject()
                .put("error", "Session non authentifiée (faire handshake au préalable)")
        return try {
            val clair = String(NodeCrypto.decryptPayload(
                session, corps.optString("payload", "")), Charsets.UTF_8)
            val requete = JSONObject(clair)
            val action = requete.optString("action", "ping")
            val donnees: JSONObject = when (action) {
                "ping" -> JSONObject().put("status", "ok").put("action", "pong")
                    .put("ts", System.currentTimeMillis() / 1000.0)
                "get_whitelist" -> JSONObject()
                    .put("nodes", JSONArray(whitelist.noeuds()))
                    .put("digest", whitelist.digest())
                    .put("signed", false)
                else -> JSONObject().put("status", "ok").put("action", action)
                    .put("echo", requete)
            }
            200 to JSONObject().put("status", "ok").put("sender_id", nodeId)
                .put("payload", NodeCrypto.encryptPayload(session, donnees.toString()))
        } catch (e: Exception) {
            400 to JSONObject().put("error", "Erreur RPC sécurisé : $e")
        }
    }
}
