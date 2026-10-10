# SwarmGlu — Application Android (com.tree4five.swarmglu)

Application de l'essaim pour tablettes et téléphones Android du PoC
Tree4Five (Djerba). Elle embarque **le même noyau Python** que les PC via
[Chaquopy](https://chaquo.com/chaquopy/) : le protocole (élection par bail,
chaîne de hachage, whitelist, passerelle) ne peut pas diverger entre
plateformes.

## Construire l'APK / l'AAB

Chaîne **compilée et validée localement** (même combinaison que
harnessDroid) : **AGP 8.7.2 · Gradle 8.9 · SDK android-36 · Chaquopy 17
(Maven Central) · targetSdk 36** — au-dessus de l'exigence Play (35) et
couvrant l'alignement mémoire 16 Ko.

1. Ouvrir le dossier `android/` dans **Android Studio**. La synchronisation
   Gradle télécharge AGP/Kotlin/Chaquopy — *seule la machine de build a
   besoin d'Internet, une seule fois*.
2. **Debug (test tablette)** : `Build ▸ Build APK(s)` → `app-debug.apk`,
   ou `adb install app-debug.apk`.
3. **Release Google Play (AAB signé)** : la clé est déjà générée
   (`swarmglu-release.keystore` + `keystore.properties`, non versionnés —
   **faites une sauvegarde hors ligne**). Puis :

   ```bash
   cd android
   ./gradlew bundleRelease   # → app/build/outputs/bundle/release/app-release.aab
   ```

4. Suivre [`../google-play/PLAY_CHECKLIST.md`](../google-play/PLAY_CHECKLIST.md)
   pour la soumission Play Console (fiche, sécurité des données,
   classification, captures).

Aucun paquet pip n'est requis : le noyau SwarmGlu n'utilise que la
bibliothèque standard Python. La tâche Gradle `syncSwarmGluCore`
re-copie automatiquement les modules depuis la racine du dépôt à chaque
build (source de vérité unique).

## Tests (émulateur ou tablette USB)

La boucle SwarmGlu complète est validée par un test instrumenté exécuté
DANS Android — émulateur `tablet` (Pixel Tablet, API 34) ou appareil réel :

```bash
cd android && ./gradlew connectedDebugAndroidTest
```

Quatre tests (`SwarmGluNodeInstrumentedTest`) : bootstrap/auto-élection du
leader, requête d'élève avec pare-feu OSP v0.6 (g=0.6, VERIFIED), barrière
humaine des tâches agent (blocage sans validation, cycle unique), et
rejeu de la chaîne de hachage INTACTE. Statut au 10/10/2026 : **4/4 PASS**
sur l'émulateur local (Android 14).

## Utilisation en classe

1. **Tableau de bord** → interrupteur « Nœud » : démarre le pair SwarmGlu
   (service de premier plan : la tablette reste un membre actif de
   l'essaim, écran éteint compris).
2. **⚙ Réglages** : identifiant, port (7001), essaim (`salle-1`), mode
   (`follower` conseillé pour les tablettes élèves), seeds `ip:port` du
   PC professeur, mock d'inférence (démo) ou URL du pont GGUF local.
3. **Élève** : poser une question — routage vers le meilleur modèle local,
   verdict du pare-feu OSP v0.6 (g ≥ 0.30) affiché.
4. **Audit** : vérifier la chaîne de hachage de la tablette à tout moment.

## Conseils Android (terrain)

- Désactiver l'« optimisation de batterie » pour l'application sur les
  tablettes élèves : sinon Doze suspend les heartbeats et la tablette est
  déclarée morte par l'essaim (déclenchant des élections inutiles).
- Le beacon UDP de découverte nécessite le `MulticastLock` (acquis par le
  service). Sur certains routeurs scolaires, le broadcast est filtré :
  renseigner alors les seeds `ip:port` dans les réglages.
- Une tablette peut créer le réseau elle-même : **Mode point d'accès
  mobile** → les PC et autres tablettes s'y joignent ; aucun routeur
  n'est nécessaire.
