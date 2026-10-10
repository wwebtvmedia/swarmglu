package com.tree4five.swarmglu

import android.content.Context
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPython
import org.json.JSONObject

/**
 * Pont Kotlin -> Python (module android_bridge.py, via Chaquopy).
 *
 * Le nœud SwarmGlu complet (HTTP + consensus + chaîne d'audit + passerelle)
 * tourne DANS le processus de l'application : l'UI l'appelle directement,
 * sans boucle réseau locale.
 *
 * Toutes les méthodes retournent un JSONObject — le côté Python garantit
 * qu'aucune exception ne fuit (les erreurs arrivent en {"error": "..."}).
 */
object PythonBridge {

    @Volatile
    var appContext: Context? = null

    /** Initialise l'interpréteur CPython embarqué (une seule fois). */
    @Synchronized
    private fun py(): Python {
        val ctx = appContext
            ?: throw IllegalStateException("appContext non initialisé")
        if (!Python.isStarted()) {
            Python.start(AndroidPython(ctx.applicationContext))
        }
        return Python.getInstance()
    }

    private fun module() = py().getModule("android_bridge")

    // -- cycle de vie du nœud ----------------------------------------------

    fun nodeStart(config: JSONObject): JSONObject =
        JSONObject(module().callAttr("start", config.toString()).toString())

    fun nodeStop(): JSONObject =
        JSONObject(module().callAttr("stop").toString())

    fun isRunning(): Boolean = try {
        module().callAttr("is_running").toBoolean()
    } catch (e: Exception) {
        false
    }

    // -- données pour l'UI ---------------------------------------------------

    fun status(): JSONObject =
        JSONObject(module().callAttr("status_json").toString())

    fun ask(prompt: String, student: String, model: String, swarm: String): JSONObject =
        JSONObject(module().callAttr("ask_json", prompt, student, model, swarm).toString())

    fun auditVerify(): JSONObject =
        JSONObject(module().callAttr("audit_verify_json").toString())

    fun auditTail(n: Int): JSONObject =
        JSONObject(module().callAttr("audit_tail_json", n).toString())

    fun whitelistAdd(nodeId: String): JSONObject =
        JSONObject(module().callAttr("whitelist_add", nodeId).toString())

    /** Les appels réseau (ask...) peuvent durer : toujours hors du thread UI. */
    fun <T> onBackground(block: () -> T, onDone: (T) -> Unit) {
        Thread {
            val result = block()
            android.os.Handler(android.os.Looper.getMainLooper()).post { onDone(result) }
        }.apply { name = "swarmglu-ui-call" }.start()
    }
}
