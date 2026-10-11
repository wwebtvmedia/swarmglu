package com.tree4five.swarmglu.core

import org.json.JSONObject

/** Toutes les clés d'un JSONObject (le framework Android n'expose pas keySet()). */
fun cles(o: JSONObject): List<String> = o.keys().asSequence().toList()
