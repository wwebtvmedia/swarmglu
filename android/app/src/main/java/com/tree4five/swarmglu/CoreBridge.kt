package com.tree4five.swarmglu

import com.tree4five.swarmglu.core.NoeudAndroid
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/**
 * Pont UI -> NOYAU KOTLIN NATIF (phase 2 — remplace PythonBridge/Chaquopy).
 * Même surface d'API : les fragments n'ont que l'import à changer.
 */
object CoreBridge {

    private var noeud: NoeudAndroid? = null
    private val verrou = ReentrantLock()

    fun nodeStart(config: JSONObject): JSONObject {
        verrou.withLock {
            noeud?.let { return JSONObject().put("ok", true).put("already_running", true)
                .put("node_id", it.nodeId) }
            val dossier = config.optString("data_dir").ifEmpty {
                "${appContext?.filesDir?.absolutePath ?: "/data/local/tmp"}/node" }
            val graines = ArrayList<String>()
            val arr = config.optJSONArray("peers") ?: JSONArray()
            for (i in 0 until arr.length()) graines.add(arr.getString(i))
            // Essaims voisins (mode ambassador) : Digest Exchange passerelle.
            val essaimsVoisins = ArrayList<String>()
            val voisinArr = config.optJSONArray("neighbors") ?: JSONArray()
            for (i in 0 until voisinArr.length())
                voisinArr.optString(i).takeIf { v -> v.isNotEmpty() }?.let(essaimsVoisins::add)
            return try {
                val n = NoeudAndroid(
                    nodeId = config.optString("node_id", "android-node"),
                    port = config.optInt("port", 7001),
                    ipAnnonce = config.optString("advertise_ip",
                        com.tree4five.swarmglu.core.ipLocale()),
                    portAnnonce = config.optInt("advertise_port", config.optInt("port", 7001)),
                    mode = config.optString("mode", "follower"),
                    essaim = config.optString("swarm_id", "salle-1"),
                    dossierDonnees = dossier,
                    cheminWhitelist = config.optString("whitelist_file",
                        "$dossier/whitelist.json"),
                    heartbeat = config.optDouble("heartbeat_interval", 3.0),
                    bail = config.optDouble("lease_timeout", 6.0),
                    graines = graines,
                    ggufUrl = config.optString("gguf_url", ""),
                    // Le pilote transmet une chaîne « a,b » (comme peers).
                    ggufModeles = config.optString("gguf_models", "").split(",")
                        .map { it.trim() }.filter { it.isNotEmpty() },
                    ggufToken = config.optString("gguf_token", ""),
                    voisinsEssaims = essaimsVoisins)
                n.demarrer()
                noeud = n
                JSONObject().put("ok", true).put("node_id", n.nodeId)
                    .put("ip", n.ipAnnonce).put("port", n.portAnnonce)
                    .put("crypto_mode", n.crypto.mode)
            } catch (e: Exception) {
                JSONObject().put("ok", false).put("error", "$e")
            }
        }
    }

    fun nodeStop(): JSONObject {
        verrou.withLock {
            noeud?.arreter()
            noeud = null
        }
        return JSONObject().put("ok", true)
    }

    fun isRunning(): Boolean = verrou.withLock { noeud != null }

    private fun noeudOuErreur(): Pair<NoeudAndroid?, JSONObject?> {
        val n = verrou.withLock { noeud }
        return if (n == null) null to JSONObject()
            .put("error", "nœud SwarmGlu non démarré (activez-le sur le tableau de bord)")
        else n to null
    }

    fun status(): JSONObject {
        val (n, err) = noeudOuErreur()
        if (n == null) return err!!
        val (s, p) = n.transport.get("127.0.0.1", n.port, "/status", 5.0)
        return p ?: err!!
    }

    fun ask(prompt: String, student: String, model: String, swarm: String): JSONObject {
        val (n, err) = noeudOuErreur()
        if (n == null) return err!!
        val corps = JSONObject().put("prompt", prompt)
            .put("student_id", student.ifEmpty { "eleve-android" })
        if (model.isNotEmpty()) corps.put("model_hint", model)
        if (swarm.isNotEmpty()) corps.put("swarm_id", swarm)
        // Le transport peut lever (réseau coupé, port mort…) : renvoyer un
        // JSON d'erreur plutôt que de faire échouer tout le thread arrière.
        val (s, p) = try {
            n.transport.post("127.0.0.1", n.port, "/ask", corps, 90.0)
        } catch (e: Exception) {
            return JSONObject().put("error", "échec de communication : $e")
                .put("http_status", 0)
        }
        return (p ?: JSONObject()).put("http_status", s)
    }

    fun auditVerify(): JSONObject {
        val (n, err) = noeudOuErreur()
        if (n == null) return err!!
        val (s, p) = n.transport.get("127.0.0.1", n.port, "/audit/verify", 5.0)
        return p ?: err!!
    }

    fun auditTail(nb: Int): JSONObject {
        val (n, err) = noeudOuErreur()
        if (n == null) return err!!
        val (s, p) = n.transport.get("127.0.0.1", n.port, "/audit/tail?n=$nb", 5.0)
        return p ?: err!!
    }

    fun agentTask(bodyJson: String): JSONObject {
        val (n, err) = noeudOuErreur()
        if (n == null) return err!!
        val (s, p) = n.transport.post("127.0.0.1", n.port, "/agent/task",
            JSONObject(bodyJson), 15.0)
        return (p ?: JSONObject()).put("http_status", s)
    }

    fun agentTaskValidate(taskId: String, approved: Boolean, by: String): JSONObject {
        val (n, err) = noeudOuErreur()
        if (n == null) return err!!
        val (s, p) = n.transport.post("127.0.0.1", n.port,
            "/agent/task/$taskId/validate",
            JSONObject().put("approved", approved).put("by", by), 30.0)
        return (p ?: JSONObject()).put("http_status", s)
    }

    fun whitelistAdd(nodeId: String): JSONObject {
        val (n, err) = noeudOuErreur()
        if (n == null) return err!!
        val ajoute = n.whitelist.ajouter(nodeId, "android-admin")
        n.etat.setWhitelistNodes(n.whitelist.noeuds())
        return JSONObject().put("ok", true).put("added", ajoute)
            .put("nodes", JSONArray(n.whitelist.noeuds()))
    }

    @Volatile var appContext: android.content.Context? = null

    /**
     * Les appels réseau peuvent durer : toujours hors du thread UI.
     * Une exception dans `bloc` ne doit JAMAIS laisser l'UI figée (bouton
     * désactivé, spinner éternel) : elle est capturée et remontée à
     * `onError` sur le thread principal. `onError` est placé AVANT `onDone`
     * (dernier paramètre) pour que les appels à lambda traînante
     * `onBackground({ … }) { résultat -> … }` continuent de compiler.
     */
    fun <T> onBackground(
        bloc: () -> T,
        onError: (Throwable) -> Unit = { e ->
            android.util.Log.e("SwarmGlu", "appel arrière-plan échoué", e)
        },
        onDone: (T) -> Unit
    ) {
        Thread {
            try {
                val resultat = bloc()
                android.os.Handler(android.os.Looper.getMainLooper()).post { onDone(resultat) }
            } catch (t: Throwable) {
                android.util.Log.e("SwarmGlu", "exception dans onBackground", t)
                android.os.Handler(android.os.Looper.getMainLooper()).post { onError(t) }
            }
        }.apply { name = "swarmglu-ui-call" }.start()
    }
}
