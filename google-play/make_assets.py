#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmGlu — google-play/make_assets.py
=====================================
Génère tous les visuels requis par la Google Play Console :

* icône applicative 512×512 (PNG, ≤ 1 Mo) ;
* bannière « feature graphic » 1024×500 (PNG) ;
* mipmap icônes lanceur toutes densités (48→192 px, carré + rond) pour le
  repli Android 7 (l'icône adaptative XML couvre Android 8+).

Requis : Pillow (uniquement sur la machine qui prépare la fiche Play —
jamais sur les tablettes). Usage :

    python3 google-play/make_assets.py
"""

import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Pillow manquant : pip install pillow (machine de build uniquement)")

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
RES = os.path.join(RACINE, "android", "app", "src", "main", "res")

# Palette SwarmGlu (voir res/values/colors.xml).
BLEU_FONCE = (13, 71, 161)      # #0D47A1
BLEU = (21, 101, 192)           # #1565C0
ORANGE = (255, 111, 0)          # #FF6F00
BLANC = (255, 255, 255)


def police(taille: int, gras: bool = True) -> ImageFont.FreeTypeFont:
    """DejaVu (Debian/Ubuntu) avec replis raisonnables."""
    candidats = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if gras
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" if gras
        else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    for chemin in candidats:
        if os.path.exists(chemin):
            return ImageFont.truetype(chemin, taille)
    return ImageFont.load_default()


def fond_degrade(l: int, h: int) -> Image.Image:
    """Dégradé vertical bleu foncé -> bleu."""
    img = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        img.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(BLEU_FONCE, BLEU)))
    return img.resize((l, h))


def dessiner_essaim(draw: ImageDraw.ImageDraw, l: int, h: int, rayon: float) -> None:
    """Le motif SwarmGlu : trois nœuds reliés (un essaim)."""
    top = (l * 0.50, h * 0.30)
    gauche = (l * 0.26, h * 0.68)
    droite = (l * 0.74, h * 0.68)
    trait = max(2, int(rayon * 0.30))
    # Liens d'abord (sous les nœuds).
    for a, b in ((top, gauche), (top, droite), (gauche, droite)):
        draw.line([a, b], fill=BLANC, width=trait)
    for centre, couleur in ((top, BLANC), (gauche, BLANC), (droite, ORANGE)):
        draw.ellipse([centre[0] - rayon, centre[1] - rayon,
                      centre[0] + rayon, centre[1] + rayon], fill=couleur)


def icone_512() -> None:
    img = fond_degrade(512, 512)
    draw = ImageDraw.Draw(img)
    dessiner_essaim(draw, 512, 512, rayon=52)
    img.save(os.path.join(ASSETS, "icon-512.png"), optimize=True)
    print("✓ google-play/assets/icon-512.png (512×512)")


def banniere_1024x500() -> None:
    img = fond_degrade(1024, 500)
    draw = ImageDraw.Draw(img)
    # Motif à gauche, textes à droite.
    dessiner_essaim(draw, 500, 500, rayon=58)
    titre = police(96)
    sous = police(40, gras=False)
    draw.text((560, 170), "SwarmGlu", font=titre, fill=BLANC)
    draw.text((564, 290), "Essaim d'IA locale et hors-ligne", font=sous, fill=(220, 228, 240))
    draw.text((564, 345), "pour l'école — Tree4Five Tools", font=sous, fill=(220, 228, 240))
    img.save(os.path.join(ASSETS, "feature-graphic-1024x500.png"), optimize=True)
    print("✓ google-play/assets/feature-graphic-1024x500.png")


def mipmaps() -> None:
    """ic_launcher.png + ic_launcher_round.png pour mdpi→xxxhdpi (repli < API 26)."""
    tailles = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    for densite, px in tailles.items():
        carre = fond_degrade(px, px)
        draw = ImageDraw.Draw(carre)
        dessiner_essaim(draw, px, px, rayon=px * 0.10)
        dossier = os.path.join(RES, "mipmap-%s" % densite)
        os.makedirs(dossier, exist_ok=True)
        carre.save(os.path.join(dossier, "ic_launcher.png"), optimize=True)

        rond = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        rond.paste(carre, (0, 0))
        masque = Image.new("L", (px, px), 0)
        ImageDraw.Draw(masque).ellipse([0, 0, px - 1, px - 1], fill=255)
        rond.putalpha(masque)
        rond.save(os.path.join(dossier, "ic_launcher_round.png"), optimize=True)
        print("✓ mipmap-%s/ic_launcher(.png|_round.png) (%dpx)" % (densite, px))


if __name__ == "__main__":
    os.makedirs(ASSETS, exist_ok=True)
    icone_512()
    banniere_1024x500()
    mipmaps()
    print("\nVisuels Play générés — importez google-play/assets/ dans la Play Console.")
