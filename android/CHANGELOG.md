# Changelog — SwarmGlu Android (com.tree4five.swarmglu)

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
