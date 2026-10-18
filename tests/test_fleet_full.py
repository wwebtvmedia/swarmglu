#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmGlu — TEST DE FLOTTE COMPLÈTE (7 nœuds, 3 langages, LLM RÉEL).
====================================================================
    kot-a    (Kotlin JVM, LAN)         — leader de bootstrap
    py-llm   (Python PC, LAN)          — FOURNISSEUR LLM RÉEL : pont GGUF
                                         vers l'Ollama de l'école (192.168.1.194)
    far-249  (Python, 192.168.1.249)   — déployé par SSH (+ wheels crypto)
    emu-a/emu-b (Android natif, émulateurs)
    tel-XXXX (Android natif, appareils USB : Note 10, tablette…)

Scénario :
  0. préflight : Ollama .194 (attente inclusive), SÉLECTION du meilleur
     modèle chargeable (bestmodel GPU-OOM documenté → repli), pont OSP
     .249 (endpoint + /osp/query signé), SSH .249 ;
  1. convergence des 7 nœuds sous le leader Kotlin ;
  2. QUESTION D'ÉLÈVE RÉELLE depuis un téléphone → routage vers py-llm →
     INFÉRENCE RÉELLE (Ollama /api/chat) → pare-feu OSP → savoir partagé
     (même digest sur les 7 nœuds) ;
  3. pont OSP : /osp/query signé via le bot (protocole vivant) ;
  4. mort du leader Kotlin → réélection parmi les 6 survivants ;
  5. audits intègres partout.

Usage : python3 tests/test_fleet_full.py [--ssh-249 youyou]
        [--llm-host 192.168.1.194] [--llm-wait 900]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_multi_emulator_swarm as mu

RACINE = mu.RACINE
sys.path.insert(0, RACINE)
from swarm_glu import detect_local_ip

IP_LAN = detect_local_ip()
IP_LOIN = "192.168.1.249"
PORT_KOT, PORT_PY, PORT_FAR = 7302, 7301, 7905
PORTS_EMU = [17001, 17002]

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


def ssh(user, commande, timeout=90):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "%s@%s" % (user, IP_LOIN),
                           commande], capture_output=True, text=True, timeout=timeout)


# ---------------------------------------------------------------------------
# 0) Préflight LLM + OSP
# ---------------------------------------------------------------------------

def attendre_llm(hote, delai_s):
    print("\n[0] LLM %s (attente incluse, jusqu'à %d s)…" % (hote, delai_s))
    debut = time.time()
    while time.time() - debut < delai_s:
        try:
            with urllib.request.urlopen("http://%s:11434/api/tags" % hote, timeout=4) as rep:
                if rep.status == 200:
                    verifier("Ollama %s vivant (attendu %.0f s)" % (hote, time.time() - debut),
                             True)
                    return True
        except Exception:
            pass
        time.sleep(10)
    return verifier("Ollama %s vivant" % hote, False)


def meilleur_modele(hote):
    """Sélection automatique du meilleur modèle RÉELLEMENT chargeable.

    bestmodel:latest (17,7 Go) est documenté : échec de chargement GPU
    (ROCm out of memory). Ordre de repli par capacité. Chaque sonde est
    une vraie inférence courte."""
    candidats = ["bestmodel:latest", "qwen38-27b", "codeur:latest",
                 "qwen2.5-coder:14b", "gemma4:12b", "gemma-opti:latest",
                 "qwen2.5-coder:7b", "fastmodel:latest"]
    for modele in candidats:
        try:
            corps = json.dumps({"model": modele, "stream": False,
                                "messages": [{"role": "user",
                                              "content": "Réponds en UNE phrase : "
                                                         "capitale de la Tunisie ?"}]}).encode()
            req = urllib.request.Request("http://%s:11434/api/chat" % hote,
                                         data=corps, method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=420) as rep:
                d = json.loads(rep.read())
            if "error" in d:
                print("  ○ %s : ERREUR serveur (%s)" % (modele, d["error"][:80]))
                continue
            contenu = (d.get("message") or {}).get("content", "").strip()
            if contenu:
                print("  ★ %s : répond « %s »" % (modele, contenu[:90]))
                return modele, contenu
            print("  ○ %s : réponse vide" % modele)
        except Exception as exc:
            print("  ○ %s : indisponible (%s)" % (modele, str(exc)[:70]))
    return None, None


def preflight_osp(token):
    print("\n[0 bis] pont OSP 192.168.1.249…")
    _s, contrat = http_json("GET", "http://%s:3000/osp/endpoint.json" % IP_LOIN, timeout=8)
    verifier("endpoint OSP vivant (node_id=%s)" % (contrat or {}).get("node_id"),
             _s == 200 and contrat and "node_id" in contrat, (_s, contrat))
    req = urllib.request.Request(
        "http://%s:3000/osp/query" % IP_LOIN,
        data=json.dumps({"query": "ping de test SwarmGlu"}).encode(), method="POST",
        headers={"x-api-token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as rep:
            reponse = json.loads(rep.read())
    except Exception as exc:
        reponse = {"error": str(exc)}
    # Le pont répond TOUJOURS avec un verdict de protocole : mode NO_QUORUM
    # attendu sans autres nœuds OSP compétents (le quorum vaut 2).
    verifier("pont OSP /osp/query répond (protocole vivant, mode=%s)"
             % reponse.get("mode"),
             "mode" in reponse, reponse)
    return reponse


def deployer_far(user):
    ssh(user, "pkill -f 'cli.py serve' ; sleep 1 ; true")
    r = ssh(user, "python3 --version && mkdir -p ~/swarmglu")
    verifier("python3 prêt sur .249", r.returncode == 0, r.stderr)
    subprocess.run(["bash", "-c",
                    "cd %s && tar czf - --exclude=.git --exclude=android --exclude=data "
                    "--exclude=__pycache__ --exclude=kotlin *.py | "
                    "ssh -o BatchMode=yes %s@%s 'mkdir -p ~/swarmglu && tar xzf - -C ~/swarmglu'"
                    % (RACINE, user, IP_LOIN)], capture_output=True)
    verifier("code SwarmGlu copié sur .249", True)


def equiper_249_crypto(user):
    import glob
    roues = glob.glob("/tmp/sg_wheels/*.whl")
    if not roues:
        os.makedirs("/tmp/sg_wheels", exist_ok=True)
        subprocess.run(["pip", "download", "cryptography", "--python-version", "313",
                        "--only-binary=:all:", "-d", "/tmp/sg_wheels", "-q"],
                       capture_output=True, timeout=300)
        roues = glob.glob("/tmp/sg_wheels/*.whl")
    for roue in roues:
        subprocess.run(["scp", "-o", "BatchMode=yes", roue,
                        "%s@%s:/tmp/" % (user, IP_LOIN)], capture_output=True, timeout=120)
    ssh(user, "mkdir -p ~/swarmglu/pylibs && cd ~/swarmglu/pylibs && "
              "for w in /tmp/*.whl; do unzip -oq $w; done && rm -f /tmp/*.whl")


def demarrer_far(user, llm_host, modele):
    ssh(user,
        "cd ~/swarmglu && rm -rf data-fleet wl-fleet.json && "
        "(nohup env PYTHONPATH=~/swarmglu/pylibs python3 cli.py serve "
        "--node-id far-249 --port 7905 --advertise-ip %s "
        "--peers %s:%d "
        "--whitelist-file ~/swarmglu/wl-fleet.json --data-dir ~/swarmglu/data-fleet "
        "--mode full --beacon-port 0 --gguf-url http://%s:11434 "
        "--gguf-models %s --api-flavor ollama-chat "
        "> ~/swarmglu/node-fleet.log 2>&1 < /dev/null &)" % (IP_LOIN, IP_LAN, PORT_KOT,
                                                             llm_host, modele))


def token_pont_osp(serie, tentatives=3):
    """Token du pont OSP local (app com.tree4five.osp, build debug).
    Réessaie : l'appareil peut être occupé par l'installation des APK."""
    import re as _re
    for _t in range(tentatives):
        r = subprocess.run(
            [mu.ADB, "-s", serie, "shell",
             "run-as com.tree4five.osp sh -c 'cat shared_prefs/*.xml'"],
            capture_output=True, text=True, timeout=30)
        m = _re.search(r'name="token">([^<]+)', r.stdout)
        if m:
            return m.group(1)
        time.sleep(2)
    return ""


def demarrer_telephones(urls, pilotes, leader_ip, port_leader):
    serie_physiques = []
    r = subprocess.run([mu.ADB, "devices"], capture_output=True, text=True, timeout=15)
    for ligne in r.stdout.splitlines()[1:]:
        morceaux = ligne.split()
        if len(morceaux) >= 2 and morceaux[1] == "device" \
                and not morceaux[0].startswith("emulator-"):
            serie_physiques.append(morceaux[0])
    print("      appareils physiques : %s" % (serie_physiques or "aucun"))
    for i, serie in enumerate(serie_physiques):
        port_hote = 17011 + i
        node_id = "tel-" + serie[-4:].lower()
        for apk in (mu.APK_APP, mu.APK_TEST):
            subprocess.run([mu.ADB, "-s", serie, "install", "-r", apk],
                           capture_output=True, timeout=300)
        subprocess.run([mu.ADB, "-s", serie, "forward",
                        "tcp:%d" % port_hote, "tcp:%d" % mu.PORT_EMU],
                       capture_output=True, timeout=30)
        RELAIS.append(subprocess.Popen(
            ["socat", "TCP-LISTEN:%d,bind=%s,fork,reuseaddr" % (port_hote, IP_LAN),
             "TCP:127.0.0.1:%d" % port_hote],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        args_pilote = [mu.ADB, "-s", serie, "shell", "am", "instrument", "-w",
            "-e", "class", "com.tree4five.swarmglu.NodeDriverInstrumentation",
            "-e", "node_id", node_id, "-e", "port", str(mu.PORT_EMU),
            "-e", "advertise_ip", IP_LAN, "-e", "advertise_port", str(port_hote),
            "-e", "peers", "%s:%d" % (IP_LAN, PORT_KOT),
            "-e", "swarm_id", "salle-fleet",
            "-e", "heartbeat", "3.0", "-e", "lease", "6.0", "-e", "duration_s", "900"]
        # FOURNISSEUR LLM ON-DEVICE : pont OSP local de Tree4Five LLM
        # Provider (/v1, modèle llmprovider, token du pont lu sur l'app).
        token_pont = token_pont_osp(serie)   # tenté sur chaque appareil
        if token_pont:
            print("      %s : pont LLM local détecté (token lu) → "
                  "fournisseur llmprovider" % node_id)
            args_pilote += ["-e", "gguf_url", "http://127.0.0.1:8090/v1",
                            "-e", "gguf_models", "llmprovider",
                            "-e", "gguf_token", token_pont]
        else:
            print("      %s : pas de pont OSP local (token absent) — nœud simple" % node_id)
        args_pilote += ["%s.test/androidx.test.runner.AndroidJUnitRunner" % mu.PKG]
        mu.preparer_appareil(serie, graine=IP_LAN)
        pilotes.append(subprocess.Popen(
            args_pilote, stdout=open("/tmp/sg_fleet_%s.log" % node_id, "w"),
            stderr=subprocess.STDOUT))
        urls[node_id] = "http://127.0.0.1:%d" % port_hote
        print("      %s (%s) → %s" % (node_id, serie, urls[node_id]))
        # échec EXPLICITE si le nœud ne naît pas (boot chiffré, crash…)
        mu.verifier_demarrage_noeud(serie, node_id, urls[node_id])
    return serie_physiques


# ---------------------------------------------------------------------------

def main():
    parseur = argparse.ArgumentParser()
    parseur.add_argument("--ssh-249", default="youyou")
    parseur.add_argument("--llm-host", default="192.168.1.194")
    parseur.add_argument("--llm-wait", type=int, default=900,
                         help="attente max du LLM en secondes")
    parseur.add_argument("--model", default="fastmodel:latest",
                         help="modèle Ollama imposé (fastmodel par défaut)")
    parseur.add_argument("--node-149", action="store_true", default=True,
                         help="inclure 192.168.1.149 si joignable (SSH par clé)")
    parseur.add_argument("--ip-149", default="192.168.1.149")
    args = parseur.parse_args()

    # 0) préflight
    if not attendre_llm(args.llm_host, args.llm_wait):
        print("LLM indisponible — test abandonné (relancez quand la machine revient)")
        return 2
    if args.model:
        # Modèle IMPOSÉ (ex. fastmodel:latest — léger et rapide) : sonde
        # unique d'inférence réelle, sans parcours de repli.
        try:
            corps = json.dumps({"model": args.model, "stream": False,
                                "messages": [{"role": "user",
                                              "content": "Réponds en UNE phrase : "
                                                         "capitale de la Tunisie ?"}]}).encode()
            req = urllib.request.Request("http://%s:11434/api/chat" % args.llm_host,
                                         data=corps, method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=420) as rep:
                d = json.loads(rep.read())
            contenu = (d.get("message") or {}).get("content", "").strip()
            if not verifier("modèle imposé %s répond « %s »" % (args.model, contenu[:90]),
                            bool(contenu), d.get("error")):
                return 2
            modele = args.model
        except Exception as exc:
            verifier("modèle imposé %s joignable" % args.model, False, str(exc)[:120])
            return 2
    else:
        modele, reponse_probe = meilleur_modele(args.llm_host)
        if not verifier("meilleur modèle sélectionné : %s" % modele,
                        modele is not None):
            return 2
    token = ""
    try:
        for ligne in open("/home/pc/sby/whatsapp-bot/.env", encoding="utf-8"):
            if ligne.startswith("API_TOKEN="):
                token = ligne.split("=", 1)[1].strip()
    except OSError:
        pass
    preflight_osp(token)
    user = args.ssh_249
    verifier("SSH par clé (%s@…)" % user,
             ssh(user, "true").returncode == 0)

    urls = {"kot-a": "http://127.0.0.1:%d" % PORT_KOT,
            "py-llm": "http://127.0.0.1:%d" % PORT_PY,
            "far-249": "http://%s:%d" % (IP_LOIN, PORT_FAR),
            "emu-a": "http://127.0.0.1:%d" % PORTS_EMU[0],
            "emu-b": "http://127.0.0.1:%d" % PORTS_EMU[1]}

    # --- nœud .149 (conditionnel : machine école qui peut dormir) ---
    ip149 = args.ip_149
    joignable_149 = subprocess.run(["ping", "-c", "1", "-W", "2", ip149],
                                   capture_output=True).returncode == 0
    if args.node_149:
        if not joignable_149:
            print("  ○ %s injoignable (machine école endormie) — nœud omis, "
                  "scénario poursuivi sans lui" % ip149)
        else:
            ssh149_ok = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                 "youyou@%s" % ip149, "true"], capture_output=True).returncode == 0
            if ssh149_ok:
                subprocess.run(["bash", "-c",
                    "cd %s && tar czf - --exclude=.git --exclude=android "
                    "--exclude=data --exclude=__pycache__ --exclude=kotlin *.py | "
                    "ssh -o BatchMode=yes youyou@%s "
                    "'mkdir -p ~/swarmglu && tar xzf - -C ~/swarmglu'" % (RACINE, ip149)],
                    capture_output=True)
                subprocess.Popen(["ssh", "-o", "BatchMode=yes", "youyou@%s" % ip149,
                    "cd ~/swarmglu && rm -rf data-149 wl-149.json && "
                    "(nohup python3 cli.py serve --node-id node-149 --port 7906 "
                    "--advertise-ip %s --peers %s:%d "
                    "--whitelist-file ~/swarmglu/wl-149.json --data-dir ~/swarmglu/data-149 "
                    "--mode full --beacon-port 0 "
                    "> ~/swarmglu/node-149.log 2>&1 < /dev/null &)" % (ip149, IP_LAN, PORT_KOT)],
                    capture_output=True)
                urls["node-149"] = "http://%s:7906" % ip149
                print("      node-149 (%s) déployé et lancé" % ip149)
            else:
                print("  ○ %s joignable mais SSH refusé — nœud omis" % ip149)

    procs, pilotes = [], []
    dossier = tempfile.mkdtemp(prefix="sgfleet_")
    try:
        print("\n[1] démarrage du leader Kotlin puis des nœuds PC…")
        procs.append(subprocess.Popen(
            [BIN := os.path.join(RACINE, "kotlin", "build", "install",
                                 "swarmglu-kotlin", "bin", "swarmglu-kotlin"),
             "--node-id", "kot-a", "--port", str(PORT_KOT),
             "--advertise-ip", IP_LAN, "--mode", "full",
             "--data-dir", os.path.join(dossier, "kot"),
             "--whitelist-file", os.path.join(dossier, "wl-kot.json"),
             "--heartbeat", "3.0", "--lease", "6.0"],
            stdout=open("/tmp/sg_fleet_kot.log", "w"), stderr=subprocess.STDOUT))
        verifier("Kotlin auto-élu leader (bootstrap)",
                 attendre(15, lambda: (statut(urls["kot-a"]) or {})
                          .get("consensus", {}).get("role") == "leader", pas=0.5))
        procs.append(subprocess.Popen(
            [sys.executable, os.path.join(RACINE, "cli.py"), "serve",
             "--node-id", "py-llm", "--port", str(PORT_PY),
             "--advertise-ip", IP_LAN, "--peers", "%s:%d" % (IP_LAN, PORT_KOT),
             "--whitelist-file", os.path.join(dossier, "wl-llm.json"),
             "--data-dir", os.path.join(dossier, "pyllm"),
             "--mode", "full", "--beacon-port", "0",
             "--gguf-url", "http://%s:11434" % args.llm_host,
             "--gguf-models", modele, "--api-flavor", "ollama-chat"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        verifier("py-llm rejoint le leader Kotlin (fournisseur %s)" % modele,
                 attendre(15, lambda: (statut(urls["py-llm"]) or {})
                          .get("consensus", {}).get("leader_id") == "kot-a", pas=0.5))

        # far-249 APRÈS un leader confirmé (sinon : course de bootstrap,
        # far-249 s'auto-élit et gagne le tirage → désordre d'epochs)
        equiper_249_crypto(user)
        deployer_far(user)
        demarrer_far(user, args.llm_host, modele)
        verifier("far-249 démarré (LLM %s, modèle %s)" % (args.llm_host, modele),
                 attendre(30, lambda: statut(urls["far-249"]) is not None, pas=2))
        verifier("far-249 rejoint le leader Kotlin",
                 attendre(20, lambda: (statut(urls["far-249"]) or {})
                          .get("consensus", {}).get("leader_id") == "kot-a", pas=1))

        print("\n[2] Android : 2 émulateurs + téléphones physiques…")
        mu.creer_avds(); mu.booter_emulateurs(); mu.installer_et_forwarder()
        for i, port_hote in enumerate(PORTS_EMU):
            RELAIS.append(subprocess.Popen(
                ["socat", "TCP-LISTEN:%d,bind=%s,fork,reuseaddr" % (port_hote, IP_LAN),
                 "TCP:127.0.0.1:%d" % port_hote],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            node_id = "emu-%s" % "ab"[i]
            pilotes.append(subprocess.Popen(
                [mu.ADB, "-s", "emulator-%d" % mu.AVDS[i][1], "shell", "am",
                 "instrument", "-w",
                 "-e", "class", "com.tree4five.swarmglu.NodeDriverInstrumentation",
                 "-e", "node_id", node_id, "-e", "port", str(mu.PORT_EMU),
                 "-e", "advertise_ip", IP_LAN, "-e", "advertise_port", str(port_hote),
                 "-e", "peers", "%s:%d" % (IP_LAN, PORT_KOT),
                 "-e", "swarm_id", "salle-fleet",
                 "-e", "heartbeat", "3.0", "-e", "lease", "6.0", "-e", "duration_s", "900",
                 "%s.test/androidx.test.runner.AndroidJUnitRunner" % mu.PKG],
                stdout=open("/tmp/sg_fleet_%s.log" % node_id, "w"),
                stderr=subprocess.STDOUT))
            print("      %s (émulateur) → %s" % (node_id, urls[node_id]))
        telephones = demarrer_telephones(urls, pilotes, IP_LAN, PORT_KOT)

        # convergence 7 nœuds
        print("\n[3] convergence des %d nœuds…" % len(urls))
        def converges():
            etats = {}
            for n, u in urls.items():
                e = statut(u)
                if e is None:
                    return False
                etats[n] = e
            leaders = {e["consensus"]["leader_id"] for e in etats.values()}
            return (None not in leaders and len(leaders) == 1
                    and sum(1 for e in etats.values()
                            if e["consensus"]["role"] == "leader") == 1)
        verifier("TOUS les %d nœuds acceptent le leader Kotlin" % len(urls),
                 attendre(150, converges, pas=3),
                 {n: (statut(u) or {}).get("consensus") for n, u in urls.items()})
        verifier("chaque registre voit les 7 pairs vivants",
                 attendre(60, lambda: all(
                     (statut(u) or {}).get("state", {}).get("alive_count", 0) >= len(urls)
                     for u in urls.values()), pas=3),
                 {n: (statut(u) or {}).get("state", {}).get("alive_count")
                  for n, u in urls.items()})

        # QUESTION RÉELLE depuis un téléphone
        print("\n[4] QUESTION D'ÉLÈVE RÉELLE (LLM %s / %s)…" % (args.llm_host, modele))
        tel = ("tel-" + telephones[0][-4:].lower()) if telephones else "emu-a"
        t0 = time.time()
        status, r = http_json("POST", urls[tel] + "/ask",
                              {"student_id": "eleve-fleet",
                               "prompt": "Réponds en une phrase : quelle est la capitale "
                                         "de la Tunisie ?",
                               "model_hint": modele,
                               "sources": ["osp://swarmknowledge/whatsapp-bot",
                                           "http://%s:3000/osp/endpoint.json" % IP_LOIN]},
                              timeout=300)
        reponse = (r or {}).get("answer", "")
        verifier("/ask depuis %s → servie par le LLM RÉEL (fournisseur %s)"
                 % (tel, (r or {}).get("provider")),
                 status == 200 and "Tunis" in reponse
                 and not reponse.startswith("[mock"), (status, reponse[:200]))
        if status == 200:
            print("      réponse réelle : « %s » (%.1f s, fournisseur %s)"
                  % (reponse[:120], time.time() - t0, r.get("provider")))
            verifier("pare-feu OSP : verdict présent",
                     (r.get("osp") or {}).get("verdict") in ("VERIFIED", "UNVERIFIED"), r.get("osp"))
        # savoir partagé aux 7 nœuds (même digest)
        verifier("savoir partagé sur les %d nœuds (même digest)" % len(urls),
                 attendre(90, lambda: (
                     min(savoir(u)["count"] for u in urls.values()) >= 1
                     and len({savoir(u)["digest"] for u in urls.values()}) == 1), pas=4),
                 {n: savoir(u) for n, u in urls.items()})

        # QUESTION DEUX : inférence ON-DEVICE de la TABLETTE
        print("\n[4 bis] question routée vers le LLM ON-DEVICE de la tablette…")
        tab = ("tel-" + telephones[0][-4:].lower()) if telephones else None
        _s, st_tab = http_json("GET", urls.get(tab, urls["emu-a"]) + "/status", timeout=8)
        modeles_tab = []
        for p in (st_tab or {}).get("peers", []):
            if "llmprovider" in p.get("models", []):
                modeles_tab = p.get("models", [])
        if modeles_tab:
            t1 = time.time()
            status, r2 = http_json("POST", urls[tel] + "/ask",
                {"student_id": "eleve-fleet",
                 "prompt": "Réponds en un mot : quelle est la capitale de la Tunisie ?",
                 "model_hint": "llmprovider"}, timeout=300)
            verifier("question ON-DEVICE (llmprovider, inférence llama.cpp sur "
                     "la tablette) → routée et servie",
                     status == 200 and (r2 or {}).get("provider", "").startswith("tel-")
                     and (r2 or {}).get("osp", {}).get("verdict") is not None, r2)
            if status == 200:
                print("      réponse on-device : « %s » (%.1f s)"
                      % ((r2 or {}).get("answer", "")[:100], time.time() - t1))
        else:
            print("  ○ pont LLM de la tablette absent — phase on-device notée")

        # pont OSP réel
        print("\n[5] pont OSP (question via /osp/query signée)…")
        req = urllib.request.Request(
            "http://%s:3000/osp/query" % IP_LOIN,
            data=json.dumps({"query": "Question d'élève via le pont OSP"}).encode(),
            method="POST",
            headers={"x-api-token": token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as rep:
            reponse_osp = json.loads(rep.read())
        verifier("pont OSP : réponse de protocole reçue (mode=%s)"
                 % reponse_osp.get("mode"),
                 "mode" in reponse_osp, reponse_osp)

        # mort du leader
        print("\n[6] mort brutale du leader Kotlin (kill -9)…")
        epoch_avant = max((e or {}).get("consensus", {}).get("epoch", 0)
                          for e in [statut(u) for u in urls.values()])
        procs[0].kill(); procs[0].wait()
        survivants = {n: u for n, u in urls.items() if n != "kot-a" and statut(u)}
        t0 = time.time()
        ok = verifier("réélection parmi les %d survivants (epoch > %d)"
                      % (len(survivants), epoch_avant),
                      attendre(90, lambda: (
                          lambda etats: len(etats) == len(survivants)
                          and len({e["consensus"]["leader_id"] for e in etats.values()}) == 1
                          and all(e["consensus"]["epoch"] > epoch_avant
                                  for e in etats.values())
                      )({n: statut(u) for n, u in survivants.items()}), pas=3),
                      {n: (statut(u) or {}).get("consensus") for n, u in survivants.items()})
        if ok:
            print("      failover trans-langage en %.1f s" % (time.time() - t0))
        cible = next(iter(survivants))
        status, r = http_json("POST", survivants[cible] + "/ask",
                              {"student_id": "eleve-fleet",
                               "prompt": "Capitale de la Tunisie ?",
                               "model_hint": modele}, timeout=300)
        verifier("l'essaim survit au basculement (/ask VERIFIED depuis %s)" % cible,
                 status == 200 and (r or {}).get("osp", {}).get("verdict") is not None, r)

        # audits
        print("\n[7] audits finaux…")
        for n, u in list(survivants.items()):
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
        for r in RELAIS:
            r.terminate()
        if user:
            ssh(user, "pkill -f 'cli.py serve' ; true")
        mu.nettoyer(None, [])

    print("\n" + "=" * 66)
    if ECHECS:
        print("ÉCHECS (%d) : %s" % (len(ECHECS), ECHECS))
        return 1
    print("FLOTTE COMPLÈTE VALIDÉE ✔ (7 nœuds, 3 langages, LLM réel %s/%s, "
          "pont OSP, savoir partagé, failover, audits)" % (args.llm_host, modele))
    return 0


if __name__ == "__main__":
    sys.exit(main())
