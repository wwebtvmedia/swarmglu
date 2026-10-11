# -*- coding: utf-8 -*-
"""
Tests du relais applicatif (/relay) — « étendre la zone » de l'essaim.

Le nœud qui ouvre le point d'accès (hotspot local-only Android, ou AP
Linux) route les requêtes HTTP de ses clients vers le réseau principal.
Vérifié ici : transmission GET/POST, blocage des destinations publiques
(pas de proxy ouvert), scellement dans l'audit.
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import swarm_glu
from swarm_glu import NodeConfig
from test_swarm_integration import attendre, http_json, nouveau_noeud, roles, LEASE


class ServiceFactice(BaseHTTPRequestHandler):
    """Service du « réseau principal » (stand-in swarmknowledge/harnessdroid)."""

    def do_GET(self):
        corps = json.dumps({"service": "principal", "chemin": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def do_POST(self):
        longueur = int(self.headers.get("Content-Length", 0))
        recu = json.loads(self.rfile.read(longueur) or b"{}")
        corps = json.dumps({"echo": recu}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def log_message(self, *a):
        pass


class TestRelais(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.serveur = ThreadingHTTPServer(("127.0.0.1", 0), ServiceFactice)
        cls.port_service = cls.serveur.server_address[1]
        threading.Thread(target=cls.serveur.serve_forever, daemon=True).start()
        cls.noeud = nouveau_noeud("routeur-relais")
        attendre(10, lambda: roles([cls.noeud]).get("routeur-relais", ("?",))[0] == "leader")

    @classmethod
    def tearDownClass(cls):
        cls.noeud.stop()
        cls.serveur.shutdown()

    def relayer(self, corps):
        return http_json("POST", "http://127.0.0.1:%d/relay" % self.noeud.config.port,
                         corps, timeout=15)

    def test_get_transmis_au_service_principal(self):
        status, rep = self.relayer({"host": "127.0.0.1", "port": self.port_service,
                                    "path": "/osp/endpoint.json"})
        self.assertEqual(status, 200)
        self.assertEqual(rep["status"], 200)
        self.assertEqual(rep["data"]["service"], "principal")
        self.assertEqual(rep["data"]["chemin"], "/osp/endpoint.json")
        self.assertEqual(rep["relayed_by"], "routeur-relais")

    def test_post_avec_corps_transmis(self):
        status, rep = self.relayer({"host": "127.0.0.1", "port": self.port_service,
                                    "path": "/osp/query", "method": "POST",
                                    "body": {"query": "Djerba"}})
        self.assertEqual(status, 200)
        self.assertEqual(rep["data"]["echo"], {"query": "Djerba"})

    def test_destination_publique_refusée(self):
        # jamais de proxy ouvert vers Internet : RFC1918/loopback uniquement
        status, rep = self.relayer({"host": "8.8.8.8", "port": 53, "path": "/"})
        self.assertEqual(status, 403)
        self.assertIn("privés", rep["error"])

    def test_hostname_refusé(self):
        status, _ = self.relayer({"host": "example.com", "port": 80})
        self.assertEqual(status, 400)

    def test_relais_scellé_dans_laudit(self):
        self.relayer({"host": "127.0.0.1", "port": self.port_service, "path": "/x"})
        _s, audit = http_json("GET", "http://127.0.0.1:%d/audit/verify"
                              % self.noeud.config.port)
        self.assertTrue(audit["ok"])
        evenements = [b["event"]["event_type"] for b in
                      json.loads(json.dumps(self.noeud.security.tail(5)))]
        self.assertIn("relayed", evenements)


if __name__ == "__main__":
    unittest.main()
