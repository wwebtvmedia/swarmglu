# Changelog — SwarmGlu Android (com.tree4five.swarmglu)

## 2.0.0-alpha.1 (versionCode 5) — noyau NATIF Kotlin

- CHAQUOPY/PYTHON RETIRÉS : l'app embarque le noyau SwarmGlu porté en
  Kotlin pur (core/ : Json canonique, chaîne de hachage, LWW, élection
  par bail, NanoHTTPD) — APK **8,8 Mo** au lieu de 62 Mo.
- CRYPTO POST-QUANTIQUE NATIVE : ECDSA P-256 (java.security) + ML-KEM-768
  (BouncyCastle) — `/secure/handshake` et `/secure/rpc` réels sur Android,
  fin du mode mock mobile.
- Interopérabilité prouvée : blocs d'audit écrits par l'app vérifiés
  bloc à bloc par le moteur Python ; essaim tri-langage réel validé
  (Kotlin JVM + 2 Python + Android natif) avec failover croisé.
- HITL natif : barrière de validation humaine des tâches agent portée.
- Clé d'exemple commitée (google-play/example-keystore/, mot de passe
  public — À CHANGER, voir son README).

## 1.1.2 (versionCode 4)

- Correctif de protocole ANTI-HERMITE : un leader sans aucun follower
  re-probe ses graines et abdique devant un leader légitime — les
  partitions de bootstrap guérissent seules (événement hermit_rejoined).
- Validé en essaim TRI-LANGAGE réel : Kotlin JVM (leader) + 2 Python PC
  + Android (Chaquopy) — savoir commun partagé aux 4 nœuds, failover
  croisé ~2 s, audits intègres.

## 1.1.1 (versionCode 3)

- Correctif crypto : la propriété `mode` avait avalé la fin du
  `__init__` de NodeCrypto (clés jamais générées sur certains chemins) —
  réparé et couvert par la suite (112 tests).
- `/status` expose `crypto_mode` (cryptography réel vs mock) — diagnostic
  immédiat de l'interopérabilité PQ sur chaque machine de l'essaim.
- Test de communication multi-machines (Note 10 + tablette + émulateur +
  192.168.1.249) intégré à la non-rgression.

## 1.1.0 (versionCode 2)

- SÉCURITÉ : authentification hybride post-quantique ML-KEM-768 + ECDSA
  (poignée de main mutuelle, RPC chiffré HKDF/AES-GCM) et re-signature
  DÉCENTRALISÉE de la whitelist : loterie pondérée (ancienneté × requêtes)
  et consensus 1/8 signé — sans autorité centrale.
- « 📡 Étendre l'essaim » : point d'accès Wi-Fi local-only — la tablette
  devient routeur ; les appareils joints (harnessDroid, LLMProvider,
  tablettes) atteignent le réseau de l'école via le relais applicatif
  (destinations privées uniquement, chaque relais scellé dans l'audit).
- SAVOIR COMMUN : chaque réponse vérifiée (OSP v0.6, g ≥ 0.30) est partagée
  automatiquement à toutes les machines de l'essaim.
- Stabilité : basculement leader renforcé (soi-même toujours vivant,
  vivacité propagée par la fusion d'état). Suite : 112 tests noyau +
  orchestrateurs multi-machines (2 essaims fédérés, hotspot, .249).

## 1.0.0 (versionCode 1) — première publication Play

- Nœud SwarmGlu complet embarqué (Python 3.11 via Chaquopy 17) :
  élection de leader par bail, chaîne d'audit scellée, pare-feu OSP v0.6,
  passerelle inter-essaims — protocole strictement identique aux PC.
- Interface 3 écrans : tableau de bord (rôle/pairs/whitelist), élève
  (question à l'essaim, verdict de véracité), audit (vérification de la
  chaîne de hachage).
- Service de premier plan avec verrous Wi-Fi/multicast/wake : la tablette
  reste un pair actif écran éteint.
- Fournisseur d'inférence de démonstration intégré (une tablette suffit).
- Préparation Google Play : targetSdk 35, signature release (keystore
  local), R8, icône adaptative, opt-out edge-to-edge Android 15.
