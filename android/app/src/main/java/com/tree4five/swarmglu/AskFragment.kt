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
            CoreBridge.onBackground({
                CoreBridge.ask(
                    prompt, "eleve-android",
                    inputModel.text.toString().trim(),
                    inputSwarm.text.toString().trim()
                )
            }, onError = { e ->
                // Exception non rattrapée par CoreBridge.ask : ne JAMAIS
                // laisser le bouton bloqué ni le spinner tourner.
                button.isEnabled = true
                progress.visibility = View.GONE
                result.setTextColor(Color.rgb(198, 40, 40))
                result.text = getString(R.string.ask_error, "$e")
            }) { reply ->
                button.isEnabled = true
                progress.visibility = View.GONE
                renderReply(reply, result)
            }
        }
    }

    private fun renderReply(reply: JSONObject, out: TextView) {
        // http_status 0 = échec de transport (aucune réponse HTTP) : il doit
        // tomber dans l'erreur, sinon un réseau coupé s'affiche comme un
        // succès vide. Seule une réponse 2xx sans « error » est un succès.
        val statut = reply.optInt("http_status", 200)
        if (reply.has("error") || statut !in 200..299) {
            out.setTypeface(null, Typeface.NORMAL)
            out.setTextColor(Color.rgb(198, 40, 40))
            out.text = getString(
                R.string.ask_error,
                reply.optString("error", reply.toString())
            )
            return
        }

        // Récupérer la réponse / résultat sous toutes ses formes (answer, result, response, text)
        val resultat = reply.optString("answer").ifEmpty {
            reply.optString("result").ifEmpty {
                reply.optString("response").ifEmpty {
                    reply.optString("output").ifEmpty {
                        val obj = reply.opt("result") ?: reply.opt("task")
                        obj?.toString() ?: ""
                    }
                }
            }
        }.ifEmpty { reply.toString() }

        val sb = StringBuilder().append(resultat)

        val modele = reply.optString("model")
        val fournisseur = reply.optString("provider")
        if (modele.isNotEmpty() || fournisseur.isNotEmpty()) {
            sb.append("\n\n— ").append(modele.ifEmpty { "modèle local" })
            if (fournisseur.isNotEmpty()) {
                sb.append("  (fournisseur : ").append(fournisseur).append(")")
            }
            sb.append('\n')
        }

        val sources = reply.optJSONArray("sources")
        if (sources != null && sources.length() > 0) {
            if (!sb.endsWith("\n")) sb.append('\n')
            sb.append('\n').append(getString(R.string.ask_sources)).append('\n')
            for (i in 0 until sources.length()) {
                sb.append("  • ").append(sources.optString(i)).append('\n')
            }
        }

        // Verdict OSP : couleur + explication pédagogique du pare-feu (si présent).
        val osp = reply.optJSONObject("osp")
        val verified: Boolean
        if (osp != null && (osp.has("verdict") || osp.has("groundedness"))) {
            verified = osp.optBoolean("rag_eligible", true)
            if (!sb.endsWith("\n")) sb.append('\n')
            sb.append('\n').append("OSP v0.6 : g = ").append(osp.optDouble("groundedness"))
                .append("  →  ").append(osp.optString("verdict"))
        } else {
            verified = true
        }

        out.text = sb.toString().trim()
        out.setTypeface(null, Typeface.NORMAL)
        out.setTextColor(
            if (verified) Color.rgb(46, 125, 50) else Color.rgb(198, 40, 40)
        )
    }
}
