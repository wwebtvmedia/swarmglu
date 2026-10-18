package com.tree4five.swarmglu

import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.Switch
import android.widget.TextView
import androidx.fragment.app.Fragment
import org.json.JSONObject

/**
 * Tableau de bord : interrupteur du nœud, état du consensus (rôle, epoch,
 * leader), registre des pairs, voisinage de passerelle et administration
 * locale de la whitelist. Rafraîchi toutes les 2 s tant que le nœud vit.
 */
class DashboardFragment : Fragment() {

    private val handler = Handler(Looper.getMainLooper())
    private var refreshing = false

    private val refresher = object : Runnable {
        override fun run() {
            if (refreshing && CoreBridge.isRunning()) {
                CoreBridge.onBackground({ CoreBridge.status() }) { render(it) }
                handler.postDelayed(this, 2000)
            }
        }
    }

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_dashboard, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        // Paramètres (roue crantée texte) + interrupteur du nœud.
        view.findViewById<View>(R.id.btn_settings).setOnClickListener {
            (activity as MainActivity).showSettingsDialog()
        }
        val sw = view.findViewById<Switch>(R.id.switch_node)
        sw.isChecked = CoreBridge.isRunning()
        sw.setOnCheckedChangeListener { _, checked ->
            val act = activity as MainActivity
            if (checked) act.startNodeService() else act.stopNodeService()
            // Laisser au nœud le temps de démarrer/arrêter avant rafraîchi.
            handler.postDelayed({
                sw.isChecked = CoreBridge.isRunning()
                startRefreshing()
            }, 1500)
        }

        // Point d'accès local-only : étendre la zone de l'essaim.
        view.findViewById<View>(R.id.btn_hotspot).setOnClickListener {
            val sortie = view.findViewById<TextView>(R.id.tv_hotspot)
            if (HotspotManager.actif) {
                sortie.text = getString(R.string.hotspot_arrete)
                return@setOnClickListener
            }
            sortie.text = getString(R.string.ask_routing)  // « en cours… »
            HotspotManager.demarrer(
                requireContext(),
                onInfo = { info -> sortie.text = info },
                onError = { err -> sortie.text = "${getString(R.string.hotspot_aide)}\n[$err]" })
        }

        // Administration : ajouter un pair à la whitelist locale.
        view.findViewById<View>(R.id.btn_wl_add).setOnClickListener {
            val input = view.findViewById<EditText>(R.id.input_wl_node)
            val nodeId = input.text.toString().trim()
            if (nodeId.isEmpty()) return@setOnClickListener
            CoreBridge.onBackground({ CoreBridge.whitelistAdd(nodeId) },
                onError = { e -> flash(false, "$e") }
            ) { res ->
                flash(res.optBoolean("ok"), res.toString())
                input.setText("")
            }
        }
        startRefreshing()
    }

    private fun startRefreshing() {
        refreshing = true
        handler.removeCallbacks(refresher)
        handler.post(refresher)
    }

    /** Rendu du JSON de statut dans les TextViews. */
    private fun render(st: JSONObject) {
        val v = view ?: return
        if (st.has("error")) {
            v.findViewById<TextView>(R.id.tv_role).text = getString(R.string.state_stopped)
            v.findViewById<TextView>(R.id.tv_role).setTextColor(Color.GRAY)
            return
        }
        val c = st.optJSONObject("consensus") ?: JSONObject()
        val state = st.optJSONObject("state") ?: JSONObject()
        val audit = st.optJSONObject("audit") ?: JSONObject()
        val role = c.optString("role", "?")

        val tvRole = v.findViewById<TextView>(R.id.tv_role)
        tvRole.text = getString(R.string.role_fmt, role.uppercase())
        tvRole.setTypeface(null, Typeface.BOLD)
        tvRole.setTextColor(
            when (role) {
                "leader" -> Color.rgb(46, 125, 50)      // vert : ce nœud coordonne
                "candidate" -> Color.rgb(230, 81, 0)    // orange : élection en cours
                else -> Color.rgb(21, 101, 192)         // bleu : follower
            }
        )

        v.findViewById<TextView>(R.id.tv_identity).text = getString(
            R.string.identity_fmt,
            st.optString("node_id"), st.optString("addr"), st.optString("swarm_id")
        )
        v.findViewById<TextView>(R.id.tv_consensus).text = getString(
            R.string.consensus_fmt,
            c.optString("leader_id", "—"), c.optInt("epoch"),
            c.optDouble("uptime_s", 0.0)
        )
        v.findViewById<TextView>(R.id.tv_state).text = getString(
            R.string.state_fmt,
            state.optString("digest").take(16), state.optInt("peer_count"),
            state.optInt("alive_count")
        )
        v.findViewById<TextView>(R.id.tv_audit).text = getString(
            R.string.audit_fmt,
            audit.optInt("entries"),
            if (audit.optBoolean("ok")) "INTACTE" else "CORROMPUE"
        )

        // Registre des pairs (● vivant / ○ muet).
        val sb = StringBuilder()
        val peers = st.optJSONArray("peers") ?: org.json.JSONArray()
        for (i in 0 until peers.length()) {
            val p = peers.optJSONObject(i) ?: continue
            sb.append(if (p.optBoolean("alive")) "● " else "○ ")
                .append(p.optString("node_id")).append("  ")
                .append(p.optString("addr")).append("  [")
                .append(p.optString("mode")).append("]\n")
        }
        v.findViewById<TextView>(R.id.tv_peers).text =
            sb.toString().ifEmpty { getString(R.string.no_peers) }

        // Essaims voisins (passerelle inter-essaims).
        val gw = StringBuilder()
        val neighbors = st.optJSONArray("gateway_neighbors") ?: org.json.JSONArray()
        for (i in 0 until neighbors.length()) {
            val n = neighbors.optJSONObject(i) ?: continue
            gw.append("◆ ").append(n.optString("swarm_id"))
                .append("  (").append(n.optInt("node_count")).append(" nœuds, epoch ")
                .append(n.optInt("epoch")).append(") @ ")
                .append(n.optString("ambassador_addr")).append('\n')
        }
        v.findViewById<TextView>(R.id.tv_gateway).text =
            gw.toString().ifEmpty { getString(R.string.no_neighbors) }
    }

    private fun flash(ok: Boolean, message: String) {
        val ctx = context ?: return
        android.widget.Toast.makeText(
            ctx, if (ok) getString(R.string.wl_added) else message,
            android.widget.Toast.LENGTH_SHORT
        ).show()
    }

    override fun onDestroyView() {
        refreshing = false
        handler.removeCallbacks(refresher)
        super.onDestroyView()
    }
}
