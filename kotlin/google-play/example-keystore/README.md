# ⚠️ CLÉS D'EXEMPLE — NE JAMAIS UTILISER EN PRODUCTION ⚠️

Ce dossier contient une clé de signature **D'EXEMPLE**, volontairement
commitée pour permettre de tester le build `bundleRelease` sans générer
sa propre clé.

- keystore   : `swarmglu-example.keystore`
- alias      : `swarmglu-example`
- mot de passe : `swarmglu-example`  (public, tout le monde le connaît !)

## ⚠️ À CHANGER IMPÉRATIVEMENT POUR VOTRE APPLICATION ⚠️

Une clé commitée et à mot de passe public n'a AUCUNE valeur de sécurité :
n'importe qui pourrait signer une mise à jour de VOTRE application si
vous la publiiez avec. Avant toute vraie publication :

1. Générez VOTRE clé (README §8 quater) :
   keytool -genkeypair -v -keystore android/swarmglu-release.keystore \
     -alias swarmglu -keyalg RSA -keysize 2048 -validity 10000 \
     -storetype PKCS12 -storepass "$PASS" -keypass "$PASS" \
     -dname "CN=SwarmGlu, OU=Tree4Five Tools, O=Tree4Five, L=Djerba, C=TN"
2. Copiez `android/keystore.properties.example` vers
   `android/keystore.properties` avec VOS valeurs ;
3. Sauvegardez keystore + mot de passe HORS LIGNE (perdus = plus aucune
   mise à jour Play possible) ;
4. Ne committez JAMAIS votre vraie clé (déjà exclu par .gitignore).

Pour utiliser l'exemple quand même (test uniquement) :
   cp google-play/example-keystore/keystore.properties.example-android \
      android/keystore.properties
