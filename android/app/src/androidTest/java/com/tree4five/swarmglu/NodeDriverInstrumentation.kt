package com.tree4five.swarmglu

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * PILOTE DE NŒUD pour le test multi-émulateurs (`tests/test_multi_emulator_swarm.py`).
 *
 * Contrairement aux tests classiques, celui-ci ne vérifie rien : il DEMARRE
 * un nœud SwarmGlu complet dans le processus de l'app, paramétré par les
 * arguments d'instrumentation, puis le MAINTIENT EN VIE le temps que
 * l'orchestrateur côté hôte joue son scénario (convergence, /ask, mort du
 * leader, basculement, audit). L'hôte l'arrête soit en expirant duration_s,
 * soit par `am force-stop` (coupure brutale, exactement le scénario école).
 *
 * Lancement manuel équivalent :
 *
 *   adb shell am instrument -w \
 *     -e class com.tree4five.swarmglu.NodeDriverInstrumentation \
 *     -e node_id emu-alpha -e advertise_port 17001 \
 *     -e peers 10.0.2.2:7901 -e duration_s 180 \
 *     com.tree4five.swarmglu.test/androidx.test.runner.AndroidJUnitRunner
 */
@RunWith(AndroidJUnit4::class)
class NodeDriverInstrumentation {

    @Test
    fun runNode() {
        val args = InstrumentationRegistry.getArguments()
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        PythonBridge.appContext = ctx

        val dataDir = java.io.File(ctx.filesDir, "node-driver").absolutePath
        val config = JSONObject().apply {
            put("node_id", args.getString("node_id") ?: "emu-node")
            put("port", (args.getString("port") ?: "7001").toInt())
            // Réseau émulateur : joignable de l'extérieur uniquement via le
            // port hôte transféré par `adb forward` (10.0.2.2 = hôte).
            put("advertise_ip", args.getString("advertise_ip") ?: "10.0.2.2")
            put("advertise_port", (args.getString("advertise_port") ?: "7001").toInt())
            put("mode", args.getString("mode") ?: "full")
            put("swarm_id", args.getString("swarm_id") ?: "salle-emu")
            put("whitelist_file", args.getString("whitelist_file") ?: "$dataDir/whitelist.json")
            put("data_dir", args.getString("data_dir") ?: dataDir)
            put("mock_inference", (args.getString("mock_inference") ?: "1") == "1")
            put("beacon_port", 0)
            put("heartbeat_interval", (args.getString("heartbeat") ?: "3.0").toDouble())
            put("lease_timeout", (args.getString("lease") ?: "6.0").toDouble())
            put("allow_join", true)
            put("log_level", "INFO")
            val peers = JSONArray()
            (args.getString("peers") ?: "").split(",").map { it.trim() }
                .filter { it.isNotEmpty() }.forEach { peers.put(it) }
            put("peers", peers)
            // Essaims voisins (mode ambassador) :Digest Exchange passerelle.
            val voisins = JSONArray()
            (args.getString("neighbors") ?: "").split(",").map { it.trim() }
                .filter { it.isNotEmpty() }.forEach { voisins.put(it) }
            put("neighbors", voisins)
        }

        val demarrage = PythonBridge.nodeStart(config)
        assertTrue("démarrage du nœud pilote: $demarrage", demarrage.optBoolean("ok"))

        // Maintien en vie : l'orchestrateur hôte pilote le scénario via HTTP
        // (ports adb forward) et termine par am force-stop ou expiration.
        val dureeS = (args.getString("duration_s") ?: "120").toLong()
        CountDownLatch(1).await(dureeS, TimeUnit.SECONDS)
        PythonBridge.nodeStop()
    }
}
