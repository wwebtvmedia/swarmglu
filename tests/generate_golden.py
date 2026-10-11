#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Génère les vecteurs « golden » de conformité Kotlin (depuis le Python).

Sortie : kotlin/src/test/resources/golden.json
  - chaîne      : fichier JSONL réel + hash attendu du BLOC SUIVANT
                  (temps fixés) — le Kotlin doit prolonger la chaîne avec
                  le MÊME hash, bit pour bit ;
  - état        : deux documents, fusion A→B et B→A — même digest, égal au
                  digest Python ;
  - divers      : digest whitelist, ordre IP, échantillons str(float).
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from security import SecurityManager, canonical_json, ip_to_sort_int, sha256_hex
from state_manager import StateManager

SORTIE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "kotlin", "src", "test", "resources", "golden.json")

# ---------------------------------------------------------------- chaîne --
dossier = tempfile.mkdtemp(prefix="golden_")
sm = SecurityManager(os.path.join(dossier, "wl.json"),
                     os.path.join(dossier, "audit.jsonl"))
sm.add_node("kot-a")
sm.add_node("py-b")
temps = [1750000000.0, 1750000001.5, 1750000002.123, 1750000003.001, 1750000004.999]
sm.record_event("node_start", "kot-a", {"x": 1, "liste": [3, 1, 2]}, 0.1, temps[0])
sm.record_event("peer_registered", "py-b", {"ip": "127.0.0.1", "port": 7002}, 0.25, temps[1])
sm.record_event("leader_elected", "kot-a", {"leader": "kot-a", "epoch": 1}, 0.0, temps[2])
sm.record_event("request_routed", "eleve-1",
                {"prompt_sha256": "ab" * 32, "osp": {"groundedness": 0.6}}, 1.287, temps[3])
sm.record_event("knowledge_shared", "eleve-1",
                {"key": "knowledge:abc123", "sources": ["osp://x", "osp://y"]}, 0.002, temps[4])

lignes = open(os.path.join(dossier, "audit.jsonl"), encoding="utf-8").read()
rapport = sm.verify_chain()
bloc_suivant_attendu = sm.record_event(
    "suivant", "kot-a", {"test": True}, 0.5, 1750000010.25)

# ------------------------------------------------------------------ état --
sa = StateManager("kot-a")
sb = StateManager("py-b")
sa.update_peer("n1", {"ip": "10.0.0.1", "port": 1, "mode": "full",
                      "capabilities": ["inference"], "models": ["m1"]})
sa.update_peer("commun", {"ip": "10.0.0.9", "port": 9, "mode": "follower"})
sa.set_whitelist_nodes(["kot-a", "py-b"])
sa.set_meta("gateway:salle-B", {"ts": 1.0})
import time as _t
sa.peers["n1"]["last_seen"] = 100.5
sa.peers["n1"]["alive"] = True
sb.update_peer("n2", {"ip": "10.0.0.2", "port": 2, "mode": "full",
                      "capabilities": [], "models": ["m2", "m1"]})
sb.update_peer("commun", {"ip": "10.0.0.9", "port": 9, "mode": "follower",
                          "capabilities": ["harnessdroid"]})
sb.set_leader("py-b", "099|py-b", 3)

docA, docB = sa.to_dict(), sb.to_dict()
sa.merge(docB); sb.merge(sa.to_dict()); sa.merge(sb.to_dict())
digest_ab = sa.digest()
sc = StateManager("py-b"); sd = StateManager("kot-a")
sc.merge(json.loads(json.dumps(docB))); sd.merge(json.loads(json.dumps(docA)))
sc.merge(sd.to_dict()); sd.merge(sc.to_dict())
digest_ba = sd.digest()
assert digest_ab == digest_ba, "fusion non commutative côté Python ?!"

vecteurs = {
    "chaine": {
        "jsonl": lignes,
        "entries": rapport["entries"],
        "last_hash": rapport["last_hash"],
        "bloc_suivant_attendu": bloc_suivant_attendu["hash"],
        "temps_suivant": 1750000010.25,
    },
    "etat": {
        "docA": docA, "docB": docB,
        "digest_attendu": digest_ab,
    },
    "whitelist": {
        "noeuds": sorted(["kot-a", "py-b", "zz"]),
        "digest": sha256_hex(canonical_json(sorted(["kot-a", "py-b", "zz"]))),
    },
    "ip_ordre": [
        ["10.0.0.2", "10.0.0.10", 10 < 2],
        ["192.168.1.9", "192.168.1.10", True],
    ],
    "floats": [
        [1750000000.0, "1750000000.0"],
        [1750000001.5, "1750000001.5"],
        [0.001, "0.001"],
        [1.0, "1.0"],
        [100.123, "100.123"],
    ],
}
os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
with open(SORTIE, "w", encoding="utf-8") as fh:
    json.dump(vecteurs, fh, ensure_ascii=False, indent=1)
print("golden.json écrit :", SORTIE)
print("  chaîne :", rapport["entries"], "blocs, dernier hash", rapport["last_hash"][:16], "…")
print("  digest état :", digest_ab[:16], "…")
