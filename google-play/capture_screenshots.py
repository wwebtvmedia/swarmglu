#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmGlu — captures d'écran Play Store (2560×1440) depuis l'UI RELEASE.

Pilote réellement l'app sur un émulateur (uiautomator + taps) pour produire
les 5 visuels de la fiche Play, tous pris sur le build de production
(signé, R8) — jamais sur le debug :

  01-dashboard-arrete.png        dashboard, nœud à l'arrêt
  02-tableau-de-bord-leader.png  nœud démarré en mode full, leader élu
  03-eleve-reponse-osp.png       requête élève servie (pare-feu OSP)
  04-audit-chaine-intacte.png    vérification de la chaîne de hachage
  05-settings-pont-llm.png       réglage du pont LLM on-device (nouveau 2.0.0)

Points d'attention (validés en run réel) :
  - le service démarre en mode « follower » (les PC du professeur
    coordonnent) : on passe en « full » par le dialog réglages AVANT
    d'allumer le nœud, sinon aucune élection ;
  - density 280 obligatoire : @320 en 1440p le dialog réglages écrase
    son buttonPanel (OK hors arbre uiautomator) ;
  - le clavier recouvre btn_ask : ÉCHAP pour le fermer avant le tap ;
  - dialogues système à acquitter à chaque boucle (permission notifs,
    ANR System UI sous swiftshader).

Prérequis : ./gradlew assembleRelease (APK release signé)
Usage :     python3 google-play/capture_screenshots.py
"""

import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SDK = os.path.expanduser("~/Android/Sdk")
ADB = os.path.join(SDK, "platform-tools", "adb")
EMULATOR = os.path.join(SDK, "emulator", "emulator")
PKG = "com.tree4five.swarmglu"
AVD = "sg-e1"
PORT_CONSOLE = 5554
SERIAL = "emulator-%d" % PORT_CONSOLE
APK = os.path.join(RACINE, "android/app/build/outputs/apk/release/app-release.apk")
SORTIE = os.path.join(RACINE, "google-play/assets/screenshots")
LARGEUR, HAUTEUR = 2560, 1440


def cmd(args, timeout=120):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def sh(args, timeout=60):
    return cmd([ADB, "-s", SERIAL, "shell"] + args, timeout=timeout)


def attendre(secondes, pred, pas=0.7):
    t0 = time.time()
    while time.time() - t0 < secondes:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(pas)
    return False


def ui_dump():
    """Arbre uiautomator courant, en acquittant au passage les dialogues
    système qui bloqueraient la capture (permission, ANR de démarrage).
    Le premier rendu sous swiftshader dépasse parfois les 5 s d'input :
    l'ANR puis le dialog de permission peuvent se succéder — on boucle."""
    for _ in range(6):
        sh(["rm", "-f", "/sdcard/ui.xml"])
        sh(["uiautomator", "dump", "/sdcard/ui.xml"])
        brut = sh(["cat", "/sdcard/ui.xml"]).stdout
        if "<hierarchy" not in brut:
            return None
        try:
            tree = ET.fromstring(brut[brut.index("<hierarchy"):])
        except ET.ParseError:
            return None
        cible = None
        for n in tree.iter():
            rid = n.get("resource-id") or ""
            # Wait (et jamais aerr_close : on ne tue jamais l'app) + permission
            if rid.endswith("permission_allow_button") or rid.endswith("aerr_wait"):
                cible = centre(n)
                if cible:
                    break
        if cible is None:
            return tree
        sh(["input", "tap", str(cible[0]), str(cible[1])])
        time.sleep(2)
    return tree


def centre(node):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def vue(tree, res_id):
    """Centre de la vue au resource-id donné."""
    if tree is None:
        return None
    for n in tree.iter():
        if n.get("resource-id") == "%s:id/%s" % (PKG, res_id):
            return centre(n)
    return None


def taper(pos):
    if pos:
        sh(["input", "tap", str(pos[0]), str(pos[1])])
        return True
    return False


def edittexts(tree):
    """Champs EditText du dialog réglages (construits en code : PAS de
    resource-id — on les indexe dans l'ordre du layout).
    0 node_id · 1 port · 2 essaim · 3 mode · 4 peers · 5 neighbors
    6 gguf_url · 7 gguf_models · 8 gguf_token · 9 mock
    """
    if tree is None:
        return []
    out = []
    for n in tree.iter():
        if n.get("class") == "android.widget.EditText":
            out.append((centre(n), n.get("text") or ""))
    return out


def remplacer_champ(pos, ancien, nouveau):
    """Vide un champ (FIN + RETOUR x longueur) et tape la nouvelle valeur,
    puis referme le clavier : il couvrirait sinon le buttonPanel du dialog
    (OK à y≈1211 px, sous le clavier → le tap serait intercepté par lui)."""
    if not taper(pos):
        return False
    time.sleep(0.6)
    sh(["input", "keyevent", "123"])            # MOVE_END
    for _ in range(len(ancien) + 4):            # DEL x N (marge)
        sh(["input", "keyevent", "67"])
    if nouveau:
        sh(["input", "text", nouveau.replace(" ", "%s")])
    time.sleep(0.5)
    sh(["input", "keyevent", "111"])            # ESC → clavier fermé
    time.sleep(0.8)
    return True


def saisir(res_id, texte):
    """Tape du texte dans un champ identifié, puis referme le clavier
    (sinon il recouvre les boutons d'action)."""
    tree = ui_dump()
    pos = vue(tree, res_id)
    if not taper(pos):
        return False
    time.sleep(0.8)
    sh(["input", "text", texte.replace(" ", "%s")])
    time.sleep(0.5)
    sh(["input", "keyevent", "111"])            # ESC → clavier fermé
    time.sleep(0.8)
    return True


def texte_ui(res_id):
    tree = ui_dump()
    if tree is None:
        return ""
    for n in tree.iter():
        if n.get("resource-id") == "%s:id/%s" % (PKG, res_id):
            return n.get("text", "") or ""
    return ""


def capturer(nom):
    appareil = "/sdcard/_sg_shot.png"
    brut = "/tmp/_sg_shot.png"
    sh(["screencap", "-p", appareil])
    cmd([ADB, "-s", SERIAL, "pull", appareil, brut])
    from PIL import Image
    im = Image.open(brut)
    if im.size != (LARGEUR, HAUTEUR):
        if im.width == LARGEUR and im.height > HAUTEUR:
            im = im.crop((0, 0, LARGEUR, HAUTEUR))
        else:
            im = im.resize((LARGEUR, HAUTEUR))
    im.save(os.path.join(SORTIE, nom))
    print("  capture %s (%dx%d)" % (nom, im.width, im.height))


def main():
    if not os.path.isfile(APK):
        print("APK release manquant : cd android && ./gradlew assembleRelease")
        return 2
    os.makedirs(SORTIE, exist_ok=True)

    # Un émulateur déjà booté sur ce port est réutilisé (les reruns sont
    # alors instantanés) ; sinon on en lance un nouveau.
    deja_pret = sh(["getprop", "sys.boot_completed"]).stdout.strip() == "1"
    proc = None
    if deja_pret:
        print("[1] émulateur %s déjà démarré, réutilisé" % SERIAL)
    else:
        print("[1] boot %s…" % AVD)
        proc = subprocess.Popen(
            [EMULATOR, "-avd", AVD, "-no-window", "-no-audio", "-no-boot-anim",
             "-gpu", "swiftshader_indirect", "-no-snapshot", "-wipe-data",
             "-port", str(PORT_CONSOLE), "-memory", "2560"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not attendre(240, lambda: sh(
                ["getprop", "sys.boot_completed"]).stdout.strip() == "1", pas=4):
            print("timeout de boot")
            return 1

        # Premier rendu sous swiftshader + load du hôte : le FocusEvent de
        # MainActivity peut dépasser les 5 s → ANR au lancement. On masque
        # les dialogues d'erreur système, on coupe les animations et on
        # laisse le système se stabiliser AVANT toute interaction.
        sh(["settings", "put", "global", "hide_error_dialogs", "1"])
        for balise in ("window_animation_scale", "transition_animation_scale",
                       "animator_duration_scale"):
            sh(["settings", "put", "global", balise, "0"])
        time.sleep(15)

        print("[2] install APK RELEASE…")
        cmd([ADB, "-s", SERIAL, "uninstall", PKG])
        r = cmd([ADB, "-s", SERIAL, "install", "-r", APK], timeout=300)
        if "Success" not in r.stdout:
            print("install échouée : " + r.stdout + r.stderr)
            return 1

        # Format Play 16:9 directement (en 2560×1600, un recadrage rognerait
        # simultanément la status bar et la bottom nav).
        sh(["wm", "size", "%dx%d" % (LARGEUR, HAUTEUR)])
        # density 280 (et non 320) : en 2560×1440 @320 la fenêtre logique ne
        # fait que 636 dp, le dialog réglages (10 champs + message) dépasse et
        # ÉCRASE son buttonPanel sur 8 px → android:id/button1 disparaît de
        # l'arbre uiautomator (impossible de valider). À 280 la fenêtre fait
        # 726 dp : le dialog tient entier, boutons OK/Annuler cliquables.
        sh(["wm", "density", "280"])
        # État reproductible : réglages par défaut (mode follower, nœud à
        # l'arrêt) même si l'AVD a déjà servi aux tests manuels.
        sh(["pm", "clear", PKG])
        time.sleep(2)

        print("[3] ouverture de l'app (nœud à l'arrêt)…")
        sh(["am", "start", "-n", "%s/.MainActivity" % PKG])
        if not attendre(60, lambda: vue(ui_dump(), "switch_node")):
            print("dashboard introuvable")
            return 1
        time.sleep(3)                      # premier rafraîchissement du dashboard
        capturer("01-dashboard-arrete.png")

        print("[4] réglages : mode full (éligible leader)…")
        if not taper(vue(ui_dump(), "btn_settings")):
            print("bouton réglages introuvable")
            return 1
        if not attendre(20, lambda: len(edittexts(ui_dump())) >= 10):
            print("dialog réglages introuvable")
            return 1
        champs = edittexts(ui_dump())
        # champ 3 = mode ; il contient « follower » par défaut
        remplacer_champ(champs[3][0], champs[3][1], "full")
        # validation : OK du dialog (clavier refermé par remplacer_champ)
        tree = ui_dump()
        pos_ok = None
        if tree is not None:
            for n in tree.iter():
                if (n.get("resource-id") or "") == "android:id/button1":
                    pos_ok = centre(n)
        if not taper(pos_ok):
            print("bouton OK du dialog introuvable")
            return 1
        time.sleep(2.5)                    # toast « réglages sauvegardés »
        # le dialog DOIT se fermer : sinon chaque tap suivant part dedans
        if not attendre(15, lambda: vue(ui_dump(), "switch_node")):
            print("dialog réglages toujours ouvert après OK")
            return 1

        print("[5] démarrage du nœud, attente du leader…")
        if not taper(vue(ui_dump(), "switch_node")):
            print("interrupteur « Nœud » introuvable")
            return 1
        def leader():
            return "leader" in texte_ui("tv_role").lower()
        if not attendre(90, leader):
            print("leader non atteint (role=%r)" % texte_ui("tv_role"))
            return 1
        print("  leader élu (%s)" % texte_ui("tv_role"))
        time.sleep(3)                      # remplissage des cartes
        capturer("02-tableau-de-bord-leader.png")

        print("[6] requête élève (pare-feu OSP)…")
        if not taper(vue(ui_dump(), "nav_ask")):
            print("onglet Élève introuvable")
            return 1
        time.sleep(2)
        if not saisir("input_prompt", "Combien de regions en Tunisie ?"):
            print("champ prompt introuvable")
            return 1
        # clavier fermé : btn_ask est accessible
        if not taper(vue(ui_dump(), "btn_ask")):
            print("bouton « Demander » introuvable")
            return 1
        if not attendre(60, lambda: len(texte_ui("tv_answer")) > 20):
            print("timeout réponse élève (tv_answer=%r)" % texte_ui("tv_answer"))
            return 1
        print("  réponse obtenue")
        capturer("03-eleve-reponse-osp.png")

        print("[7] vérification de la chaîne d'audit…")
        if not taper(vue(ui_dump(), "nav_audit")):
            print("onglet Audit introuvable")
            return 1
        time.sleep(2)
        if not taper(vue(ui_dump(), "btn_verify")):
            print("bouton « Vérifier » introuvable")
            return 1
        if not attendre(30, lambda: len(texte_ui("tv_audit_out")) > 10):
            print("audit non rendu (tv_audit_out=%r)" % texte_ui("tv_audit_out"))
            return 1
        time.sleep(1)
        capturer("04-audit-chaine-intacte.png")

        print("[8] réglage du pont LLM on-device (nouveau 2.0.0)…")
        if not taper(vue(ui_dump(), "nav_dashboard")):
            print("onglet tableau de bord introuvable")
            return 1
        time.sleep(2)
        if not taper(vue(ui_dump(), "btn_settings")):
            print("bouton réglages introuvable")
            return 1
        if not attendre(20, lambda: len(edittexts(ui_dump())) >= 10):
            print("dialog réglages introuvable")
            return 1
        champs = edittexts(ui_dump())
        # 6 = gguf_url, 7 = gguf_models : montrer le pont rempli
        remplacer_champ(champs[6][0], champs[6][1],
                        "http://192.168.1.149:8080")
        champs = edittexts(ui_dump())
        if len(champs) > 7:
            remplacer_champ(champs[7][0], champs[7][1], "fastmodel")
        time.sleep(1)                      # clavier déjà fermé par remplacer_champ
        capturer("05-settings-pont-llm.png")
        # fermeture propre du dialog (Annuler : rien n'a besoin d'être sauvé)
        tree = ui_dump()
        if tree is not None:
            for n in tree.iter():
                if (n.get("resource-id") or "") == "android:id/button2":
                    taper(centre(n))
                    break
        time.sleep(1.5)

        print("\n=== 5 captures dans %s ===" % SORTIE)
        return 0
    finally:
        # L'émulateur réutilisé était déjà allumé : on le laisse tourner
        # (utile pour d'autres captures/manipulations manuelles).
        if proc is not None:
            try:
                cmd([ADB, "-s", SERIAL, "emu", "kill"], timeout=30)
            except Exception:
                pass
            try:
                proc.wait(timeout=30)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
