package com.tree4five.swarmglu

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.ViewGroup
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.fragment.app.Fragment

/**
 * Activité principale : tableau de bord / élève / audit, via navigation
 * inférieure (Material). Le nœud lui-même vit dans [NodeService] et survit
 * à la fermeture de l'UI.
 */
class MainActivity : AppCompatActivity() {

    private val dashboard = DashboardFragment()
    private val ask = AskFragment()
    private val audit = AuditFragment()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Android 13+ : la notification du service de premier plan exige
        // une permission explicite (demandée une fois, non bloquante).
        if (Build.VERSION.SDK_INT >= 33) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }

        if (savedInstanceState == null) {
            supportFragmentManager.beginTransaction()
                .replace(R.id.fragment_container, dashboard)
                .commit()
        }

        findViewById<com.google.android.material.bottomnavigation.BottomNavigationView>(
            R.id.bottom_nav
        ).setOnItemSelectedListener { item ->
            val target: Fragment = when (item.itemId) {
                R.id.nav_dashboard -> dashboard
                R.id.nav_ask -> ask
                else -> audit
            }
            supportFragmentManager.beginTransaction()
                .replace(R.id.fragment_container, target)
                .commit()
            true
        }
    }

    /** Démarre le service de nœud (appelé par l'interrupteur du tableau de bord). */
    fun startNodeService() {
        startForegroundService(
            Intent(this, NodeService::class.java).setAction(NodeService.ACTION_START)
        )
    }

    fun stopNodeService() {
        startService(
            Intent(this, NodeService::class.java).setAction(NodeService.ACTION_STOP)
        )
    }

    /** Boîte de dialogue des réglages du nœud (id, port, essaim, seeds...). */
    fun showSettingsDialog() {
        val s = SettingsStore.read(this)
        val pad = (16 * resources.displayMetrics.density).toInt()

        fun field(hint: String, value: String, numeric: Boolean = false): EditText {
            val edit = EditText(this)
            edit.hint = hint
            edit.setText(value)
            edit.layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
            )
            if (numeric) edit.inputType = InputType.TYPE_CLASS_NUMBER
            return edit
        }

        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }
        val eId = field(getString(R.string.set_node_id), s.nodeId); box.addView(eId)
        val ePort = field(getString(R.string.set_port), s.port.toString(), numeric = true); box.addView(ePort)
        val eSwarm = field(getString(R.string.set_swarm), s.swarmId); box.addView(eSwarm)
        val eMode = field(getString(R.string.set_mode), s.mode); box.addView(eMode)
        val ePeers = field(getString(R.string.set_peers), s.peers); box.addView(ePeers)
        val eNeighbors = field(getString(R.string.set_neighbors), s.neighbors); box.addView(eNeighbors)
        val eGguf = field(getString(R.string.set_gguf), s.ggufUrl); box.addView(eGguf)
        val eMock = field(getString(R.string.set_mock), if (s.mockInference) "1" else "0"); box.addView(eMock)

        AlertDialog.Builder(this)
            .setTitle(R.string.settings_title)
            .setMessage(getString(R.string.settings_help))
            .setView(box)
            .setPositiveButton(android.R.string.ok) { _, _ ->
                SettingsStore.save(
                    this, s.copy(
                        nodeId = eId.text.toString().trim().ifEmpty { s.nodeId },
                        port = ePort.text.toString().toIntOrNull() ?: s.port,
                        swarmId = eSwarm.text.toString().trim().ifEmpty { s.swarmId },
                        mode = eMode.text.toString().trim().ifEmpty { s.mode },
                        peers = ePeers.text.toString().trim(),
                        neighbors = eNeighbors.text.toString().trim(),
                        ggufUrl = eGguf.text.toString().trim(),
                        mockInference = eMock.text.toString().trim() != "0"
                    )
                )
                Toast.makeText(this, R.string.settings_saved, Toast.LENGTH_SHORT).show()
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }
}
