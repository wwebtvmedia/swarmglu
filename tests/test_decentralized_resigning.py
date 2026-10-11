# -*- coding: utf-8 -*-
"""
Tests d'intégration complets pour :
1. La poignée de main asymétrique hybride post-quantique (ML-KEM + ECDSA) et RPC sécurisé.
2. Le cycle de sélection aléatoire pondéré (probabilité proportionnelle à l'ancienneté et aux requêtes).
3. Le consensus 1/8 sollicité auprès des pairs de l'essaim.
4. La re-signature et la propagation de la nouvelle whitelist authentifiée.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import swarm_glu
from crypto_auth import NodeCrypto
from swarm_glu import NodeConfig

PORTS = iter(range(17800, 17899))
HB, LEASE = 0.3, 0.6


def http_json(method: str, url: str, body: dict = None, timeout: float = 5.0):
    data = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json; charset=utf-8"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def wait_until(timeout_s: float, condition):
    limit = time.time() + timeout_s
    while time.time() < limit:
        if condition():
            return True
        time.sleep(0.1)
    return False


class TestDecentralizedResigningAndSecureAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workdirs = []
        cls.p1 = next(PORTS)
        cls.p2 = next(PORTS)
        cls.p3 = next(PORTS)

        # Création de 3 nœuds réels
        def create_node(nid, port, peers=None):
            d = tempfile.mkdtemp(prefix=f"sg_res_{nid}_")
            cls.workdirs.append(d)
            cfg = NodeConfig(
                node_id=nid,
                port=port,
                mode="full",
                swarm_id="swarm-pqc",
                whitelist_file=os.path.join(d, "wl.json"),
                data_dir=d,
                peers=["127.0.0.1:%d" % p for p in (peers or [])],
                beacon_port=0,
                mock_inference=True,
                allow_join=True,
                heartbeat_interval=HB,
                lease_timeout=LEASE,
                sync_interval=0.3,
                resign_interval=0,  # Déclenché manuellement dans les tests
                enable_resigning=True,
                log_level="ERROR",
            )
            return swarm_glu.serve(cfg, block=False)

        cls.n1 = create_node("node-1", cls.p1)
        time.sleep(LEASE + 0.5)
        cls.n2 = create_node("node-2", cls.p2, peers=[cls.p1])
        cls.n3 = create_node("node-3", cls.p3, peers=[cls.p1])
        cls.nodes = [cls.n1, cls.n2, cls.n3]

        # Attendre que les pairs se découvrent et s'enregistrent
        ok = wait_until(
            10.0,
            lambda: all(
                len(n.state.alive_peers()) >= 3 for n in cls.nodes
            ),
        )
        assert ok, "Les nœuds ne se sont pas tous découverts"

    @classmethod
    def tearDownClass(cls):
        for n in cls.nodes:
            n.stop()
        time.sleep(0.3)
        for d in cls.workdirs:
            shutil.rmtree(d, ignore_errors=True)

    def test_01_secure_handshake_and_rpc(self):
        """Vérifie le handshake asymétrique ML-KEM + ECDSA et le RPC chiffré."""
        # Un client externe génère ses clés et initie une poignée de main vers node-1
        client_crypto = NodeCrypto("ext-client")
        init_payload = client_crypto.initiate_handshake("node-1")

        code, resp = http_json(
            "POST", f"http://127.0.0.1:{self.p1}/secure/handshake", init_payload
        )
        self.assertEqual(code, 200)
        self.assertEqual(resp.get("sender_id"), "node-1")
        self.assertIn("ciphertext", resp)

        # Finalisation du handshake côté client
        session_key = client_crypto.finalize_handshake(init_payload, resp)
        self.assertEqual(len(session_key), 32)

        # Exécution d'une commande RPC chiffrée
        cmd = {"action": "get_whitelist"}
        enc_payload = client_crypto.encrypt_payload(
            session_key, json.dumps(cmd)
        )
        rpc_body = {"sender_id": "ext-client", "payload": enc_payload}
        code, rpc_resp = http_json(
            "POST", f"http://127.0.0.1:{self.p1}/secure/rpc", rpc_body
        )
        self.assertEqual(code, 200)
        decrypted_raw = client_crypto.decrypt_payload(
            session_key, rpc_resp.get("payload", "")
        )
        result = json.loads(decrypted_raw.decode("utf-8"))
        self.assertIn("nodes", result)
        self.assertIn("node-1", result["nodes"])

    def test_02_weighted_lottery_proportionality(self):
        """
        Vérifie que la probabilité est proportionnelle à l'ancienneté
        et au volume de requêtes dans l'essaim.
        """
        # Simulons des requêtes sur node-1 pour augmenter son activité
        for _ in range(5):
            http_json("POST", f"http://127.0.0.1:{self.p1}/ask", {"prompt": "Quelle est la capitale ?"})

        metrics = self.n1.resigner.collect_swarm_metrics()
        self.assertIn("node-1", metrics)
        self.assertIn("node-2", metrics)
        self.assertIn("node-3", metrics)

        # node-1 a traité plusieurs requêtes -> requêtes >= 5
        self.assertGreaterEqual(metrics["node-1"]["requests_count"], 5)

        # Calcul des probabilités
        from crypto_auth import WeightedLottery
        probs = WeightedLottery.compute_probabilities(metrics)

        # node-1 étant plus actif et ayant au moins autant d'ancienneté,
        # sa probabilité de sélection doit être significativement supérieure
        self.assertGreater(probs["node-1"], probs["node-2"])
        self.assertGreater(probs["node-1"], probs["node-3"])

    def test_03_consensus_1_8_and_resigning_cycle(self):
        """
        Déclenche la sélection, sollicite le consensus 1/8 auprès des pairs,
        re-signe la whitelist et vérifie la propagation sur tous les nœuds.
        """
        # Déclenchement de la re-signature sur node-1
        code, result = http_json(
            "POST", f"http://127.0.0.1:{self.p1}/whitelist/trigger-resign",
            {"force_self": True}
        )
        self.assertEqual(code, 200)
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("resigned"))
        self.assertEqual(result.get("signer"), "node-1")

        # Consensus 1/8 : pour 3 nœuds, ceil(3/8) = 1 accord requis
        self.assertGreaterEqual(result.get("approvals_count"), 1)
        self.assertEqual(result.get("threshold"), 1)

        # Vérification que le document re-signé est présent sur node-1
        signed_wl = result.get("signed_whitelist")
        self.assertIsNotNone(signed_wl)
        self.assertEqual(signed_wl["signer_node_id"], "node-1")
        self.assertIn("signature", signed_wl)

        # Attendre que la diffusion atteigne node-2 et node-3
        ok_prop = wait_until(
            5.0,
            lambda: (
                self.n2.security.get_signed_whitelist() is not None
                and self.n3.security.get_signed_whitelist() is not None
            ),
        )
        self.assertTrue(ok_prop, "La whitelist re-signée ne s'est pas propagée aux pairs")

        # Vérifier que node-2 et node-3 valident la signature et le consensus 1/8
        ok2, msg2 = self.n2.security.verify_current_signed_whitelist()
        self.assertTrue(ok2, f"node-2 a rejeté la whitelist: {msg2}")

        ok3, msg3 = self.n3.security.verify_current_signed_whitelist()
        self.assertTrue(ok3, f"node-3 a rejeté la whitelist: {msg3}")

        # Vérifier via l'API GET /whitelist/signed
        code, signed_resp = http_json("GET", f"http://127.0.0.1:{self.p2}/whitelist/signed")
        self.assertEqual(code, 200)
        self.assertTrue(signed_resp.get("ok"))
        self.assertEqual(signed_resp["signed_whitelist"]["signer_node_id"], "node-1")


if __name__ == "__main__":
    unittest.main()
