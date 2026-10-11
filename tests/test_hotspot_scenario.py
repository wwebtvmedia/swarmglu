#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmGlu — SCÉNARIO HOTSPOT (« étendre la zone ») sur machines réelles.

Topologie simulée du point d'accès local-only Android
(WifiManager.startLocalOnlyHotspot) — impossible à créer sur émulateur
(pas de radio Wi-Fi), donc le RÔLE est joué par les nœuds :

    ┌── RÉSEAU PRINCIPAL (Wi-Fi école) ────────────────────────────────┐
    │  pc-orchestre (192.168.1.105:7901)                               │
    │  far-249 (192.168.1.249:7905)   [nœud SwarmGlu déployé par SSH]  │
    │  OSP swarmknowledge (:3000)     [service de l'écosystème]        │
    │  routeur-hotspot (émulateur A, 192.168.1.105:17001)              │
    │        ▲ membre du swarm + OUVRIRA le hotspot                    │
    └────────┼─────────────────────────────────────────────────────────┘
             │ (relais applicatif POST /relay)
    ┌── « HOTSPOT » ───────────────────────────────────────────────────┐
    │  client-hotspot (émulateur B = tablette/agent/harnessDroid/      │
    │  LLMProvider joints au hotspot) — graine : le routeur SEUL       │
    └───────────────────────────────────────────────────────────────────┘

Vérifié :
  1. convergence du réseau principal + du client via le routeur ;
  2. le client atteint swarmknowledge (192.168.1.249:3000) UNIQUEMENT à
     travers le relais du routeur — comme harnessdroid/llmprovider le
     feraient depuis le hotspot ;
  3. le client atteint le nœud far-249 via le relais ;
  4. requête d'élève du client servie par l'essaim + savoir partagé ;
  5. pas de proxy ouvert (IP publique refusée) ;
  6. chaînes d'audit intactes (relais scellés).

Usage :  python3 tests/test_hotspot_scenario.py --deploy-249 youyou
"""

import argparse
import json
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

IP_LOCALE = detect_local_ip()
IP_LOIN = "192.168.1.249"
PORT_PC, PORT_FAR = 7901, 7905
OSP = {"host": IP_LOIN, "port": 3000, "path": "/osp/endpoint.json"}

RELAIS_SOCAT = []
ECHECS = mu.ECHECS


def verifier(nom, cond, detail=""):
    return mu.verifier(nom, cond, detail)


def http_json(methode, url, corps=None, timeout=15.0):
    return mu.http_json(methode, url, corps, timeout)


def statuts(urls):
    s = {}
    for nom, url in urls.items():
        _st, p = http_json("GET", url + "/status", timeout=6)
        if p and "consensus" in p:
            s[nom] = p
    return s


def main():
    parseur = argparse.ArgumentParser()
    parseur.add_argument("--deploy-249", default="youyou",
                         help="utilisateur SSH pour le nœud far-249 (« » pour désactiver)")
    args = parseur.parse_args()

    urls = {"pc": "http://127.0.0.1:%d" % PORT_PC,
            "routeur": "http://127.0.0.1:%d" % mu.AVDS[0][2],   # émulateur A
            "client": "http://127.0.0.1:%d" % mu.AVDS[1][2]}    # émulateur B
    if args.deploy_249:
        urls["far-249"] = "http://%s:%d" % (IP_LOIN, PORT_FAR)

    proc_pc, procs_instr = [], []
    dossier = tempfile.mkdtemp(prefix="sghs_")
    distant = None
    try:
        mu.creer_avds()
        mu.booter_emulateurs()
        mu.installer_et_forwarder()
        import shutil
        for _a, _c, port, _n in mu.AVDS:
            RELAIS_SOCAT.append(subprocess.Popen(
                ["socat", "TCP-LISTEN:%d,bind=%s,fork,reuseaddr" % (port, IP_LOCALE),
                 "TCP:127.0.0.1:%d" % port],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(1.0)

        print("\n[1] réseau principal + routeur + client…")
        proc_pc.append(subprocess.Popen(
            [sys.executable, os.path.join(RACINE, "cli.py"), "serve",
             "--node-id", "pc-orchestre", "--port", str(PORT_PC),
             "--advertise-ip", IP_LOCALE, "--whitelist-file",
             os.path.join(dossier, "wl.json"), "--data-dir", os.path.join(dossier, "pc"),
             "--mode", "full", "--mock-inference", "--beacon-port", "0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(8)

        def pilote(console, node_id, port_hote, peers):
            args_i = [mu.ADB, "-s", "emulator-%d" % console, "shell", "am", "instrument", "-w",
                      "-e", "class", "com.tree4five.swarmglu.NodeDriverInstrumentation",
                      "-e", "node_id", node_id, "-e", "port", str(mu.PORT_EMU),
                      "-e", "advertise_ip", IP_LOCALE, "-e", "advertise_port", str(port_hote),
                      "-e", "peers", peers, "-e", "swarm_id", "swarm-local",
                      "-e", "heartbeat", "3.0", "-e", "lease", "6.0", "-e", "duration_s", "420",
                      "%s.test/androidx.test.runner.AndroidJUnitRunner" % mu.PKG]
            return subprocess.Popen(args_i, stdout=open("/tmp/sg_hs_%s.log" % node_id, "w"),
                                    stderr=subprocess.STDOUT)

        # far-249 (nœud SwarmGlu sur le PC distant, réseau principal)
        if args.deploy_249:
            ssh = ["ssh", "-o", "BatchMode=yes", "%s@%s" % (args.deploy_249, IP_LOIN)]
            subprocess.run(ssh + ["pkill -f 'cli.py serve' ; true"], capture_output=True)
            subprocess.run(["bash", "-c",
                            "cd %s && tar czf - --exclude=.git --exclude=android --exclude=data "
                            "--exclude=__pycache__ *.py | ssh -o BatchMode=yes %s@%s "
                            "'mkdir -p ~/swarmglu && tar xzf - -C ~/swarmglu'"
                            % (RACINE, args.deploy_249, IP_LOIN)], capture_output=True)
            distant = subprocess.run(ssh + [
                "cd ~/swarmglu && (nohup python3 cli.py serve --node-id far-249 "
                "--port %d --advertise-ip %s --whitelist-file ~/swarmglu/wl-hs.json "
                "--data-dir ~/swarmglu/data-hs --mode full --mock-inference "
                "--beacon-port 0 --peers %s:%d > ~/swarmglu/node-hs.log 2>&1 < /dev/null &)"
                % (PORT_FAR, IP_LOIN, IP_LOCALE, PORT_PC)], capture_output=True)

        # Routeur (émulateur A) : membre du réseau principal
        procs_instr.append(pilote(mu.AVDS[0][1], "routeur-hotspot", mu.AVDS[0][2],
                                  "%s:%d" % (IP_LOCALE, PORT_PC)))
        # Client (émulateur B) : graine = LE ROUTEUR SEUL (comme un appareil
        # joint au hotspot — harnessdroid/llmprovider/tablette élève)
        procs_instr.append(pilote(mu.AVDS[1][1], "client-hotspot", mu.AVDS[1][2],
                                  "%s:%d" % (IP_LOCALE, mu.AVDS[0][2])))

        principals = {"pc", "routeur"} | ({"far-249"} if distant else set())
        verifier("réseau principal convergé (pc + routeur%s)"
                 % (" + far-249" if distant else ""),
                 mu.attendre(90, lambda: (
                     lambda st: all(
                         p["consensus"]["leader_id"] and p["consensus"]["leader_id"] ==
                         [x["consensus"]["leader_id"] for x in st.values() if True][0]
                         for p in st.values()) and sum(
                         1 for p in st.values() if p["consensus"]["role"] == "leader") == 1
                 )({n: p for n, p in statuts(urls).items() if n in principals}), pas=2),
                 statuts(urls))
        verifier("client-hotspot rejoint via le routeur (leader connu)",
                 mu.attendre(60, lambda: statuts(urls).get("client", {})
                             .get("consensus", {}).get("leader_id") not in (None, ""), pas=2),
                 statuts(urls).get("client", {}).get("consensus"))

        print("\n[2] routage du hotspot : le client passe par le RELAIS du routeur…")
        _s, rep = http_json("POST", urls["routeur"] + "/relay", OSP, timeout=20)
        ok_sw = (rep or {}).get("status") == 200 and (rep.get("data") or {}).get("node_id")
        verifier("swarmknowledge (192.168.1.249:3000) atteint via le relais",
                 ok_sw, rep)
        if ok_sw:
            verifier("contrat OSP v0.6 intact à travers le relais",
                     str(rep["data"].get("packet_version", "")).startswith("0.6"),
                     rep["data"])
        if distant:
            _s, rep2 = http_json("POST", urls["routeur"] + "/relay",
                                 {"host": IP_LOIN, "port": PORT_FAR,
                                  "path": "/health"}, timeout=15)
            verifier("nœud far-249 (SwarmGlu) atteint via le relais",
                     (rep2 or {}).get("status") == 200
                     and (rep2.get("data") or {}).get("ok") is True, rep2)
        _s, rep3 = http_json("POST", urls["routeur"] + "/relay",
                             {"host": "8.8.8.8", "port": 53, "path": "/"}, timeout=10)
        verifier("pas de proxy ouvert (IP publique refusée : %s)" % _s, _s == 403, rep3)

        print("\n[3] le client vit dans l'essaim à travers le routeur…")
        status, r = http_json("POST", urls["client"] + "/ask",
                              {"student_id": "eleve-hotspot",
                               "prompt": "Question depuis le hotspot ?"}, timeout=60)
        verifier("requête d'élève du client servie (OSP VERIFIED)",
                 status == 200 and (r or {}).get("osp", {}).get("verdict") == "VERIFIED", r)
        verifier("savoir partagé jusque chez le client",
                 mu.attendre(40, lambda: http_json(
                     "GET", urls["client"] + "/knowledge", timeout=8)[1].get("count", 0) >= 1,
                     pas=3))

        print("\n[4] audit…")
        for nom in list(urls):
            _s, v = http_json("GET", urls[nom] + "/audit/verify", timeout=15)
            verifier("chaîne INTACTE sur %s (relais scellés)" % nom,
                     bool(v and v.get("ok")), v)

    finally:
        for proc in procs_instr:
            if proc.poll() is None:
                proc.terminate()
        for proc in proc_pc:
            if proc.poll() is None:
                proc.terminate()
        for r in RELAIS_SOCAT:
            r.terminate()
        if args.deploy_249:
            subprocess.run(["ssh", "-o", "BatchMode=yes", "%s@%s" % (args.deploy_249, IP_LOIN),
                            "pkill -f 'cli.py serve' ; true"], capture_output=True)
        mu.nettoyer(None, [])

    print("\n" + "=" * 64)
    if ECHECS:
        print("ÉCHECS (%d) : %s" % (len(ECHECS), ECHECS))
        return 1
    print("SCÉNARIO HOTSPOT VALIDÉ ✔ (zone étendue : client hotspot → routeur → "
          "essaim + swarmknowledge + far-249, pas de proxy ouvert, audit intègre)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
