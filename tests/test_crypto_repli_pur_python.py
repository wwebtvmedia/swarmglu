# -*- coding: utf-8 -*-
"""
Repli pur-Python de crypto_auth (mode « mock ») — le cas « machine neuve,
AUCUN pip » : sans la bibliothèque cryptography, la poignée de main, la
dérivation de clé de session, les signatures de consensus et la whitelist
signée doivent TOUJOURS fonctionner.

Bug historique que ce verrouille (constaté dans un conteneur Debian sans pip,
11 tests de la suite en échec) :
  * sign() HMAC était indexé sur la graine privée alors que verify_signature()
    était indexé sur la clé publique → toute poignée de main refusée (400) ;
  * encapsulate() injectait un aléa dans le secret que decapsulate() ne
    pouvait pas recalculer → clés de session divergentes → déchiffrement faux.

Sur l'hôte de développement cryptography est présent : sans ce patch
HAS_CRYPTOGRAPHY=False, ces branches ne seraient JAMAIS exercées.
"""

import unittest
from unittest import mock

import crypto_auth
from crypto_auth import (
    AuthenticatedWhitelist,
    Consensus18Verifier,
    NodeCrypto,
)


class TestRepliPurPython(unittest.TestCase):
    """Force HAS_CRYPTOGRAPHY=False : chaque nœud est en mode « mock »."""

    def setUp(self):
        patcher = mock.patch.object(crypto_auth, "HAS_CRYPTOGRAPHY", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Pas de key_dir → clés éphémères en mémoire, format identique
        # à un vrai nœud (repli de secours sans fichier de clés).
        self.a = NodeCrypto("natif-a")
        self.b = NodeCrypto("natif-b")

    def _poignee(self):
        """Poignée de main complète A → B → A, retourne les2 clés."""
        init = self.a.initiate_handshake("natif-b")
        resp, cle_b = self.b.respond_handshake(init)
        cle_a = self.a.finalize_handshake(init, resp)
        return init, resp, cle_a, cle_b

    def test_01_mode_mock_effectif(self):
        self.assertEqual(self.a.mode, "mock")
        self.assertEqual(self.b.mode, "mock")

    def test_02_poignee_de_main_puis_clé_commune(self):
        _init, _resp, cle_a, cle_b = self._poignee()
        self.assertEqual(cle_a, cle_b, "clés de session divergentes")
        self.assertEqual(len(cle_a), 32)

    def test_03_chiffrement_roundtrip_entre_noeuds(self):
        _init, _resp, cle_a, cle_b = self._poignee()
        phrase = "Quelle est la capitale de la Tunisie ?"
        enveloppe = NodeCrypto.encrypt_payload(cle_a, phrase)
        dechiffre = NodeCrypto.decrypt_payload(cle_b, enveloppe)
        self.assertEqual(dechiffre, phrase.encode("utf-8"))

    def test_04_handshake_rejette_signature_alteree(self):
        init = self.a.initiate_handshake("natif-b")
        init["signature"] = init["signature"][:-4] + "0000"
        with self.assertRaises(ValueError):
            self.b.respond_handshake(init)

    def test_05_signatures_de_consensus_consistentes(self):
        msg = "SWARM_1_8_VOTE:5:natif-a:natif-b:1700000000.0"
        sig = self.b.sign(msg)
        self.assertTrue(NodeCrypto.verify_signature(msg, sig, self.b.ecdsa_pub_hex))
        self.assertFalse(NodeCrypto.verify_signature(msg + "!", sig,
                                                     self.b.ecdsa_pub_hex))
        # Une autre paire ne valide pas la signature d'un pair.
        autre = NodeCrypto("natif-c")
        self.assertFalse(NodeCrypto.verify_signature(msg, sig,
                                                     autre.ecdsa_pub_hex))

    def test_06_consensus_1_8_et_whitelist_signée(self):
        epoch, cible = 5, "natif-a"
        ts = 1700000000.0

        def vote(crypto):
            m = f"SWARM_1_8_VOTE:{epoch}:{cible}:{crypto.node_id}:{ts}"
            return {"voter_id": crypto.node_id,
                    "signature": crypto.sign(m),
                    "timestamp": ts,
                    "voter_ecdsa_pub": crypto.ecdsa_pub_hex}

        proof = {"target_signer": cible, "epoch": epoch,
                 "approvals": [vote(self.a), vote(self.b)], "total_nodes": 4}
        ok, raison = Consensus18Verifier.verify_proof(proof, {}, 4)
        self.assertTrue(ok, raison)

        doc = AuthenticatedWhitelist.build_signed_doc(
            self.a, ["natif-a", "natif-b"], proof, epoch, "salle-natif")
        ok, raison = AuthenticatedWhitelist.verify_signed_doc(
            doc, {}, active_nodes_count=4)
        self.assertTrue(ok, raison)

        # Altération du corps → signature invalide.
        doc_alt = dict(doc, epoch=epoch + 1)
        ok, raison = AuthenticatedWhitelist.verify_signed_doc(
            doc_alt, {}, active_nodes_count=4)
        self.assertFalse(ok, raison)


if __name__ == "__main__":
    unittest.main()
