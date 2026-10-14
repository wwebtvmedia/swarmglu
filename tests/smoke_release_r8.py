#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmGlu — smoke-test du build RELEASE signé + R8 (non-régression Play).

But : vérifier que l'app minifiée/obfusquée (bundle/APK release) embarque
bien un nœud fonctionnel AVANT envoi Play Console. Sous R8, une classe mal
gardée (ex. androidx.tracing.Trace) fait crasher l'instrumentation avant
même le démarrage du nœud — c'est ce que ce script attrape.

Scénario :
  1. boot d'un émulateur vierge ;
  2. uninstall des paquets (debug ≠ release : INSTALL_FAILED_UPDATE_INCOMPATIBLE) ;
  3. install app-release.apk + app-release-androidTest.apk (même signature) ;
  4. `am instrument` NodeDriverInstrumentation (nœud release) ;
  5. GET /health → prêt, GET /status → noyau natif, POST /ask → réponse,
     GET /audit/verify → chaîne intègre ;
  6. arrêt propre.

Prérequis (build temporaire) — dans android/app/build.gradle :
    testBuildType "release"      # pour assemblerReleaseAndroidTest
puis :
    ./gradlew assembleRelease assembleReleaseAndroidTest

Usage :  python3 tests/smoke_release_r8.py
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SDK = os.path.expanduser("~/Android/Sdk")
ADB = os.path.join(SDK, "platform-tools", "adb")
EMULATOR = os.path.join(SDK, "emulator", "emulator")
PKG = "com.tree4five.swarmglu"
AVD = "sg-e1"
PORT_CONSOLE = 5554
PORT_EMU = 7001           # port du nœud DANS l'émulateur
PORT_HOTE = 17001         # adb forward côté hôte
SERIAL = "emulator-%d" % PORT_CONSOLE
DUREE_S = 180             # vie du pilote instrumenté

APK_APP = os.path.join(RACINE, "android/app/build/outputs/apk/release/app-release.apk")
APK_TEST = os.path.join(RACINE, "android/app/build/outputs/apk/androidTest/release/"
                                 "app-release-androidTest.apk")
BASE = "http://127.0.0.1:%d" % PORT_HOTE

ECHECS = []


def verifier(libelle, condition, detail=""):
    etat = "OK  " if condition else "ECHEC"
    print("  [%s] %s%s" % (etat, libelle, (" — " + str(detail)[:300]) if (detail and not condition) else ""))
    if not condition:
        ECHECS.append(libelle)
    return condition


def cmd(args, timeout=120):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def http(methode, url, corps=None, timeout=15.0):
    data = json.dumps(corps).encode() if corps is not None else None
    req = urllib.request.Request(url, data=data, method=methode,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except Exception:
        return None, None


def attendre(secondes, pred, pas=1.0):
    t0 = time.time()
    while time.time() - t0 < secondes:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(pas)
    return False


def logcat_driver():
    """Dernières lignes utiles (crash FATAL / ClassNotFound / test runner)."""
    lc = cmd([ADB, "-s", SERIAL, "logcat", "-d"], timeout=60).stdout
    utiles = [l for l in lc.splitlines()
              if any(m in l for m in ("FATAL", "ClassNotFound", "IllegalState",
                                      "NodeDriver", "AndroidJUnitRunner", "SwarmGlu"))]
    return "\n".join(utiles[-25:])


def main():
    print("=== Smoke-test RELEASE + R8 (non-régression Play) ===")

    # 0) Artefacts présents ?
    if not (os.path.isfile(APK_APP) and os.path.isfile(APK_TEST)):
        print("APKs release manquants :\n  %s\n  %s" % (APK_APP, APK_TEST))
        print("Build d'abord :\n  cd android && ./gradlew assembleRelease assembleReleaseAndroidTest")
        return 2

    # 1) Boot émulateur vierge
    print("\n[1] boot de l'émulateur %s…" % AVD)
    proc_emu = subprocess.Popen(
        [EMULATOR, "-avd", AVD, "-no-window", "-no-audio", "-no-boot-anim",
         "-gpu", "swiftshader_indirect", "-no-snapshot", "-wipe-data",
         "-port", str(PORT_CONSOLE), "-memory", "2560"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not attendre(240, lambda: cmd(
                [ADB, "-s", SERIAL, "shell", "getprop", "sys.boot_completed"]
        ).stdout.strip() == "1", pas=4):
            print("timeout de boot")
            return 1
        print("  boot OK")

        # 2) Nettoyage des paquets (debug ≠ release)
        print("\n[2] nettoyage des anciens paquets…")
        for p in (PKG, PKG + ".test"):
            cmd([ADB, "-s", SERIAL, "uninstall", p])

        # 3) Installation release + test (même signature)
        print("\n[3] installation des APKs RELEASE…")
        for apk in (APK_APP, APK_TEST):
            r = cmd([ADB, "-s", SERIAL, "install", "-r", apk], timeout=300)
            if not verifier("install %s" % os.path.basename(apk),
                            "Success" in r.stdout, r.stdout + r.stderr):
                return 1

        cmd([ADB, "-s", SERIAL, "forward",
             "tcp:%d" % PORT_HOTE, "tcp:%d" % PORT_EMU])

        # 4) Démarrage du nœud via instrumentation release
        print("\n[4] démarrage du nœud (NodeDriverInstrumentation, release)…")
        pilote = subprocess.Popen(
            [ADB, "-s", SERIAL, "shell", "am", "instrument", "-w",
             "-e", "class", "com.tree4five.swarmglu.NodeDriverInstrumentation",
             "-e", "node_id", "emu-r8", "-e", "port", str(PORT_EMU),
             "-e", "advertise_ip", "10.0.2.2", "-e", "advertise_port", str(PORT_HOTE),
             "-e", "peers", "10.0.2.2:1",
             "-e", "swarm_id", "smoke-r8",
             "-e", "heartbeat", "3.0", "-e", "lease", "6.0",
             "-e", "duration_s", str(DUREE_S),
             "%s.test/androidx.test.runner.AndroidJUnitRunner" % PKG],
            stdout=open("/tmp/sg_smoke_drv.log", "w"), stderr=subprocess.STDOUT)

        # 5) Observations HTTP
        print("\n[5] vérifications HTTP du nœud release…")
        pret = attendre(90, lambda: (http("GET", BASE + "/health")[0] == 200))
        if not verifier("GET /health → 200 (nœud prêt sous R8)", pret,
                        "driver : %s\nlogcat :\n%s" % (
                            open("/tmp/sg_smoke_drv.log").read()[-400:],
                            logcat_driver())):
            return 1

        st, p = http("GET", BASE + "/status")
        verifier("GET /status : noyau natif + consensus",
                 st == 200 and p and p.get("implementation") == "android-kotlin-native"
                 and "consensus" in (p or {}),
                 "status=%s body=%s" % (st, str(p)[:300]))
        # L'élection (bail 3 s / 6 s) a besoin de quelques secondes après le
        # démarrage du nœud : on attend plutôt que de tester trop tôt.
        def leader_elu():
            _s, _p = http("GET", BASE + "/status")
            return bool(_p and _p.get("consensus", {}).get("epoch", 0) >= 1
                        and _p.get("consensus", {}).get("leader_id"))
        attendre(45, leader_elu, pas=1.0)
        _s, _p = http("GET", BASE + "/status")
        verifier("leader élu (epoch ≥ 1)",
                 _p and _p.get("consensus", {}).get("epoch", 0) >= 1,
                 str(_p and _p.get("consensus"))[:300])

        st, rep = http("POST", BASE + "/ask",
                       {"prompt": "Combien de regions en Tunisie ?",
                        "sources": ["geo:tunisie-regions"], "_fwd": 1})
        verifier("POST /ask → 200 avec réponse", st == 200 and rep is not None,
                 "status=%s body=%s" % (st, str(rep)[:300]))
        if rep:
            print("        réponse : %s" % json.dumps(rep, ensure_ascii=False)[:220])

        st, ver = http("GET", BASE + "/audit/verify")
        verifier("GET /audit/verify → chaîne intègre",
                 st == 200 and ver and ver.get("valid") is not False
                 and ver.get("ok", True) is not False,
                 "body=%s" % str(ver)[:300])
        if ver:
            print("        audit : %s" % json.dumps(ver, ensure_ascii=False)[:220])

        # 6) Bilan
        print("\n=== RÉSULTAT : %s (%d échec(s)) ==="
              % ("VERT" if not ECHECS else "ROUGE", len(ECHECS)))
        if ECHECS:
            print("  - " + "\n  - ".join(ECHECS))
        return 0 if not ECHECS else 1
    finally:
        try:
            subprocess.run([ADB, "-s", SERIAL, "emu", "kill"], capture_output=True, timeout=30)
        except Exception:
            pass
        try:
            proc_emu.wait(timeout=30)
        except Exception:
            proc_emu.kill()


if __name__ == "__main__":
    sys.exit(main())
