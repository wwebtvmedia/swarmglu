# Play Console — formulaire « Sécurité des données » (réponses exactes)

SwarmGlu ne collecte **aucune donnée** et n'en partage **aucune**. Les
réponses ci-dessous correspondent champ par champ au formulaire
(Play Console ▸ Présence sur Play ▸ Sécurité des données).

## 1. « Votre application collecte-t-elle ou partage-t-elle l'une des données utilisateur requises ? »

> **Non** — l'application fonctionne entièrement sur l'appareil et sur le
> réseau Wi-Fi local ; aucun serveur du développeur n'est contacté,
> aucune donnée ne sort du réseau de l'école.

Conséquence : le formulaire se termine là ; la fiche affichera
« Aucune donnée partagée avec des tiers · Aucune donnée collectée ».

## 2. Justification à conserver par écrit (en cas de revue manuelle)

- L'application embarque un moteur natif Kotlin local : tout
  traitement (routage des questions, consensus, journal d'audit) est
  exécuté sur l'appareil.
- Les communications réseau sont exclusivement **intranet** : HTTP en
  clair vers les pairs SwarmGlu du même Wi-Fi (ports 7001, UDP 47479).
  Aucune requête Internet sortante n'est émise par le code applicatif.
- Le journal d'audit local (`swarm_audit.jsonl`) reste sur l'appareil ;
  les questions des élèves y sont **hachées** (SHA-256) par défaut.
- Aucun identifiant publicitaire, aucun SDK tiers de mesure, aucun
  compte utilisateur.

## 3. Points de vigilance (transparence)

| Rubrique Play | Réalité SwarmGlu | Déclaration |
|---|---|---|
| Données collectées | aucune (tout est local) | « Non » |
| Données partagées | aucune | « Non » |
| Chiffrement en transit | LAN HTTP clair entre pairs — mais **aucune donnée personnelle ne transite vers Internet** | champ non applicable (aucune donnée) |
| Suppression possible | journal local supprimable en désinstallant l'app | champ non applicable |

## 4. Déclaration e-mail de contact

L'adresse saisie dans « Coordonnées » est publique ; utiliser une adresse
dédiée au projet plutôt qu'une adresse personnelle.
