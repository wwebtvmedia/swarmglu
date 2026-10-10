package com.tree4five.swarmglu

import android.graphics.Color
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.TextView
import androidx.fragment.app.Fragment
import org.json.JSONObject

/**
 * Écran AUDIT : vérifier l'intégrité de la chaîne de hachage locale et
 * consulter les derniers événements scellés (requêtes, élections,
 * synchronisations...). C'est la transparence « Scorona-like » du PoC :
 * n'importe quel contrôleur peut vérifier le journal d'une tablette.
 */
class AuditFragment : Fragment() {

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_audit, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        val out = view.findViewById<TextView>(R.id.tv_audit_out)

        view.findViewById<Button>(R.id.btn_verify).setOnClickListener {
            out.text = getString(R.string.audit_running)
            PythonBridge.onBackground({ PythonBridge.auditVerify() }) { render(it, out) }
        }

        view.findViewById<Button>(R.id.btn_tail).setOnClickListener {
            out.text = getString(R.string.audit_running)
            PythonBridge.onBackground({ PythonBridge.auditTail(20) }) { renderTail(it, out) }
        }
    }

    private fun render(report: JSONObject, out: TextView) {
        if (report.has("error")) {
            out.text = getString(R.string.audit_node_off)
            return
        }
        val ok = report.optBoolean("ok")
        out.text = if (ok) {
            getString(
                R.string.audit_ok,
                report.optInt("entries"),
                report.optString("first_hash"),
                report.optString("last_hash")
            )
        } else {
            getString(
                R.string.audit_ko,
                report.optInt("line", -1),
                report.optString("error")
            )
        }
        out.setTextColor(if (ok) Color.rgb(46, 125, 50) else Color.rgb(198, 40, 40))
    }

    private fun renderTail(reply: JSONObject, out: TextView) {
        val events = reply.optJSONArray("events")
        if (events == null || events.length() == 0) {
            out.text = getString(R.string.audit_empty)
            return
        }
        val sb = StringBuilder()
        for (i in events.length() - 1 downTo 0) {
            val b = events.optJSONObject(i) ?: continue
            val ev = b.optJSONObject("event") ?: JSONObject()
            sb.append(String.format("#%d  %s  %-22s %s  (%.3fs)%n",
                b.optInt("seq"),
                b.optString("timestamp").take(19),
                ev.optString("event_type"),
                ev.optString("node_id"),
                b.optDouble("processing_time")))
        }
        out.text = sb
        out.setTextColor(Color.DKGRAY)
    }
}
