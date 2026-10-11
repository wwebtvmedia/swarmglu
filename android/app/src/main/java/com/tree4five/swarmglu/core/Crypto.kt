package com.tree4five.swarmglu.core

import org.bouncycastle.pqc.crypto.mlkem.MLKEMExtractor
import org.bouncycastle.pqc.crypto.mlkem.MLKEMGenerator
import org.bouncycastle.pqc.crypto.mlkem.MLKEMKeyPairGenerator
import org.bouncycastle.pqc.crypto.mlkem.MLKEMParameters
import org.bouncycastle.pqc.crypto.mlkem.MLKEMPrivateKeyParameters
import org.bouncycastle.pqc.crypto.mlkem.MLKEMPublicKeyParameters
import org.bouncycastle.crypto.digests.SHA256Digest
import org.bouncycastle.crypto.generators.HKDFBytesGenerator
import org.bouncycastle.crypto.params.HKDFParameters
import org.json.JSONObject
import java.io.File
import java.math.BigDecimal
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.PrivateKey
import java.security.PublicKey
import java.security.SecureRandom
import java.security.Signature
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.ECPoint
import java.security.spec.ECPublicKeySpec
import java.security.spec.PKCS8EncodedKeySpec
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * CRYPTO POST-QUANTIQUE NATIVE Android — portage de crypto_auth.NodeCrypto.
 *
 * ECDSA P-256 (java.security, signatures DER SHA256 — identiques au Python
 * `cryptography`) ; ML-KEM-768 (BouncyCastle, API légère SANS Provider —
 * Android l'interdit au runtime) ; HKDF-SHA256 + AES-256-GCM
 * (javax.crypto). Chaînes signées et enveloppes IDENTIQUES au Python —
 * conformité prouvée par CryptoCompatTest (vecteurs golden).
 */
class NodeCrypto(val nodeId: String, keyDir: String?) {

    private lateinit var ecdsaPriv: PrivateKey
    private lateinit var ecdsaPubHexChamp: String
    private lateinit var mlkemPriv: MLKEMPrivateKeyParameters
    private val alea = SecureRandom()

    companion object {
        const val CONTEXTE_HKDF = "SwarmGlu-Auth-v1"
        private val ALEA = SecureRandom()

        private fun parametresP256(): ECParameterSpec {
            val params = java.security.AlgorithmParameters.getInstance("EC")
            params.init(ECGenParameterSpec("secp256r1"))
            return params.getParameterSpec(ECParameterSpec::class.java)
        }

        fun clePubliqueDepuisPoint(hex: String): PublicKey {
            val o = hexVersOctets(hex)
            require(o.size == 65 && o[0] == 0x04.toByte()) {
                "clé publique ECDSA non compressée attendue (04||X||Y)"
            }
            val point = ECPoint(java.math.BigInteger(1, o.copyOfRange(1, 33)),
                java.math.BigInteger(1, o.copyOfRange(33, 65)))
            return KeyFactory.getInstance("EC")
                .generatePublic(ECPublicKeySpec(point, parametresP256()))
        }

        fun verifySignature(message: String, signatureHex: String, ecdsaPubHex: String): Boolean =
            try {
                val sig = Signature.getInstance("SHA256withECDSA")
                sig.initVerify(clePubliqueDepuisPoint(ecdsaPubHex))
                sig.update(message.toByteArray(Charsets.UTF_8))
                sig.verify(hexVersOctets(signatureHex))
            } catch (_: Exception) {
                false
            }

        fun deriveSessionKey(secretPartage: ByteArray, sel: ByteArray): ByteArray {
            val gen = HKDFBytesGenerator(SHA256Digest())
            gen.init(HKDFParameters(secretPartage, sel, CONTEXTE_HKDF.toByteArray()))
            val sortie = ByteArray(32)
            gen.generateBytes(sortie, 0, 32)
            return sortie
        }

        fun encryptPayload(cle: ByteArray, texte: String): String {
            val nonce = ByteArray(12).also { ALEA.nextBytes(it) }
            val chiffre = Cipher.getInstance("AES/GCM/NoPadding")
            chiffre.init(Cipher.ENCRYPT_MODE, SecretKeySpec(cle, "AES"),
                GCMParameterSpec(128, nonce))
            val ct = chiffre.doFinal(texte.toByteArray(Charsets.UTF_8))
            return JSONObject().put("nonce", octetsVersHex(nonce))
                .put("ct", octetsVersHex(ct)).toString()
        }

        fun decryptPayload(cle: ByteArray, enveloppe: String): ByteArray {
            val e = JSONObject(enveloppe)
            val chiffre = Cipher.getInstance("AES/GCM/NoPadding")
            chiffre.init(Cipher.DECRYPT_MODE, SecretKeySpec(cle, "AES"),
                GCMParameterSpec(128, hexVersOctets(e.getString("nonce"))))
            return chiffre.doFinal(hexVersOctets(e.getString("ct")))
        }

        fun hexVersOctets(hex: String): ByteArray =
            ByteArray(hex.length / 2) { ((Character.digit(hex[2 * it], 16) shl 4)
                + Character.digit(hex[2 * it + 1], 16)).toByte() }

        fun octetsVersHex(o: ByteArray): String =
            o.joinToString("") { "%02x".format(it) }
    }

    init {
        val fichier = keyDir?.let { File(it, "keys_$nodeId.json") }
        var charge = false
        if (fichier != null && fichier.exists()) {
            try {
                val doc = JSONObject(fichier.readText())
                val prive = KeyFactory.getInstance("EC").generatePrivate(
                    PKCS8EncodedKeySpec(Base64.getDecoder().decode(pemCorps(doc.getString("ecdsa_priv_pem")))))
                ecdsaPriv = prive
                ecdsaPubHexChamp = doc.getString("ecdsa_pub_hex")
                mlkemPriv = MLKEMPrivateKeyParameters(
                    MLKEMParameters.ml_kem_768,
                    hexVersOctets(doc.getString("mlkem_priv_hex")))
                charge = true
            } catch (_: Exception) {
                charge = false
            }
        }
        if (!charge) {
            val gen = KeyPairGenerator.getInstance("EC")
            gen.initialize(ECGenParameterSpec("secp256r1"), alea)
            val paire = gen.generateKeyPair()
            ecdsaPriv = paire.private
            ecdsaPubHexChamp = pointHexDePublique(paire.public as ECPublicKey)
            val genMlkem = MLKEMKeyPairGenerator()
            genMlkem.init(org.bouncycastle.pqc.crypto.mlkem.MLKEMKeyGenerationParameters(
                alea, MLKEMParameters.ml_kem_768))
            mlkemPriv = genMlkem.generateKeyPair().private as MLKEMPrivateKeyParameters
            if (fichier != null) {
                fichier.parentFile?.mkdirs()
                fichier.writeText(JSONObject()
                    .put("ecdsa_priv_pem", pemDePrivee(ecdsaPriv))
                    .put("ecdsa_pub_hex", ecdsaPubHexChamp)
                    .put("mlkem_priv_hex", octetsVersHex(mlkemPriv.getEncoded()))
                    .toString())
            }
        }
    }

    val mode: String get() = "android-native"   // crypto réelle, toujours

    val ecdsaPubHex: String get() = ecdsaPubHexChamp
    val mlkemPubHex: String
        get() = octetsVersHex(mlkemPriv.getPublicKeyParameters().getEncoded())

    fun sign(message: String): String {
        val sig = Signature.getInstance("SHA256withECDSA")
        sig.initSign(ecdsaPriv)
        sig.update(message.toByteArray(Charsets.UTF_8))
        return octetsVersHex(sig.sign())
    }

    fun encapsuler(pubHexDistant: String): Pair<ByteArray, String> {
        val distant = MLKEMPublicKeyParameters(
            MLKEMParameters.ml_kem_768, hexVersOctets(pubHexDistant))
        val res = MLKEMGenerator(alea).generateEncapsulated(distant)
        return res.secret to octetsVersHex(res.encapsulation)
    }

    fun decapsuler(ciphertextHex: String): ByteArray =
        MLKEMExtractor(mlkemPriv).extractSecret(hexVersOctets(ciphertextHex))

    // -- poignée de main (chaînes signées identiques au Python) ---------------

    fun initiateHandshake(cible: String): JSONObject {
        val nonce = octetsVersHex(ByteArray(16).also { alea.nextBytes(it) })
        val ts = Math.round(System.currentTimeMillis() / 10.0) / 1000.0
        val msg = "SWARM_AUTH_INIT:$nodeId:$cible:$mlkemPubHex:$nonce:${Json.pythonFloat(ts)}"
        return JSONObject()
            .put("type", "handshake_init").put("sender_id", nodeId)
            .put("target_id", cible).put("ecdsa_pub", ecdsaPubHex)
            .put("mlkem_pub", mlkemPubHex).put("nonce", nonce)
            .put("timestamp", BigDecimal(Json.pythonFloat(ts)))
            .put("signature", sign(msg))
    }

    fun respondHandshake(init: JSONObject): Pair<JSONObject, ByteArray> {
        val emetteur = init.getString("sender_id")
        val aVerifier = "SWARM_AUTH_INIT:$emetteur:$nodeId:" +
            "${init.getString("mlkem_pub")}:${init.getString("nonce")}:${init.opt("timestamp")}"
        if (!verifySignature(aVerifier, init.getString("signature"), init.getString("ecdsa_pub")))
            throw IllegalArgumentException("Signature d'initiation de poignée de main invalide")
        val (secret, ctHex) = encapsuler(init.getString("mlkem_pub"))
        val nonceRep = octetsVersHex(ByteArray(16).also { alea.nextBytes(it) })
        val tsRepStr = Json.pythonFloat(Math.round(System.currentTimeMillis() / 10.0) / 1000.0)
        val aSigner = "SWARM_AUTH_RESP:$nodeId:$emetteur:$ctHex:" +
            "${init.getString("nonce")}:$nonceRep:$tsRepStr"
        val sel = java.security.MessageDigest.getInstance("SHA-256")
            .digest("${init.getString("nonce")}:$nonceRep".toByteArray())
        val session = deriveSessionKey(secret, sel)
        return JSONObject()
            .put("type", "handshake_resp").put("sender_id", nodeId)
            .put("target_id", emetteur).put("ecdsa_pub", ecdsaPubHex)
            .put("ciphertext", ctHex).put("nonce", nonceRep)
            .put("timestamp", BigDecimal(tsRepStr))
            .put("signature", sign(aSigner)) to session
    }

    fun finalizeHandshake(init: JSONObject, rep: JSONObject): ByteArray {
        val emetteur = rep.getString("sender_id")
        val aVerifier = "SWARM_AUTH_RESP:$emetteur:${init.getString("sender_id")}:" +
            "${rep.getString("ciphertext")}:${init.getString("nonce")}:" +
            "${rep.getString("nonce")}:${rep.opt("timestamp")}"
        if (!verifySignature(aVerifier, rep.getString("signature"), rep.getString("ecdsa_pub")))
            throw IllegalArgumentException("Signature de réponse de poignée de main invalide")
        val secret = decapsuler(rep.getString("ciphertext"))
        val sel = java.security.MessageDigest.getInstance("SHA-256")
            .digest("${init.getString("nonce")}:${rep.getString("nonce")}".toByteArray())
        return deriveSessionKey(secret, sel)
    }

    private fun pemCorps(pem: String): String =
        pem.lines().filter { !it.startsWith("-----") && it.isNotBlank() }.joinToString("")

    private fun pemDePrivee(k: PrivateKey): String =
        "-----BEGIN PRIVATE KEY-----\n" +
            Base64.getEncoder().encodeToString(k.encoded) + "\n-----END PRIVATE KEY-----\n"

    private fun pointHexDePublique(pub: ECPublicKey): String {
        val w = pub.w
        val x = ByteArray(32); val y = ByteArray(32)
        val xa = w.affineX.toByteArray(); val ya = w.affineY.toByteArray()
        System.arraycopy(xa, maxOf(0, xa.size - 32), x, maxOf(0, 32 - xa.size), minOf(32, xa.size))
        System.arraycopy(ya, maxOf(0, ya.size - 32), y, maxOf(0, 32 - ya.size), minOf(32, ya.size))
        return "04" + octetsVersHex(x) + octetsVersHex(y)
    }
}
