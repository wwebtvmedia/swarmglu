# -*- coding: utf-8 -*-
"""Tests unitaires — security.py (whitelist + chaîne de hachage)."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from security import (GENESIS_HASH, SecurityManager, canonical_json,
                      deterministic_key, ip_to_sort_int, sha256_hex)


class TestUtilitaires(unittest.TestCase):
    def test_ordre_ip_numerique(self):
        # 192.168.1.9 < 192.168.1.10 (faux en comparaison lexicographique)
        self.assertLess(ip_to_sort_int("192.168.1.9"), ip_to_sort_int("192.168.1.10"))
        self.assertEqual(ip_to_sort_int("10.0.0.1"), ip_to_sort_int("10.0.0.1"))

    def test_cle_deterministe(self):
        self.assertEqual(deterministic_key("10.0.0.1", "a"), deterministic_key("10.0.0.1", "a"))
        self.assertLess(deterministic_key("10.0.0.1", "z"), deterministic_key("10.0.0.2", "a"))

    def test_json_canonical_stable(self):
        self.assertEqual(canonical_json({"b": 1, "a": 2}), canonical_json({"a": 2, "b": 1}))

    def test_sha256_hex(self):
        self.assertEqual(sha256_hex("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


class TestWhitelist(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="sgt_")
        self.sm = SecurityManager(
            whitelist_path=os.path.join(self.d, "wl.json"),
            log_path=os.path.join(self.d, "audit.jsonl"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_ajout_et_approbation(self):
        self.assertFalse(self.sm.is_node_allowed("t1"))
        self.assertTrue(self.sm.add_node("t1"))
        self.assertTrue(self.sm.is_node_allowed("t1"))
        self.assertFalse(self.sm.add_node("t1"))          # idempotent

    def test_retrait(self):
        self.sm.add_node("t1")
        self.assertTrue(self.sm.remove_node("t1"))
        self.assertFalse(self.sm.is_node_allowed("t1"))
        self.assertFalse(self.sm.remove_node("t1"))        # déjà absent

    def test_digest_depend_du_contenu_pas_de_l_ordre(self):
        a = SecurityManager(os.path.join(self.d, "a.json"), os.path.join(self.d, "a.jsonl"))
        b = SecurityManager(os.path.join(self.d, "b.json"), os.path.join(self.d, "b.jsonl"))
        a.add_node("x"); a.add_node("y")
        b.add_node("y"); b.add_node("x")
        self.assertEqual(a.whitelist_digest(), b.whitelist_digest())
        b.add_node("z")
        self.assertNotEqual(a.whitelist_digest(), b.whitelist_digest())

    def test_fusion_union(self):
        self.sm.add_node("a")
        ajoutes = self.sm.sync_from_swarm(["a", "b", "c"])
        self.assertEqual(sorted(ajoutes), ["b", "c"])
        self.assertEqual(sorted(self.sm.nodes()), ["a", "b", "c"])

    def test_sync_idempotent_et_cumulatif(self):
        self.sm.add_node("a")
        self.assertEqual(sorted(self.sm.sync_from_swarm(["a", "b"])), ["b"])
        self.assertEqual(self.sm.sync_from_swarm(["a", "b"]), [])  # rien de neuf
        self.assertEqual(sorted(self.sm.nodes()), ["a", "b"])


class TestChaineDeHachage(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="sgt_")
        self.sm = SecurityManager(
            whitelist_path=os.path.join(self.d, "wl.json"),
            log_path=os.path.join(self.d, "audit.jsonl"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_genese_puis_chainage(self):
        self.assertEqual(self.sm.get_last_hash(), GENESIS_HASH)
        b1 = self.sm.record_event("node_start", "a", {"x": 1}, processing_time=0.5)
        b2 = self.sm.record_event("request_routed", "eleve-1", {"y": 2},
                                  processing_time=1.25, request_time=1700000000.0)
        self.assertEqual(b2["previous_hash"], b1["hash"])
        self.assertEqual(self.sm.verify_chain()["entries"], 2)

    def test_horodatages_presents(self):
        b = self.sm.record_event("request_routed", "e", {"q": 1},
                                 processing_time=0.42, request_time=1750000000.123)
        self.assertEqual(b["request_time"], 1750000000.123)
        self.assertEqual(b["processing_time"], 0.42)
        self.assertIn("T", b["timestamp"])  # ISO-8601

    def test_determinisme_du_scellement(self):
        """Deux SecurityManager vierges scellent l'événement identique pareil."""
        other = SecurityManager(os.path.join(self.d, "w2.json"), os.path.join(self.d, "c2.jsonl"))
        t = 1700000123.456
        b1 = self.sm.record_event("e", "n", {"p": 1}, 0.1, t)
        b2 = other.record_event("e", "n", {"p": 1}, 0.1, t)
        self.assertEqual(b1["hash"], b2["hash"])

    def test_altération_d_un_bloc_détectée(self):
        for i in range(5):
            self.sm.record_event("e%d" % i, "n", {"i": i})
        chemin = os.path.join(self.d, "audit.jsonl")
        lignes = open(chemin, encoding="utf-8").read().splitlines()
        bloc = json.loads(lignes[2])
        bloc["event"]["payload"]["i"] = 999              # falsification
        lignes[2] = json.dumps(bloc, ensure_ascii=False)
        open(chemin, "w", encoding="utf-8").write("\n".join(lignes) + "\n")
        rapport = self.sm.verify_chain()
        self.assertFalse(rapport["ok"])
        self.assertEqual(rapport["line"], 3)

    def test_suppression_d_un_bloc_détectée(self):
        for i in range(5):
            self.sm.record_event("e%d" % i, "n", {"i": i})
        chemin = os.path.join(self.d, "audit.jsonl")
        lignes = open(chemin, encoding="utf-8").read().splitlines()
        del lignes[2]                                    # trou de séquence
        open(chemin, "w", encoding="utf-8").write("\n".join(lignes) + "\n")
        self.assertFalse(self.sm.verify_chain()["ok"])

    def test_réordonnancement_détecté(self):
        for i in range(4):
            self.sm.record_event("e%d" % i, "n", {"i": i})
        chemin = os.path.join(self.d, "audit.jsonl")
        lignes = open(chemin, encoding="utf-8").read().splitlines()
        lignes[1], lignes[2] = lignes[2], lignes[1]      # échange
        open(chemin, "w", encoding="utf-8").write("\n".join(lignes) + "\n")
        self.assertFalse(self.sm.verify_chain()["ok"])

    def test_corruption_intermédiaire_bloque_écriture(self):
        """Un bloc corrompu AU MILIEU du journal : fatal (fail-safe)."""
        for i in range(4):
            self.sm.record_event("e%d" % i, "n", {"i": i})
        chemin = os.path.join(self.d, "audit.jsonl")
        lignes = open(chemin, encoding="utf-8").read().splitlines()
        lignes[1] = "pas du json"                     # ligne 2 sur 4
        open(chemin, "w", encoding="utf-8").write("\n".join(lignes) + "\n")
        with self.assertRaises(RuntimeError):
            SecurityManager(os.path.join(self.d, "wl.json"), chemin)

    def test_queue_déchirée_réparée_au_démarrage(self):
        """Dernière ligne tronquée par un kill : réparée + événement scellé."""
        for i in range(4):
            self.sm.record_event("e%d" % i, "n", {"i": i})
        chemin = os.path.join(self.d, "audit.jsonl")
        with open(chemin, "a", encoding="utf-8") as fh:
            fh.write('{"seq": 5, "timestamp": "2026-10-11T')   # écriture déchirée
        sm2 = SecurityManager(os.path.join(self.d, "wl.json"), chemin)   # répare
        rapport = sm2.verify_chain()
        self.assertTrue(rapport["ok"])
        self.assertEqual(rapport["entries"], 5)  # 4 blocs + 1 événement de réparation
        types = [b["event"]["event_type"] for b in sm2.tail(3)]
        self.assertIn("chain_tail_repaired", types)      # réparation scellée
        # et l'écriture reprend normalement
        bloc = sm2.record_event("apres", "n", {})
        self.assertEqual(bloc["seq"], 6)

    def test_taille_et_queue(self):
        for i in range(7):
            self.sm.record_event("e", "n", {"i": i})
        self.assertEqual(len(self.sm.tail(3)), 3)
        self.assertEqual(self.sm.tail(50)[-1]["seq"], 7)


if __name__ == "__main__":
    unittest.main()
