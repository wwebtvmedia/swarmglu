#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmGlu — ESSAIM MIXTE PYTHON ↔ KOTLIN (interopérabilité câble réelle).
===========================================================
1 nœud Kotlin (JVM) + 2 nœuds Python forment UN essaim : élection
déterministe trans-langage, requêtes /ask, savoir commun, mort du leader
(failover croisé), audit — chacun vérifiant l'AUTRE langue.

Topologie (127.0.0.1, heartbeats accélérés) :
    py-1 (Python, 7301) ◄──► kot-a (Kotlin, 7302) ◄──► py-2 (Python, 7303)
    clés identiques des deux côtés (ipVersEntier == ip_to_sort_int,
    golden) → leader = plus petit node_id : « kot-a ».

Usage :  python3 tests/test_kotlin_interop.py   (prérequis : cd kotlin &&
         ./gradlew installDist)
"""
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_multi_emulator_swarm as mu

RACINE = mu.RACINE
BIN_KOTLIN = os.path.join(RACINE, "kotlin", "build", "install",
                          "swarmglu-kotlin", "bin", "swarmglu-kotlin")
HB, LEASE = 0.6, 1.2
ECHECS = mu.ECHECS


def verifier(nom, cond, detail=""):
    return mu.verifier(nom, cond, detail)


def http_json(methode, url, corps=None, timeout=15.0):
    return mu.http_json(methode, url, corps, timeout)


def statut(url):
    _s, p = http_json("GET", url + "/status", timeout=6)
    return p if p and "consensus" in p else None


def savoir(url):
    _s, k = http_json("GET", url + "/knowledge", timeout=8)
    return k if k and "count" in k else {"count": -1, "digest": "?"}


def attendre(delai, f, pas=0.5):
    limite = time.time() + delai
    while time.time() < limite:
        if f():
            return True
        time.sleep(pas)
    return False


def main():
    if not os.access(BIN_KOTLIN, os.X_OK):
        raise SystemExit("nœud Kotlin absent : cd kotlin && ./gradlew installDist")

    urls = {"py-1": "http://127.0.0.1:7301",
            "kot-a": "http://127.0.0.1:7302",
            "py-2": "http://127.0.0.1:7303"}
    dossier = tempfile.mkdtemp(prefix="sgmix_")
    procs = []
    try:
        # Le nœud KOTLIN démarre le premier (seed pour py-2 ; py-1 solitaire)
        procs.append(subprocess.Popen(
            [BIN_KOTLIN, "--node-id", "kot-a", "--port", "7302",
             "--advertise-ip", "127.0.0.1", "--mode", "full",
             "--data-dir", os.path.join(dossier, "kot"),
             "--whitelist-file", os.path.join(dossier, "wl-kot.json"),
             "--heartbeat", str(HB), "--lease", str(LEASE)],
            stdout=open("/tmp/sg_mix_kot.log", "w"), stderr=subprocess.STDOUT))
        # Séquençage DÉTERMINISTE (tue la race « leader ermite » : un nœud
        # qui s'auto-élit avant la réussite de son join ne rejoint jamais).
        ok_kot = attendre(12, lambda: (statut(urls["kot-a"]) or {})
                          .get("consensus", {}).get("role") == "leader", pas=0.5)
        verifier("nœud Kotlin auto-élu leader (bootstrap)", ok_kot)
        procs.append(subprocess.Popen(
            [sys.executable, os.path.join(RACINE, "cli.py"), "serve",
             "--node-id", "py-1", "--port", "7301",
             "--advertise-ip", "127.0.0.1", "--peers", "127.0.0.1:7302",
             "--whitelist-file", os.path.join(dossier, "wl1.json"),
             "--data-dir", os.path.join(dossier, "py1"),
             "--mode", "full", "--mock-inference", "--beacon-port", "0",
             "--heartbeat-interval", str(HB), "--lease-timeout", str(LEASE)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        procs.append(subprocess.Popen(
            [sys.executable, os.path.join(RACINE, "cli.py"), "serve",
             "--node-id", "py-2", "--port", "7303",
             "--advertise-ip", "127.0.0.1", "--peers", "127.0.0.1:7302",
             "--whitelist-file", os.path.join(dossier, "wl2.json"),
             "--data-dir", os.path.join(dossier, "py2"),
             "--mode", "full", "--mock-inference", "--beacon-port", "0",
             "--heartbeat-interval", str(HB), "--lease-timeout", str(LEASE)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        verifier("py-1 rejoint le leader Kotlin",
                 attendre(10, lambda: (statut(urls["py-1"]) or {})
                          .get("consensus", {}).get("leader_id") == "kot-a", pas=0.5))
        verifier("py-2 rejoint le leader Kotlin",
                 attendre(10, lambda: (statut(urls["py-2"]) or {})
                          .get("consensus", {}).get("leader_id") == "kot-a", pas=0.5))

        print("\n[1] convergence de l'essaim mixte (1 Kotlin + 2 Python)…")
        def converges():
            etats = {}
            for n, u in urls.items():
                e = statut(u)
                if e:
                    etats[n] = e
            if len(etats) != 3:
                return False, etats
            leaders = {e["consensus"]["leader_id"] for e in etats.values()}
            roles = sum(1 for e in etats.values() if e["consensus"]["role"] == "leader")
            return (None not in leaders and len(leaders) == 1 and roles == 1), etats
        ok = attendre(25, lambda: converges()[0], pas=1)
        _, etats_diag = converges()
        verifier("UN leader trans-langage, accepté par les 3 nœuds", ok,
                 {n: (e or {}).get("consensus") for n, e in etats_diag.items()})
        etats = {n: statut(u) for n, u in urls.items()}
        if ok and all(etats.values()):
            leader = next(iter(etats.values()))["consensus"]["leader_id"]
            print("      leader = %s (%s)" % (
                leader, "KOTLIN" if leader == "kot-a" else "Python"))
            verifier("chaque registre voit les 3 pairs vivants",
                     attendre(15, lambda: all(
                         (statut(u) or {}).get("state", {}).get("alive_count", 0) >= 3
                         for u in urls.values())),
                     {n: (e or {}).get("state", {}).get("alive_count") for n, e in etats.items()})

        print("\n[2] communication croisée…")
        status, r = http_json("POST", urls["kot-a"] + "/ask",
                              {"student_id": "eleve-mixte",
                               "prompt": "Question posée au nœud KOTLIN ?"}, timeout=30)
        verifier("/ask sur le nœud Kotlin → VERIFIED",
                 status == 200 and (r or {}).get("osp", {}).get("verdict") == "VERIFIED", r)
        status, r = http_json("POST", urls["py-1"] + "/ask",
                              {"student_id": "eleve-mixte",
                               "prompt": "Et une question au nœud Python ?"}, timeout=30)
        verifier("/ask sur un nœud Python → VERIFIED",
                 status == 200 and (r or {}).get("osp", {}).get("verdict") == "VERIFIED", r)
        verifier("savoir partagé sur les 3 nœuds (Python ET Kotlin)",
                 attendre(20, lambda: all(savoir(u)["count"] >= 2 for u in urls.values())),
                 {n: savoir(u)["count"] for n, u in urls.items()})
        _s, a = http_json("GET", urls["kot-a"] + "/audit/verify")
        verifier("chaîne d'audit KOTLIN intègre (vérifiée par son propre moteur)",
                 bool(a and a.get("ok")), a)

        print("\n[3] mort du leader → failover croisé…")
        leader = next(iter(etats.values()))["consensus"]["leader_id"]
        proc_leader = procs[1] if leader == "py-1" else (procs[0] if leader == "kot-a" else procs[2])
        proc_leader.kill(); proc_leader.wait()
        survivants = {n: u for n, u in urls.items() if n != leader}
        t0 = time.time()
        ok = verifier("réélection chez les survivants (epoch > ancien)",
                      attendre(20, lambda: (
                          lambda etats2: len(etats2) == 2
                          and len({e["consensus"]["leader_id"] for e in etats2.values()}) == 1
                          and all(e["consensus"]["epoch"] >
                                  (etats.get(leader) or {}).get("consensus", {}).get("epoch", 0)
                                  for e in etats2.values())
                      )({n: statut(u) for n, u in survivants.items() if statut(u)}), pas=1))
        if ok:
            print("      failover trans-langage en %.1f s" % (time.time() - t0))
        cible = next(iter(survivants))
        status, r = http_json("POST", survivants[cible] + "/ask",
                              {"student_id": "eleve-mixte",
                               "prompt": "Après la mort du leader ?"}, timeout=30)
        verifier("l'essaim mixte survit (/ask VERIFIED)", status == 200
                 and (r or {}).get("osp", {}).get("verdict") == "VERIFIED", r)

        print("\n[4] audits finaux…")
        for n, u in survivants.items():
            _s, v = http_json("GET", u + "/audit/verify", timeout=10)
            verifier("chaîne INTACTE sur %s (%s)" % (
                n, "Kotlin" if n == "kot-a" else "Python"), bool(v and v.get("ok")), v)

    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        time.sleep(0.5)
        for p in procs:
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()

    print("\n" + "=" * 66)
    if ECHECS:
        print("ÉCHECS (%d) : %s" % (len(ECHECS), ECHECS))
        return 1
    print("ESSAIM MIXTE PYTHON ↔ KOTLIN VALIDÉ ✔ (élection trans-langage, "
          "savoir partagé, failover croisé, audits intègres)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
