# -*- coding: utf-8 -*-
"""
Tests unitaires pour crypto_auth.py :
- Clés asymétriques ECDSA (P-256) et post-quantique ML-KEM (ML-KEM-768).
- Poignée de main (handshake) mutuellement authentifiée et chiffrement symétrique de session.
- Tirage probabiliste pondéré proportionnel au temps et aux requêtes.
- Règle de consensus 1/8 (seuil et vérification des signatures des pairs).
- Génération et validation de la whitelist signée.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crypto_auth import (
    AuthenticatedWhitelist,
    Consensus18Verifier,
    NodeCrypto,
    WeightedLottery,
    canonical_json,
)


class TestNodeCrypto(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="crypto_test_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_keypair_generation_and_persistence(self):
        c1 = NodeCrypto("node-alpha", key_dir=self.temp_dir)
        self.assertTrue(len(c1.ecdsa_pub_hex) > 0)
        self.assertTrue(len(c1.mlkem_pub_hex) > 0)

        # Rechargement des mêmes clés depuis le disque
        c2 = NodeCrypto("node-alpha", key_dir=self.temp_dir)
        self.assertEqual(c1.ecdsa_pub_hex, c2.ecdsa_pub_hex)
        self.assertEqual(c1.mlkem_pub_hex, c2.mlkem_pub_hex)

    def test_ecdsa_signature_and_verification(self):
        c = NodeCrypto("node-signer", key_dir=self.temp_dir)
        message = "SwarmGlu whitelist event verification 123"
        sig = c.sign(message)
        self.assertTrue(isinstance(sig, str))
        self.assertTrue(NodeCrypto.verify_signature(message, sig, c.ecdsa_pub_hex))

        # Échec avec message altéré
        self.assertFalse(NodeCrypto.verify_signature("Tampered message", sig, c.ecdsa_pub_hex))

        # Échec avec clé publique différente
        other = NodeCrypto("node-other", key_dir=self.temp_dir)
        self.assertFalse(NodeCrypto.verify_signature(message, sig, other.ecdsa_pub_hex))

    def test_mlkem_encapsulation_decapsulation(self):
        alice = NodeCrypto("alice", key_dir=self.temp_dir)
        bob = NodeCrypto("bob", key_dir=self.temp_dir)

        # Bob encapsule pour Alice
        shared_secret_bob, ciphertext = bob.encapsulate(alice.mlkem_pub_hex)
        self.assertTrue(len(shared_secret_bob) > 0)

        # Alice décapsule avec sa clé privée
        shared_secret_alice = alice.decapsulate(ciphertext)
        self.assertEqual(shared_secret_bob, shared_secret_alice)

    def test_hybrid_handshake_and_authenticated_encryption(self):
        alice = NodeCrypto("alice", key_dir=self.temp_dir)
        bob = NodeCrypto("bob", key_dir=self.temp_dir)

        # 1. Alice initie le handshake vers Bob
        init_msg = alice.initiate_handshake("bob")
        self.assertEqual(init_msg["sender_id"], "alice")

        # 2. Bob répond et dérive sa clé de session
        resp_msg, bob_session_key = bob.respond_handshake(init_msg)
        self.assertEqual(resp_msg["sender_id"], "bob")

        # 3. Alice finalise et dérive sa clé de session
        alice_session_key = alice.finalize_handshake(init_msg, resp_msg)
        self.assertEqual(alice_session_key, bob_session_key)

        # 4. Échange de payload chiffré et authentifié
        original_data = {"action": "whitelist_sync", "count": 42}
        encrypted = NodeCrypto.encrypt_payload(alice_session_key, canonical_json(original_data))
        decrypted_bytes = NodeCrypto.decrypt_payload(bob_session_key, encrypted)
        decrypted_data = json.loads(decrypted_bytes.decode("utf-8"))
        self.assertEqual(original_data, decrypted_data)


class TestWeightedLottery(unittest.TestCase):
    def test_weight_calculation(self):
        # W = max(time, 1) * (requests + 1)
        w1 = WeightedLottery.calculate_weight(uptime_s=10.0, requests_count=4)
        self.assertEqual(w1, 50.0)

        w_zero = WeightedLottery.calculate_weight(uptime_s=0.2, requests_count=0)
        self.assertEqual(w_zero, 1.0)

    def test_probabilities_proportional_to_time_and_requests(self):
        metrics = {
            "node_old_active": {"uptime_s": 200.0, "requests_count": 9},    # W = 200 * 10 = 2000
            "node_new_idle": {"uptime_s": 20.0, "requests_count": 0},      # W = 20 * 1 = 20
        }
        probs = WeightedLottery.compute_probabilities(metrics)
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=4)
        # 2000 / 2020 = 0.9901 (soit environ 99%)
        self.assertGreater(probs["node_old_active"], 0.98)
        self.assertLess(probs["node_new_idle"], 0.02)

    def test_deterministic_seed_selection(self):
        metrics = {
            "node_a": {"uptime_s": 100.0, "requests_count": 5},
            "node_b": {"uptime_s": 50.0, "requests_count": 2},
            "node_c": {"uptime_s": 10.0, "requests_count": 0},
        }
        sel1, _ = WeightedLottery.select_node(metrics, seed="round-epoch-42")
        sel2, _ = WeightedLottery.select_node(metrics, seed="round-epoch-42")
        self.assertEqual(sel1, sel2)


class TestConsensus18(unittest.TestCase):
    def test_threshold_rule(self):
        # 1 à 8 nœuds -> seuil 1 (1/8 de 8 = 1)
        for n in range(1, 9):
            self.assertEqual(Consensus18Verifier.threshold(n), 1)

        # 9 à 16 nœuds -> seuil 2 (ceil(9/8)=2)
        for n in range(9, 17):
            self.assertEqual(Consensus18Verifier.threshold(n), 2)

        # 24 nœuds -> 3
        self.assertEqual(Consensus18Verifier.threshold(24), 3)

    def test_verify_proof_valid_and_invalid(self):
        c1 = NodeCrypto("node-1")
        c2 = NodeCrypto("node-2")
        c3 = NodeCrypto("node-3")

        keys = {
            "node-1": c1.ecdsa_pub_hex,
            "node-2": c2.ecdsa_pub_hex,
            "node-3": c3.ecdsa_pub_hex,
        }

        epoch = 1
        ts2 = 100.0
        sig2 = c2.sign(f"SWARM_1_8_VOTE:{epoch}:node-1:node-2:{ts2}")

        # Pour 8 nœuds, 1 vote suffit
        proof_ok = {
            "target_signer": "node-1",
            "epoch": epoch,
            "total_nodes": 8,
            "approvals": [
                {"voter_id": "node-2", "signature": sig2, "timestamp": ts2, "voter_ecdsa_pub": c2.ecdsa_pub_hex}
            ]
        }
        ok, reason = Consensus18Verifier.verify_proof(proof_ok, keys, active_nodes_count=8)
        self.assertTrue(ok)

        # Pour 16 nœuds, 1 vote ne suffit pas (seuil = 2)
        ok_fail, reason_fail = Consensus18Verifier.verify_proof(proof_ok, keys, active_nodes_count=16)
        self.assertFalse(ok_fail)
        self.assertIn("Quorum 1/8 insuffisant", reason_fail)

        # Ajout du vote de node-3
        ts3 = 101.0
        sig3 = c3.sign(f"SWARM_1_8_VOTE:{epoch}:node-1:node-3:{ts3}")
        proof_ok["approvals"].append(
            {"voter_id": "node-3", "signature": sig3, "timestamp": ts3, "voter_ecdsa_pub": c3.ecdsa_pub_hex}
        )
        ok_16, _ = Consensus18Verifier.verify_proof(proof_ok, keys, active_nodes_count=16)
        self.assertTrue(ok_16)


class TestAuthenticatedWhitelist(unittest.TestCase):
    def test_build_and_verify_whitelist(self):
        c_signer = NodeCrypto("signer-node")
        c_voter = NodeCrypto("voter-node")

        keys = {
            "signer-node": c_signer.ecdsa_pub_hex,
            "voter-node": c_voter.ecdsa_pub_hex,
        }

        ts = 200.0
        vote_sig = c_voter.sign(f"SWARM_1_8_VOTE:2:signer-node:voter-node:{ts}")
        proof = {
            "target_signer": "signer-node",
            "epoch": 2,
            "total_nodes": 4,
            "approvals": [
                {"voter_id": "voter-node", "signature": vote_sig, "timestamp": ts, "voter_ecdsa_pub": c_voter.ecdsa_pub_hex}
            ]
        }

        nodes = [
            {"node_id": "signer-node", "ecdsa_pub": c_signer.ecdsa_pub_hex},
            {"node_id": "voter-node", "ecdsa_pub": c_voter.ecdsa_pub_hex},
        ]

        doc = AuthenticatedWhitelist.build_signed_doc(
            signer_crypto=c_signer,
            nodes_list=nodes,
            consensus_proof=proof,
            epoch=2,
            swarm_id="salle-test",
        )

        ok, msg = AuthenticatedWhitelist.verify_signed_doc(doc, known_ecdsa_keys=keys, active_nodes_count=4)
        self.assertTrue(ok)

        # Falsification : altérer un nœud dans la whitelist
        doc_tampered = dict(doc)
        doc_tampered["nodes"] = [{"node_id": "hacked-node"}]
        ok_tampered, _ = AuthenticatedWhitelist.verify_signed_doc(doc_tampered, known_ecdsa_keys=keys, active_nodes_count=4)
        self.assertFalse(ok_tampered)


if __name__ == "__main__":
    unittest.main()
