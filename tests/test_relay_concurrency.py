# -*- coding: utf-8 -*-
"""
Relais (/relay) — cas limites — et CONCURRENCE réelle (threads) :
chaîne d'audit sous charge, requêtes parallèles, whitelist sous courses.
"""

import json
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_swarm_integration import attendre, http_json, nouveau_noeud, roles


class TestRelaisCasLimites(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n = nouveau_noeud("relais-limites")
        assert attendre(10, lambda: roles([cls.n]).get("relais-limites", ("?",))[0] == "leader")
        cls.base = "http://127.0.0.1:%d" % cls.n.config.port

    @classmethod
    def tearDownClass(cls):
        cls.n.stop()

    def relayer(self, corps):
        return http_json("POST", self.base + "/relay", corps, timeout=20)

    def test_methode_non_supportée(self):
        status, rep = self.relayer({"host": "127.0.0.1", "port": 1,
                                    "method": "DELETE", "path": "/"})
        self.assertEqual(status, 400)
        self.assertIn("GET ou POST", rep["error"])

    def test_port_invalide_requête_propre(self):
        status, rep = self.relayer({"host": "127.0.0.1", "port": "pas-un-nombre"})
        self.assertEqual(status, 400)
        self.assertIn("port", rep["error"].lower())

    def test_destination_morte_privee_renvoie_statut_interne(self):
        # 127.0.0.1:9 (discard) — fermé : le relais rend la main proprement
        # (pas d'exception, statut interne 0), pas de crash du nœud.
        status, rep = self.relayer({"host": "127.0.0.1", "port": 9, "path": "/x"})
        self.assertEqual(status, 200)      # le relais répond proprement…
        self.assertEqual(rep["status"], 0) # …avec le statut interne d'échec
        _s, sante = http_json("GET", self.base + "/health")
        self.assertTrue(sante["ok"])      # le nœud a survécu

    def test_ipv6_loopback_privé(self):
        # ::1 est loopback => accepté par la politique privée (chemin IPv6)
        status, _rep = self.relayer({"host": "::1", "port": 9, "path": "/"})
        self.assertEqual(status, 200)     # routé (échec interne ok)


class TestConcurrence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n = nouveau_noeud("concurrence")
        assert attendre(10, lambda: roles([cls.n]).get("concurrence", ("?",))[0] == "leader")
        cls.base = "http://127.0.0.1:%d" % cls.n.config.port

    @classmethod
    def tearDownClass(cls):
        cls.n.stop()

    def test_chaîne_daudit_sous_charge_parallèle(self):
        avant = self.n.security.verify_chain()["entries"]
        erreurs = []

        def ecrire(i):
            try:
                for j in range(10):
                    self.n.security.record_event("charge", "t%d" % i, {"j": j})
            except Exception as exc:      # pragma: no cover
                erreurs.append(exc)

        fils = [threading.Thread(target=ecrire, args=(i,)) for i in range(8)]
        for f in fils:
            f.start()
        for f in fils:
            f.join()
        rapport = self.n.security.verify_chain()
        self.assertEqual(erreurs, [])
        self.assertTrue(rapport["ok"])
        self.assertEqual(rapport["entries"], avant + 80)   # RIEN perdu

    def test_requêtes_ask_parallèles_toutes_servies(self):
        resultats = []

        def demander(i):
            _s, r = http_json("POST", self.base + "/ask",
                              {"student_id": "eleve-%d" % i,
                               "prompt": "Question parallèle numéro %d" % i},
                              timeout=60)
            resultats.append(r)

        fils = [threading.Thread(target=demander, args=(i,)) for i in range(4)]
        for f in fils:
            f.start()
        for f in fils:
            f.join()
        self.assertEqual(len([r for r in resultats if r.get("answer")]), 4)
        _s, k = http_json("GET", self.base + "/knowledge")
        self.assertEqual(k["count"], 4)   # 4 questions distinctes → 4 savoirs

    def test_whitelist_sous_courses_écritures_concurrentes(self):
        import tempfile
        from security import SecurityManager
        dossier = tempfile.mkdtemp(prefix="sgcc_")
        sm = SecurityManager(os.path.join(dossier, "wl.json"),
                             os.path.join(dossier, "a.jsonl"))

        def ajouter(i):
            for j in range(15):
                sm.add_node("noeud-%d-%d" % (i, j))

        fils = [threading.Thread(target=ajouter, args=(i,)) for i in range(4)]
        for f in fils:
            f.start()
        for f in fils:
            f.join()
        # 4 fils × 15 ajouts distincts = 60 nœuds, sans doublon ni perte
        self.assertEqual(len(sm.nodes()), 60)
        self.assertEqual(len(set(sm.nodes())), 60)
        recharge = SecurityManager(os.path.join(dossier, "wl.json"),
                                   os.path.join(dossier, "a.jsonl"))
        self.assertEqual(sorted(recharge.nodes()), sorted(sm.nodes()))


if __name__ == "__main__":
    unittest.main()
