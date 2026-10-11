#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmGlu — ESSAIM MIXTE MULTI-LANGAGE (interopérabilité câble réelle).
=====================================================================
1 nœud Kotlin (JVM) + 2 nœuds Python (+ 1 nœud ANDROID émulateur avec
--emulateur → essaim TRI-LANGAGE : Kotlin JVM + Python PC + Python
Android/Chaquopy) forment UN essaim : élection déterministe
trans-langage, requêtes /ask, savoir commun, mort du leader (failover
croisé), audit — chaque langue vérifiant les autres.

Topologie (heartbeats accélérés ; avec --emulateur, le nœud Android
rejoint via le LAN hôte) :
    py-1 (Python PC, 7301) ◄──► kot-a (Kotlin JVM, 7302) ◄──► py-2 (PC, 7303)
                                 └──► emu-a (Python Android, socat 17001)

Usage :  python3 tests/test_kotlin_interop.py [--emulateur]
         (prérequis : cd kotlin && ./gradlew installDist)
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


def ipLanHote() -> str:
    sys.path.insert(0, RACINE)
    from swarm_glu import detect_local_ip
    return detect_local_ip()


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


def demarrer_emulateur(urls, pilotes):
    """Nœud ANDROID (émulateur) : APK + forward/socat + NodeDriver.
    Rejoint le Kotlin via l'IP LAN de l'hôte (le slirp route le LAN)."""
    import shutil
    sys.path.insert(0, RACINE)
    from swarm_glu import detect_local_ip
    ip_lan = detect_local_ip()
    avd, console, port_hote, _ = mu.AVDS[0]
    mu.AVDS[:] = [(avd, console, port_hote, "emu-a")]
    mu.creer_avds(); mu.booter_emulateurs(); mu.installer_et_forwarder()
    if not shutil.which("socat"):
        raise SystemExit("socat requis pour l'émulateur")
    subprocess.Popen(
        ["socat", "TCP-LISTEN:%d,bind=%s,fork,reuseaddr" % (port_hote, ip_lan),
         "TCP:127.0.0.1:%d" % port_hote],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    args = [mu.ADB, "-s", "emulator-%d" % console, "shell", "am", "instrument", "-w",
            "-e", "class", "com.tree4five.swarmglu.NodeDriverInstrumentation",
            "-e", "node_id", "emu-a", "-e", "port", str(mu.PORT_EMU),
            "-e", "advertise_ip", ip_lan, "-e", "advertise_port", str(port_hote),
            "-e", "peers", "%s:7302" % ip_lan, "-e", "swarm_id", "salle-mixte",
            "-e", "heartbeat", "0.6", "-e", "lease", "1.2", "-e", "duration_s", "180",
            "%s.test/androidx.test.runner.AndroidJUnitRunner" % mu.PKG]
    pilotes.append(subprocess.Popen(args, stdout=open("/tmp/sg_mix_emu.log", "w"),
                                    stderr=subprocess.STDOUT))
    urls["emu-a"] = "http://127.0.0.1:%d" % port_hote
    print("      emu-a (Android) → %s" % urls["emu-a"])


def main():
    avec_emulateur = "--emulateur" in sys.argv
    if not os.access(BIN_KOTLIN, os.X_OK):
        raise SystemExit("nœud Kotlin absent : cd kotlin && ./gradlew installDist")
    pilotes_emu = []

    urls = {"py-1": "http://127.0.0.1:7301",
            "kot-a": "http://127.0.0.1:7302",
            "py-2": "http://127.0.0.1:7303"}
    dossier = tempfile.mkdtemp(prefix="sgmix_")
    procs = []
    try:
        # Le nœud KOTLIN démarre le premier (seed pour py-2 ; py-1 solitaire)
        procs.append(subprocess.Popen(
            [BIN_KOTLIN, "--node-id", "kot-a", "--port", "7302",
             "--advertise-ip", ipLanHote(), "--mode", "full",
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

        if avec_emulateur:
            print("\n[0 bis] nœud Android (émulateur)…")
            demarrer_emulateur(urls, pilotes_emu)
            verifier("emu-a (Android) rejoint le leader Kotlin",
                     attendre(60, lambda: (statut(urls.get("emu-a", "")) or {})
                              .get("consensus", {}).get("leader_id") == "kot-a", pas=2),
                     (statut(urls.get("emu-a", "")) or {}).get("consensus"))
        _s, st_android = http_json("GET", urls["emu-a"] + "/status", timeout=8)
        verifier("Android tourne sur le NOYAU NATIF (android-kotlin-native)",
                 (st_android or {}).get("implementation") == "android-kotlin-native"
                 and (st_android or {}).get("crypto_mode") == "android-native",
                 (st_android or {}).get("implementation", "nœud injoignable"))

        print("\n[1] convergence de l'essaim mixte (%d nœuds, %d langages)…" % (
            len(urls), 3 if avec_emulateur else 2))
        def converges():
            etats = {}
            for n, u in urls.items():
                e = statut(u)
                if e:
                    etats[n] = e
            if len(etats) != len(urls):
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
        survivants = {n: u for n, u in urls.items()
                      if n != leader and statut(u) is not None}
        t0 = time.time()
        ok = verifier("réélection chez les survivants (epoch > ancien)",
                      attendre(20, lambda: (
                          lambda etats2: len(etats2) == len(survivants)
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
        for p in pilotes_emu:
            if p.poll() is None:
                p.terminate()
        if avec_emulateur:
            mu.nettoyer(None, [])
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
