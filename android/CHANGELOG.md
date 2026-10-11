# Changelog — SwarmGlu Android (com.tree4five.swarmglu)

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
