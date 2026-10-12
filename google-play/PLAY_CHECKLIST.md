# Google Play — checklist de soumission SwarmGlu

Guide pas à pas pour publier `com.tree4five.swarmglu`. Tout ce qui est
machine-faisable a été préparé dans ce dépôt ; les étapes ☐ restent
humaines (compte Play Console, 25 USD une fois).

## 0. État de préparation (déjà fait dans ce dépôt)

- ✅ **targetSdk 36** — au-dessus de l'exigence Play pour toute nouvelle
  app (35) ; chaîne **compilée et validée localement** : AGP 8.7.2 ·
  Gradle 8.9 · SDK android-36 (même combinaison que harnessDroid).
  Chaquopy 17 (Maven Central) couvre l'alignement mémoire 16 Ko.
- ✅ **Clé de signature release** générée : `android/swarmglu-release.keystore`
  + `android/keystore.properties` (NON versionnés). **SAUVEGARDEZ-LES hors
  machine** : sans elles, aucune mise à jour future de l'app n'est possible.
- ✅ R8 activé (minify + shrinkResources) avec règles Chaquopy.
- ✅ Icône adaptative + repli toutes densités (API 24/25).
- ✅ Visuels de fiche : `google-play/assets/icon-512.png`,
  `feature-graphic-1024x500.png` (régénérables : `make_assets.py`).
- ✅ `versionCode 6` / `versionName "2.0.0"` — **noyau natif Kotlin**
  (plus de Python embarqué, APK 8,8 Mo), crypto PQ native, fournisseur
  LLM on-device (pont OSP), hotspot/relais, savoir commun. Signature
  release + R8 vérifiés par smoke-test installé.

## 1. Compiler l'AAB signé (machine avec Android Studio + Internet au 1er build)

```bash
cd android
./gradlew bundleRelease        # → app/build/outputs/bundle/release/app-release.aab
# Vérification d'usage local avant la Play Console :
./gradlew installRelease       # installe la version signée sur une tablette USB
```

L'AAB doit être **signé par la clé release** (keystore.properties détecté
automatiquement). Play refuse un AAB signé debug.

## 2. Play Console — création et formulaire « Contenu de l'app »

1. ☐ [play.google.com/console](https://play.google.com/console) → **Créer
   une application** : nom `SwarmGlu`, langue par défaut Français, app
   **gratuite**, « App (téléphone/tablette) ».
2. ☐ **Déclaration d'accessibilité** : aucune promotion spécifique —
   répondre « non » aux sections publicitaires/du Web non concernées.
3. ☐ **Sécurité des données** : suivre mot à mot
   [`DATA_SAFETY.md`](DATA_SAFETY.md) — aucune donnée collectée, aucune
   donnée partagée, aucune publication hors appareil.
4. ☐ **Classer le contenu** : questionnaire dans
   [`CONTENT_RATING.md`](CONTENT_RATING.md) →Attendu : **3+ / Tous publics**.
5. ☐ **Public cible et contenu** : groupe d'âge 13+ (établissement
   scolaire ; l'app ne cible pas intentionnellement <13 ans), pas de
   contenu sensible.
6. ☐ **Sélection des pays** : commencer par **Tunisie** (PoC Djerba) —
   extensible ensuite.
7. ☐ **Coordonnées** : e-mail de contact obligatoire (affiché publiquement).

## 3. Fiche Play (Store listing)

- ☐ Textes prêts à coller : [`store-listing-fr.md`](store-listing-fr.md)
  et [`store-listing-en.md`](store-listing-en.md) (titre ≤ 30 car.,
  description courte ≤ 80 car., description longue ≤ 4000 car.).
- ☐ Icône 512×512 : `assets/icon-512.png`.
- ☐ Bannière : `assets/feature-graphic-1024x500.png`.
- ☑ **Captures d'écran** : 4 captures natives 2560×1440 dans
  `assets/screenshots/` (dashboard, LEADER, réponse OSP, audit) ;
- ☑ ~~Captures d'écran~~ **4 captures réelles fournies** (émulateur Pixel
  Tablet, 2560×1440, 16:9) : `assets/screenshots/` — tableau de bord
  (nœud arrêté puis LEADER élu), réponse élève avec verdict OSP vert,
  audit « CHAÎNE INTACTE ». Importer directement dans la fiche.

## 4. Test interne → production (recommandé pour un PoC école)

1. ☐ **Test interne** : téléverser `app-release.aab`, ajouter les listes
   de testeurs (e-mails des enseignants), obtention du lien
   d'installation en ~5 min — validation sur 2-3 tablettes réelles.
2. ☐ Relecture Play : compter ~1 à 7 jours. Préparer une note de review :
   *« Application éducative destinée à un réseau Wi-Fi local d'école ;
   sans autre nœud SwarmGlu sur le réseau, elle affiche son tableau de
   bord et reste fonctionnelle (mode démo intégré). »* — évite un rejet
   pour « app qui ne fait rien en solo ».
3. ☐ **Production** (ou diffusion restreinte « pays : Tunisie » si la
   fiche doit rester discrète pendant le PoC).

## 5. Après la première publication

- ☐ Inscription à la **signature d'application Play** (proposée au 1er
  téléversement : accepter — Play gère la clé remise aux utilisateurs ;
  notre keystore reste la clé de téléversement).
- ☐ Chaque mise à jour : `versionCode += 1` dans
  `android/app/build.gradle`, `./gradlew bundleRelease`, téléverser.
- ☐ Suivi : Play Console → Statistiques/Vitals ; les tablettes scolales
  sans Google Services peuvent aussi installer l'AAB via
  `adb install` ou [bundletool](https://developer.android.com/tools/bundletool).

## Pièges connus (gagnés sur le terrain)

| Symptôme | Cause / correctif |
|---|---|
| « Your app is using an old targetSdk » | vérifier `targetSdk 36` dans app/build.gradle |
| AAB rejeté « not signed » | keystore.properties absent du dossier `android/` au moment du build |
| Review rejetée « app non fonctionnelle » | joindre la note de review du §4.2 (app en réseau local) |
| Écran vide au 1er lancement | l'app démarre ARRÊTÉE : activer l'interrupteur « Nœud » du tableau de bord |
