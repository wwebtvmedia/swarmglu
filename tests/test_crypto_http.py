# -*- coding: utf-8 -*-
"""
Cryptographie de bout en bout PAR HTTP — ML-KEM-768 + ECDSA :

poignée de main mutuelle (/secure/handshake), RPC chiffré (/secure/rpc),
cycle de re-signature décentralisée 1/8 (/whitelist/trigger-resign) et
whitelist signée (/whitelist/signed).
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crypto_auth import NodeCrypto
from test_swarm_integration import attendre, http_json, nouveau_noeud, roles, LEASE


class TestCryptoHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n = nouveau_noeud("crypto-serveur")
        assert attendre(10, lambda: roles([cls.n]).get("crypto-serveur", ("?",))[0] == "leader")
        cls.base = "http://127.0.0.1:%d" % cls.n.config.port
        cls.dossier = tempfile.mkdtemp(prefix="sgcr_")
        cls.client = NodeCrypto("crypto-client", key_dir=cls.dossier)

    @classmethod
    def tearDownClass(cls):
        cls.n.stop()

    # -- poignée de main puis RPC (ordre des étapes) ------------------------

    def test_01_handshake_puis_rpc_ping_chiffré(self):
        init = self.client.initiate_handshake("crypto-serveur")
        status, reponse = http_json("POST", self.base + "/secure/handshake", init)
        self.assertEqual(status, 200, reponse)
        cle = self.client.finalize_handshake(init, reponse)
        self.assertEqual(len(cle), 32)  # clé de session 256 bits (HKDF)

        chiffre = self.client.encrypt_payload(cle, json.dumps({"action": "ping"}))
        status, rep = http_json("POST", self.base + "/secure/rpc",
                                {"sender_id": "crypto-client", "payload": chiffre})
        self.assertEqual(status, 200, rep)
        clair = json.loads(self.client.decrypt_payload(cle, rep["payload"]))
        self.assertEqual(clair["action"], "pong")

    def test_02_rpc_get_whitelist_chiffré(self):
        init = self.client.initiate_handshake("crypto-serveur")
        _s, reponse = http_json("POST", self.base + "/secure/handshake", init)
        cle = self.client.finalize_handshake(init, reponse)
        chiffre = self.client.encrypt_payload(cle, json.dumps({"action": "get_whitelist"}))
        _s, rep = http_json("POST", self.base + "/secure/rpc",
                            {"sender_id": "crypto-client", "payload": chiffre})
        clair = json.loads(self.client.decrypt_payload(cle, rep["payload"]))
        self.assertIn("crypto-serveur", clair["nodes"])  # whitelist via canal sûr
        self.assertIn("digest", clair)
        self.assertIn("signed", clair)

    def test_03_rpc_sans_session_refusé(self):
        status, rep = http_json("POST", self.base + "/secure/rpc",
                                {"sender_id": "inconnu", "payload": "x"})
        self.assertEqual(status, 401)
        self.assertIn("Session non authentifiée", rep["error"])

    def test_04_handshake_type_invalide(self):
        status, rep = http_json("POST", self.base + "/secure/handshake",
                                {"type": "nimporte"})
        self.assertEqual(status, 400)

    def test_05_rpc_payload_falsifié_refusé(self):
        init = self.client.initiate_handshake("crypto-serveur")
        _s, reponse = http_json("POST", self.base + "/secure/handshake", init)
        cle = self.client.finalize_handshake(init, reponse)
        status, rep = http_json("POST", self.base + "/secure/rpc",
                                {"sender_id": "crypto-client",
                                 "payload": "Z2FyYmFnZQ=="})   # pas du AES-GCM valide
        self.assertEqual(status, 400)

    # -- re-signature décentralisée (1/8 ; nœud seul => quorum 1) ----------

    def test_06_cycle_resign_puis_whitelist_signée(self):
        status, resultat = http_json(
            "POST", self.base + "/whitelist/trigger-resign", {}, timeout=60)
        self.assertEqual(status, 200, resultat)
        self.assertTrue(resultat.get("ok"), resultat)

        status, rep = http_json("GET", self.base + "/whitelist/signed")
        self.assertEqual(status, 200, rep)
        doc = rep["signed_whitelist"]
        self.assertEqual(doc.get("signer_node_id"), "crypto-serveur")
        self.assertIn("consensus_proof", doc)
        # Vérification INTÉGRALE via l'API du module : signature ECDSA du
        # corps canonique + preuve de consensus 1/8.
        from crypto_auth import AuthenticatedWhitelist
        ok, raison = AuthenticatedWhitelist.verify_signed_doc(
            doc, known_ecdsa_keys={"crypto-serveur": doc["signer_ecdsa_pub"]},
            active_nodes_count=1)
        self.assertTrue(ok, raison)

    def test_07_whitelist_signée_avant_premier_cycle_absente(self):
        # sur un nœud FRAIS (jamais signé) : 404 documenté
        frais = nouveau_noeud("crypto-neuf")
        try:
            status, rep = http_json("GET", "http://127.0.0.1:%d/whitelist/signed"
                                    % frais.config.port)
            self.assertEqual(status, 404)
            self.assertIn("Aucune whitelist signée", rep["error"])
        finally:
            frais.stop()


if __name__ == "__main__":
    unittest.main()
