# -*- coding: utf-8 -*-
"""Tests unitaires — consensus.py (élection déterministe, baux, conflits)."""

import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from consensus import (LEADER_ELIGIBLE_MODES, ConsensusConfig, ConsensusManager,
                       Role)
from security import SecurityManager
from state_manager import StateManager


class FauxTransport:
    """Transport enregistrant les envois ; réponses pilotées par le test."""

    def __init__(self, reponses=None):
        self.envois = []            # (ip, port, chemin, corps)
        self.reponses = reponses or {}

    def post(self, ip, port, path, body, timeout=5.0):
        self.envois.append((ip, port, path, body))
        reponse = self.reponses.get(path, {"ok": True})
        return 200, dict(reponse)

    def get(self, ip, port, path, timeout=5.0):
        return 200, {"state": {}}


def moteur(node_id="a", ip="10.0.0.1", reponses=None, mode="full",
           hb=3.0, lease=6.0):
    d = tempfile.mkdtemp(prefix="sgc_")
    security = SecurityManager(os.path.join(d, "wl.json"), os.path.join(d, "a.jsonl"))
    state = StateManager(node_id, security=security)
    transport = FauxTransport(reponses)
    moteur = ConsensusManager(
        {"node_id": node_id, "ip": ip, "port": 7001, "mode": mode},
        state, transport, security, ConsensusConfig(hb, lease))
    return moteur, state, transport, security, d


class TestConfiguration(unittest.TestCase):
    def test_bail_minimum_deux_battements(self):
        # un bail < 2 battements élit par spasmes : corrigé automatiquement
        cfg = ConsensusConfig(heartbeat_interval=3.0, lease_timeout=4.0)
        self.assertGreaterEqual(cfg.lease_timeout, 2 * cfg.heartbeat_interval)

    def test_valeurs_du_cahier_des_charges(self):
        cfg = ConsensusConfig()
        self.assertEqual(cfg.heartbeat_interval, 3.0)
        self.assertEqual(cfg.lease_timeout, 6.0)


class TestElectionDeterministe(unittest.TestCase):
    def test_candidats_tries_par_ip_puis_node_id(self):
        m, state, *_ = moteur("a", "10.0.0.5")
        for nid, ip in (("grand", "10.0.0.9"), ("petit", "10.0.0.2"),
                        ("moyen", "10.0.0.5")):
            state.update_peer(nid, {"ip": ip, "port": 7001, "mode": "full"})
            state.touch_peer(nid, alive=True)
        cles = [k for k, _nid in m._eligible_candidates()]
        self.assertEqual(cles, sorted(cles))  # tri garanti par la clé
        self.assertEqual([nid for _k, nid in m._eligible_candidates()][0], "petit")
        self.assertIn("a", [nid for _k, nid in m._eligible_candidates()])

    def test_modes_non_éligibles_exclus(self):
        m, state, *_ = moteur("a", "10.0.0.5")
        state.update_peer("tablette", {"ip": "10.0.0.1", "port": 1, "mode": "follower"})
        state.touch_peer("tablette", alive=True)
        noms = [nid for _k, nid in m._eligible_candidates()]
        self.assertNotIn("tablette", noms)
        self.assertTrue(all(m in LEADER_ELIGIBLE_MODES for m in ("full", "ambassador")))

    def test_rang_delai_proportionnel(self):
        m, state, *_ = moteur("c", "10.0.0.5")
        for nid, ip in (("a", "10.0.0.1"), ("b", "10.0.0.2"), ("d", "10.0.0.4")):
            state.update_peer(nid, {"ip": ip, "port": 7001, "mode": "full"})
            state.touch_peer(nid, alive=True)
        m.start_election("test")
        # 4 candidats, "c" est rang 2 -> délai = 2 * 0.3
        self.assertAlmostEqual(m._proclaim_at - time.time(), 0.6, delta=0.3)

    def test_proclamation_incrémente_epoch_et_journalise(self):
        m, *_b, security, d = moteur()
        m._proclaim("test")
        self.assertEqual(m.role, Role.LEADER)
        self.assertEqual(m.epoch, 1)
        self.assertEqual(m.leader_id, "a")
        types = [b["event"]["event_type"] for b in security.tail(5)]
        self.assertIn("leader_elected", types)


class TestConflitsEtBaux(unittest.TestCase):
    def test_abdication_devant_cle_plus_petite_a_epoch_egale(self):
        m, *_t, security, _d = moteur("b", "10.0.0.1")
        security.add_node("a")
        m.role = Role.LEADER; m.epoch = 2; m.leader_id = "b"
        status, _ = m.handle_leader_announce({
            "leader_id": "a", "ip": "10.0.0.1", "port": 7001,
            "epoch": 2, "key": "%010d|%s" % (167772161, "a")}, "")
        self.assertEqual(status, 200)
        self.assertEqual(m.role, Role.FOLLOWER)
        self.assertEqual(m.leader_id, "a")

    def test_ignore_annonce_stale(self):
        m, *_ = moteur("b", "10.0.0.1")
        m.role = Role.LEADER; m.epoch = 5; m.leader_id = "b"
        status, _ = m.handle_leader_announce({
            "leader_id": "a", "ip": "10.0.0.1", "port": 7001, "epoch": 3,
            "key": "0|a"}, "")
        self.assertEqual(m.role, Role.LEADER)  # ancienne epoch ignorée

    def test_annonce_epoch_superieure_adoptée(self):
        m, state, _t, security, _d = moteur("b", "10.0.0.1")
        security.add_node("z")
        state.update_peer("z", {"ip": "10.0.0.9", "port": 7001, "mode": "full"})
        status, _ = m.handle_leader_announce({
            "leader_id": "z", "ip": "10.0.0.9", "port": 7001, "epoch": 4,
            "key": "%010d|%s" % (167772169, "z")}, "")
        self.assertEqual(m.role, Role.FOLLOWER)
        self.assertEqual((m.leader_id, m.epoch), ("z", 4))

    def test_battement_du_leader_renoue_le_bail(self):
        m, state, _t, security, _d = moteur("b", "10.0.0.1")
        security.add_node("z")
        m._accept_leader("z", 1, "", ("10.0.0.9", 7001), "test")
        avant = m._lease_deadline
        time.sleep(0.05)
        avant = min(avant, time.time() + 0.05)
        status, _ = m.handle_leader_beat({
            "leader_id": "z", "ip": "10.0.0.9", "port": 7001, "epoch": 1,
            "key": "", "peers": []}, "")
        self.assertGreater(m._lease_deadline, avant)

    def test_join_inconnu_approuvé_par_le_leader(self):
        m, state, transport, security, _d = moteur("chef", "10.0.0.1")
        m._proclaim("test")
        status, reponse = m.handle_join(
            {"node_id": "nouveau", "ip": "10.0.0.7", "port": 7099,
             "mode": "full"}, "10.0.0.7")
        self.assertEqual(status, 200)
        self.assertTrue(security.is_node_allowed("nouveau"))
        self.assertEqual(reponse["leader_id"], "chef")

    def test_join_refusé_si_strict(self):
        m, *_ = moteur("chef", "10.0.0.1")
        m.allow_join = False
        status, _ = m.handle_join(
            {"node_id": "etranger", "ip": "10.0.0.7", "port": 7099}, "10.0.0.7")
        self.assertEqual(status, 403)

    def test_heartbeat_enregistre_le_pair(self):
        m, state, transport, security, _d = moteur("chef", "10.0.0.1")
        security.add_node("b")
        status, reponse = m.handle_heartbeat(
            {"node_id": "b", "ip": "10.0.0.2", "port": 7002,
             "capabilities": ["inference"], "models": ["qwen"]}, "10.0.0.2")
        self.assertEqual(status, 200)
        pair = state.peers["b"]
        self.assertEqual(pair["capabilities"], ["inference"])
        self.assertTrue(pair["alive"])

    def test_carnet_adresses_appris_passivement(self):
        m, state, *_ = moteur("b", "10.0.0.1")
        state.update_peer("b", {"ip": "10.0.0.1", "port": 7001})  # entrée propre
        m._merge_address_book([
            {"node_id": "x", "ip": "10.0.0.3", "port": 7003},
            {"node_id": "sans_ip", "ip": "", "port": 0},
            {"node_id": "b", "ip": "9.9.9.9", "port": 1},  # soi-même ignoré
        ])
        self.assertIn("x", state.peers)
        self.assertNotIn("sans_ip", state.peers)
        self.assertNotEqual(state.peers["b"]["ip"], "9.9.9.9")


if __name__ == "__main__":
    unittest.main()
