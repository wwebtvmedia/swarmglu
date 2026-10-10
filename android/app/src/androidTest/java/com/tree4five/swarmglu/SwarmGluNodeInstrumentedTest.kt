package com.tree4five.swarmglu

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.AfterClass
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.BeforeClass
import org.junit.FixMethodOrder
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.MethodSorters

/**
 * Test instrumenté SwarmGlu — s'exécute DANS l'émulateur (ou tablette USB) :
 *
 *     cd android && ./gradlew connectedDebugAndroidTest
 *
 * Il valide la boucle complète du nœud embarqué, en conditions réelles
 * Android (processus app, filesystem, threads, CPU émulé) :
 *
 *  1. bootstrap   : un nœud seul s'auto-élit Leader (zéro SPOF) ;
 *  2. inférence   : /ask routé vers le fournisseur mock, pare-feu OSP v0.6
 *                   (groundedness >= 0.30 -> VERIFIED, éligible RAG) ;
 *  3. HITL        : une tâche agent reste bloquée jusqu'à validation
 *                   humaine, puis s'exécute et se scelle ;
 *  4. audit       : la chaîne de hachage rejouée est INTACTE ;
 *  5. arrêt propre.
 */
@RunWith(AndroidJUnit4::class)
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
class SwarmGluNodeInstrumentedTest {

    companion object {
        @BeforeClass
        @JvmStatic
        fun demarrerNoeud() {
            val ctx = InstrumentationRegistry.getInstrumentation().targetContext
            PythonBridge.appContext = ctx
            val dataDir = java.io.File(
                ctx.filesDir, "node-test-" + System.currentTimeMillis()
            ).absolutePath
            val config = JSONObject().apply {
                put("node_id", "emu-tablette")
                put("port", 7401)
                put("mode", "full")
                put("swarm_id", "salle-test")
                put("whitelist_file", "$dataDir/whitelist.json")
                put("data_dir", dataDir)
                put("mock_inference", true)
                put("beacon_port", 0)          // pas de broadcast : test isolé
                put("heartbeat_interval", 1.0) // bootstrap rapide (~2 s)
                put("lease_timeout", 2.0)
                put("allow_join", true)
                put("log_level", "WARNING")
            }
            val start = PythonBridge.nodeStart(config)
            assertTrue("démarrage du nœud: $start", start.optBoolean("ok"))
        }

        @AfterClass
        @JvmStatic
        fun arreterNoeud() {
            PythonBridge.nodeStop()
        }

        /** Attend que le consensus atteigne l'état attendu (polling 500 ms). */
        fun attendreJusquA(delaiS: Long, condition: (JSONObject) -> Boolean): JSONObject {
            val deadline = System.currentTimeMillis() + delaiS * 1000
            var dernier = JSONObject()
            while (System.currentTimeMillis() < deadline) {
                dernier = PythonBridge.status()
                if (dernier.length() > 0 && !dernier.has("error") && condition(dernier)) {
                    return dernier
                }
                Thread.sleep(500)
            }
            return dernier
        }
    }

    // ------------------------------------------------------------------
    // 1) Bootstrap : élection automatique du nœud seul
    // ------------------------------------------------------------------
    @Test
    fun t01_bootstrap_autoElectionLeader() {
        val status = attendreJusquA(15) { st ->
            st.optJSONObject("consensus")?.optString("role") == "leader"
        }
        val c = status.optJSONObject("consensus")
        assertTrue("pas de leader élu (dernier état: $status)", c != null)
        assertEquals("leader", c!!.optString("role"))
        assertEquals("emu-tablette", c.optString("leader_id"))
        assertTrue("epoch attendu >= 1, trouvé ${c.optInt("epoch")}", c.optInt("epoch") >= 1)
    }

    // ------------------------------------------------------------------
    // 2) Requête d'élève : routage + pare-feu OSP v0.6
    // ------------------------------------------------------------------
    @Test
    fun t02_ask_routageEtPareFeuOsp() {
        // On s'assure d'abord que le leader est en place (ordre des tests).
        attendreJusquA(15) { it.optJSONObject("consensus")?.optString("role") == "leader" }

        val reponse = PythonBridge.ask(
            "Quelle est la capitale de la Tunisie ?", "eleve-emu", "", "")
        assertEquals("échec /ask: $reponse", 200, reponse.optInt("http_status"))
        assertTrue("réponse vide", reponse.optString("answer").isNotBlank())
        assertEquals("emu-tablette", reponse.optString("provider"))

        val osp = reponse.optJSONObject("osp")
        assertTrue("bloc OSP absent", osp != null)
        // Mock : 2 sources ancrées -> g = 0.4 + 0.2*(2-1) = 0.6 >= 0.30
        assertEquals(0.6, osp!!.optDouble("groundedness"), 0.001)
        assertEquals("VERIFIED", osp.optString("verdict"))
        assertTrue("doit être éligible au RAG distribué", osp.optBoolean("rag_eligible"))
        assertTrue("sources ancrées attendues", reponse.optJSONArray("sources").length() >= 2)
    }

    // ------------------------------------------------------------------
    // 3) Tâche agent : barrière Human-in-the-Loop
    // ------------------------------------------------------------------
    @Test
    fun t03_agent_barriereHumaine() {
        attendreJusquA(15) { it.optJSONObject("consensus")?.optString("role") == "leader" }

        val creation = PythonBridge.agentTask(
            "{\"student_id\":\"eleve-emu\",\"mcp\":{\"server\":\"harnessdroid\"," +
            "\"tool\":\"test_outil\",\"args\":{}}}")
        val task = creation.optJSONObject("task")
        assertTrue("tâche non créée: $creation", task != null)
        assertEquals("pending_validation", task!!.optString("status"))

        // a) La validation lève la barrière...
        val validation = PythonBridge.agentTaskValidate(
            task.optString("task_id"), true, "enseignant-test")
        assertEquals("done", validation.optJSONObject("task").optString("status"))

        // b) ...et une re-validation est refusée (cycle unique).
        val revalidation = PythonBridge.agentTaskValidate(
            task.optString("task_id"), true, "enseignant-test")
        assertEquals(409, revalidation.optInt("http_status"))
    }

    // ------------------------------------------------------------------
    // 4) Audit : chaîne de hachage immuable vérifiable
    // ------------------------------------------------------------------
    @Test
    fun t04_audit_chaineIntacte() {
        val rapport = PythonBridge.auditVerify()
        assertTrue("chaîne corrompue: $rapport", rapport.optBoolean("ok"))
        // node_start + election + ask + tâches => au moins 6 blocs scellés.
        assertTrue("journal trop court: ${rapport.optInt("entries")}",
                   rapport.optInt("entries") >= 6)
        assertFalse("hash genèse inattendu en dernier",
                    "0".repeat(64) == rapport.optString("last_hash"))
    }
}
