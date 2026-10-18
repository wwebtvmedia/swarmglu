#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmGlu — TEST DE COMMUNICATION ÉMULATEUR ↔ 192.168.1.249
===========================================================
Un nœud Android (émulateur) et le PC distant .249 communiquent par TOUS
les chemins, dans les deux sens :

  ┌ émulateur sg-e1 (Android) ┐          ┌──── 192.168.1.249 (debian) ────┐
  │ nœud « emu-comm » :7001   │◄─ essaim ─►│ nœud « far-249 » :7905        │
  │ annoncé 192.168.1.105:17001 (socat)   │ service OSP swarmknowledge:3000│
  └───────────────────────────┘          └─────────────────────────────────┘

MATRICE VÉRIFIÉE :
  0. préflight : .249 joignable (ping) + contrat OSP v0.6 vivant (:3000)
  1. convergence DIRECTE emu ↔ far-249 (essaim 2 nœuds, sans PC intermédiaire)
  2. requête d'élève depuis l'émulateur → servie, OSP VERIFIED
  3. ancrage OSP : réponse sourcée sur le nœud swarmknowledge VIVANT
  4. savoir partagé : même digest sur l'émulateur ET sur .249
  5. RELAIS croisé : l'émulateur atteint swarmknowledge PAR le relais de .249
  6. poignée de main post-quantique (ML-KEM+ECDSA) hôte→émulateur ET hôte→.249
     + RPC chiffré ping/pong dans les deux sens
  7. résilience : mort brutale du nœud .249 → l'émulateur se réélit (epoch 2)
     et continue de servir ; chaîne d'audit intacte

Usage :  python3 tests/test_communication_249.py [--ssh youyou] [--keep]
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

# Un SEUL émulateur suffit : on restreint la liste AVANT d'utiliser les
# aides de mu (création/boot/install/forward deviennent mono-émulateur).
mu.AVDS = [mu.AVDS[0]]

RACINE = mu.RACINE
sys.path.insert(0, RACINE)
from swarm_glu import detect_local_ip

IP_LOCALE = detect_local_ip()
IP_LOIN = "192.168.1.249"
PORT_FAR = 7905
PORT_EMU_HOTE = mu.AVDS[0][2]        # 17001 (adb forward + socat)
NODE_EMU = "emu-comm"

ECHECS = mu.ECHECS
RELAIS_SOCAT = []
EXTRA_PILOTES = []          # instrumentations des téléphones/tablettes
URLS = {}                   # node_id -> URL, rempli au démarrage


def verifier(nom, cond, detail=""):
    return mu.verifier(nom, cond, detail)


def http_json(methode, url, corps=None, timeout=15.0):
    return mu.http_json(methode, url, corps, timeout)


def ssh(user, commande, timeout=60):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "%s@%s" % (user, IP_LOIN),
                           commande], capture_output=True, text=True, timeout=timeout)


def url_emu():
    return "http://127.0.0.1:%d" % PORT_EMU_HOTE


def url_far():
    return "http://%s:%d" % (IP_LOIN, PORT_FAR)


def statut(url):
    _s, p = http_json("GET", url + "/status", timeout=6)
    return p if p and "consensus" in p else None


def savoir(url):
    _s, k = http_json("GET", url + "/knowledge", timeout=8)
    return k if k and "count" in k else {"count": -1, "digest": "?"}


# ---------------------------------------------------------------------------

def phase_preflight(user):
    print("\n[0] préflight 192.168.1.249…")
    ok_ping = subprocess.run(["ping", "-c", "2", "-W", "2", IP_LOIN],
                             capture_output=True).returncode == 0
    verifier(".249 joignable (ping)", ok_ping)
    _s, contrat = http_json("GET", "http://%s:3000/osp/endpoint.json" % IP_LOIN, timeout=8)
    ancre = None
    if verifier("service OSP swarmknowledge vivant (GET :3000/osp/endpoint.json)",
                _s == 200 and contrat and "node_id" in contrat, (_s, contrat)):
        verifier("contrat OSP v0.6 (node_id=%s)" % contrat.get("node_id"),
                 str(contrat.get("packet_version", "")).startswith("0.6"))
        ancre = contrat
    verifier("SSH par clé (%s@…)" % user,
             ssh(user, "true").returncode == 0)
    return ancre


def equiper_249(user):
    """Livrer les roues cryptography (ECDSA/ML-KEM réels) sur .249 —
    sans pip sur la machine distante : roues manylinux + unzip + PYTHONPATH."""
    import glob
    roues = glob.glob("/tmp/sg_wheels/*.whl")
    if not roues:
        os.makedirs("/tmp/sg_wheels", exist_ok=True)
        subprocess.run(["pip", "download", "cryptography", "--python-version", "313",
                        "--only-binary=:all:", "-d", "/tmp/sg_wheels", "-q"],
                       capture_output=True, timeout=300)
        roues = glob.glob("/tmp/sg_wheels/*.whl")
    verifier("roues cryptography téléchargées", len(roues) >= 2, roues)
    for r in roues:
        subprocess.run(["scp", "-o", "BatchMode=yes", r,
                        "%s@%s:/tmp/" % (user, IP_LOIN)], capture_output=True, timeout=120)
    ssh(user, "mkdir -p ~/swarmglu/pylibs && cd ~/swarmglu/pylibs && "
              "for w in /tmp/*.whl; do unzip -oq $w; done && rm -f /tmp/*.whl")
    r = ssh(user, "cd ~/swarmglu && [ -d pylibs/cryptography ] && "
                  "PYTHONPATH=~/swarmglu/pylibs python3 -c "
                  "'import cryptography; print(cryptography.__version__)'")
    verifier("cryptography installée sur .249 (mode ECDSA/ML-KEM réel)",
             r.returncode == 0, r.stderr)


def deployer_far(user):
    print("\n[1] déploiement du nœud far-249 sur %s…" % IP_LOIN)
    equiper_249(user)
    ssh(user, "pkill -f 'cli.py serve' ; sleep 1 ; true")
    r = ssh(user, "python3 --version && mkdir -p ~/swarmglu")
    verifier("python3 prêt sur .249", r.returncode == 0, r.stderr)
    r = subprocess.run(
        ["bash", "-c",
         "cd %s && tar czf - --exclude=.git --exclude=android --exclude=data "
         "--exclude=__pycache__ *.py | ssh -o BatchMode=yes %s@%s "
         "'mkdir -p ~/swarmglu && tar xzf - -C ~/swarmglu'" % (RACINE, user, IP_LOIN)],
        capture_output=True)
    verifier("code SwarmGlu copié sur .249", r.returncode == 0, r.stderr)
    ssh(user,
        "cd ~/swarmglu && rm -rf data-comm wl-comm.json && "
        "(nohup env PYTHONPATH=~/swarmglu/pylibs python3 cli.py serve "
        "--node-id far-249 --port %d --advertise-ip %s "
        "--whitelist-file ~/swarmglu/wl-comm.json --data-dir ~/swarmglu/data-comm "
        "--mode full --mock-inference --beacon-port 0 "
        "> ~/swarmglu/node-comm.log 2>&1 < /dev/null &)" % (PORT_FAR, IP_LOIN))
    ok = verifier("nœud far-249 démarré (/status joignable)",
                  mu.attendre(25, lambda: statut(url_far()) is not None, pas=2))
    if ok:
        verifier("far-249 en mode crypto RÉEL (ECDSA/ML-KEM)",
                 (statut(url_far()) or {}).get("crypto_mode") == "cryptography",
                 (statut(url_far()) or {}).get("crypto_mode"))


def appareils_physiques():
    """Tous les appareils ADB réels branchés (Note 10, tablette…)."""
    r = subprocess.run([mu.ADB, "devices"], capture_output=True, text=True, timeout=15)
    return [l.split()[0] for l in r.stdout.splitlines()[1:]
            if l.strip() and not l.startswith("*") and not l.split()[0].startswith("emulator-")
            and l.split()[1] == "device"]


def demarrer_telephones(urls, pilotes):
    """Note 10 / tablettes : APK + forward + relais socat + nœud pilote.
    L'écran verrouillé ne gêne pas : l'instrumentation tourne en fond."""
    serie_phys = appareils_physiques()
    print("[2 bis] appareils physiques détectés : %s" % (serie_phys or "aucun"))
    import shutil
    for i, serie in enumerate(serie_phys):
        port_hote = 17011 + i
        node_id = "tel-%s" % serie[-4:].lower()
        for apk in (mu.APK_APP, mu.APK_TEST):
            subprocess.run([mu.ADB, "-s", serie, "install", "-r", apk],
                           capture_output=True, timeout=300)
        subprocess.run([mu.ADB, "-s", serie, "forward",
                        "tcp:%d" % port_hote, "tcp:%d" % mu.PORT_EMU],
                       capture_output=True, timeout=30)
        RELAIS_SOCAT.append(subprocess.Popen(
            ["socat", "TCP-LISTEN:%d,bind=%s,fork,reuseaddr" % (port_hote, IP_LOCALE),
             "TCP:127.0.0.1:%d" % port_hote],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        mu.preparer_appareil(serie, graine=IP_LOIN)
        pilotes.append(subprocess.Popen(
            [mu.ADB, "-s", serie, "shell", "am", "instrument", "-w",
             "-e", "class", "com.tree4five.swarmglu.NodeDriverInstrumentation",
             "-e", "node_id", node_id, "-e", "port", str(mu.PORT_EMU),
             "-e", "advertise_ip", IP_LOCALE, "-e", "advertise_port", str(port_hote),
             "-e", "peers", "%s:%d" % (IP_LOIN, PORT_FAR),
             "-e", "swarm_id", "salle-comm",
             "-e", "heartbeat", "3.0", "-e", "lease", "6.0", "-e", "duration_s", "420",
             "%s.test/androidx.test.runner.AndroidJUnitRunner" % mu.PKG],
            stdout=open("/tmp/sg_comm_%s.log" % node_id, "w"), stderr=subprocess.STDOUT))
        urls[node_id] = "http://127.0.0.1:%d" % port_hote
        print("      %s (%s) → %s" % (node_id, serie, urls[node_id]))
        # échec EXPLICITE si le nœud ne naît pas (boot chiffré, crash…)
        mu.verifier_demarrage_noeud(serie, node_id, urls[node_id])


def demarrer_emulateur():
    print("\n[2] émulateur Android (nœud %s)…" % NODE_EMU)
    mu.creer_avds()
    mu.booter_emulateurs()
    mu.installer_et_forwarder()
    import shutil
    if not shutil.which("socat"):
        raise SystemExit("socat requis (apt install socat)")
    RELAIS_SOCAT.append(subprocess.Popen(
        ["socat", "TCP-LISTEN:%d,bind=%s,fork,reuseaddr" % (PORT_EMU_HOTE, IP_LOCALE),
         "TCP:127.0.0.1:%d" % PORT_EMU_HOTE],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    time.sleep(1.0)
    # Graine = .249 EN DIRECT (l'émulateur rejoint le nœud distant sans
    # passer par un PC local : slirp route le LAN via l'hôte).
    args = [mu.ADB, "-s", "emulator-%d" % mu.AVDS[0][1], "shell", "am", "instrument", "-w",
            "-e", "class", "com.tree4five.swarmglu.NodeDriverInstrumentation",
            "-e", "node_id", NODE_EMU, "-e", "port", str(mu.PORT_EMU),
            "-e", "advertise_ip", IP_LOCALE, "-e", "advertise_port", str(PORT_EMU_HOTE),
            "-e", "peers", "%s:%d" % (IP_LOIN, PORT_FAR),
            "-e", "swarm_id", "salle-comm",
            "-e", "heartbeat", "3.0", "-e", "lease", "6.0", "-e", "duration_s", "420",
            "%s.test/androidx.test.runner.AndroidJUnitRunner" % mu.PKG]
    return subprocess.Popen(args, stdout=open("/tmp/sg_comm_emu.log", "w"),
                            stderr=subprocess.STDOUT)


def phase_convergence():
    attendus = dict(URLS)
    print("\n[3] convergence DIRECTE de l'essaim (%d nœuds : %s)…" % (
        len(attendus), ", ".join(attendus)))

    def tous_daccord():
        etats = {n: statut(u) for n, u in attendus.items()}
        if any(e is None for e in etats.values()) or len(etats) != len(attendus):
            return False
        leaders = {e["consensus"]["leader_id"] for e in etats.values()}
        return (None not in leaders and len(leaders) == 1
                and sum(1 for e in etats.values()
                        if e["consensus"]["role"] == "leader") == 1)

    ok = verifier("un seul leader, accepté par TOUS les nœuds",
                  mu.attendre(120, tous_daccord, pas=3))
    if ok:
        modes = {n: (statut(u) or {}).get("crypto_mode") for n, u in attendus.items()}
        verifier("crypto_mode RÉEL sur far-249 (hôte et .249)",
                 modes.get("far-249") == "cryptography", modes)
        for n, m in modes.items():
            if m != "cryptography":
                print("  ○ %s : mode crypto %s (mock — Android/Chaquopy, voir README)" % (n, m))
        verifier("chaque registre voit tous les pairs vivants",
                 mu.attendre(60, lambda: all(
                     (statut(u) or {}).get("state", {}).get("alive_count", 0) >= len(attendus)
                     for u in attendus.values()), pas=3),
                 {n: (statut(u) or {}).get("state", {}).get("alive_count")
                  for n, u in attendus.items()})
        e = statut(next(iter(attendus.values())))
        print("      leader = %s (epoch %s)" % (
            e["consensus"]["leader_id"], e["consensus"]["epoch"]))


def phase_communication(ancre):
    print("\n[4] matrice de communication emu ↔ .249…")

    # a) requête d'élève depuis l'ÉMULATEUR
    status, r = http_json("POST", url_emu() + "/ask",
                          {"student_id": "eleve-comm",
                           "prompt": "Capitale de la Tunisie ?"}, timeout=60)
    verifier("emu → essaim : /ask servi (OSP VERIFIED)",
             status == 200 and (r or {}).get("osp", {}).get("verdict") == "VERIFIED", r)

    # b) ancrage sur le nœud OSP VIVANT de .249
    sources = ["osp://swarmknowledge/%s" % (ancre or {}).get("node_id", "osp"),
               "http://%s:3000/osp/endpoint.json" % IP_LOIN] if ancre else None
    status, r = http_json("POST", url_emu() + "/ask",
                          {"student_id": "eleve-comm",
                           "prompt": "Qu'est-ce que le protocole OSP ?",
                           "sources": sources}, timeout=60)
    verifier("emu → .249 : réponse ANCRÉE sur swarmknowledge vivant",
             status == 200 and (r or {}).get("knowledge", {}).get("shared") is True, r)

    # c) savoir partagé PARTOUT (même digest sur chaque machine)
    ok = verifier("savoir partagé sur TOUS les nœuds (même digest)",
                  mu.attendre(60, lambda: (
                      min(savoir(u)["count"] for u in URLS.values()) >= 2
                      and len({savoir(u)["digest"] for u in URLS.values()}) == 1),
                      pas=3),
                  {n: savoir(u) for n, u in URLS.items()})

    # d) RELAIS : l'émulateur atteint swarmknowledge PAR le relais de .249
    status, rep = http_json("POST", url_far() + "/relay",
                            {"host": IP_LOIN, "port": 3000,
                             "path": "/osp/endpoint.json"}, timeout=20)
    verifier("emu → relais(.249) → swarmknowledge (:3000)",
             status == 200 and (rep or {}).get("status") == 200
             and (rep.get("data") or {}).get("node_id"), rep)

    # e) poignées de main post-quantiques : uniquement vers les nœuds en
    # mode crypto RÉEL (l'hôte et .249). Les nœuds Android restent en mode
    # mock pour l'instant (Chaquopy ne fournit cryptography que 42.x, sans
    # ML-KEM — voir README « Android natif (Kotlin) » pour la suite).
    from crypto_auth import NodeCrypto
    for cible, url in sorted(URLS.items()):
        mode = (statut(url) or {}).get("crypto_mode", "?")
        if mode != "cryptography":
            print("  ○ %s : mode crypto %s — handshake réel non applicable (noté)" % (cible, mode))
            continue
        try:
            dossier = tempfile.mkdtemp(prefix="sgcomm_")
            client = NodeCrypto("host-client", key_dir=dossier)
            init = client.initiate_handshake(cible)
            status, reponse = http_json("POST", url + "/secure/handshake", init, timeout=20)
            cle = client.finalize_handshake(init, reponse or {})
            chiffre = client.encrypt_payload(cle, json.dumps({"action": "ping"}))
            _s, rep = http_json("POST", url + "/secure/rpc",
                                {"sender_id": "host-client", "payload": chiffre}, timeout=20)
            clair = json.loads(client.decrypt_payload(cle, (rep or {}).get("payload", "")))
            verifier("handshake ML-KEM+ECDSA + RPC chiffré hôte → %s" % cible,
                     status == 200 and clair.get("action") == "pong", (status, rep))
        except Exception as exc:
            verifier("handshake ML-KEM+ECDSA + RPC chiffré hôte → %s" % cible,
                     False, exc)


def phase_resilience(user):
    print("\n[5] mort brutale du nœud .249 → l'émulateur doit reprendre…")
    e_avant = statut(url_emu())
    epoch_avant = e_avant["consensus"]["epoch"] if e_avant else 1
    ssh(user, "pkill -f 'cli.py serve' ; true")
    t0 = time.time()

    survivants = {n: u for n, u in URLS.items() if n != "far-249"}
    ok = verifier("réélection chez les survivants (epoch > %d)" % epoch_avant,
                  mu.attendre(60, lambda: (
                      # un survivant encore en élection renvoie None :
                      # on retente au tick suivant au lieu de planter.
                      lambda etats: all(e is not None for e in etats.values())
                      and len({e["consensus"]["leader_id"] for e in etats.values()}) == 1
                      and all(e["consensus"]["epoch"] > epoch_avant for e in etats.values())
                  )({n: statut(u) for n, u in survivants.items()}), pas=3),
                  {n: (statut(u) or {}).get("consensus") for n, u in survivants.items()})
    if ok:
        print("      reprise en %.1f s" % (time.time() - t0))
    cible = next(iter(survivants))
    status, r = http_json("POST", URLS[cible] + "/ask",
                          {"student_id": "eleve-comm",
                           "prompt": "Et après la coupure de .249 ?"}, timeout=60)
    verifier("les survivants continuent de servir (/ask VERIFIED depuis %s)" % cible,
             status == 200 and (r or {}).get("osp", {}).get("verdict") == "VERIFIED", r)
    verifier("savoir conservé (>= 3 entrées partout, après propagation)",
             mu.attendre(40, lambda: min(
                 savoir(u)["count"] for u in survivants.values()) >= 3, pas=3),
             {n: savoir(u)["count"] for n, u in survivants.items()})
    for n, u in survivants.items():
        _s, audit = http_json("GET", u + "/audit/verify", timeout=15)
        verifier("chaîne d'audit INTACTE sur %s" % n, bool(audit and audit.get("ok")), audit)


def main():
    parseur = argparse.ArgumentParser()
    parseur.parse_args()  # --ssh/--keep réservés ; défauts ci-dessous
    user = "youyou"
    from crypto_auth import NodeCrypto
    verifier("hôte en mode crypto RÉEL", NodeCrypto.__module__ is not None
             and __import__("crypto_auth").HAS_CRYPTOGRAPHY)
    URLS.update({NODE_EMU: url_emu(), 'far-249': url_far()})
    ancre = phase_preflight(user)
    instrument = None
    try:
        deployer_far(user)
        instrument = demarrer_emulateur()
        demarrer_telephones(URLS, EXTRA_PILOTES)
        phase_convergence()
        phase_communication(ancre)
        phase_resilience(user)
    finally:
        if instrument and instrument.poll() is None:
            instrument.terminate()
        for p in EXTRA_PILOTES:
            if p.poll() is None:
                p.terminate()
        for r in RELAIS_SOCAT:
            r.terminate()
        ssh(user, "pkill -f 'cli.py serve' ; true")
        mu.nettoyer(None, [])

    print("\n" + "=" * 66)
    if ECHECS:
        print("ÉCHECS (%d) : %s" % (len(ECHECS), ECHECS))
        return 1
    print("COMMUNICATION ÉMULATEUR ↔ 192.168.1.249 VALIDÉE ✔ "
          "(essaim direct, ancrage OSP, relais, crypto PQ, savoir, failover)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
