# -*- coding: utf-8 -*-
"""Tests unitaires — state_manager.py (fusion LWW commutative, epoch, digest)."""

import copy
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from security import SecurityManager
from state_manager import StateManager


def etat(node_id="a"):
    d = tempfile.mkdtemp(prefix="sgs_")
    security = SecurityManager(os.path.join(d, "wl.json"), os.path.join(d, "a.jsonl"))
    return StateManager(node_id, security=security), d


class TestRegistreLocal(unittest.TestCase):
    def setUp(self):
        self.state, self.d = etat("a")

    def test_update_pair_logique_bump_version(self):
        v0 = self.state._doc["version"]
        self.assertTrue(self.state.update_peer("b", {"ip": "10.0.0.2", "port": 7002}))
        self.assertGreater(self.state._doc["version"], v0)

    def test_touch_volatile_ne_change_pas_le_digest(self):
        self.state.update_peer("b", {"ip": "10.0.0.2", "port": 7002})
        d1 = self.state.digest()
        time.sleep(0.01)
        self.state.touch_peer("b", alive=True)
        self.assertEqual(self.state.digest(), d1)  # la vivacité n'est pas logique

    def test_alive_peers_fenetre(self):
        self.state.update_peer("b", {"ip": "10.0.0.2", "port": 7002})
        self.state.touch_peer("b", alive=True)
        self.state.peers["b"]["last_seen"] = time.time() - 999  # périmé
        self.assertEqual([p for p in dict(self.state.alive_peers(5)) if p == "b"], [])
        self.state.touch_peer("b", alive=True)
        self.assertIn("b", dict(self.state.alive_peers(5)))

    def test_set_leader_epoch_monotone(self):
        self.state.set_leader("x", "cle1", 3)
        self.state.set_leader("y", "cle2", 2)          # epoch inférieure ignorée
        self.assertEqual((self.state._doc["leader_id"], self.state.epoch), ("x", 3))

    def test_meta_add_supprime_lis(self):
        self.state.set_meta("gateway:salle-2", {"ts": 1})
        self.assertEqual(self.state.get_meta("gateway:salle-2"), {"ts": 1})
        self.state.del_meta("gateway:salle-2")
        self.assertIsNone(self.state.get_meta("gateway:salle-2"))


class TestFusionLWW(unittest.TestCase):
    def _etats(self):
        sa, _ = etat("a")
        sb, _ = etat("b")
        return sa, sb

    def test_fusion_commutive_convergence_digest(self):
        """A merge B puis B merge A => mêmes états (digests égaux)."""
        sa, sb = self._etats()
        sa.update_peer("n1", {"ip": "10.0.0.1", "port": 1, "mode": "full"})
        time.sleep(0.01)
        sb.update_peer("n2", {"ip": "10.0.0.2", "port": 2, "mode": "full"})
        sa.merge(sb.to_dict())
        sb.merge(sa.to_dict())
        # une seconde vague pour répandre les résultats
        sa.merge(sb.to_dict())
        sb.merge(sa.to_dict())
        self.assertEqual(sa.digest(), sb.digest())
        self.assertEqual(set(sa.peers), set(sb.peers))

    def test_lww_la_plus_récente_gagne(self):
        sa, sb = self._etats()
        sa.update_peer("n1", {"ip": "10.0.0.1", "port": 1, "capabilities": ["vieux"]})
        time.sleep(0.01)
        sb.update_peer("n1", {"ip": "10.0.0.1", "port": 1, "capabilities": ["neuf"]})
        sa.merge(sb.to_dict())
        self.assertEqual(sa.peers["n1"]["capabilities"], ["neuf"])

    def test_epoch_maximale_gagne(self):
        sa, sb = self._etats()
        sa.set_leader("chef-a", "cle-a", 7)
        sb.set_leader("chef-b", "cle-b", 3)
        rapport = sa.merge(sb.to_dict())
        self.assertFalse(rapport["changed"])          # rien à apprendre
        self.assertEqual(sa.epoch, 7)

    def test_epoch_egale_plus_petite_cle_gagne(self):
        sa, sb = self._etats()
        sa.set_leader("chef-b", "099|chef-b", 4)
        sb.set_leader("chef-a", "050|chef-a", 4)
        sa.merge(sb.to_dict())
        self.assertEqual(sa._doc["leader_id"], "chef-a")

    def test_whitelist_union_propagée(self):
        sa, _ = etat("a")
        sb, _ = etat("b")
        sb.set_whitelist_nodes(["x", "y"])
        sa.merge(sb.to_dict())
        self.assertEqual(sa._doc["whitelist_nodes"], ["x", "y"])

    def test_volatile_plus_récente_adoptée(self):
        """Un follower apprend la vivauté des pairs vue par le leader."""
        sa, sb = self._etats()
        for n, ip in (("n1", "10.0.0.1"), ("n2", "10.0.0.2")):
            sb.update_peer(n, {"ip": ip, "port": 1})
        # vue leader : n1 vivant, très frais
        sb.peers["n1"]["last_seen"] = time.time()
        sb.peers["n1"]["alive"] = True
        sa.merge(sb.to_dict())
        self.assertTrue(sa.peers["n1"]["alive"])

    def test_etat_serialisable_json(self):
        import json
        sa, _ = etat("a")
        sa.update_peer("n1", {"ip": "10.0.0.1", "port": 1})
        doc = sa.to_dict()
        self.assertIsInstance(json.loads(json.dumps(doc, default=str)), dict)
        self.assertNotIn("last_seen", sa.logical_projection()["peers"]["n1"])


if __name__ == "__main__":
    unittest.main()
