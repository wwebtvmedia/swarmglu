#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmGlu × swarmknowledge — test de PARTAGE DE CONNAISSANCES (« share all »)
sur un essaim multi-plateforme : 2 émulateurs Android + 1 PC local (+ nœud
distant 192.168.1.249 si SSH disponible).

Interopérabilité réelle avec l'écosystème :
  * le nœud OSP swarmknowledge vivant sur http://192.168.1.249:3000
    (GET /osp/endpoint.json) est vérifié et utilisé comme SOURCE D'ANCRAGE
    d'une question d'élève → sa réponse entre au savoir commun ;
  * toute réponse VERIFIED (OSP v0.6, g >= 0.30) posée sur N'IMPORTE QUEL
    nœud (Android ou PC) devient savoir commun propagé à TOUS (RAG
    distribué) — vérifié par digest identique partout ;
  * une réponse non sourcée est EXCLUE par le pare-feu ;
  * mort du leader → réélection → le savoir continue de croître partout ;
  * audit des chaînes de hachage intact.

Déploiement sur 192.168.1.249 (« check install ») :
  * vérifie ping + endpoint OSP + port SSH ;
  * si une clé SSH fonctionne :  --deploy-249 <user>  recopie SwarmGlu,
    démarre un nœud `far-249` et l'intègre à l'essaim ;
  * sinon : diagnostic + commande exacte à lancer.

Usage :
    python3 tests/test_swarmknowledge_sharing.py
    python3 tests/test_swarmknowledge_sharing.py --deploy-249 pc
"""

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_multi_emulator_swarm as mu   # réutilise : cmd, attendre, verifier…

RACINE = mu.RACINE
IP_LOIN = "192.168.1.249"
OSP_URL = "http://%s:3000" % IP_LOIN
PORT_PC = 7901
PORT_LOIN = 7905
IP_LOCALE = "192.168.1.105"     # cette machine, vue du LAN

ECHECS = mu.ECHECS


def http_json(methode, url, corps=None, timeout=15.0):
    return mu.http_json(methode, url, corps, timeout)


def verifier(nom, cond, detail=""):
    return mu.verifier(nom, cond, detail)


# ---------------------------------------------------------------------------
# 0) Écosystème swarmknowledge + installabilité de 192.168.1.249
# ---------------------------------------------------------------------------

def phase_ecosysteme(deploy_user):
    print("\n[0] écosystème swarmknowledge (192.168.1.249)…")
    ping = cmd_ok(["ping", "-c", "2", "-W", "2", IP_LOIN])
    verifier("machine 192.168.1.249 joignable (ping)", ping)

    status, contrat = http_json("GET", OSP_URL + "/osp/endpoint.json", timeout=8)
    ancre = None
    if verifier("nœud OSP swarmknowledge vivant (GET /osp/endpoint.json)",
                status == 200 and contrat and "node_id" in contrat, (status, contrat)):
        verifier("contrat OSP v0.6 (node_id=%s, packet %s)" % (
            contrat.get("node_id"), contrat.get("packet_version")),
            str(contrat.get("packet_version", "")).startswith("0.6"))
        ancre = contrat

    ssh_ok = port_ouvert(IP_LOIN, 22)
    verifier("port SSH ouvert sur 192.168.1.249 (installation possible)", ssh_ok)

    cle_ok = False
    if deploy_user:
        r = mu.cmd(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                    "-o", "StrictHostKeyChecking=accept-new",
                    "%s@%s" % (deploy_user, IP_LOIN), "true"])
        cle_ok = r.returncode == 0
        verifier("authentification SSH par clé (%s@…)" % deploy_user, cle_ok,
                 "fournir une machine avec clé autorisée, ou installer vous-même (voir sortie)")
    return ancre, (deploy_user if cle_ok else None)


def cmd_ok(args):
    return subprocess.run(args, capture_output=True).returncode == 0


def port_ouvert(ip, port):
    import socket
    try:
        with socket.create_connection((ip, port), timeout=3):
            return True
    except OSError:
        return False


def deployer_249(user):
    """Recopie SwarmGlu sur .249 et démarre le nœud distant `far-249`."""
    cible = "%s@%s" % (user, IP_LOIN)
    print("\n[0.bis] déploiement SwarmGlu sur %s…" % cible)
    r = mu.cmd(["ssh", "-o", "BatchMode=yes", cible,
                "python3 --version && mkdir -p ~/swarmglu/data"])
    verifier("python3 disponible sur .249", r.returncode == 0 and "Python 3" in r.stdout, r.stderr)
    if r.returncode != 0:
        return None
    r = mu.cmd(["bash", "-c",
                "cd %s && tar czf - --exclude=.git --exclude=android --exclude=data "
                "--exclude=__pycache__ *.py README.md | ssh -o BatchMode=yes %s "
                "'tar xzf - -C ~/swarmglu'" % (RACINE, cible)])
    verifier("code SwarmGlu copié (tar over ssh)", r.returncode == 0, r.stderr)
    if r.returncode != 0:
        return None
    distant = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", cible,
         "cd ~/swarmglu && nohup python3 cli.py serve --node-id far-249 "
         "--port %d --advertise-ip %s --whitelist-file ~/swarmglu/wl-249.json "
         "--data-dir ~/swarmglu/data --mode full --mock-inference "
         "--beacon-port 0 --peers %s:%d > ~/swarmglu/node.log 2>&1 &" % (
             PORT_LOIN, IP_LOIN, IP_LOCALE, PORT_PC)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok = mu.attendre(25, lambda: http_json(
        "GET", "http://%s:%d/status" % (IP_LOIN, PORT_LOIN), timeout=5)[1] is not None, pas=2)
    verifier("nœud far-249 démarré et joignable sur le LAN", ok)
    return distant


def tuer_249(user):
    mu.cmd(["ssh", "-o", "BatchMode=yes", "%s@%s" % (user, IP_LOIN),
            "pkill -f 'cli.py serve --node-id far-249' || true"])


# ---------------------------------------------------------------------------
# Essaim + partage de connaissances
# ---------------------------------------------------------------------------

def statuts(urls):
    s = {}
    for nom, url in urls.items():
        _st, p = http_json("GET", url + "/status", timeout=6)
        if p and "consensus" in p:
            s[nom] = p
    return s


def savoir(url):
    _s, k = http_json("GET", url + "/knowledge", timeout=8)
    return k or {"count": -1, "digest": "?"}


def question(url, q, sources=None, student="eleve-partage", model_hint=""):
    corps = {"student_id": student, "prompt": q}
    if sources is not None:
        corps["sources"] = sources
    if model_hint:
        corps["model_hint"] = model_hint
    return http_json("POST", url + "/ask", corps, timeout=60)


def main():
    parseur = argparse.ArgumentParser()
    parseur.add_argument("--deploy-249", default="",
                         help="utilisateur SSH pour déployer un nœud sur 192.168.1.249")
    args = parseur.parse_args()

    ancre, user_ssh = phase_ecosysteme(args.deploy_249)
    distant = deployer_249(user_ssh) if user_ssh else None

    urls = {"pc-orchestre": "http://127.0.0.1:%d" % PORT_PC,
            "emu-alpha": "http://127.0.0.1:%d" % mu.AVDS[0][2],
            "emu-beta": "http://127.0.0.1:%d" % mu.AVDS[1][2]}
    if distant:
        urls["far-249"] = "http://%s:%d" % (IP_LOIN, PORT_LOIN)

    pc = instrumentations = None
    try:
        mu.creer_avds()
        mu.booter_emulateurs()
        mu.installer_et_forwarder()
        print("\n[1] démarrage de l'essaim (%d nœuds : PC + 2 émulateurs%s)…" % (
            len(urls), " + .249" if distant else ""))
        pc, instrumentations = mu.lancer_noeuds()

        # -- convergence ---------------------------------------------------
        print("\n[2] convergence…")
        verifier("un seul leader, accepté par tous",
                 mu.attendre(60, lambda: (
                     len(statuts(urls)) == len(urls)
                     and len({p["consensus"]["leader_id"] for p in statuts(urls).values()}) == 1
                     and sum(1 for p in statuts(urls).values()
                             if p["consensus"]["role"] == "leader") == 1), pas=2),
                 statuts(urls))
        st = statuts(urls)
        if st:
            verifier("chaînes d'audit scellées et intactes",
                     all(p["audit"]["ok"] for p in st.values()))

        # -- partage de connaissances : 3 questions, 3 origines -------------
        print("\n[3] partage du savoir (share all) : une question par origine…")
        sources_ancre = ["osp://swarmknowledge/%s" % ancre.get("node_id", "osp"),
                         "%s/osp/endpoint.json" % OSP_URL] if ancre else None
        # model_hint « pc-coordonnateur » : le leader PC se sert lui-même
        # (l'hôte ne peut pas joindre les émulateurs via 10.0.2.2, alias
        # interne à l'émulateur — cf. README tests).
        _, r1 = question(urls["emu-beta"], "Qu'est-ce que l'OSP v0.6 ?",
                         sources=sources_ancre, student="eleve-emu",
                         model_hint="pc-coordonnateur")
        _, r2 = question(urls["pc-orchestre"], "Comment fonctionne l'élection par bail ?",
                         student="eleve-pc", model_hint="pc-coordonnateur")
        _, r3 = question(urls["emu-alpha"], "Pourquoi un pare-feu anti-hallucination ?",
                         student="eleve-emu2", model_hint="pc-coordonnateur")
        attendues = sum(1 for r in (r1, r2, r3) if r and r.get("knowledge"))
        verifier("3 réponses VERIFIED produites sur 3 origines différentes",
                 attendues == 3,
                 [(r or {}).get("osp") for r in (r1, r2, r3)])

        # -- LE point central : TOUT LE MONDE a TOUT le savoir --------------
        def tous_pareil():
            savoirs = {nom: savoir(url) for nom, url in urls.items()}
            if any("count" not in k for k in savoirs.values()):
                return False    # un nœud pas prêt / en erreur : on attend
            comptes = {k["count"] for k in savoirs.values()}
            digests = {k["digest"] for k in savoirs.values()}
            return len(comptes) == 1 and len(digests) == 1 and attendues in comptes

        verifier("savoir partagé PARTOUT (même compte ET même digest sur les %d nœuds)" % len(urls),
                 mu.attendre(40, tous_pareil, pas=3),
                 {nom: savoir(url) for nom, url in urls.items()})
        k = savoir(urls["emu-alpha"])
        if k.get("entries"):
            verifier("ancrage OSP swarmknowledge dans le savoir commun",
                     any(src.startswith("osp://swarmknowledge")
                         for e in k["entries"] for src in e.get("sources", [])),
                     k["entries"][0].get("sources"))

        # -- pare-feu : le non-sourcé n'entre JAMAIS dans le savoir ---------
        avant = savoir(urls["emu-beta"]).get("count", 0)
        _, r4 = question(urls["emu-beta"], "Question piège sans ancrage", sources=[],
                         model_hint="pc-coordonnateur")
        verifier("réponse non sourcée EXCLUE du savoir (pare-feu OSP)",
                 (r4 or {}).get("osp", {}).get("verdict") == "UNVERIFIED"
                 and savoir(urls["emu-beta"])["count"] == avant)

        # -- mort du leader : le savoir continue de croître ------------------
        if st:
            leader = next(iter(st.values()))["consensus"]["leader_id"]
            print("\n[4] coupure brutale du leader : %s" % leader)
            t0 = time.time()
            if leader == "pc-orchestre":
                pc.kill(); pc.wait()
                del urls["pc-orchestre"]
            elif leader.startswith("emu-"):
                cible = next(c for a, c, p, n in mu.AVDS if n == leader)
                mu.cmd([mu.ADB, "-s", "emulator-%d" % cible, "shell", "am", "force-stop", mu.PKG])
                mu.cmd([mu.ADB, "-s", "emulator-%d" % cible, "shell", "am", "force-stop", mu.PKG + ".test"])
                del urls[leader]
            epoch_avant = max(p["consensus"]["epoch"] for p in st.values())
            verifier("réélection automatique (epoch > %d)" % epoch_avant,
                     mu.attendre(50, lambda: (
                         len(statuts(urls)) == len(urls)
                         and all(p["consensus"]["epoch"] > epoch_avant
                                 for p in statuts(urls).values())
                         and len({p["consensus"]["leader_id"] for p in statuts(urls).values()}) == 1),
                         pas=2), statuts(urls))
            print("      basculement en %.1f s" % (time.time() - t0))

        survivant = next(iter(urls))
        _, r5 = question(urls[survivant], "Le savoir survit-il au leader ?",
                         student="eleve-post")
        verifier("nouvelle réponse VERIFIED après basculement",
                 (r5 or {}).get("knowledge", {}).get("shared") is True)
        verifier("le savoir a crû PARTOUT après le basculement",
                 mu.attendre(40, lambda: all(
                     savoir(u)["count"] >= attendues + 1 for u in urls.values()), pas=3),
                 {nom: savoir(u)["count"] for nom, u in urls.items()})

        # -- audit final -----------------------------------------------------
        print("\n[5] audit final…")
        for nom, url in urls.items():
            _s, v = http_json("GET", url + "/audit/verify", timeout=15)
            verifier("chaîne INTACTE sur %s" % nom, bool(v and v.get("ok")), v)

    finally:
        mu.nettoyer(pc, instrumentations or [])
        if user_ssh:
            tuer_249(user_ssh)
        if user_ssh is None and args.deploy_249:
            print("\n[déploiement .249] pas de clé SSH pour %s@%s. Pour installer :" % (
                args.deploy_249, IP_LOIN))
            print("  ssh-copy-id %s@%s   # une fois, avec mot de passe" % (args.deploy_249, IP_LOIN))
            print("  puis relancez : python3 tests/test_swarmknowledge_sharing.py --deploy-249 %s"
                  % args.deploy_249)

    print("\n" + "=" * 64)
    if ECHECS:
        print("ÉCHECS (%d) : %s" % (len(ECHECS), ECHECS))
        return 1
    print("PARTAGE DE CONNAISSANCES VALIDÉ ✔ (swarmknowledge ancré, "
          "share-all multi-plateforme, pare-feu, failover, audit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
