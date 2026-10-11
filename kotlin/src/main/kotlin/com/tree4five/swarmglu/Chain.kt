package com.tree4five.swarmglu

import org.json.JSONObject
import java.io.File
import java.time.Instant
import java.time.format.DateTimeFormatter

/**
 * Chaîne de hachage immuable — PORTAGE BIT À BIT de security.HashChain.
 *
 * Scellement identique au Python :
 *     hash = sha256(canonical(event_data) + previous_hash + str(request_time))
 * avec event_data = {seq, event_type, node_id, payload, request_time,
 * processing_time}. Les vecteurs golden (générés par le Python) prouvent
 * l'égalité des hash séquence par séquence.
 */
class HashChain(chemin: String) {

    private val fichier = File(chemin)
    private var seq = 0
    private var dernierHash = "0".repeat(64)

    init {
        if (fichier.exists()) {
            val rapport = verifier()
            if (!rapport.optBoolean("ok")) {
                throw IllegalStateException(
                    "Journal '${fichier.absolutePath}' corrompu (ligne ${rapport.opt("line")}): ${rapport.optString("error")}")
            }
            seq = rapport.optInt("entries")
            dernierHash = rapport.optString("last_hash")
        }
    }

    private fun maintenantIso(): String =
        DateTimeFormatter.ISO_INSTANT.format(Instant.now().truncatedTo(java.time.temporal.ChronoUnit.MILLIS))
            .replace("Z", "Z")

    private fun donneesEvenement(
        seq: Int, type: String, nodeId: String,
        payload: JSONObject?, requestTime: Double?, processingTime: Double?,
    ): String = Json.canonical(
        linkedMapOf(
            "seq" to seq,
            "event_type" to type,
            "node_id" to nodeId,
            "payload" to (payload ?: JSONObject()),
            "request_time" to requestTime,
            "processing_time" to processingTime,
        )
    )

    /** BigDecimal calé sur la chaîne pythonFloat — org.json sérialise
     *  l'échelle EXACTE (« 0.0 », « 1791712146.294 ») au lieu de rendre
     *  Double 0.0 en « 0 » (qui casserait le canonical trans-langage). */
    private fun bdPythonFloat(x: Double): java.math.BigDecimal =
        java.math.BigDecimal(Json.pythonFloat(x))

    fun ajouter(
        type: String, nodeId: String,
        payload: JSONObject? = null,
        processingTime: Double? = 0.0,
        requestTime: Double? = null,
    ): JSONObject {
        synchronized(this) {
            val rt = (requestTime ?: System.currentTimeMillis() / 1000.0)
            val pt = processingTime ?: 0.0
            val corps = donneesEvenement(seq + 1, type, nodeId, payload, rt, pt)
            val sceau = Sha.hex(corps + dernierHash + Json.pythonFloat(rt))
            val bloc = JSONObject()
                .put("seq", seq + 1)
                .put("timestamp", maintenantIso())
                .put("request_time", bdPythonFloat(rt))
                .put("processing_time", bdPythonFloat(pt))
                .put("previous_hash", dernierHash)
                .put("hash", sceau)
                .put("event", JSONObject(
                    mapOf(
                        "seq" to (seq + 1), "event_type" to type, "node_id" to nodeId,
                        "payload" to (payload ?: JSONObject()),
                        "request_time" to bdPythonFloat(rt),
                        "processing_time" to bdPythonFloat(pt),
                    )
                ))
            // Sérialisation CANONIQUE (jamais org.json.toString, qui
            // dépouille BigDecimal « 0.0 » en « 0 » et casserait le sceau).
            fichier.appendText(Json.canonical(bloc) + "\n")
            seq += 1
            dernierHash = sceau
            return bloc
        }
    }

    /** Rejoue toute la chaîne (liaison, séquence, recalcul du sceau). */
    fun verifier(): JSONObject {
        val rapport = JSONObject()
            .put("ok", true).put("entries", 0)
            .put("first_hash", "0".repeat(64))
            .put("last_hash", "0".repeat(64))
        if (!fichier.exists()) return rapport
        var precedent = "0".repeat(64)
        var attendu = 1
        var premier: String? = null
        var ligneNo = 0
        for (ligne in fichier.readLines()) {
            ligneNo += 1
            val propre = ligne.trim()
            if (propre.isEmpty()) continue
            val bloc: JSONObject = try {
                JSONObject(propre)
            } catch (e: Exception) {
                rapport.put("ok", false).put("line", ligneNo)
                    .put("error", "JSON illisible: $e")
                return rapport
            }
            if (bloc.optString("previous_hash") != precedent) {
                rapport.put("ok", false).put("line", ligneNo)
                    .put("error", "previous_hash non conforme (chaîne rompue)")
                return rapport
            }
            if (bloc.optInt("seq") != attendu) {
                rapport.put("ok", false).put("line", ligneNo)
                    .put("error", "séquence attendue $attendu, trouvée ${bloc.optInt("seq")}")
                return rapport
            }
            val ev = bloc.optJSONObject("event") ?: JSONObject()
            // str(request_time) du JSON : re-canonicaliser l'événement tel
            // que lu (les flottants relus conservent la forme sérialisée).
            val corps = Json.canonical(
                linkedMapOf(
                    "seq" to ev.optInt("seq"),
                    "event_type" to ev.optString("event_type"),
                    "node_id" to ev.optString("node_id"),
                    "payload" to ev.optJSONObject("payload"),
                    "request_time" to ev.opt("request_time"),
                    "processing_time" to ev.opt("processing_time"),
                )
            )
            // Reconstruire le str(float) Python — JAMAIS le toString JVM
            // (qui rend « 1.75E9 ») : c'est lui qui entre dans le sceau.
            val rtStr = when (val brut = bloc.opt("request_time")) {
                is java.math.BigDecimal -> brut.toPlainString()
                else -> Json.pythonFloat(bloc.optDouble("request_time"))
            }
            if (Sha.hex(corps + precedent + rtStr) != bloc.optString("hash")) {
                rapport.put("ok", false).put("line", attendu)
                    .put("error", "hash invalide — bloc altéré")
                return rapport
            }
                if (premier == null) premier = bloc.optString("hash")
            precedent = bloc.optString("hash")
            rapport.put("last_hash", precedent).put("entries", attendu)
            attendu += 1
        }
        if (premier != null) rapport.put("first_hash", premier)
        return rapport
    }

    fun derniers(n: Int): List<JSONObject> {
        if (!fichier.exists()) return emptyList()
        val lignes = fichier.readLines().filter { it.isNotBlank() }
        return lignes.takeLast(n.coerceAtLeast(1)).map { JSONObject(it) }
    }
}

/**
 * Whitelist — même format de fichier que SecurityManager Python
 * ({"version":1, "nodes":[…]}), même digest (sha256 du tri canonique).
 */
class Whitelist(chemin: String) {
    private val fichier = File(chemin)
    private val doc: JSONObject

    init {
        doc = if (fichier.exists()) JSONObject(fichier.readText())
        else JSONObject().put("version", 1).put("nodes", org.json.JSONArray())
        if (!doc.has("nodes")) doc.put("nodes", org.json.JSONArray())
    }

    @Synchronized
    fun noeuds(): List<String> {
        val arr = doc.optJSONArray("nodes") ?: org.json.JSONArray()
        return List(arr.length()) { arr.getString(it) }
    }

    @Synchronized
    fun estAutorise(nodeId: String) = nodeId in noeuds()

    @Synchronized
    fun ajouter(nodeId: String, approuvePar: String = "local-admin"): Boolean {
        val arr = doc.optJSONArray("nodes") ?: org.json.JSONArray().also { doc.put("nodes", it) }
        if (noeuds().contains(nodeId)) return false
        arr.put(nodeId)
        enregistrer()
        return true
    }

    @Synchronized
    fun synchroniser(distants: List<String>): List<String> {
        val avant = noeuds().toSet()
        distants.forEach { if (it !in avant) ajouter(it, "swarm-sync") }
        return noeuds().filter { it !in avant }
    }

    fun digest(): String = Sha.hex(Json.canonical(noeuds().sorted()))

    private fun enregistrer() {
        fichier.parentFile?.mkdirs()
        val tmp = File(fichier.absolutePath + ".tmp")
        tmp.writeText(doc.toString())
        java.nio.file.Files.move(
            tmp.toPath(), fichier.toPath(),
            java.nio.file.StandardCopyOption.REPLACE_EXISTING)
    }
}
