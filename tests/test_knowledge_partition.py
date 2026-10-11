# -*- coding: utf-8 -*-
"""
Pool de savoir (RAG distribué) — règles fines + résilience :
déduplication par question, exclusion du non-verifié, propagation,
survie au changement de leader ; et cicatrisation de partition
(deux leaders epoch 1 qui se réconcilient).
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_swarm_integration import (LEASE, attendre, http_json, nouveau_noeud,
                                    roles)


class TestPoolDeSavoir(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n1 = nouveau_noeud("sav-1")
        time.sleep(LEASE + 1.5)
        cls.n2 = nouveau_noeud("sav-2", peers=[cls.n1.config.port])
        assert attendre(12, lambda: sum(
            1 for r, _l, _e in roles([cls.n1, cls.n2]).values() if r == "leader") == 1)

    @classmethod
    def tearDownClass(cls):
        for n in (cls.n1, cls.n2):
            try:
                n.stop()
            except Exception:
                pass

    def url(self, n):
        return "http://127.0.0.1:%d" % n.config.port

    def demander(self, n, question, sources=None):
        corps = {"student_id": "eleve", "prompt": question}
        if sources is not None:
            corps["sources"] = sources
        return http_json("POST", self.url(n) + "/ask", corps, timeout=30)

    def savoir(self, n):
        _s, k = http_json("GET", self.url(n) + "/knowledge", timeout=8)
        return k

    def test_déduplication_par_question(self):
        for _ in range(3):
            _s, r = self.demander(self.n1, "Toujours la même question ?")
            self.assertTrue(r["knowledge"]["shared"])
        self.assertEqual(self.savoir(self.n1)["count"], 1)   # UNE entrée

    def test_questions_distinctes_cumulées_et_propagées(self):
        self.demander(self.n1, "Une autre question, différente ?")
        ok = attendre(12, lambda: self.savoir(self.n2)["count"] == 2)
        self.assertTrue(ok, self.savoir(self.n2))
        self.assertEqual(self.savoir(self.n1)["digest"],
                         self.savoir(self.n2)["digest"])     # même savoir

    def test_non_verifié_jamais_dans_le_savoir(self):
        avant = self.savoir(self.n2)["count"]
        _s, r = self.demander(self.n2, "Question sans ancrage", sources=[])
        self.assertEqual(r["osp"]["verdict"], "UNVERIFIED")
        self.assertIsNone(r.get("knowledge"))
        self.assertEqual(self.savoir(self.n2)["count"], avant)

    def test_savoir_survit_au_changement_de_leader(self):
        leader = self.n1 if roles([self.n1, self.n2]).get("sav-1", ("?",))[0] == "leader" else self.n2
        survivant = self.n2 if leader is self.n1 else self.n1
        leader.stop()
        ok = attendre(20, lambda: roles([survivant]).get(
            survivant.node_id, ("?",))[0] == "leader")
        self.assertTrue(ok, "pas de réélection chez le survivant")
        # la copie locale du savoir est intacte et le service continue
        self.assertEqual(self.savoir(survivant)["count"], 2)
        _s, r = self.demander(survivant, "Et après le basculement ?")
        self.assertTrue(r["knowledge"]["shared"])
        self.assertEqual(self.savoir(survivant)["count"], 3)


class TestCicatrisationPartition(unittest.TestCase):
    def test_deux_leaders_epoch1_se_réconcilient(self):
        """Partition : chaque côté s'auto-élit (epoch 1) ; au rétablissement
        du lien, la règle « plus petite clé gagne à epoch égale » referme la
        brèche SANS incrément d'epoch ni intervention."""
        n1 = nouveau_noeud("part-a")
        time.sleep(LEASE + 1.5)                  # n1 leader epoch 1
        # n2 démarre ISOLÉ (son unique graine pointe un port mort)
        n2 = nouveau_noeud("part-b", peers=[1])
        try:
            self.assertTrue(attendre(8, lambda: all(
                r == "leader" for r, _l, _e in roles([n1, n2]).values())),
                "attendu 2 leaders isolés : %s" % roles([n1, n2]))
            self.assertEqual(roles([n1])["part-a"][2], 1)
            self.assertEqual(roles([n2])["part-b"][2], 1)

            # RÉTABLISSEMENT DU LIEN (comme le beacon UDP le ferait) :
            # CHAQUE côté découvre l'adresse de l'autre. Le leader à la
            # clé la plus petite (part-a) bat alors part-b, qui abdique à
            # epoch égale devant la règle déterministe.
            n2.state.update_peer("part-a", {"ip": "127.0.0.1",
                                            "port": n1.config.port})
            n2.state.touch_peer("part-a", alive=True)
            n1.state.update_peer("part-b", {"ip": "127.0.0.1",
                                            "port": n2.config.port})
            n1.state.touch_peer("part-b", alive=True)
            ok = attendre(15, lambda: (
                roles([n1, n2])["part-a"][0] == "leader"
                and roles([n1, n2])["part-b"][0] == "follower"
                and roles([n1, n2])["part-b"][1] == "part-a"))
            self.assertTrue(ok, "partition non refermée : %s" % roles([n1, n2]))
            # epoch inchangée : pas de basculement, juste réconciliation
            self.assertEqual(roles([n1])["part-a"][2], 1)
        finally:
            n1.stop(); n2.stop()


if __name__ == "__main__":
    unittest.main()
