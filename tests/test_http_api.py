# -*- coding: utf-8 -*-
"""
Tests de la SURFACE HTTP (routes, CORS, tolérances) — le contrat d'API.
"""

import json
import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_swarm_integration import attendre, http_json, nouveau_noeud, roles, LEASE


def requete_brute(methode, url, corps=None):
    """Retourne (status, corps_texte, headers) — pour lire les en-têtes CORS."""
    donnees = json.dumps(corps).encode() if corps is not None else None
    req = urllib.request.Request(url, data=donnees, method=methode,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as rep:
            return rep.status, rep.read().decode(), dict(rep.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(), dict(exc.headers)


class TestSurfaceHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n = nouveau_noeud("api-solo")
        assert attendre(10, lambda: roles([cls.n]).get("api-solo", ("?",))[0] == "leader")
        cls.base = "http://127.0.0.1:%d" % cls.n.config.port

    @classmethod
    def tearDownClass(cls):
        cls.n.stop()

    def test_route_inconnue_404_avec_chemin(self):
        status, corps, _ = requete_brute("GET", self.base + "/route/inexistante")
        self.assertEqual(status, 404)
        self.assertIn("/route/inexistante", json.loads(corps)["path"])

    def test_cors_ouvert(self):
        _s, _c, entetes = requete_brute("GET", self.base + "/health")
        self.assertEqual(entetes.get("Access-Control-Allow-Origin"), "*")

    def test_options_prevolight(self):
        status, _c, entetes = requete_brute("OPTIONS", self.base + "/ask")
        self.assertEqual(status, 204)
        self.assertIn("POST", entetes.get("Access-Control-Allow-Methods", ""))

    def test_barre_oblique_finale_tolérée(self):
        status, rep = http_json("GET", self.base + "/status/")
        self.assertEqual(status, 200)
        self.assertIn("consensus", rep)

    def test_json_invalide_400(self):
        req = urllib.request.Request(self.base + "/ask", data=b"{pas du json",
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10):
                self.fail("attendu 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)

    def test_health_contract(self):
        _s, rep = http_json("GET", self.base + "/health")
        for champ in ("ok", "node_id", "role", "epoch"):
            self.assertIn(champ, rep)
        self.assertTrue(rep["ok"])

    def test_providers_contient_le_fournisseur_mock(self):
        _s, rep = http_json("GET", self.base + "/providers")
        self.assertEqual(rep["providers"][0]["node_id"], "api-solo")
        self.assertIn("mock-qwen", rep["providers"][0]["models"][0])

    def test_tasks_vide_puis_liste(self):
        _s, rep = http_json("GET", self.base + "/tasks")
        self.assertEqual(rep["summary"]["total"], 0)
        _s, cree = http_json("POST", self.base + "/agent/task",
                             {"student_id": "s", "mcp": {"tool": "t"}})
        _s, rep2 = http_json("GET", self.base + "/tasks")
        self.assertEqual(rep2["summary"]["total"], 1)
        self.assertEqual(rep2["tasks"][0]["status"], "pending_validation")

    def test_state_digest_et_registry(self):
        _s, d = http_json("GET", self.base + "/state/digest")
        self.assertEqual(len(d["digest"]), 64)     # sha256 hex
        self.assertGreaterEqual(d["epoch"], 1)
        _s, r = http_json("GET", self.base + "/registry")
        self.assertIn("state", r)
        self.assertIn("peers", r["state"])

    def test_agent_tache_inconnue_404(self):
        status, _ = http_json("GET", self.base + "/agent/task/inexistant")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
