#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmGlu — cli.py
=================
Interface en ligne de commande complète.

Usage principal (cahier des charges) :

    python cli.py serve --port 7001 --node-id pc-salle1 \\
        --whitelist-file whitelist.json --mode full

Sous-commandes :

    serve      démarre un nœud de l'essaim (options --port, --node-id,
               --whitelist-file, --mode, --peers, --neighbor, ...)
    status     interroge /status d'un nœud et l'affiche lisiblement
    ask        envoie une requête d'élève (test rapide de routage)
    audit      verify | tail — vérifie/affiche la chaîne de hachage
    whitelist  init | add | remove | list | digest — gère la whitelist
    demo       scénario de résilience automatisé : N nœuds, coupure
               brutale du leader, réélection observée, audit final

Exemples école (réseau Wi-Fi hors-ligne) :

    # 1. Préparer la whitelist sur les postes (copie USB du fichier)
    python cli.py whitelist init --nodes pc-prof,tablette-1,tablette-2,tablette-3

    # 2. Démarrer sur chaque machine (adapter --advertise-ip)
    python cli.py serve --node-id pc-prof --port 7001 --mode full
    python cli.py serve --node-id tablette-1 --port 7001 --mode follower \\
        --peers 192.168.4.10:7001

    # 3. Vérifier la santé de l'essaim et l'audit
    python cli.py status --url http://192.168.4.10:7001
    python cli.py audit verify --data-dir data/pc-prof

    # 4. Test de résilience automatique (3 nœuds locaux, leader tué)
    python cli.py demo --nodes 3
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# Les modules vivent à côté de cli.py : s'assurer qu'ils sont importables
# quel que soit le répertoire courant d'appel.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from security import SecurityManager  # noqa: E402


# ---------------------------------------------------------------------------
# Utilitaires HTTP (client)
# ---------------------------------------------------------------------------

def http_json(method: str, url: str, body: Optional[Dict[str, Any]] = None,
              timeout: float = 10.0) -> Tuple[int, Optional[Dict[str, Any]]]:
    data = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return exc.code, None
    except Exception:
        return 0, None


# ---------------------------------------------------------------------------
# Sous-commande : serve
# ---------------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> int:
    import swarm_glu
    config = swarm_glu.NodeConfig(
        node_id=args.node_id,
        port=args.port,
        mode=args.mode,
        whitelist_file=args.whitelist_file,
        data_dir=args.data_dir,
        swarm_id=args.swarm_id,
        peers=args.peers.split(",") if args.peers else [],
        neighbors=args.neighbor,
        beacon_port=args.beacon_port,
        advertise_ip=args.advertise_ip,
        heartbeat_interval=args.heartbeat_interval,
        lease_timeout=args.lease_timeout,
        allow_join=not args.strict,
        mock_inference=args.mock_inference,
        gguf_url=args.gguf_url,
        gguf_models=args.gguf_models.split(",") if args.gguf_models else [],
        api_flavor=args.api_flavor,
        log_raw_prompts=args.log_raw_prompts,
        log_level=args.log_level,
    )
    swarm_glu.serve(config, block=True)
    return 0


# ---------------------------------------------------------------------------
# Sous-commande : status
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    status, payload = http_json("GET", args.url.rstrip("/") + "/status", timeout=5.0)
    if status != 200 or not payload:
        print("Nœud injoignable (%s) : %s" % (status, args.url))
        return 1
    c = payload.get("consensus", {})
    s = payload.get("state", {})
    print("=" * 64)
    print(" NŒUD        : %s  (%s)" % (payload.get("node_id"), payload.get("addr")))
    print(" ESSAIM      : %s   mode: %s" % (payload.get("swarm_id"), payload.get("mode")))
    print(" RÔLE        : %s   epoch: %s   leader: %s (%s)" % (
        c.get("role"), c.get("epoch"), c.get("leader_id"), c.get("leader_addr")))
    print(" ÉTAT        : digest %.16s…  pairs: %s (%s vivants)" % (
        s.get("digest"), s.get("peer_count"), s.get("alive_count")))
    print(" WHITELIST   : %s entrée(s), digest %.12s…" % (
        payload.get("whitelist_count"), payload.get("whitelist_digest", "")))
    audit = payload.get("audit", {})
    print(" AUDIT       : %s blocs, chaîne %s (dernier hash %.12s…)" % (
        audit.get("entries"), "INTACTE ✓" if audit.get("ok") else "CORROMPUE ✗",
        audit.get("last_hash", "")))
    print("-" * 64)
    print(" PAIRS :")
    for p in payload.get("peers", []):
        mark = "●" if p.get("alive") else "○"
        print("  %s %-18s %-22s %-10s %s" % (
            mark, p.get("node_id"), p.get("addr"), p.get("mode", ""),
            ",".join(p.get("capabilities", [])) or "-"))
    neighbors = payload.get("gateway_neighbors", [])
    if neighbors:
        print("-" * 64)
        print(" ESSAIMS VOISINS (passerelle) :")
        for n in neighbors:
            print("  ◆ %-16s epoch %-4s %s nœud(s) — %s" % (
                n.get("swarm_id"), n.get("epoch"), n.get("node_count"),
                n.get("ambassador_addr")))
    print("=" * 64)
    return 0


# ---------------------------------------------------------------------------
# Sous-commande : ask (requête d'élève de test)
# ---------------------------------------------------------------------------

def cmd_ask(args: argparse.Namespace) -> int:
    body = {"student_id": args.student, "prompt": args.prompt}
    if args.model:
        body["model_hint"] = args.model
    if args.swarm:
        body["swarm_id"] = args.swarm
    status, payload = http_json("POST", args.url.rstrip("/") + "/ask", body, timeout=120.0)
    if status != 200 or not payload:
        print("Échec (%s): %s" % (status, payload))
        return 1
    osp = payload.get("osp", {})
    print("RÉPONSE  : %s" % payload.get("answer"))
    print("MODÈLE   : %s  (fournisseur: %s)" % (payload.get("model"), payload.get("provider")))
    print("SOURCES  : %s" % ", ".join(payload.get("sources", [])) or "-")
    print("OSP v0.6 : g=%s → %s %s" % (
        osp.get("groundedness"), osp.get("verdict"),
        "(admis RAG)" if osp.get("rag_eligible") else "(EXCLU du RAG)"))
    print("LATENCE  : %s s (epoch %s)" % (payload.get("processing_time"), payload.get("epoch")))
    return 0


# ---------------------------------------------------------------------------
# Sous-commande : audit
# ---------------------------------------------------------------------------

def cmd_audit(args: argparse.Namespace) -> int:
    log_path = os.path.join(args.data_dir, "swarm_audit.jsonl")
    wl_path = os.path.join(args.data_dir, "..", "whitelist.json")
    sm = SecurityManager(whitelist_path=wl_path if os.path.exists(wl_path)
                         else args.whitelist_file, log_path=log_path)

    if args.action == "verify":
        report = sm.verify_chain()
        if report["ok"]:
            print("✓ CHAÎNE INTACTE — %d bloc(s) scellé(s)" % report["entries"])
            print("  premier hash : %s" % report["first_hash"])
            print("  dernier hash : %s" % report["last_hash"])
            return 0
        print("✗ CHAÎNE CORROMPUE — ligne %s : %s" % (report.get("line"), report.get("error")))
        return 2

    # audit tail
    n = args.n
    for block in reversed(sm.tail(n)):
        ev = block.get("event", {})
        print("#%4d %-22s %-14s %s  (trait. %ss)" % (
            block.get("seq", 0), block.get("timestamp", ""),
            ev.get("event_type", "?"), ev.get("node_id", "?"),
            block.get("processing_time", 0)))
    return 0


# ---------------------------------------------------------------------------
# Sous-commande : whitelist
# ---------------------------------------------------------------------------

def cmd_whitelist(args: argparse.Namespace) -> int:
    sm = SecurityManager(whitelist_path=args.whitelist_file,
                         log_path=os.path.join(args.data_dir, "swarm_audit.jsonl"))

    if args.action == "init":
        nodes = [n.strip() for n in (args.nodes or "").split(",") if n.strip()]
        for n in nodes:
            sm.add_node(n, approved_by="cli-init")
        print("Whitelist initialisée : %s (%d nœud(s)) — fichier %s"
              % (nodes, len(nodes), args.whitelist_file))
        print("Copiez ce fichier par clé USB sur chaque machine de l'essaim.")

    elif args.action == "add":
        sm.add_node(args.node_id, approved_by="cli")
        print("Nœud '%s' ajouté. Whitelist: %s" % (args.node_id, sm.nodes()))

    elif args.action == "remove":
        if sm.remove_node(args.node_id):
            print("Nœud '%s' retiré." % args.node_id)
        else:
            print("Nœud '%s' absent de la whitelist." % args.node_id)
            return 1

    elif args.action == "list":
        for n in sm.nodes():
            info = sm.whitelist_doc.get("approved", {}).get(n, {})
            print(" • %-20s approuvé par %-14s le %s" % (
                n, info.get("by", "?"), info.get("at", "?")))
        print("Digest : %s" % sm.whitelist_digest())

    elif args.action == "digest":
        print(sm.whitelist_digest())
    return 0


# ---------------------------------------------------------------------------
# Sous-commande : demo (protocole de test de résilience automatisé)
# ---------------------------------------------------------------------------

def cmd_demo(args: argparse.Namespace) -> int:
    """
    Scénario complet du protocole de résilience :

    1. lance N nœuds locaux (127.0.0.1, ports 7101..) avec heartbeats
       accélérés (1 s / bail 2 s) et mock d'inférence ;
    2. attend la stabilisation et identifie le leader (epoch 1) ;
    3. TUE BRUTALEMENT le leader (SIGKILL — équivalent tablette débranchée) ;
    4. observe l'élection automatique du successeur (epoch 2) ;
    5. envoie une requête d'élève pour prouver que l'essaim survit ;
    6. vérifie la chaîne de hachage de CHAQUE nœud (audit).
    """
    cli_path = os.path.abspath(__file__)
    workdir = tempfile.mkdtemp(prefix="swarmglu_demo_")
    base_port = args.base_port
    n = args.nodes
    ids = ["demo-%d" % i for i in range(n)]
    wl_file = os.path.join(workdir, "whitelist.json")

    # Whitelist partagée pré-générée (équivalent de la copie USB).
    wl = SecurityManager(whitelist_path=wl_file,
                         log_path=os.path.join(workdir, "unused.jsonl"))
    for nid in ids:
        wl.add_node(nid, approved_by="demo")

    print("[démo] Répertoire de travail : %s" % workdir)
    print("[démo] Lancement de %d nœuds (heartbeats %.1fs / bail %.1fs)..."
          % (n, args.heartbeat, args.lease))

    procs: Dict[str, subprocess.Popen] = {}
    try:
        for i, nid in enumerate(ids):
            port = base_port + i
            cmd = [sys.executable, cli_path, "serve",
                   "--node-id", nid, "--port", str(port),
                   "--whitelist-file", wl_file,
                   "--data-dir", os.path.join(workdir, nid),
                   "--advertise-ip", "127.0.0.1",
                   "--mode", "full", "--mock-inference",
                   "--beacon-port", "0",           # pas de broadcast en local
                   "--heartbeat-interval", str(args.heartbeat),
                   "--lease-timeout", str(args.lease),
                   "--log-level", "WARNING"]
            if i > 0:
                # Les suivants connaissent le premier (seed statique).
                cmd += ["--peers", "127.0.0.1:%d" % base_port]
            procs[nid] = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)

        urls = {nid: "http://127.0.0.1:%d" % (base_port + i)
                for i, nid in enumerate(ids)}

        def snapshot() -> Dict[str, Dict[str, Any]]:
            out = {}
            for nid, url in urls.items():
                _s, payload = http_json("GET", url + "/status", timeout=3.0)
                if payload:
                    out[nid] = payload
            return out

        # -- phase 1 : stabilisation -----------------------------------
        leader_id, epoch1 = None, 0
        deadline = time.time() + max(20.0, args.lease * 6 + 10)
        while time.time() < deadline:
            snap = snapshot()
            leaders = {nid: p["consensus"]["epoch"] for nid, p in snap.items()
                       if p["consensus"]["role"] == "leader"}
            if len(snap) == n and len(leaders) == 1:
                leader_id = next(iter(leaders))
                epoch1 = leaders[leader_id]
                break
            time.sleep(1.0)
        if not leader_id:
            print("[démo] ✗ PAS DE LEADER ÉLU — échec")
            return 1
        print("[démo] ✓ Leader élu : %s (epoch %d)" % (leader_id, epoch1))
        for nid, p in snapshot().items():
            print("         %s → rôle %s" % (nid, p["consensus"]["role"]))

        # -- phase 2 : coupure brutale du leader ------------------------
        print("[démo] ✂ COUPURE BRUTALE du leader %s (kill -9, équivalent"
              " tablette hors tension)..." % leader_id)
        procs[leader_id].send_signal(signal.SIGKILL)
        procs[leader_id].wait()
        t_kill = time.time()

        # -- phase 3 : observation de l'élection ------------------------
        # On attend un nouvel epoch ET une STABILITÉ (un seul leader,
        # deux relevés consécutifs identiques) : la règle de la plus
        # petite clé referme les co-proclamations transitoires.
        new_leader, epoch2 = None, 0
        deadline = t_kill + args.lease * 5 + 15
        stable_count, stable_leader = 0, None
        while time.time() < deadline:
            snap = snapshot()
            current = None
            for nid, p in snap.items():
                if nid == leader_id:
                    continue
                if p["consensus"]["role"] == "leader" and \
                        p["consensus"]["epoch"] > epoch1:
                    if current is None:
                        current, epoch2 = nid, p["consensus"]["epoch"]
                    else:
                        current = None  # co-leaders transitoires : instable
                        break
            if current and current == stable_leader:
                stable_count += 1
            else:
                stable_leader, stable_count = current, 1
            if current and stable_count >= 2:
                new_leader = current
                break
            time.sleep(0.5)
        if not new_leader:
            print("[démo] ✗ AUCUN NOUVEAU LEADER — échec de résilience")
            for nid, p in snapshot().items():
                print("         %s → rôle %s, leader=%s, epoch=%s" % (
                    nid, p["consensus"]["role"], p["consensus"]["leader_id"],
                    p["consensus"]["epoch"]))
            return 1
        failover_s = time.time() - t_kill
        print("[démo] ✓ RÉÉLECTION AUTOMATIQUE en %.1fs : %s (epoch %d → %d)"
              % (failover_s, new_leader, epoch1, epoch2))
        for nid in ids:
            if nid == leader_id:
                continue
            _s, p = http_json("GET", urls[nid] + "/status", timeout=3.0)
            if p:
                print("         %s → rôle %s, leader=%s" % (
                    nid, p["consensus"]["role"], p["consensus"]["leader_id"]))

        # -- phase 4 : l'essaim survit (requête d'élève) -----------------
        survivor = next(nid for nid in ids if nid != leader_id)
        status, payload = http_json("POST", urls[survivor] + "/ask", {
            "student_id": "eleve-demo",
            "prompt": "Quelle est la capitale de la Tunisie ?",
        }, timeout=60.0)
        if status == 200 and payload.get("answer"):
            osp = payload.get("osp", {})
            print("[démo] ✓ /ask sur %s → réponse routée via %s (g=%s, %s)" % (
                survivor, payload.get("provider"),
                osp.get("groundedness"), osp.get("verdict")))
        else:
            print("[démo] ✗ /ask en échec après basculement : %s" % payload)
            return 1

        # -- phase 5 : audit des chaînes de hachage ----------------------
        all_ok = True
        for nid in ids:
            sm = SecurityManager(
                whitelist_path=wl_file,
                log_path=os.path.join(workdir, nid, "swarm_audit.jsonl"))
            report = sm.verify_chain()
            note = "" if nid != leader_id else "  (leader tué — journal figé)"
            print("[démo] audit %-8s : %s — %d bloc(s)%s" % (
                nid, "INTACTE ✓" if report["ok"] else "CORROMPUE ✗",
                report["entries"], note))
            all_ok = all_ok and report["ok"]

        print("[démo] %s — répertoire conservé : %s" % (
            "RÉSILIENCE VALIDÉE ✔" if all_ok else "ÉCHEC", workdir))
        return 0 if all_ok else 1

    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.terminate()
        for proc in procs.values():
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


# ---------------------------------------------------------------------------
# Analyse des arguments
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="SwarmGlu — liaison, consensus et routage pour "
                    "Tree4Five Tools (essaim Wi-Fi hors-ligne)")
    sub = parser.add_subparsers(dest="command")

    # -- serve ----------------------------------------------------------
    p = sub.add_parser("serve", help="démarre un nœud de l'essaim")
    p.add_argument("--port", type=int, default=7001,
                   help="port HTTP du nœud (défaut 7001)")
    p.add_argument("--node-id", default="",
                   help="identifiant du nœud (défaut: hostname-port)")
    p.add_argument("--whitelist-file", default="whitelist.json",
                   help="chemin de la whitelist (défaut whitelist.json)")
    p.add_argument("--mode", default="full",
                   choices=["full", "follower", "ambassador", "provider"],
                   help="full: éligible leader ; follower/provider: jamais "
                        "leader ; ambassador: + passerelle inter-essaims")
    p.add_argument("--data-dir", default="",
                   help="répertoire de données/journal (défaut data/<node-id>)")
    p.add_argument("--swarm-id", default="swarm-local",
                   help="identifiant de l'essaim (ex. salle-1)")
    p.add_argument("--advertise-ip", default="",
                   help="IP annoncée aux pairs (autodétection sinon)")
    p.add_argument("--peers", default="",
                   help="seeds statiques « ip:port,ip:port » (si UDP bloqué)")
    p.add_argument("--neighbor", action="append", default=[],
                   help="essaim voisin « ip:port » (mode ambassador), "
                        "répétable pour plusieurs voisins")
    p.add_argument("--beacon-port", type=int, default=47479,
                   help="port UDP du beacon de découverte (0 = désactivé)")
    p.add_argument("--heartbeat-interval", type=float, default=3.0,
                   help="intervalle des heartbeats en s (défaut 3)")
    p.add_argument("--lease-timeout", type=float, default=6.0,
                   help="silence du leader déclenchant l'élection (défaut 6)")
    p.add_argument("--strict", action="store_true",
                   help="refuse l'enrôlement libre (whitelist préchargée seule)")
    p.add_argument("--mock-inference", action="store_true",
                   help="fournisseur d'inférence factice (démos/tests)")
    p.add_argument("--gguf-url", default="",
                   help="URL du pont GGUF local (ex. http://127.0.0.1:11434)")
    p.add_argument("--gguf-models", default="",
                   help="modèles offerts, séparés par virgule (qwen2.5:0.5b,...)")
    p.add_argument("--api-flavor", default="ollama", choices=["ollama", "openai"],
                   help="saveur d'API du pont GGUF")
    p.add_argument("--log-raw-prompts", action="store_true",
                   help="journalise le prompt en clair (défaut: hashé, vie privée)")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.set_defaults(func=cmd_serve)

    # -- status ----------------------------------------------------------
    p = sub.add_parser("status", help="affiche /status d'un nœud")
    p.add_argument("--url", default="http://127.0.0.1:7001")
    p.set_defaults(func=cmd_status)

    # -- ask ---------------------------------------------------------------
    p = sub.add_parser("ask", help="requête d'élève de test (routage + OSP)")
    p.add_argument("--url", default="http://127.0.0.1:7001")
    p.add_argument("--prompt", required=True)
    p.add_argument("--student", default="eleve-cli")
    p.add_argument("--model", default="")
    p.add_argument("--swarm", default="",
                   help="essaim cible (routage fédéré)")
    p.set_defaults(func=cmd_ask)

    # -- audit -----------------------------------------------------------
    p = sub.add_parser("audit", help="vérifie / affiche le journal immuable")
    p.add_argument("action", choices=["verify", "tail"])
    p.add_argument("--data-dir", default="data",
                   help="répertoire contenant swarm_audit.jsonl")
    p.add_argument("--whitelist-file", default="whitelist.json")
    p.add_argument("-n", type=int, default=20, help="nombre d'entrées (tail)")
    p.set_defaults(func=cmd_audit)

    # -- whitelist ---------------------------------------------------------
    p = sub.add_parser("whitelist", help="gestion de la whitelist")
    p.add_argument("action", choices=["init", "add", "remove", "list", "digest"])
    p.add_argument("--whitelist-file", default="whitelist.json")
    p.add_argument("--nodes", default="", help="pour init: a,b,c")
    p.add_argument("--node-id", default="", help="pour add/remove")
    p.add_argument("--data-dir", default="data")
    p.set_defaults(func=cmd_whitelist)

    # -- demo ---------------------------------------------------------------
    p = sub.add_parser("demo", help="test de résilience automatisé "
                                    "(élection après coupure du leader)")
    p.add_argument("--nodes", type=int, default=3, help="nombre de nœuds (défaut 3)")
    p.add_argument("--base-port", type=int, default=7101)
    p.add_argument("--heartbeat", type=float, default=1.0,
                   help="heartbeat accéléré pour la démo (défaut 1s)")
    p.add_argument("--lease", type=float, default=2.0,
                   help="bail accéléré pour la démo (défaut 2s)")
    p.set_defaults(func=cmd_demo)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    # Valeur par défaut de --data-dir dépendant du node-id.
    if args.command == "serve" and not args.data_dir:
        nid = args.node_id or ("node-%d" % args.port)
        args.data_dir = os.path.join("data", nid)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
