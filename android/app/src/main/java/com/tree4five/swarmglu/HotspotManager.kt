package com.tree4five.swarmglu

import android.content.Context
import android.net.wifi.WifiManager
import android.os.Build

/**
 * Point d'accès local-only (« étendre la zone ») :
 * https://developer.android.com/develop/connectivity/wifi/localonlyhotspot
 *
 * La tablette qui active le hotspot devient le ROUTEUR de l'essaim : les
 * appareils qui s'y joignent (autres tablettes, agents HarnessDroid, ponts
 * GGUF/LLMProvider) atteignent le réseau principal UNIQUEMENT via le
 * relais applicatif de ce nœud (POST /relay — destinations privées only).
 * Le nœud reste membre de l'essaim principal sur les deux réseaux.
 *
 * Non fonctionnel sur émulateur (pas de radio Wi-Fi) : l'erreur est
 * remontée proprement à l'UI.
 */
object HotspotManager {

    @Volatile var actif = false
        private set

    /** Démarre le hotspot ; callbacks sur le thread appelant. */
    fun demarrer(ctx: Context, onInfo: (String) -> Unit, onError: (String) -> Unit) {
        val appCtx = ctx.applicationContext
        val wifi = appCtx.getSystemService(Context.WIFI_SERVICE) as? WifiManager
        if (wifi == null) {
            onError(appCtx.getString(R.string.hotspot_invalide))
            return
        }
        try {
            wifi.startLocalOnlyHotspot(object : WifiManager.LocalOnlyHotspotCallback() {
                override fun onStarted(reservation: WifiManager.LocalOnlyHotspotReservation) {
                    actif = true
                    val (ssid, passe) = lireIdentifiants(reservation)
                    onInfo(appCtx.getString(R.string.hotspot_info, ssid, passe))
                }

                override fun onFailed(reason: Int) {
                    actif = false
                    onError(appCtx.getString(R.string.hotspot_echec, reason))
                }

                override fun onStopped() {
                    actif = false
                    onError(appCtx.getString(R.string.hotspot_arrete))
                }
            }, null)
        } catch (e: Exception) {
            // Émulateur (pas de Wi-Fi) ou permission manquante.
            onError(e.message ?: appCtx.getString(R.string.hotspot_invalide))
        }
    }

    private fun lireIdentifiants(reservation: WifiManager.LocalOnlyHotspotReservation)
            : Pair<String, String> {
        return if (Build.VERSION.SDK_INT >= 30) {
            val cfg = reservation.softApConfiguration
            (cfg.ssid ?: "SwarmGlu") to (cfg.passphrase ?: "")
        } else {
            // API < 30 : WifiConfiguration n'est plus exposé au compilateur
            // SDK 36 — l'élève lit le SSID/mot de passe dans Réglages ▸ Hotspot.
            "voir Réglages ▸ Point d'accès" to ""
        }
    }
}
