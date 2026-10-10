#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmGlu — test multi-émulateurs (dans l'esprit des tests swarmknowledge,
en nettement plus strict) : DEUX émulateurs Android + UN nœud PC forment
un essaim réel, aux valeurs du cahier des charges (heartbeats 3 s, bail 6 s).

Topologie réseau émulateur (chaque instance est isolée ; 10.0.2.2 = hôte) :

    PC (cli.py, port 7901, annoncé 10.0.2.2:7901)
     ▲ 10.0.2.2:7901            │ adb forward 17001→emu-a:7001
    emu-a (7001, annoncé         │ adb forward 17002→emu-b:7001
     10.0.2.2:17001)  ◄──────────┘
    emu-b (7001, annoncé 10.0.2.2:17002)   [seed → PC]

Scénario vérifié :
  1. convergence : les 3 nœuds s'accordent sur UN leader, epoch >= 1,
     chacun voit 3 pairs vivants ;
  2. requête d'élève servie par l'essaim (pare-feu OSP VERIFIED) ;
  3. MORT BRUTALE du leader (kill -9 côté PC, am force-stop côté Android) ;
  4. réélection automatique chez les survivants, epoch incrémentée ;
  5. l'essaim survit : /ask à nouveau servi ;
  6. chaînes de hachage INTACTES sur les survivants.

Usage :  python3 tests/test_multi_emulator_swarm.py
(émulateurs créés/bootés/arrêtés automatiquement ; ~4-6 min au total)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SDK = os.path.expanduser("~/Android/Sdk")
ADB = os.path.join(SDK, "platform-tools", "adb")
EMULATOR = os.path.join(SDK, "emulator", "emulator")
AVDMANAGER = os.path.join(SDK, "cmdline-tools", "latest", "bin", "avdmanager")
APK_APP = os.path.join(RACINE, "android/app/build/outputs/apk/debug/app-debug.apk")
APK_TEST = os.path.join(RACINE, "android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk")
PKG = "com.tree4five.swarmglu"

PORT_PC = 7901           # nœud PC (hôte)
AVDS = [("sg-e1", 5554, 17001, "emu-alpha"),   # (avd, port-emu, forward, node_id)
        ("sg-e2", 5556, 17002, "emu-beta")]
PORT_EMU = 7001

ECHECS = []


def http_json(methode, url, corps=None, timeout=10.0):
    donnees = json.dumps(corps or {}).encode() if methode == "POST" else None
    req = urllib.request.Request(url, data=donnees, method=methode,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as rep:
            return rep.status, json.loads(rep.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")
    except Exception:
        return 0, None


def verifier(nom, condition, detail=""):
    etat = "✓" if condition else "✗"
    print("  %s %s %s" % (etat, nom, ("— " + str(detail)[:160]) if (detail and not condition) else ""))
    if not condition:
        ECHECS.append(nom)
    return condition


def cmd(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, timeout=300, **kw)


def attendre(delai_s, f, pas=1.0):
    limite = time.time() + delai_s
    while time.time() < limite:
        if f():
            return True
        time.sleep(pas)
    return False


# ---------------------------------------------------------------------------
# Étapes
# ---------------------------------------------------------------------------

def creer_avds():
    for avd, *_ in AVDS:
        existe = cmd([AVDMANAGER, "list", "avd"]).stdout
        if ("Name: %s" % avd) in existe:
            continue
        print("[setup] création de l'AVD %s…" % avd)
        r = cmd([AVDMANAGER, "create", "avd", "-n", avd, "-k",
                 "system-images;android-34;google_apis;x86_64",
                 "-d", "pixel_tablet", "--force"])
        if r.returncode != 0:
            print(r.stderr[-400:])
            raise SystemExit("impossible de créer l'AVD %s" % avd)


def booter_emulateurs():
    procs = {}
    for avd, port_console, *_ in AVDS:
        print("[setup] boot %s (console %d)…" % (avd, port_console))
        procs[port_console] = subprocess.Popen(
            [EMULATOR, "-avd", avd, "-no-window", "-no-audio",
             "-no-boot-anim", "-gpu", "swiftshader_indirect",
             "-no-snapshot", "-port", str(port_console), "-memory", "2560"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for avd, port_console, *_ in AVDS:
        serial = "emulator-%d" % port_console
        ok = attendre(240, lambda s=serial: cmd(
            [ADB, "-s", s, "shell", "getprop", "sys.boot_completed"]
        ).stdout.strip() == "1", pas=4)
        if not ok:
            raise SystemExit("timeout de boot pour %s" % avd)
        print("       %s démarré" % serial)


def installer_et_forwarder():
    for _avd, port_console, port_hote, _nid in AVDS:
        serial = "emulator-%d" % port_console
        for apk in (APK_APP, APK_TEST):
            r = cmd([ADB, "-s", serial, "install", "-r", apk])
            if "Success" not in r.stdout:
                raise SystemExit("install échouée sur %s : %s" % (serial, r.stdout + r.stderr))
        cmd([ADB, "-s", serial, "forward", "tcp:%d" % port_hote, "tcp:%d" % PORT_EMU])
    print("[setup] APKs installés, ports transférés : %s" %
          [p for _a, _c, p, _n in AVDS])


def lancer_noeuds():
    # Nœud PC de l'essaim (même binaire que les écoles)
    dossier = tempfile.mkdtemp(prefix="sgmupc_")
    pc = subprocess.Popen(
        [sys.executable, os.path.join(RACINE, "cli.py"), "serve",
         "--node-id", "pc-orchestre", "--port", str(PORT_PC),
         "--whitelist-file", os.path.join(dossier, "wl.json"),
         "--data-dir", os.path.join(dossier, "data"),
         "--advertise-ip", "10.0.2.2",
         "--mode", "full", "--mock-inference", "--beacon-port", "0",
         "--gguf-models", "pc-coordonnateur"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Pilotes instrumentés sur chaque émulateur
    instrumentations = []
    for _avd, port_console, port_hote, node_id in AVDS:
        serial = "emulator-%d" % port_console
        instrumentations.append(subprocess.Popen(
            [ADB, "-s", serial, "shell", "am", "instrument", "-w",
             "-e", "class", "com.tree4five.swarmglu.NodeDriverInstrumentation",
             "-e", "node_id", node_id,
             "-e", "port", str(PORT_EMU),
             "-e", "advertise_port", str(port_hote),
             "-e", "peers", "10.0.2.2:%d" % PORT_PC,
             "-e", "swarm_id", "salle-multi-emu",
             "-e", "heartbeat", "3.0", "-e", "lease", "6.0",
             "-e", "duration_s", "360",
             "%s.test/androidx.test.runner.AndroidJUnitRunner" % PKG],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return pc, instrumentations


def statuts():
    s = {}
    for url, nom in ([("http://127.0.0.1:%d" % PORT_PC, "pc-orchestre")] +
                     [("http://127.0.0.1:%d" % p, n) for _a, _c, p, n in AVDS]):
        _st, p = http_json("GET", url + "/status", timeout=6)
        if p and "consensus" in p:
            s[nom] = p
    return s


def phase_convergence():
    print("\n[1] convergence de l'essaim 3 nœuds (heartbeats 3 s)…")

    def tous_daccord():
        st = statuts()
        if len(st) != 3:
            return False
        leaders = {p["consensus"]["leader_id"] for p in st.values()}
        roles_ok = sum(1 for p in st.values() if p["consensus"]["role"] == "leader")
        return len(leaders) == 1 and roles_ok == 1 and None not in leaders

    verifier("un seul leader, accepté par les 3 nœuds (≤ 45 s)",
             attendre(45, tous_daccord, pas=2), statuts())
    st = statuts()
    if len(st) == 3:
        verifier("epoch >= 1",
                 all(p["consensus"]["epoch"] >= 1 for p in st.values()),
                 {n: p["consensus"] for n, p in st.items()})
        verifier("3 pairs vivants dans chaque registre",
                 all(p["state"]["alive_count"] >= 3 for p in st.values()),
                 {n: p["state"]["alive_count"] for n, p in st.items()})
        verifier("chaînes d'audit déjà scellées et intactes",
                 all(p["audit"]["ok"] for p in st.values()))
        return st
    return {}


def phase_ask(url, etiquette, model_hint=""):
    corps = {"student_id": "eleve-multi-emu",
             "prompt": "Quelle est la capitale de la Tunisie ?"}
    if model_hint:
        corps["model_hint"] = model_hint
    status, rep = http_json("POST", url + "/ask", corps, timeout=60)
    return verifier("requête d'élève servie depuis %s (OSP VERIFIED)" % etiquette,
                    status == 200 and rep and rep.get("osp", {}).get("verdict") == "VERIFIED",
                    (status, rep))


def phase_mort_du_leader(st, pc, instrumentations):
    if not st:
        return None
    leader = next(iter(st.values()))["consensus"]["leader_id"]
    print("\n[3] coupure brutale du leader : %s" % leader)
    t_kill = time.time()
    if leader == "pc-orchestre":
        pc.kill(); pc.wait()
    else:
        cible = next(c for a, c, p, n in AVDS if n == leader)
        serial = "emulator-%d" % cible
        cmd([ADB, "-s", serial, "shell", "am", "force-stop", PKG])
        cmd([ADB, "-s", serial, "shell", "am", "force-stop", PKG + ".test"])
    return leader, t_kill


def phase_basculement(leader_mort, t_kill, st_avant):
    survivants = ["pc-orchestre" if leader_mort != "pc-orchestre" else None] + \
                 [n for _a, _c, _p, n in AVDS if n != leader_mort]
    survivants = [s for s in survivants if s]
    urls = {"pc-orchestre": "http://127.0.0.1:%d" % PORT_PC}
    urls.update({n: "http://127.0.0.1:%d" % p for _a, _c, p, n in AVDS})

    epoch_avant = max(p["consensus"]["epoch"] for p in st_avant.values())

    def converge():
        st = statuts()
        vivants = {n: p for n, p in st.items() if n in survivants}
        if len(vivants) != len(survivants):
            return False
        leaders = {p["consensus"]["leader_id"] for p in vivants.values()}
        return (len(leaders) == 1 and None not in leaders
                and all(p["consensus"]["epoch"] > epoch_avant for p in vivants.values()))

    ok = verifier("réélection automatique chez les survivants (≤ 45 s)",
                  attendre(45, converge, pas=2), statuts())
    if ok:
        print("      basculement en %.1f s (epoch %d → >%d)" % (
            time.time() - t_kill, epoch_avant, epoch_avant))
    return survivants, urls


def phase_audit(urls, survivants):
    print("\n[6] audit des chaînes de hachage des survivants…")
    for n in survivants:
        _s, verif = http_json("GET", urls[n] + "/audit/verify", timeout=15)
        verifier("chaîne INTACTE sur %s" % n,
                 bool(verif and verif.get("ok")), verif)


def nettoyer(pc, instrumentations):
    for proc in instrumentations:
        if proc.poll() is None:
            proc.terminate()
    if pc and pc.poll() is None:
        pc.terminate()
    for _avd, port_console, *_ in AVDS:
        cmd([ADB, "-s", "emulator-%d" % port_console, "emu", "kill"])
    time.sleep(4)


# ---------------------------------------------------------------------------

def main():
    for chemin in (ADB, EMULATOR, AVDMANAGER, APK_APP, APK_TEST):
        if not os.path.exists(chemin):
            raise SystemExit("introuvable : %s (build au préalable : "
                             "cd android && ./gradlew assembleDebug "
                             "assembleDebugAndroidTest)" % chemin)

    creer_avds()
    pc = instrumentations = None
    try:
        booter_emulateurs()
        installer_et_forwarder()
        print("[setup] démarrage des 3 nœuds (1 PC + 2 émulateurs)…")
        pc, instrumentations = lancer_noeuds()

        st = phase_convergence()
        print("\n[2] requête d'élève…")
        phase_ask("http://127.0.0.1:%d" % AVDS[1][2], AVDS[1][3],
                  model_hint="pc-coordonnateur")  # depuis emu-beta → leader PC

        mort = phase_mort_du_leader(st, pc, instrumentations)
        survivants, urls = phase_basculement(mort[0], mort[1], st)

        print("\n[5] l'essaim survit…")
        cible = survivants[-1]
        phase_ask(urls[cible], cible)
        phase_audit(urls, survivants)

    finally:
        nettoyer(pc, instrumentations or [])

    print("\n" + "=" * 60)
    if ECHECS:
        print("ÉCHECS (%d) : %s" % (len(ECHECS), ECHECS))
        return 1
    print("ESSAIM MULTI-ÉMULATEURS VALIDÉ ✔ (2 Android + 1 PC, "
          "leader tué, réélection, audit intègre)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
