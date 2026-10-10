# -*- coding: utf-8 -*-
"""
Intégration complète EN PROCESSUS — plusieurs SwarmGlu réels (serveurs HTTP,
threads de consensus, chaînes d'audit) sur 127.0.0.1, timings accélérés.

Couvre : élection, registre replicué, routage /ask inter-nœuds, basculement
après mort du leader, barrière HITL inter-nœuds, fédération de deux essaims
via la passerelle ambassadrice.

Exécution :  python3 -m unittest tests.test_swarm_integration -v
(timings serrés pour rester < 60 s au total)
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
from swarm_glu import NodeConfig

PORTS = iter(range(17501, 17599))
HB, LEASE = 0.4, 0.8          # battements accélérés (tests rapides)


def http_json(methode, url, corps=None, timeout=15.0):
    donnees = json.dumps(corps or {}).encode() if methode == "POST" else None
    req = urllib.request.Request(url, data=donnees, method=methode,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as rep:
            return rep.status, json.loads(rep.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def nouveau_noeud(node_id, swarm_id="salle-test", peers=None, neighbor=None,
                  mode="full", port=None, allow_join=True):
    d = tempfile.mkdtemp(prefix="sgi_")
    cfg = NodeConfig(
        node_id=node_id, port=port or next(PORTS), mode=mode,
        swarm_id=swarm_id,
        whitelist_file=os.path.join(d, "wl.json"),
        data_dir=d,
        peers=["127.0.0.1:%d" % p for p in (peers or [])],
        neighbors=[neighbor] if neighbor else [],
        beacon_port=0, mock_inference=True, allow_join=allow_join,
        heartbeat_interval=HB, lease_timeout=LEASE,
        sync_interval=0.5, log_level="ERROR")
    return swarm_glu.serve(cfg, block=False)


def attendre(delai_s, condition):
    limite = time.time() + delai_s
    while time.time() < limite:
        if condition():
            return True
        time.sleep(0.2)
    return False


def roles(noeuds):
    etats = {}
    for n in noeuds:
        _s, p = http_json("GET", "http://127.0.0.1:%d/status" % n.config.port, timeout=5)
        if p:
            etats[n.node_id] = (p["consensus"]["role"], p["consensus"]["leader_id"],
                                p["consensus"]["epoch"])
    return etats


class EssaimDeuxNoeuds(unittest.TestCase):
    """Cycle de vie standard : élection, routage, basculement."""

    @classmethod
    def setUpClass(cls):
        cls.n1 = nouveau_noeud("int-a")
        time.sleep(LEASE + 1.5)                 # bootstrap : int-a s'auto-élit
        cls.n2 = nouveau_noeud("int-b", peers=[cls.n1.config.port])
        cls.noeuds = [cls.n1, cls.n2]
        ok = attendre(15, lambda: (
            sum(1 for r, _l, _e in roles(cls.noeuds).values() if r == "leader") == 1
            and all(l for r, l, _e in roles(cls.noeuds).values())))
        assert ok, "pas de leader unique : %s" % roles(cls.noeuds)

    @classmethod
    def tearDownClass(cls):
        for n in cls.noeuds:
            n.stop()
        time.sleep(0.3)

    def test_01_leader_unique_deterministe(self):
        etats = roles(self.noeuds)
        leaders = [nid for nid, (r, _l, _e) in etats.items() if r == "leader"]
        self.assertEqual(len(leaders), 1)
        self.assertEqual(leaders[0], "int-a")   # clé la plus petite (même IP, node_id)

    def test_02_registre_replicué(self):
        for n in self.noeuds:
            _s, p = http_json("GET", "http://127.0.0.1:%d/status" % n.config.port)
            ids = {q["node_id"] for q in p["peers"]}
            self.assertSupersetOrEqual(ids, {"int-a", "int-b"})

    def test_03_ask_depuis_le_follower_routé(self):
        status, reponse = http_json(
            "POST", "http://127.0.0.1:%d/ask" % self.n2.config.port,
            {"student_id": "eleve-int", "prompt": "Capitale de la Tunisie ?"},
            timeout=30)
        self.assertEqual(status, 200)
        self.assertTrue(reponse["answer"])
        self.assertGreaterEqual(reponse["osp"]["groundedness"], 0.30)
        self.assertEqual(reponse["osp"]["verdict"], "VERIFIED")
        self.assertIn(reponse["provider"], {"int-a", "int-b"})

    def test_04_basculement_apres_mort_du_leader(self):
        ancien_epoch = max(e for _r, _l, e in roles(self.noeuds).values())
        self.n1.stop()                           # le leader meurt
        try:
            ok = attendre(20, lambda: (
                [nid for nid, (r, _l, _e) in roles([self.n2]).items() if r == "leader"]
                == ["int-b"]))
            self.assertTrue(ok, "int-b n'a pas repris : %s" % roles([self.n2]))
            etat = roles([self.n2])["int-b"]
            self.assertEqual(etat[0], "leader")
            self.assertGreater(etat[2], ancien_epoch)   # epoch incrémentée
            # l'essaim survit : requête servie par le nouveau leader
            status, reponse = http_json(
                "POST", "http://127.0.0.1:%d/ask" % self.n2.config.port,
                {"student_id": "eleve-int", "prompt": "Suite du cours ?"}, timeout=30)
            self.assertEqual(status, 200, reponse)
            self.assertEqual(reponse["provider"], "int-b")
        finally:
            self.noeuds.remove(self.n1)

    def test_05_chaînes_d_audit_intactes(self):
        for n in self.noeuds:
            _s, p = http_json("GET", "http://127.0.0.1:%d/audit/verify" % n.config.port)
            self.assertTrue(p["ok"], "chaîne corrompue sur %s" % n.node_id)
            self.assertGreaterEqual(p["entries"], 3)

    def assertSupersetOrEqual(self, ensemble, attendu):
        self.assertTrue(attendu.issubset(ensemble),
                        "registre incomplet : %s ⊉ %s" % (ensemble, attendu))


class HitlInterNoeuds(unittest.TestCase):
    def test_cycle_agent_complet_en_http(self):
        n1 = nouveau_noeud("hitl-a")
        time.sleep(LEASE + 1.5)
        n2 = nouveau_noeud("hitl-b", peers=[n1.config.port])
        try:
            self.assertTrue(attendre(12, lambda: (
                sum(1 for r, _l, _e in roles([n1, n2]).values() if r == "leader") == 1)))
            # création depuis le follower → la tâche vit chez le leader
            status, rep = http_json(
                "POST", "http://127.0.0.1:%d/agent/task" % n2.config.port,
                {"student_id": "eleve-2",
                 "mcp": {"server": "harnessdroid", "tool": "tool_x", "args": {}}})
            self.assertEqual(status, 200, rep)
            tid = rep["task"]["task_id"]
            self.assertEqual(rep["task"]["status"], "pending_validation")
            # dispatch direct sans validation : refusé (barrière humaine)
            status, rep2 = http_json(
                "POST", "http://127.0.0.1:%d/agent/dispatch" % n2.config.port,
                {"task": rep["task"]})
            self.assertEqual(status, 409)
            # validation par l'enseignant (sur le follower) → exécutée
            status, rep3 = http_json(
                "POST", "http://127.0.0.1:%d/agent/task/%s/validate" % (n2.config.port, tid),
                {"approved": True, "by": "prof"})
            self.assertEqual(status, 200)
            self.assertEqual(rep3.get("status"), "done")
            # cycle unique
            status, _ = http_json(
                "POST", "http://127.0.0.1:%d/agent/task/%s/validate" % (n2.config.port, tid),
                {"approved": True})
            self.assertEqual(status, 409)
        finally:
            n1.stop(); n2.stop()


class FédérationDeuxEssaims(unittest.TestCase):
    def test_requête_fédérée_traverse_la_passerelle(self):
        a1 = nouveau_noeud("fed-a1", swarm_id="salle-A")
        b1 = nouveau_noeud("fed-b1", swarm_id="salle-B")
        time.sleep(LEASE + 1.5)              # chaque salle auto-élit
        a2 = nouveau_noeud("fed-a2", swarm_id="salle-A", peers=[a1.config.port],
                           neighbor="127.0.0.1:%d" % b1.config.port,
                           mode="ambassador")   # a2 pousse son digest à salle-B
        b2 = nouveau_noeud("fed-b2", swarm_id="salle-B", peers=[b1.config.port])
        # b2 ambassadeur pousse son digest vers b1 dès 3 s (boucle passerelle)
        try:
            # chaque salle élit son leader
            self.assertTrue(attendre(12, lambda: (
                sum(1 for r, _l, _e in roles([a1, a2]).values() if r == "leader") == 1
                and sum(1 for r, _l, _e in roles([b1, b2]).values() if r == "leader") == 1)))
            # la connaissance de salle-B se propage dans salle-A (meta + digests)
            ok = attendre(30, lambda: a1.state.get_meta("gateway:salle-B") is not None)
            self.assertTrue(ok, "digest salle-B jamais propagé dans salle-A")
            # requête fédérée depuis un nœud de salle-A vers salle-B
            status, rep = http_json(
                "POST", "http://127.0.0.1:%d/ask" % a1.config.port,
                {"student_id": "eleve-A", "prompt": "Question pour la salle B",
                 "swarm_id": "salle-B"}, timeout=60)
            self.assertEqual(status, 200, rep)
            self.assertEqual(rep.get("served_by_swarm"), "salle-B")
            self.assertTrue(rep["answer"])
            # l'échange est scellé dans les chaînes des deux côtés
            for n in (a1, b1):
                _s, verif = http_json("GET", "http://127.0.0.1:%d/audit/verify" % n.config.port)
                self.assertTrue(verif["ok"])
        finally:
            for n in (a1, a2, b1, b2):
                n.stop()


class ModeStrictEtRejoindre(unittest.TestCase):
    def test_nœud_strict_refuse_les_inconnus(self):
        n1 = nouveau_noeud("strict-1", allow_join=False)
        time.sleep(LEASE + 1.5)
        n2 = nouveau_noeud("strict-2", peers=[n1.config.port], allow_join=False)
        # les deux s'excluent mutuellement (whitelists disjointes, strict)
        # → chacun reste leader de son propre epoch 1
        try:
            self.assertTrue(attendre(6, lambda: (
                all(r == "leader" for r, _l, _e in roles([n1, n2]).values()))),
                "attendu : deux essaims isolés, obtenu %s" % roles([n1, n2]))
            status, rep = http_json(
                "POST", "http://127.0.0.1:%d/join" % n1.config.port,
                {"node_id": "pirate2", "ip": "127.0.0.1", "port": 1})
            self.assertEqual(status, 403)
        finally:
            n1.stop(); n2.stop()


if __name__ == "__main__":
    unittest.main()
