package com.tree4five.swarmglu

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject

/**
 * Service de premier plan : porte le nœud SwarmGlu (Python embarqué).
 *
 * Verrous maintenus pendant toute la vie du nœud :
 *  * WifiLock (haute perf)  : le Wi-Fi ne s'endort pas écran éteint ;
 *  * MulticastLock          : autorise la RÉCEPTION des broadcasts UDP
 *                              (beacons de découverte) que beaucoup de
 *                              chipsets filtrent par défaut ;
 *  * WakeLock (partielle)   : le nœud continue de battre (heartbeats 3 s)
 *                              même écran éteint — sinon la tablette est
 *                              déclarée morte par ses pairs au bout de 9 s.
 */
class NodeService : Service() {

    companion object {
        const val TAG = "SwarmGlu.NodeService"
        const val CHANNEL_ID = "swarmglu_node"
        const val ACTION_START = "com.tree4five.swarmglu.START"
        const val ACTION_STOP = "com.tree4five.swarmglu.STOP"
    }

    private var wifiLock: WifiManager.WifiLock? = null
    private var multicastLock: WifiManager.MulticastLock? = null
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopNode()
                stopSelf()
                return START_NOT_STICKY
            }
            else -> startNode()
        }
        return START_STICKY  // tué par le système ? le nœud renaît (résilience)
    }

    // ---------------------------------------------------------------------

    private fun startNode() {
        startForegroundCompat()

        val s = SettingsStore.read(this)
        val files = filesDir.absolutePath

        // Configuration transmise au noyau Python (NodeConfig).
        val config = JSONObject().apply {
            put("node_id", s.nodeId)
            put("port", s.port)
            put("mode", s.mode)
            put("swarm_id", s.swarmId)
            put("whitelist_file", "$files/whitelist.json")
            put("data_dir", "$files/node")
            put("peers", splitAddrs(s.peers))
            put("neighbors", splitAddrs(s.neighbors))
            put("mock_inference", s.mockInference || s.ggufUrl.isBlank())
            put("gguf_url", s.ggufUrl)
            put("gguf_models", if (s.ggufModels.isBlank())
                JSONArray(listOf("mock-qwen-0.5b")) else JSONArray(s.ggufModels.split(",")))
            put("gguf_token", s.ggufToken)
            put("api_flavor", "ollama")
            put("allow_join", s.allowJoin)
            put("beacon_port", 47479)
            // Valeurs du cahier des charges : heartbeat 3 s, bail 6 s.
            put("heartbeat_interval", 3.0)
            put("lease_timeout", 6.0)
            put("log_level", "INFO")
        }

        acquireLocks()

        // Le démarrage Python (interpréteur + serveur HTTP) est bloquant :
        // hors du thread principal, comme l'exige Android (StrictMode).
        Thread {
            try {
                CoreBridge.appContext = applicationContext
                val result = CoreBridge.nodeStart(config)
                Log.i(TAG, "Nœud démarré : $result")
            } catch (e: Exception) {
                Log.e(TAG, "Échec du démarrage du nœud", e)
            }
        }.apply { name = "swarmglu-node-start" }.start()
    }

    private fun stopNode() {
        Thread {
            try {
                Log.i(TAG, "Arrêt du nœud : ${CoreBridge.nodeStop()}")
            } catch (e: Exception) {
                Log.w(TAG, "Arrêt du nœud : ${e.message}")
            }
        }.start()
        releaseLocks()
    }

    // -- verrous réseau / CPU ----------------------------------------------

    private fun acquireLocks() {
        val wifi = applicationContext.getSystemService(WIFI_SERVICE) as WifiManager
        wifiLock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "swarmglu:wifi")
            .apply { setReferenceCounted(false); acquire() }
        multicastLock = wifi.createMulticastLock("swarmglu:multicast")
            .apply { setReferenceCounted(false); acquire() }
        val pm = applicationContext.getSystemService(POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "swarmglu:wake")
            .apply { setReferenceCounted(false); acquire() }
        Log.i(TAG, "Verrous Wi-Fi/multicast/wake acquis")
    }

    private fun releaseLocks() {
        runCatching { wifiLock?.release() }
        runCatching { multicastLock?.release() }
        runCatching { wakeLock?.takeIf { it.isHeld }?.release() }
        wifiLock = null; multicastLock = null; wakeLock = null
    }

    // -- notification de premier plan ---------------------------------------

    private fun createChannel() {
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                getString(R.string.notif_channel),
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = getString(R.string.notif_channel_desc)
                setShowBadge(false)
            }
        )
    }

    private fun startForegroundCompat() {
        val notification: Notification =
            androidx.core.app.NotificationCompat.Builder(this, CHANNEL_ID)
                .setContentTitle(getString(R.string.notif_title))
                .setContentText(getString(R.string.notif_text))
                .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
                .setOngoing(true)
                .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(1, notification)
        }
    }

    private fun splitAddrs(csv: String): JSONArray {
        val arr = JSONArray()
        csv.split(',', ';').map { it.trim() }.filter { it.isNotEmpty() }
            .forEach { arr.put(it) }
        return arr
    }

    override fun onDestroy() {
        releaseLocks()
        super.onDestroy()
    }
}
