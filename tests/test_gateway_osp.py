# -*- coding: utf-8 -*-
"""Tests unitaires — gateway.py, pare-feu OSP, pont MCP, transport."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gateway import MAX_FEDERATION_HOPS, NEIGHBOR_TTL, SwarmGateway
from swarm_glu import (GroundednessFirewall, HttpTransport, McpDispatcher,
                       NodeConfig)


class NoeudFactice:
    """Juste ce qu'il faut pour SwarmGateway (pas de réseau)."""

    def __init__(self):
        self.d = tempfile.mkdtemp(prefix="sgg_")
        self.identity = {"node_id": "amb", "ip": "10.0.0.1", "port": 7001}
        self.node_id = "amb"
        self.security = __import__("security").SecurityManager(
            os.path.join(self.d, "wl.json"), os.path.join(self.d, "a.jsonl"))
        self.state = __import__("state_manager").StateManager("amb", security=self.security)


class TestPareFeuOsp(unittest.TestCase):
    """OSP v0.6 : indice de véracité ancrée, seuil g >= 0.30."""

    def test_aucune_source_g_nul(self):
        self.assertEqual(GroundednessFirewall.compute({"sources": []}), 0.0)

    def test_une_source_puis_saturation(self):
        self.assertEqual(GroundednessFirewall.compute({"sources": ["s1"]}), 0.4)
        self.assertAlmostEqual(GroundednessFirewall.compute({"sources": ["s1", "s2"]}), 0.6, places=6)
        self.assertEqual(
            GroundednessFirewall.compute({"sources": ["s%d" % i for i in range(20)]}), 1.0)

    def test_valeur_explicite_prise_et_bornée(self):
        self.assertEqual(GroundednessFirewall.compute({"groundedness": 0.75}), 0.75)
        self.assertEqual(GroundednessFirewall.compute({"groundedness": 4.2}), 1.0)
        self.assertEqual(GroundednessFirewall.compute({"groundedness": -1}), 0.0)

    def test_verdict_seuil_030(self):
        v = GroundednessFirewall.verdict(0.30)
        self.assertEqual(v["verdict"], "VERIFIED")
        self.assertTrue(v["rag_eligible"])
        v = GroundednessFirewall.verdict(0.299)
        self.assertEqual(v["verdict"], "UNVERIFIED")
        self.assertFalse(v["rag_eligible"])
        self.assertIn("pare-feu", v["note_osp"].lower())


class TestMcpDispatcher(unittest.TestCase):
    def test_mode_echo_documente_lextension(self):
        resultat = McpDispatcher(NodeConfig()).execute(
            {"mcp": {"server": "harnessdroid", "tool": "t", "args": {}},
             "approved_by": "prof"})
        self.assertEqual(resultat["status"], "executed")
        self.assertEqual(resultat["mode"], "echo")
        self.assertTrue(any("AIDL" in ligne for ligne in resultat["transcript"]))


class TestTransport(unittest.TestCase):
    def test_injoignable_renvoie_zero(self):
        transport = HttpTransport()
        status, reponse = transport.post("127.0.0.1", 1, "/status", {}, timeout=1.0)
        self.assertEqual(status, 0)
        self.assertIsNone(reponse)


class TestPasserelle(unittest.TestCase):
    def setUp(self):
        self.noeud = NoeudFactice()
        self.gw = SwarmGateway(self.noeud, "salle-1")

    def test_echange_de_digest_enregistre_les_deux_côtés(self):
        status, reponse = self.gw.handle_sync({
            "swarm_id": "salle-2", "epoch": 3, "state_hash": "abc",
            "node_count": 5, "ambassador_addr": "10.0.0.9:7001"})
        self.assertEqual(status, 200)
        voisin = self.gw.neighbors()[0]
        self.assertEqual((voisin["swarm_id"], voisin["epoch"], voisin["node_count"]),
                         ("salle-2", 3, 5))
        self.assertEqual(reponse["digest"]["swarm_id"], "salle-1")
        # propagé dans l'état distribué pour tout l'essaim
        self.assertIsNotNone(self.noeud.state.get_meta("gateway:salle-2"))

    def test_même_essaim_ignoré(self):
        status, reponse = self.gw.handle_sync({"swarm_id": "salle-1"})
        self.assertTrue(reponse["self"])
        self.assertEqual(self.gw.neighbors(), [])

    def test_digest_local_complet(self):
        digest = self.gw.local_digest()
        for champ in ("swarm_id", "epoch", "state_hash", "node_count",
                      "whitelist_digest", "ambassador_addr"):
            self.assertIn(champ, digest)

    def test_essaim_inconnu_404(self):
        status, reponse = self.gw.route_federated({"swarm_id": "nulle-part"})
        self.assertEqual(status, 404)
        self.assertIn("known_swarms", reponse)

    def test_garde_anti_boucle_par_sauts(self):
        self.noeud.state.set_meta("gateway:loin", {
            "swarm_id": "loin", "ambassador_addr": "10.0.0.9:7001", "ts": 1e18})
        status, _ = self.gw.route_federated(
            {"swarm_id": "loin", "hops": MAX_FEDERATION_HOPS})
        self.assertEqual(status, 508)

    def test_ttl_et_adresses(self):
        self.assertEqual(NEIGHBOR_TTL, 90.0)
        self.assertEqual(SwarmGateway._parse_addr("10.0.0.9:7001"), ("10.0.0.9", 7001))
        self.assertIsNone(SwarmGateway._parse_addr("sans-port"))
        self.assertEqual(MAX_FEDERATION_HOPS, 2)


class TestNodeConfigAdvertise(unittest.TestCase):
    def test_port_annoncé_par_défaut_et_surchargé(self):
        cfg = NodeConfig(port=7001)
        self.assertEqual(cfg.advertise_port, 7001)
        cfg = NodeConfig(port=7001, advertise_port=17001)
        self.assertEqual(cfg.advertise_port, 17001)


if __name__ == "__main__":
    unittest.main()
