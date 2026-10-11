#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmGlu — run_all.py : NON-RÉGRESSION EN UNE COMMANDE
=======================================================
Exécute toute la chaîne de validation, dans l'ordre recommandé, avec
synthèse finale et code de sortie non nul au premier échec.

Usage :
    python3 run_all.py                # rapide : noyau (112 tests) + démo résilience
    python3 run_all.py --full         # + scénarios émulateurs/réseau (~25 min)
    python3 run_all.py --full --ssh-249 youyou   # avec le nœud distant .249
    python3 run_all.py --android      # + tests dans Android (appareil/émulateur ADB)

Chaque étape correspond à une section de howto.txt — voir ce fichier pour
les prérequis (SDK, socat, clé SSH) et les résultats attendus.
"""

import argparse
import os
import subprocess
import sys
import time

RACINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(RACINE, "tests"))


def etape(nom, commande, duree_max=1800):
    """Exécute une étape ; affiche le verdict ; retourne (ok, durée)."""
    print("\n" + "=" * 70)
    print("▶ %s" % nom)
    print("  $ %s" % " ".join(commande))
    print("=" * 70)
    debut = time.time()
    r = subprocess.run(commande, cwd=RACINE, timeout=duree_max)
    duree = time.time() - debut
    verdict = "✓" if r.returncode == 0 else "✗ (exit %d)" % r.returncode
    print("%s %s — %.0f s" % (verdict, nom, duree))
    return r.returncode == 0, duree


def main():
    parseur = argparse.ArgumentParser(description="Non-régression SwarmGlu")
    parseur.add_argument("--full", action="store_true",
                         help="inclure les scénarios émulateurs/réseau")
    parseur.add_argument("--android", action="store_true",
                         help="inclure les tests dans Android (ADB requis)")
    parseur.add_argument("--ssh-249", default="",
                         help="utilisateur SSH pour déployer 192.168.1.249")
    args = parseur.parse_args()

    etapes = [
        ("1. Suite noyau (112 tests)", [sys.executable, "-m", "unittest",
                                        "discover", "-s", "tests"], 600),
        ("2. Démo résilience (3 nœuds, leader tué)", [sys.executable,
                                                      "cli.py", "demo",
                                                      "--nodes", "3"], 600),
    ]
    if args.full:
        opt249 = ["--deploy-249", args.ssh_249] if args.ssh_249 else []
        etapes += [
            ("3. Communication émulateur + téléphones ↔ 192.168.1.249",
             [sys.executable, "tests/test_communication_249.py"] + opt249, 1800),
            ("4. Essaim 2 émulateurs + PC", [sys.executable,
                                             "tests/test_multi_emulator_swarm.py"], 1800),
            ("5. Interop swarmknowledge (partage du savoir)",
             [sys.executable, "tests/test_swarmknowledge_sharing.py"] + opt249, 1800),
            ("6. Multi-essaims fédérés (2 salles)",
             [sys.executable, "tests/test_multi_swarm_federation.py"] + opt249, 1800),
            ("7. Scénario hotspot (zone étendue)",
             [sys.executable, "tests/test_hotspot_scenario.py"] + opt249, 1800),
        ]
    if args.android:
        etapes.append(("A. Tests DANS Android (gradle connected)",
                       ["./gradlew", "connectedDebugAndroidTest"], 2400))
        # cwd spécial : android/
        etapes[-1] = (etapes[-1][0], etapes[-1][1], etapes[-1][2], "android")

    resultats = []
    for item in etapes:
        nom, commande, duree_max = item[0], item[1], item[2]
        dossier = item[3] if len(item) > 3 else RACINE
        ok, duree = etape(nom, commande, duree_max) if dossier == RACINE else \
            _etape_dossier(nom, commande, dossier, duree_max)
        resultats.append((nom, ok, duree))
        if not ok:
            break   # premier échec : on s'arrête (commenter pour tout lancer)

    print("\n" + "=" * 70)
    print("SYNTHÈSE")
    print("=" * 70)
    for nom, ok, duree in resultats:
        print("  %s %s (%.0f s)" % ("✓" if ok else "✗", nom, duree))
    echecs = [n for n, ok, _ in resultats if not ok]
    if echecs:
        print("\nÉCHECS : %s" % echecs)
        return 1
    print("\nNON-RÉGRESSION PASSÉE ✔ (%d étape(s))" % len(resultats))
    return 0


def _etape_dossier(nom, commande, dossier, duree_max):
    print("\n" + "=" * 70)
    print("▶ %s" % nom)
    print("=" * 70)
    debut = time.time()
    r = subprocess.run(commande, cwd=dossier, timeout=duree_max)
    duree = time.time() - debut
    print("%s %s — %.0f s" % ("✓" if r.returncode == 0 else "✗", nom, duree))
    return r.returncode == 0, duree


if __name__ == "__main__":
    sys.exit(main())
