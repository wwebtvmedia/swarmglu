package com.tree4five.swarmglu

import android.content.Context
import android.os.Build

/**
 * Réglages persistants du nœud (SharedPreferences).
 *
 * Valeurs par défaut pensées pour une TABLETTE ÉLÈVE du PoC Djerba :
 * mode "follower" (jamais leader — les PC du professeur coordonnent),
 * mock d'inférence actif pour la démonstration hors-ligne, essaim
 * "salle-1", heartbeat/bail aux valeurs du cahier des charges (3 s / 6 s).
 */
data class NodeSettings(
    var nodeId: String = "",
    var port: Int = 7001,
    var mode: String = "follower",          // full | follower | ambassador | provider
    var swarmId: String = "salle-1",
    var peers: String = "",                 // seeds "192.168.4.10:7001,..."
    var neighbors: String = "",             // essaims voisins (ambassadeur)
    var mockInference: Boolean = true,      // PoC : fournisseur factice sourcé
    var ggufUrl: String = "",               // pont LLM local (ex. pont OSP :8090/v1)
    var ggufModels: String = "",            // modèles déclarés (ex. llmprovider)
    var ggufToken: String = "",             // token du pont (x-api-token)
    var allowJoin: Boolean = true           // enrôlement approuvé par le leader
)

object SettingsStore {
    private const val PREFS = "swarmglu_settings"

    fun read(ctx: Context): NodeSettings {
        val sp = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val defaultId = "android-" + (Build.MODEL ?: "tablette")
            .lowercase().replace(Regex("[^a-z0-9]+"), "-").take(16)
        return NodeSettings(
            nodeId = sp.getString("node_id", defaultId) ?: defaultId,
            port = sp.getInt("port", 7001),
            mode = sp.getString("mode", "follower") ?: "follower",
            swarmId = sp.getString("swarm_id", "salle-1") ?: "salle-1",
            peers = sp.getString("peers", "") ?: "",
            neighbors = sp.getString("neighbors", "") ?: "",
            mockInference = sp.getBoolean("mock_inference", true),
            ggufUrl = sp.getString("gguf_url", "") ?: "",
            ggufModels = sp.getString("gguf_models", "") ?: "",
            ggufToken = sp.getString("gguf_token", "") ?: "",
            allowJoin = sp.getBoolean("allow_join", true)
        )
    }

    fun save(ctx: Context, s: NodeSettings) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString("node_id", s.nodeId)
            .putInt("port", s.port)
            .putString("mode", s.mode)
            .putString("swarm_id", s.swarmId)
            .putString("peers", s.peers)
            .putString("neighbors", s.neighbors)
            .putBoolean("mock_inference", s.mockInference)
            .putString("gguf_url", s.ggufUrl)
            .putString("gguf_models", s.ggufModels)
            .putString("gguf_token", s.ggufToken)
            .putBoolean("allow_join", s.allowJoin)
            .apply()
    }
}
