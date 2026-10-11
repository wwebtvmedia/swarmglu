package com.tree4five.swarmglu

import org.json.JSONArray
import org.json.JSONObject

/**
 * État distribué replicué — PORTAGE de state_manager.StateManager.
 * Fusion LWW commutative : chaque entrée arbitrée par (updated_at, writer),
 * epoch maximale, égalité → plus petite leader_key ; volatil (last_seen,
 * alive) TOUJOURS adopté du plus récent, indépendamment du LWW logique.
 * Le digest (sha256 de la projection logique canonique) est IDENTIQUE au
 * Python — prouvé par les vecteurs golden.
 */
class StateManager(val nodeId: String, var whitelist: Whitelist?) {

    val doc = JSONObject()
        .put("epoch", 0).put("leader_id", JSONObject.NULL)
        .put("leader_key", JSONObject.NULL).put("version", 0)
        .put("updated_at", "").put("whitelist_nodes", JSONArray())
        .put("peers", JSONObject()).put("meta", JSONObject())

    private val infosPropres = JSONObject()
        .put("capabilities", JSONArray()).put("models", JSONArray())

    // ------------------------------------------------------------------
    // Registre local
    // ------------------------------------------------------------------

    @Synchronized
    fun pairs(): JSONObject = doc.optJSONObject("peers") ?: JSONObject()

    fun capacitesPropres(): List<String> =
        infosPropres.optJSONArray("capabilities").let { arr ->
            List(arr!!.length()) { arr.getString(it) }
        }

    fun modelesPropres(): List<String> =
        infosPropres.optJSONArray("models").let { arr ->
            List(arr!!.length()) { arr.getString(it) }
        }

    fun declarerSoi(capacites: List<String>, modeles: List<String>) {
        infosPropres.put("capabilities", JSONArray(capacites))
        infosPropres.put("models", JSONArray(modeles))
    }

    /** Champs logiques d'un pair (exclut le volatil last_seen/alive). */
    private fun logiques(p: JSONObject): JSONObject {
        val o = JSONObject()
        listOf("ip", "port", "mode", "capabilities", "models", "api_flavor",
               "base_url", "updated_at", "writer").forEach { k ->
            if (p.has(k)) o.put(k, p.get(k))
        }
        return o
    }

    @Synchronized
    fun majPair(id: String, champs: Map<String, Any?>, ecrivain: String = nodeId): Boolean {
        val pairs = pairs()
        val pair = pairs.optJSONObject(id) ?: JSONObject()
        var change = false
        for ((k, v) in champs) {
            if (k == "last_seen" || k == "alive") continue
            val ancien = if (pair.has(k)) pair.get(k) else null
            if (ancien == null || JSONObject.valueToString(ancien) != JSONObject.valueToString(v)) {
                pair.put(k, v ?: JSONObject.NULL)
                change = true
            }
        }
        if (change) {
            pair.put("writer", ecrivain)
            pair.put("updated_at", arrondi())
            pairs.put(id, pair)
            bump()
        }
        return change
    }

    @Synchronized
    fun toucher(id: String, vivant: Boolean = true) {
        val pairs = pairs()
        val pair = pairs.optJSONObject(id) ?: JSONObject()
        pair.put("last_seen", arrondi())
        pair.put("alive", vivant)
        pairs.put(id, pair)
    }

    @Synchronized
    fun pairsVivants(fenetre: Double): List<Pair<String, JSONObject>> {
        val maintenant = System.currentTimeMillis() / 1000.0
        val sortie = mutableListOf<Pair<String, JSONObject>>()
        val pairs = pairs()
        for (id in pairs.keySet()) {
            val p = pairs.getJSONObject(id)
            if (id == nodeId && p.length() > 0) { sortie.add(id to copieProfonde(p) as JSONObject); continue }
            if (p.optBoolean("alive", false) &&
                maintenant - p.optDouble("last_seen", 0.0) <= fenetre) {
                sortie.add(id to copieProfonde(p) as JSONObject)
            }
        }
        return sortie
    }

    @Synchronized
    fun setLeader(id: String?, cle: String?, epoch: Int) {
        if (epoch >= doc.optInt("epoch")) {
            doc.put("epoch", epoch).put("leader_id", id ?: JSONObject.NULL)
                .put("leader_key", cle ?: JSONObject.NULL)
            bump()
        }
    }

    @Synchronized
    fun setWhitelistNodes(noeuds: List<String>) {
        val fusionnes = (doc.optJSONArray("whitelist_nodes").let { a -> List(a!!.length()) { a.getString(it) } } + noeuds).distinct().sorted()
        if (fusionnes != doc.optJSONArray("whitelist_nodes").let { a -> List(a!!.length()) { a.getString(it) } }) {
            doc.put("whitelist_nodes", JSONArray(fusionnes)); bump()
        }
    }

    @Synchronized
    fun setMeta(cle: String, valeur: Any?) {
        val actuel = doc.optJSONObject("meta")?.opt(cle)
        if (actuel == null || JSONObject.valueToString(actuel) != JSONObject.valueToString(valeur)) {
            doc.optJSONObject("meta")!!.put(cle, valeur ?: JSONObject.NULL)
            bump()
        }
    }

    @Synchronized
    fun getMeta(cle: String): Any? = doc.optJSONObject("meta")?.opt(cle)

    private fun bump() {
        doc.put("version", doc.optInt("version") + 1)
    }

    // ------------------------------------------------------------------
    // Sérialisation & digest
    // ------------------------------------------------------------------

    /**
     * Copie profonde PRÉSERVANT les types numériques — jamais toString()
     * (org.json y rend « 1 » pour Double 1.0, ce qui divergerait du
     * canonical Python « 1.0 » et casserait les digest trans-langage).
     */
    fun copieProfonde(v: Any?): Any? = when (v) {
        is JSONObject -> {
            val o = JSONObject()
            for (k in v.keySet()) o.put(k, copieProfonde(v.get(k)))
            o
        }
        is JSONArray -> {
            val a = JSONArray()
            for (i in 0 until v.length()) a.put(copieProfonde(v.get(i)))
            a
        }
        else -> v
    }

    @Synchronized
    fun versDict(avecVolatile: Boolean = true): JSONObject {
        val copie = copieProfonde(doc) as JSONObject
        val pairsCopie = JSONObject()
        for (id in pairs().keySet()) {
            val p = copieProfonde(pairs().getJSONObject(id)) as JSONObject
            if (!avecVolatile) { p.remove("last_seen"); p.remove("alive") }
            pairsCopie.put(id, p)
        }
        copie.put("peers", pairsCopie)
        return copie
    }

    fun projectionLogique(): JSONObject {
        val proj = versDict(false)
        proj.remove("version"); proj.remove("updated_at")
        return proj
    }

    fun digest(): String = Sha.hex(Json.canonical(projectionLogique()))

    // ------------------------------------------------------------------
    // Fusion LWW commutative (portage exact des règles Python)
    // ------------------------------------------------------------------

    private fun cleLww(e: JSONObject): Pair<Double, String> =
        Pair(e.optDouble("updated_at", 0.0), e.optString("writer", ""))

    @Synchronized
    fun fusionner(distant: JSONObject?): JSONObject {
        val rapport = JSONObject().put("pairs", JSONArray()).put("ajoutes_whitelist", JSONArray())
            .put("leader", false)
        if (distant == null) return rapport.put("change", false)
        var change = false

        // 1) epoch / leader : max ; égalité -> plus petite leader_key
        val rEpoch = distant.optInt("epoch", 0)
        val rLeader = distant.optString("leader_id", "")
        val rCle = distant.optString("leader_key", "")
        if (rEpoch > doc.optInt("epoch")) {
            doc.put("epoch", rEpoch).put("leader_id", rLeader.ifEmpty { JSONObject.NULL })
                .put("leader_key", rCle.ifEmpty { JSONObject.NULL })
            change = true; rapport.put("leader", true)
        } else if (rEpoch == doc.optInt("epoch") && rLeader.isNotEmpty() &&
            rCle.isNotEmpty() && rLeader != doc.optString("leader_id") &&
            (doc.isNull("leader_key") || rCle < doc.optString("leader_key"))) {
            doc.put("leader_id", rLeader).put("leader_key", rCle)
            change = true; rapport.put("leader", true)
        }

        // 2) registre : LWW par (updated_at, writer) + volatil toujours frais
        val pairsDistant = distant.optJSONObject("peers") ?: JSONObject()
        for (id in pairsDistant.keySet()) {
            val rp = pairsDistant.getJSONObject(id)
            val existant = pairs().optJSONObject(id)
            if (existant == null) {
                val entree = logiques(rp)
                entree.put("writer", rp.optString("writer", id))
                entree.put("updated_at", if (rp.has("updated_at")) rp.get("updated_at") else 0)
                entree.put("last_seen", if (rp.has("last_seen")) rp.get("last_seen") else 0)
                entree.put("alive", rp.optBoolean("alive", false))
                pairs().put(id, entree)
                change = true
                rapport.getJSONArray("pairs").put(id)
            } else {
                // Comparaison lexicographique (updated_at, writer) comme
                // les tuples Python : le double d'abord, puis la chaîne.
                val cleDistante = cleLww(rp)
                val cleLocale = cleLww(existant)
                val distanteGagne =
                    if (cleDistante.first != cleLocale.first) cleDistante.first > cleLocale.first
                    else cleDistante.second > cleLocale.second
                if (distanteGagne) {
                    val fusionne = JSONObject(existant.toString())
                    val l = logiques(rp)
                    for (k in l.keySet()) fusionne.put(k, l.get(k))
                    fusionne.put("writer", rp.optString("writer", existant.optString("writer")))
                    fusionne.put("updated_at",
                        if (rp.has("updated_at")) rp.get("updated_at")
                        else existant.get("updated_at"))
                    pairs().put(id, fusionne)
                    change = true
                    rapport.getJSONArray("pairs").put(id)
                }
                val courant = pairs().getJSONObject(id)
                if (rp.optDouble("last_seen", 0.0) > courant.optDouble("last_seen", 0.0)) {
                    val rafraichi = JSONObject(courant.toString())
                    rafraichi.put("last_seen", rp.get("last_seen"))
                    rafraichi.put("alive", rp.optBoolean("alive", false))
                    pairs().put(id, rafraichi)
                }
            }
        }

        // 3) whitelist : union
        val rNoeuds = distant.optJSONArray("whitelist_nodes") ?: JSONArray()
        val avant = doc.optJSONArray("whitelist_nodes").let { a -> List(a!!.length()) { a.getString(it) } }
        val ajoutes = rNoeuds.let { a -> List(a.length()) { a.getString(it) } }.filter { it !in avant }
        if (ajoutes.isNotEmpty()) {
            rapport.put("ajoutes_whitelist", JSONArray(ajoutes))
            setWhitelistNodes(avant + ajoutes)
            change = true
        }

        // 4) meta : remplacement simple si différent
        val rMeta = distant.optJSONObject("meta") ?: JSONObject()
        for (k in rMeta.keySet()) {
            val actuel = doc.optJSONObject("meta")!!.opt(k)
            if (actuel == null || JSONObject.valueToString(actuel) != JSONObject.valueToString(rMeta.get(k))) {
                doc.optJSONObject("meta")!!.put(k, rMeta.get(k)); change = true
            }
        }

        if (change) bump()
        rapport.put("change", change)
        // Appliquer la whitelist propagée au fichier local (comme Python).
        whitelist?.synchroniser(
            doc.optJSONArray("whitelist_nodes").let { a -> List(a!!.length()) { a.getString(it) } })
        return rapport
    }

    private fun arrondi(): Double =
        Math.round(System.currentTimeMillis() / 10.0) / 1000.0 // ms -> s, 3 déc.

}
