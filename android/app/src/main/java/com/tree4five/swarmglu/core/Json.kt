package com.tree4five.swarmglu.core

import org.json.JSONArray
import org.json.JSONObject
import java.math.BigDecimal
import java.math.RoundingMode

/**
 * Sérialisation canonique IDENTIQUE à `security.canonical_json` (Python) :
 * clés triées, séparateurs compacts, UTF-8, tableaux ordre préservé.
 *
 * Point critique — les FLOTTANTS : Python `str(float)` donne le repr le
 * plus court (« 1.5 », « 1.0 », « 1760000000.123 »). Le portage reproduit
 * ce formatage pour tous les nombres produits par les horloges (arrondis
 * à 3 décimales côté chaîne/état) — validé bit à bit par les vecteurs
 * golden générés depuis Python.
 */
object Json {

    fun canonical(valeur: Any?): String {
        val sb = StringBuilder()
        ecrire(valeur, sb)
        return sb.toString()
    }

    private fun ecrire(v: Any?, sb: StringBuilder) {
        when {
            v === null || v === JSONObject.NULL -> sb.append("null")
            v is String -> ecrireChaine(v, sb)
            v is Boolean -> sb.append(if (v) "true" else "false")
            v is Int || v is Long -> sb.append(v.toString())
            v is Double -> sb.append(pythonFloat(v))
            v is Float -> sb.append(pythonFloat(v.toDouble()))
            v is BigDecimal -> sb.append(v.toPlainString())
            v is JSONObject -> {
                sb.append('{')
                val clesListe = v.keys().asSequence().toList().sorted()
                clesListe.forEachIndexed { i, c ->
                    if (i > 0) sb.append(',')
                    ecrireChaine(c, sb)
                    sb.append(':')
                    ecrire(v.get(c), sb)
                }
                sb.append('}')
            }
            v is Map<*, *> -> {
                sb.append('{')
                val cles = v.keys.map { it.toString() }.sorted()
                cles.forEachIndexed { i, c ->
                    if (i > 0) sb.append(',')
                    ecrireChaine(c, sb)
                    sb.append(':')
                    ecrire((v as Map<Any?, Any?>)[c] ?: v.entries.first { it.key.toString() == c }.value, sb)
                }
                sb.append('}')
            }
            v is JSONArray -> {
                sb.append('[')
                for (i in 0 until v.length()) {
                    if (i > 0) sb.append(',')
                    ecrire(v.get(i), sb)
                }
                sb.append(']')
            }
            v is List<*> -> {
                sb.append('[')
                v.forEachIndexed { i, e ->
                    if (i > 0) sb.append(',')
                    ecrire(e, sb)
                }
                sb.append(']')
            }
            else -> ecrireChaine(v.toString(), sb)
        }
    }

    /** Formatage flottant façon Python (repr le plus court, « .0 » conservé). */
    fun pythonFloat(x: Double): String {
        if (x.isNaN() || x.isInfinite()) return x.toString()
        // Arrondi à 3 décimales (HALF_EVEN comme Python round) puis
        // suppression des zéros de fin, en gardant au moins une décimale.
        var bd = BigDecimal(x).setScale(3, RoundingMode.HALF_EVEN)
        if (bd.scale() > 0) {
            bd = bd.stripTrailingZeros()
            if (bd.scale() < 0) bd = bd.setScale(0)
            if (bd.scale() == 0) bd = bd.setScale(1) // « 5 » -> « 5.0 » comme Python
        }
        return bd.toPlainString()
    }

    private fun ecrireChaine(s: String, sb: StringBuilder) {
        sb.append('"')
        for (c in s) {
            when (c) {
                '"' -> sb.append("\\\"")
                '\\' -> sb.append("\\\\")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                '\b' -> sb.append("\\b")
                '' -> sb.append("\\f")
                else -> if (c < ' ') sb.append("\\u%04x".format(c.code)) else sb.append(c)
            }
        }
        sb.append('"')
    }
}

/** Utilitaires SHA-256 hex (empreintes de chaîne, d'état, de whitelist). */
object Sha {
    fun hex(texte: String): String =
        java.security.MessageDigest.getInstance("SHA-256")
            .digest(texte.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
}

/** Adresse IPv4 -> entier ordonnable (même règle que security.ip_to_sort_int). */
fun ipVersEntier(ip: String): Long = try {
    val p = ip.split(".").map { it.toInt() }
    (p[0].toLong() shl 24) or (p[1].toLong() shl 16) or (p[2].toLong() shl 8) or p[3].toLong()
} catch (_: Exception) {
    0xFFFFFFFFL
}

/** Clé de tri déterministe de l'élection : (IP la plus basse, puis node_id). */
fun cleDeterministe(ip: String, nodeId: String): Pair<Long, String> = Pair(ipVersEntier(ip), nodeId)

/** IP LAN du dispositif (sans émettre de paquet). */
fun ipLocale(): String = try {
    java.net.NetworkInterface.getNetworkInterfaces().asSequence()
        .filter { it.isUp && !it.isLoopback }
        .flatMap { it.inetAddresses.asSequence() }
        .firstOrNull { it is java.net.Inet4Address && !it.isLoopbackAddress }
        ?.hostAddress ?: "127.0.0.1"
} catch (_: Exception) { "127.0.0.1" }
