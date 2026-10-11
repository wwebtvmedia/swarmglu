package com.tree4five.swarmglu

import org.json.JSONObject
import java.io.File
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/**
 * CONFORMITÉ GOLDEN — le Kotlin doit reproduire le Python BIT POUR BIT :
 *  1. vérifier un fichier de chaîne GÉNÉRÉ PAR LE PYTHON (liaison, sceller) ;
 *  2. PROLONGER cette chaîne avec le hash attendu calculé par le Python ;
 *  3. fusionner deux états dans les deux ordres → digest identique ET égal
 *     au digest Python (LWW commutatif trans-langage) ;
 *  4. digest whitelist, ordre IP, formatage str(float) Python.
 */
class GoldenTest {

    private val golden: JSONObject =
        JSONObject(this::class.java.classLoader.getResource("golden.json")!!.readText())

    @Test
    fun `chaine python verifiee et prolongee avec le meme hash`() {
        val c = golden.getJSONObject("chaine")
        val jsonl = c.getString("jsonl")
        val dossier = createTempDirectory()
        val fichier = File(dossier, "swarm_audit.jsonl")
        fichier.writeText(jsonl)

        // 1) le vérificateur Kotlin accepte la chaîne Python
        val chaine = HashChain(fichier.absolutePath)
        val rapport = chaine.verifier()
        assertTrue(rapport.optBoolean("ok"), "chaîne Python rejetée: ${rapport.optString("error")}")
        assertEquals(c.getInt("entries"), rapport.getInt("entries"))
        assertEquals(c.getString("last_hash"), rapport.getString("last_hash"))

        // 2) le bloc SUIVANT scellé par le Kotlin == hash calculé par Python
        val bloc = chaine.ajouter("suivant", "kot-a",
            JSONObject().put("test", true), 0.5, c.getDouble("temps_suivant"))
        assertEquals(c.getString("bloc_suivant_attendu"), bloc.getString("hash"),
            "le scellement Kotlin diverge du Python !")

        // et la chaîne prolongée reste vérifiable
        assertTrue(HashChain(fichier.absolutePath).verifier().optBoolean("ok"))
    }

    @Test
    fun `fusion lww commutative et identique au python`() {
        val e = golden.getJSONObject("etat")
        val docA = e.getJSONObject("docA")
        val docB = e.getJSONObject("docB")

        val ab = StateManager("kot-a", null)
        ab.fusionner(docA); ab.fusionner(docB)
        val ba = StateManager("py-b", null)
        ba.fusionner(docB); ba.fusionner(docA)

        assertEquals(ab.digest(), ba.digest(), "fusion non commutative en Kotlin")
        assertEquals(e.getString("digest_attendu"), ab.digest(),
            "digest Kotlin != digest Python (divergence de fusion LWW)")
    }

    @Test
    fun `digest whitelist identique`() {
        val w = golden.getJSONObject("whitelist")
        val dossier = createTempDirectory()
        val fichier = File(dossier, "wl.json")
        val wl = Whitelist(fichier.absolutePath)
        wl.synchroniser(w.getJSONArray("noeuds").let { a -> List(a.length()) { a.getString(it) } })
        assertEquals(w.getString("digest"), wl.digest())
    }

    @Test
    fun `ordre ip numerique`() {
        assertEquals(ipVersEntier("10.0.0.2") < ipVersEntier("10.0.0.10"), true)
        assertEquals(ipVersEntier("10.0.0.9"), ipVersEntier("10.0.0.9"))
        val (k1, _) = cleDeterministe("10.0.0.1", "z")
        val (k2, _) = cleDeterministe("10.0.0.2", "a")
        assertTrue(k1 < k2)
    }

    @Test
    fun `formatage flottant python`() {
        val f = golden.getJSONArray("floats")
        for (i in 0 until f.length()) {
            val echantillon = f.getJSONArray(i)
            assertEquals(echantillon.getString(1),
                Json.pythonFloat(echantillon.getDouble(0)),
                "str(float) divergent pour ${echantillon.get(0)}")
        }
    }

    private fun createTempDirectory(): File =
        createTempDir(prefix = "sgkt_")
}
