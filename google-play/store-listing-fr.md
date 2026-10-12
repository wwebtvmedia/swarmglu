# Fiche Play — français (à coller dans la Play Console)

## Titre (≤ 30 caractères)

```
SwarmGlu — essaim IA hors-ligne
```

## Description courte (≤ 80 caractères)

```
Reliez PC et tablettes en essaim d'IA local, sans Internet ni serveur.
```

## Description longue (≤ 4000 caractères)

```
SwarmGlu transforme les tablettes et téléphones d'une classe en un essaim
d'intelligence locale, sans Internet et sans serveur central. Pensé pour
les écoles (PoC Tree4Five, Djerba), il fait tourner sur chaque appareil un
nœud du réseau SwarmGlu : les machines se découvrent sur le Wi-Fi, élisent
un coordinateur, se surveillent par battements de cœur et se remplacent
automatiquement si l'une tombe en panne.

N'importe quelle tablette peut être débranchée : l'essaim élit son
successeur en quelques secondes, sans intervention.

POUR LES ÉLÈVES
• Posez une question à l'essaim : elle est routée vers la machine qui
  héberge le meilleur modèle local (Qwen, Llama, Phi…).
• Pare-feu anti-hallucination OSP : chaque réponse affiche son indice de
  véracité ancrée (sources citées) et son verdict — les réponses non
  sourcées sont signalées et exclues du savoir partagé.
• Savoir commun : chaque réponse vérifiée est partagée automatiquement à
  toutes les machines de l'essaim (PC, tablettes) — la classe construit
  une base de connaissances commune, vérifiable à tout moment.

POUR LES ENSEIGNANTS
• Tableau de bord temps réel : rôle du nœud, coordinateur élu, pairs
  vivants, essaim, passerelle inter-salles.
• Journal d'audit inaltérable : chaque requête, élection et
  synchronisation est scellée par une chaîne de hachage vérifiable en un
  tap.
• Validation humaine : les tâches déléguées aux agents autonomes
  (HarnessDroid/MCP) ne partent qu'après votre approbation explicite.
• Liste blanche : seuls les appareils approuvés rejoignent l'essaim.

CARACTÉRISTIQUES
• 100 % hors-ligne : aucune donnée ne quitte le réseau Wi-Fi local.
• Zéro serveur central : élection de leader par bail (IP la plus basse),
  basculement automatique en ~3 s.
• Consensus distribué : état global versionné (epoch), synchronisation
  Last-Write-Wins validée par empreinte cryptographique.
• Sécurité post-quantique : authentification ML-KEM-768 + ECDSA, et
  re-signature décentralisée de la whitelist par consensus 1/8 signé —
  aucune autorité centrale.
• Étendre la zone : un bouton transforme la tablette en point d'accès
  Wi-Fi routeur de l'essaim (relais sécurisé vers le réseau de l'école).
• Passerelle inter-essaims : reliez deux salles de classe en un tap
  (nœud ambassadeur).
• Noyau natif Kotlin ultra-léger (APK < 9 Mo) strictement interopérable
  avec les nœuds de la classe : le protocole reste identique sur tout l'essaim.

L'application inclut un fournisseur de démonstration : une seule tablette
suffit pour l'essayer. Pour un essaim complet, lancez aussi SwarmGlu sur
un PC (github.com/wwebtvmedia/swarmglu).

SwarmGlu est un module de Tree4Five Tools — IA souveraine, locale et
décentralisée.
```

## Mots-clés (si utilisés pour l'ASO)

```
IA hors-ligne, réseau local, école, décentralisé, swarm, consensus, IA souveraine
```
