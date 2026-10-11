#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmGlu — TEST MULTI-ESSAIMS : deux salles fédérées par la passerelle,
sur machines réelles (2 émulateurs Android + 2 PC, + .249 en option).

    ┌── ESSAIM « salle-A » ──────────┐      ┌── ESSAIM « salle-B » ──────────┐
    │  pc-a   (PC local, :7901)      │      │  pc-b   (PC local, :7902)      │
    │  emu-alpha (Android, :17001)   │      │  emu-beta (Android, :17002)     │
    │      mode AMBASSADEUR ─────────┼──────┼─► /gateway/sync (Digest        │
    └────────────────────────────────┘      │   Exchange, TTL 90 s)          │
                                            │  far-249 (.249, :7905) opt.    │
                                            └────────────────────────────────┘

Chaque essaim élit SON leader, garde SA whitelist et SON savoir ; la
passerelle relie les salles pour le ROUTAGE FÉDÉRÉ des requêtes.

Scénario vérifié :
  1. convergence INDÉPENDANTE des deux essaims (chacun son leader) ;
  2. Digest Exchange : les voisins se connaissent des deux côtés ;
  3. savoir cloisonné : la connaissance de salle-A reste dans salle-A ;
  4. requête FÉDÉRÉE salle-B → salle-A (servie par salle-A) et retour ;
  5. MORT du leader de salle-A : réélection dans salle-A, salle-B INTACTE ;
  6. requête fédérée toujours servie après le basculement ;
  7. audit des chaînes intact partout.

Usage :
    python3 tests/test_multi_swarm_federation.py
    python3 tests/test_multi_swarm_federation.py --deploy-249 youyou
"""

import argparse
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

ESSAIM_A, ESSAIM_B = "salle-A", "salle-B"
PORT_A, PORT_B, PORT_FAR = 7901, 7902, 7905
IP_LOIN = "192.168.1.249"

RELAIS = []
ECHECS = mu.ECHECS


def verifier(nom, cond, detail=""):
    return mu.verifier(nom, cond, detail)


def http_json(methode, url, corps=None, timeout=15.0):
    return mu.http_json(methode, url, corps, timeout)


def relais():
    import shutil
    if not shutil.which("socat"):
        raise SystemExit("socat requis (apt install socat)")
    for _a, _c, port, _n in mu.AVDS:
        RELAIS.append(subprocess.Popen(
            ["socat", "TCP-LISTEN:%d,bind=%s,fork,reuseaddr" % (port, IP_LOCALE),
             "TCP:127.0.0.1:%d" % port],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    time.sleep(1.0)


def noeud_pc(node_id, port, swarm_id, dossier, neighbor=None):
    cmd = [sys.executable, os.path.join(RACINE, "cli.py"), "serve",
           "--node-id", node_id, "--port", str(port),
           "--advertise-ip", IP_LOCALE,
           "--swarm-id", swarm_id,
           "--whitelist-file", os.path.join(dossier, "wl-%s.json" % node_id),
           "--data-dir", os.path.join(dossier, node_id),
           "--mode", "full", "--mock-inference", "--beacon-port", "0"]
    if neighbor:
        cmd += ["--mode", "ambassador", "--neighbor", neighbor]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def pilote(console, node_id, port_hote, swarm_id, peers, neighbors=""):
    serial = "emulator-%d" % console
    args = [mu.ADB, "-s", serial, "shell", "am", "instrument", "-w",
            "-e", "class", "com.tree4five.swarmglu.NodeDriverInstrumentation",
            "-e", "node_id", node_id, "-e", "port", str(mu.PORT_EMU),
            "-e", "advertise_ip", IP_LOCALE, "-e", "advertise_port", str(port_hote),
            "-e", "peers", peers, "-e", "swarm_id", swarm_id,
            "-e", "heartbeat", "3.0", "-e", "lease", "6.0", "-e", "duration_s", "420"]
    if neighbors:
        args += ["-e", "mode", "ambassador", "-e", "neighbors", neighbors]
    args.append("%s.test/androidx.test.runner.AndroidJUnitRunner" % mu.PKG)
    # Sortie capturée : un `am instrument` malformé (p.ex. valeur vide)
    # affiche l'aide au lieu de tourner — il faut pouvoir le voir.
    journal = "/tmp/sg_instr_%s.log" % node_id
    return subprocess.Popen(args, stdout=open(journal, "w"),
                            stderr=subprocess.STDOUT)


def statuts(urls):
    s = {}
    for nom, url in urls.items():
        _st, p = http_json("GET", url + "/status", timeout=6)
        if p and "consensus" in p:
            s[nom] = p
    return s


def savoir(url):
    _s, k = http_json("GET", url + "/knowledge", timeout=8)
    return k if k and "count" in k else {"count": -1, "digest": "?"}


def essaim_converge(urls, attendus):
    """Convergence de CET essaim uniquement (les nœuds de l'autre salle
    sontjoignables mais ne comptent pas — bug corrigé : on filtre d'abord)."""
    st = statuts(urls)
    membres = {n: p for n, p in st.items() if n in attendus}
    if len(membres) != len(attendus):
        return False
    leader = {p["consensus"]["leader_id"] for p in membres.values()}
    roles = sum(1 for p in membres.values() if p["consensus"]["role"] == "leader")
    return len(leader) == 1 and roles == 1 and None not in leader


def main():
    parseur = argparse.ArgumentParser()
    parseur.add_argument("--deploy-249", default="")
    args = parseur.parse_args()

    urls = {"pc-a": "http://127.0.0.1:%d" % PORT_A,
            "emu-alpha": "http://127.0.0.1:%d" % mu.AVDS[0][2],
            "pc-b": "http://127.0.0.1:%d" % PORT_B,
            "emu-beta": "http://127.0.0.1:%d" % mu.AVDS[1][2]}

    proc_pc, procs_instr, distant = [], [], None
    dossier = tempfile.mkdtemp(prefix="sgfed_")
    try:
        mu.creer_avds()
        mu.booter_emulateurs()
        mu.installer_et_forwarder()
        relais()

        print("\n[1] deux essaims indépendants (salle-A / salle-B)…")
        # salle-A : pc-a + emu-alpha (AMBASSADEUR → pointe un nœud de salle-B)
        proc_pc.append(noeud_pc("pc-a", PORT_A, ESSAIM_A, dossier))
        # salle-B : pc-b + emu-beta (+ far-249 si demandé)
        proc_pc.append(noeud_pc("pc-b", PORT_B, ESSAIM_B, dossier))
        time.sleep(8)   # laisser les leaders s'élire avant les joins
        procs_instr.append(pilote(mu.AVDS[0][1], "emu-alpha", mu.AVDS[0][2],
                                  ESSAIM_A, "%s:%d" % (IP_LOCALE, PORT_A),
                                  neighbors="%s:%d" % (IP_LOCALE, PORT_B)))
        procs_instr.append(pilote(mu.AVDS[1][1], "emu-beta", mu.AVDS[1][2],
                                  ESSAIM_B, "%s:%d" % (IP_LOCALE, PORT_B)))
        if args.deploy_249:
            ssh = ["ssh", "-o", "BatchMode=yes", "%s@%s" % (args.deploy_249, IP_LOIN)]
            subprocess.run(ssh + ["pkill -f 'cli.py serve' ; true"], capture_output=True)
            subprocess.run(
                ["bash", "-c",
                 "cd %s && tar czf - --exclude=.git --exclude=android --exclude=data "
                 "--exclude=__pycache__ *.py | ssh -o BatchMode=yes %s@%s "
                 "'mkdir -p ~/swarmglu && tar xzf - -C ~/swarmglu'" % (RACINE, args.deploy_249, IP_LOIN)],
                capture_output=True)
            subprocess.run(ssh + [
                "cd ~/swarmglu && (nohup python3 cli.py serve --node-id far-249 "
                "--port %d --advertise-ip %s --swarm-id %s "
                "--whitelist-file ~/swarmglu/wl-fed.json --data-dir ~/swarmglu/data-fed "
                "--mode full --mock-inference --beacon-port 0 --peers %s:%d "
                "> ~/swarmglu/node-fed.log 2>&1 < /dev/null &)" % (
                    PORT_FAR, IP_LOIN, ESSAIM_B, IP_LOCALE, PORT_B)],
                capture_output=True)
            urls["far-249"] = "http://%s:%d" % (IP_LOIN, PORT_FAR)
            print("      far-249 déployé dans %s" % ESSAIM_B)

        membres_A = {"pc-a", "emu-alpha"}
        membres_B = {"pc-b", "emu-beta"} | ({"far-249"} if "far-249" in urls else set())
        verifier("%s convergé (leader propre)" % ESSAIM_A,
                 mu.attendre(90, lambda: essaim_converge(urls, membres_A), pas=2), statuts(urls))
        verifier("%s convergé (leader propre)" % ESSAIM_B,
                 mu.attendre(90, lambda: essaim_converge(urls, membres_B), pas=2), statuts(urls))

        print("\n[2] Digest Exchange (passerelle ambassadeur)…")
        def voisins_connus(cote, attendu):
            st = statuts(urls)
            return any(attendu in json_voisins(p) for n, p in st.items() if n in cote)
        def json_voisins(p):
            return [v.get("swarm_id") for v in p.get("gateway_neighbors", [])]
        verifier("salle-A connaît salle-B",
                 mu.attendre(30, lambda: voisins_connus(membres_A, ESSAIM_B), pas=2),
                 {n: p.get("gateway_neighbors") for n, p in statuts(urls).items()})
        verifier("salle-B connaît salle-A",
                 mu.attendre(30, lambda: voisins_connus(membres_B, ESSAIM_A), pas=2))

        print("\n[3] savoir cloisonné…")
        status, rA = http_json("POST", urls["emu-alpha"] + "/ask",
                               {"student_id": "eleve-A", "prompt": "Histoire de Djerba ?",
                                "sources": ["osp://swarmknowledge/whatsapp-bot",
                                            "osp://local/manuel-histoire"]}, timeout=60)
        verifier("question posée dans salle-A (VERIFIED, partagée)",
                 status == 200 and (rA or {}).get("knowledge", {}).get("shared") is True, rA)
        verifier("savoir propagé DANS salle-A uniquement",
                 mu.attendre(40, lambda: (
                     savoir(urls["pc-a"])["count"] >= 1
                     and savoir(urls["pc-b"])["count"] == 0
                     and savoir(urls["emu-beta"])["count"] == 0), pas=3),
                 {n: savoir(u)["count"] for n, u in urls.items()})

        print("\n[4] requêtes fédérées…")
        status, rB = http_json("POST", urls["emu-beta"] + "/ask",
                               {"student_id": "eleve-B",
                                "prompt": "Question de la salle B pour la salle A ?",
                                "swarm_id": ESSAIM_A}, timeout=90)
        verifier("fédérée salle-B → salle-A servie par salle-A",
                 status == 200 and (rB or {}).get("served_by_swarm") == ESSAIM_A, rB)
        status, rB2 = http_json("POST", urls["pc-a"] + "/ask",
                                {"student_id": "eleve-A",
                                 "prompt": "Question de la salle A pour la salle B ?",
                                 "swarm_id": ESSAIM_B}, timeout=90)
        verifier("fédérée salle-A → salle-B servie par salle-B",
                 status == 200 and (rB2 or {}).get("served_by_swarm") == ESSAIM_B, rB2)

        print("\n[5] mort du leader de %s — salle-B doit rester intacte…" % ESSAIM_A)
        st = statuts(urls)
        leader_A = next(p["consensus"]["leader_id"] for n, p in st.items() if n in membres_A
                        and p["consensus"]["role"] == "leader")
        epoch_B = max(p["consensus"]["epoch"] for n, p in st.items() if n in membres_B)
        t0 = time.time()
        if leader_A == "pc-a":
            proc_pc[0].kill(); proc_pc[0].wait()
            del urls["pc-a"]; membres_A.discard("pc-a")
        else:
            cible = next(c for a, c, p, n in mu.AVDS if n == leader_A)
            mu.cmd([mu.ADB, "-s", "emulator-%d" % cible, "shell", "am", "force-stop", mu.PKG])
            mu.cmd([mu.ADB, "-s", "emulator-%d" % cible, "shell", "am", "force-stop", mu.PKG + ".test"])
            del urls[leader_A]; membres_A.discard(leader_A)
        verifier("réélection dans salle-A (epoch incrémenté)",
                 mu.attendre(60, lambda: essaim_converge(urls, membres_A), pas=2), statuts(urls))
        verifier("salle-B INTACTE (leader et epoch inchangés)",
                 mu.attendre(20, lambda: essaim_converge(urls, membres_B), pas=2)
                 and all(p["consensus"]["epoch"] == epoch_B
                         for n, p in statuts(urls).items() if n in membres_B),
                 {n: p["consensus"] for n, p in statuts(urls).items()})
        print("      basculement en %.1f s" % (time.time() - t0))

        survivant_A = next(iter(membres_A))
        status, rF = http_json("POST", urls[survivant_A] + "/ask",
                               {"student_id": "eleve-A",
                                "prompt": "La fédération survit-elle au leader ?",
                                "swarm_id": ESSAIM_B}, timeout=90)
        verifier("fédérée toujours servie après basculement",
                 status == 200 and (rF or {}).get("served_by_swarm") == ESSAIM_B, rF)

        print("\n[6] audit final…")
        for nom, url in urls.items():
            _s, v = http_json("GET", url + "/audit/verify", timeout=15)
            verifier("chaîne INTACTE sur %s (%s)" % (nom, ESSAIM_A if nom in membres_A else ESSAIM_B),
                     bool(v and v.get("ok")), v)

    finally:
        for proc in procs_instr:
            if proc.poll() is None:
                proc.terminate()
        for proc in proc_pc:
            if proc.poll() is None:
                proc.terminate()
        for r in RELAIS:
            r.terminate()
        if args.deploy_249:
            subprocess.run(["ssh", "-o", "BatchMode=yes", "%s@%s" % (args.deploy_249, IP_LOIN),
                            "pkill -f 'cli.py serve' ; true"], capture_output=True)
        mu.nettoyer(None, [])

    print("\n" + "=" * 64)
    if ECHECS:
        print("ÉCHECS (%d) : %s" % (len(ECHECS), ECHECS))
        return 1
    print("MULTI-ESSAIMS VALIDÉ ✔ (2 salles fédérées, cloisonnement du savoir, "
          "routage croisé, failover isolé, audit intègre)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
