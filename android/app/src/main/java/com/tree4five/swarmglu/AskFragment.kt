package com.tree4five.swarmglu

import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.ProgressBar
import android.widget.TextView
import androidx.fragment.app.Fragment
import org.json.JSONObject

/**
 * Écran ÉLÈVE : poser une question à l'essaim.
 *
 * La requête est routée par le noyau SwarmGlu : fournisseur GGUF local le
 * mieux disposé, délégation au leader, ou routage fédéré vers un autre
 * essaim (« salle-2 »...) via la passerelle. La réponse affiche le
 * VERDICT du pare-feu anti-hallucination OSP v0.6 (g >= 0.30).
 */
class AskFragment : Fragment() {

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_ask, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        val inputPrompt = view.findViewById<EditText>(R.id.input_prompt)
        val inputModel = view.findViewById<EditText>(R.id.input_model)
        val inputSwarm = view.findViewById<EditText>(R.id.input_swarm)
        val button = view.findViewById<Button>(R.id.btn_ask)
        val progress = view.findViewById<ProgressBar>(R.id.ask_progress)
        val result = view.findViewById<TextView>(R.id.tv_answer)

        button.setOnClickListener {
            val prompt = inputPrompt.text.toString().trim()
            if (prompt.isEmpty()) {
                result.text = getString(R.string.ask_empty)
                return@setOnClickListener
            }
            button.isEnabled = false
            progress.visibility = View.VISIBLE
            result.text = getString(R.string.ask_routing)

            // Appel au nœud (réseau potentiellement long) hors du thread UI.
            PythonBridge.onBackground({
                PythonBridge.ask(
                    prompt, "eleve-android",
                    inputModel.text.toString().trim(),
                    inputSwarm.text.toString().trim()
                )
            }) { reply ->
                button.isEnabled = true
                progress.visibility = View.GONE
                renderReply(reply, result)
            }
        }
    }

    private fun renderReply(reply: JSONObject, out: TextView) {
        if (reply.has("error") || reply.optInt("http_status", 200) >= 400) {
            out.text = getString(
                R.string.ask_error,
                reply.optString("error", reply.toString())
            )
            return
        }
        val osp = reply.optJSONObject("osp") ?: JSONObject()
        val verified = osp.optBoolean("rag_eligible")
        val sb = StringBuilder()
            .append(reply.optString("answer")).append("\n\n")
            .append("— ").append(reply.optString("model"))
            .append("  (fournisseur : ").append(reply.optString("provider"))
            .append(")\n")

        val sources = reply.optJSONArray("sources")
        if (sources != null && sources.length() > 0) {
            sb.append(getString(R.string.ask_sources)).append('\n')
            for (i in 0 until sources.length()) {
                sb.append("  • ").append(sources.optString(i)).append('\n')
            }
        }

        // Verdict OSP : couleur + explication pédagogique du pare-feu.
        sb.append('\n').append("OSP v0.6 : g = ").append(osp.optDouble("groundedness"))
            .append("  →  ").append(osp.optString("verdict"))
        out.text = sb.toString()
        out.setTypeface(null, Typeface.NORMAL)
        out.setTextColor(
            if (verified) Color.rgb(46, 125, 50) else Color.rgb(198, 40, 40)
        )
    }
}
