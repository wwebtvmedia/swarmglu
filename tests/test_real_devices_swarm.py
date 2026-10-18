#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmGlu — ESSAIM RÉEL AVEC TÉLÉPHONES PHYSIQUES (noyau natif v2.0).
=====================================================================
Leader Kotlin JVM + 2 nœuds Python PC + TOUS les appareils Android
physiques branchés (détection automatique adb) sur le noyau NATIF.

    kot-a (Kotlin JVM, LAN)   = leader de bootstrap
    py-1  (Python PC, LAN)
    tel-XXXX (NATIVE Android, un par appareil USB : Note 10, tablette…)

Vérifié : installation, join des téléphones au leader Kotlin, convergence
N nœuds, /ask depuis chaque téléphone (VERIFIED), savoir commun partagé
partout (même digest), mort du leader Kotlin → réélection (les téléphones
 survivent), chaînes d'audit intègres partout.

Usage :  python3 tests/test_real_devices_swarm.py
Prérequis : cd kotlin && ./gradlew installDist ; appareils « device » dans adb.
"""
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_multi_emulator_swarm as mu

RACINE = mu.RACINE
sys.path.insert(0, RACINE)
from swarm_glu import detect_local_ip

BIN_KOTLIN = os.path.join(RACINE, "kotlin", "build", "install",
                          "swarmglu-kotlin", "bin", "swarmglu-kotlin")
IP_LAN = detect_local_ip()
PORT_KOT, PORT_PY = 7302, 7301
ECHECS = mu.ECHECS
RELAIS = []


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


def attendre(delai, f, pas=1.0):
    limite = time.time() + delai
    while time.time() < limite:
        if f():
            return True
        time.sleep(pas)
    return False


def appareils_physiques():
    r = subprocess.run([mu.ADB, "devices"], capture_output=True, text=True, timeout=15)
    return [l.split()[0] for l in r.stdout.splitlines()[1:]
            if l.strip() and not l.split()[0].startswith("emulator-")
            and l.split()[1] == "device"]


def main():
    if not os.access(BIN_KOTLIN, os.X_OK):
        raise SystemExit("nœud Kotlin absent : cd kotlin && ./gradlew installDist")
    serie_physiques = appareils_physiques()
    if not serie_physiques:
        raise SystemExit("aucun appareil Android physique branché (adb devices)")

    print("appareils physiques : %s" % serie_physiques)
    urls = {"kot-a": "http://127.0.0.1:%d" % PORT_KOT,
            "py-1": "http://127.0.0.1:%d" % PORT_PY}
    procs, pilotes = [], []
    dossier = tempfile.mkdtemp(prefix="sgreal_")

    try:
        # 1) installations + forwards + relais socat par téléphone
        print("\n[1] installation du noyau NATIF sur %d appareil(s)…" % len(serie_physiques))
        for i, serie in enumerate(serie_physiques):
            port_hote = 17011 + i
            node_id = "tel-" + serie[-4:].lower()
            for apk in (mu.APK_APP, mu.APK_TEST):
                r = subprocess.run([mu.ADB, "-s", serie, "install", "-r", apk],
                                   capture_output=True, text=True, timeout=300)
                verifier("APK installé sur %s (%s)" % (node_id, serie),
                         "Success" in r.stdout, r.stdout + r.stderr)
            subprocess.run([mu.ADB, "-s", serie, "forward",
                            "tcp:%d" % port_hote, "tcp:%d" % mu.PORT_EMU],
                           capture_output=True, timeout=30)
            RELAIS.append(subprocess.Popen(
                ["socat", "TCP-LISTEN:%d,bind=%s,fork,reuseaddr" % (port_hote, IP_LAN),
                 "TCP:127.0.0.1:%d" % port_hote],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            urls[node_id] = "http://127.0.0.1:%d" % port_hote
            print("      %s (%s) → %s (relais LAN %s:%d)" % (
                node_id, serie, urls[node_id], IP_LAN, port_hote))

        # 2) leader Kotlin + nœud Python
        print("\n[2] démarrage du leader Kotlin et du nœud Python…")
        procs.append(subprocess.Popen(
            [BIN_KOTLIN, "--node-id", "kot-a", "--port", str(PORT_KOT),
             "--advertise-ip", IP_LAN, "--mode", "full",
             "--data-dir", os.path.join(dossier, "kot"),
             "--whitelist-file", os.path.join(dossier, "wl-kot.json"),
             "--heartbeat", "3.0", "--lease", "6.0"],
            stdout=open("/tmp/sg_real_kot.log", "w"), stderr=subprocess.STDOUT))
        verifier("Kotlin auto-élu leader (bootstrap)",
                 attendre(15, lambda: (statut(urls["kot-a"]) or {})
                          .get("consensus", {}).get("role") == "leader", pas=0.5))
        procs.append(subprocess.Popen(
            [sys.executable, os.path.join(RACINE, "cli.py"), "serve",
             "--node-id", "py-1", "--port", str(PORT_PY),
             "--advertise-ip", IP_LAN, "--peers", "%s:%d" % (IP_LAN, PORT_KOT),
             "--whitelist-file", os.path.join(dossier, "wl1.json"),
             "--data-dir", os.path.join(dossier, "py1"),
             "--mode", "full", "--mock-inference", "--beacon-port", "0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        verifier("py-1 rejoint le leader Kotlin",
                 attendre(15, lambda: (statut(urls["py-1"]) or {})
                          .get("consensus", {}).get("leader_id") == "kot-a", pas=0.5))

        # 3) pilotes sur les téléphones (réveil AVANT : après reboot, App
        # non « encryption aware » = refus de démarrage → nœud jamais né)
        print("\n[3] nœuds NATIFS sur les téléphones…")
        for i, serie in enumerate(serie_physiques):
            node_id = "tel-" + serie[-4:].lower()
            port_hote = 17011 + i
            mu.preparer_appareil(serie, graine=IP_LAN)
            pilotes.append(subprocess.Popen(
                [mu.ADB, "-s", serie, "shell", "am", "instrument", "-w",
                 "-e", "class", "com.tree4five.swarmglu.NodeDriverInstrumentation",
                 "-e", "node_id", node_id, "-e", "port", str(mu.PORT_EMU),
                 "-e", "advertise_ip", IP_LAN, "-e", "advertise_port", str(port_hote),
                 "-e", "peers", "%s:%d" % (IP_LAN, PORT_KOT),
                 "-e", "swarm_id", "salle-reelle",
                 "-e", "heartbeat", "3.0", "-e", "lease", "6.0",
                 "-e", "duration_s", "420",
                 "%s.test/androidx.test.runner.AndroidJUnitRunner" % mu.PKG],
                stdout=open("/tmp/sg_real_%s.log" % node_id, "w"),
                stderr=subprocess.STDOUT))
            mu.verifier_demarrage_noeud(serie, node_id, urls[node_id])

        # 4) convergence N nœuds + noyau natif vérifié
        print("\n[4] convergence de l'essaim réel (%d nœuds)…" % len(urls))
        def converges():
            etats = {}
            for n, u in urls.items():
                e = statut(u)
                if e is None:
                    return False, etats
                etats[n] = e
            leaders = {e["consensus"]["leader_id"] for e in etats.values()}
            roles = sum(1 for e in etats.values()
                        if e["consensus"]["role"] == "leader")
            return (None not in leaders and len(leaders) == 1 and roles == 1), etats
        ok = attendre(120, lambda: converges()[0], pas=2)
        _, etats_diag = converges()
        verifier("TOUS les nœuds acceptent le leader Kotlin", ok,
                 {n: (e or {}).get("consensus") for n, e in etats_diag.items()})
        for i, serie in enumerate(serie_physiques):
            node_id = "tel-" + serie[-4:].lower()
            _s, st = http_json("GET", urls[node_id] + "/status", timeout=8)
            verifier("%s sur le NOYAU NATIF (android-kotlin-native, crypto %s)" % (
                node_id, (st or {}).get("crypto_mode")),
                (st or {}).get("implementation") == "android-kotlin-native",
                (st or {}).get("implementation", "injoignable"))
        verifier("chaque registre voit tous les pairs vivants",
                 attendre(60, lambda: all(
                     (statut(u) or {}).get("state", {}).get("alive_count", 0) >= len(urls)
                     for u in urls.values()), pas=3),
                 {n: (statut(u) or {}).get("state", {}).get("alive_count")
                  for n, u in urls.items()})

        # 5) communication depuis CHAQUE téléphone
        print("\n[5] /ask depuis chaque téléphone (pare-feu OSP)…")
        for i, serie in enumerate(serie_physiques):
            node_id = "tel-" + serie[-4:].lower()
            status, r = http_json("POST", urls[node_id] + "/ask",
                                  {"student_id": "eleve-" + node_id,
                                   "prompt": "Question depuis %s ?" % node_id},
                                  timeout=60)
            verifier("/ask sur %s → VERIFIED" % node_id,
                     status == 200 and (r or {}).get("osp", {}).get("verdict") == "VERIFIED",
                     (status, r))
        verifier("savoir partagé PARTOUT (même digest sur les %d nœuds)" % len(urls),
                 attendre(60, lambda: (
                     min(savoir(u)["count"] for u in urls.values()) >= len(serie_physiques)
                     and len({savoir(u)["digest"] for u in urls.values()}) == 1), pas=3),
                 {n: savoir(u) for n, u in urls.items()})

        # 6) mort du leader Kotlin → réélection trans-langage
        print("\n[6] mort brutale du leader Kotlin (kill -9)…")
        epoch_avant = max((e or {}).get("consensus", {}).get("epoch", 0)
                          for e in [statut(u) for u in urls.values()])
        procs[0].kill(); procs[0].wait()
        survivants = {n: u for n, u in urls.items() if n != "kot-a"}
        t0 = time.time()
        ok = verifier("réélection chez les survivants (epoch > %d)" % epoch_avant,
                      attendre(60, lambda: (
                          lambda etats: len(etats) == len(survivants)
                          and len({e["consensus"]["leader_id"] for e in etats.values()}) == 1
                          and all(e["consensus"]["epoch"] > epoch_avant
                                  for e in etats.values())
                      )({n: statut(u) for n, u in survivants.items()}), pas=2),
                      {n: (statut(u) or {}).get("consensus") for n, u in survivants.items()})
        if ok:
            print("      failover trans-langage en %.1f s" % (time.time() - t0))
        cible = next(iter(survivants))
        status, r = http_json("POST", survivants[cible] + "/ask",
                              {"student_id": "eleve-real",
                               "prompt": "Après la mort du Kotlin ?"}, timeout=60)
        verifier("l'essaim réel survit (/ask VERIFIED depuis %s)" % cible,
                 status == 200 and (r or {}).get("osp", {}).get("verdict") == "VERIFIED", r)

        # 7) audits
        print("\n[7] audits finaux…")
        for n, u in survivants.items():
            _s, v = http_json("GET", u + "/audit/verify", timeout=15)
            verifier("chaîne INTACTE sur %s" % n, bool(v and v.get("ok")), v)

    finally:
        for p in pilotes:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            if p.poll() is None:
                p.terminate()
        time.sleep(1)
        for p in procs + pilotes:
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        for r in RELAIS:
            r.terminate()

    print("\n" + "=" * 66)
    if ECHECS:
        print("ÉCHECS (%d) : %s" % (len(ECHECS), ECHECS))
        return 1
    print("ESSAIM RÉEL VALIDÉ ✔ (%d nœuds : Kotlin JVM + Python + %d téléphone(s)"
          " natifs) — partage du savoir, failover, audits intègres"
          % (len(urls), len(serie_physiques)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
