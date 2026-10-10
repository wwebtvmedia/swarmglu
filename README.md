# SwarmGlu — la « glu » de l'essaim Tree4Five

**SwarmGlu** est le module de liaison, de consensus et de routage réseau de
la suite **Tree4Five Tools** (IA souveraine, locale et décentralisée).
Conçu pour un déploiement réel sur un **Wi-Fi local hétérogène et
totalement hors-ligne** (PC Windows/Linux, tablettes et téléphones
Android) — dimensionné pour le PoC d'intelligence collective dans les
écoles de Djerba.

```
            ┌──────────────────────── Wi-Fi local (aucun Internet) ───────────────────────┐
            │                                                                             │
   PC prof  │  tablette A        tablette B         PC salle     tablette C               │
 ┌─────────┐│ ┌──────────────┐  ┌──────────────┐  ┌──────────┐  ┌──────────────┐          │
 │ SwarmGlu││ │ SwarmGlu     │  │ SwarmGlu     │  │ SwarmGlu │  │ SwarmGlu     │          │
 │ leader  │◄─┤ follower     ├──┤ follower     ├──┤ follower │◄─┤ ambassador  ├──► salle-2
 │ GGUF ───┤│ │ mock/qwen    │  │ llama        │  │ phi      │  │ HarnessDroid │   (autre
 └─────────┘│ └──────────────┘  └──────────────┘  └──────────┘  └──────────────┘    essaim)
            └── heartbeats 3 s · bail 6 s · élection IP-la-plus-basse · audit scellé ──┘
```

Il unit les briques de l'écosystème : le **Pont GGUF** local
(`com.tree4five.gguf` — modèles Qwen/Llama/Phi via API Ollama/OpenAI), les
agents **HarnessDroid** (`com.ai.harnessdroid`, protocole MCP avec barrière
humaine) et le **protocole OSP v0.6** (pare-feu anti-hallucination,
véracité ancrée `g ≥ 0.30`).

**Zéro dépendance** : Python 3.8+ bibliothèque standard uniquement — le
même code tourne sur PC et dans l'application Android (Chaquopy), sans
pip ni Internet.

---

## 1. Installation

```bash
# PC (Windows/Linux) — rien à installer, Python 3.8+ suffit
python3 cli.py --help

# Android : voir android/README.md (Android Studio + Chaquopy,
# l'APK fonctionne ensuite 100 % hors-ligne).
```

## 2. Initialisation de la whitelist

La whitelist (`whitelist.json`) est la **racine de confiance** de
l'essaim : sans elle (ou sans approbation par le leader), aucun nœud ne
peut s'enregistrer ni participer.

```bash
# Générer la whitelist de la classe (puis copie USB sur chaque machine)
python3 cli.py whitelist init --nodes pc-prof,tablette-1,tablette-2,tablette-3

# Administration courante
python3 cli.py whitelist list
python3 cli.py whitelist add --node-id tablette-4
python3 cli.py whitelist remove --node-id tablette-4
python3 cli.py whitelist digest          # empreinte SHA-256 publique
```

Deux voies d'entrée légitimes (cahier des charges) : figurer dans le
fichier **ou** être approuvé dynamiquement par le consensus du Leader
(`POST /join` → événement `whitelist_approved` scellé dans l'audit, puis
propagé à tout l'essaim par synchronisation d'état). `--strict` désactive
la seconde voie.

## 3. Lancer l'essaim

### 3.1 Plusieurs instances sur la même machine (test)

```bash
python3 cli.py serve --node-id n1 --port 7101 --mock-inference --beacon-port 0 --data-dir data/n1
python3 cli.py serve --node-id n2 --port 7102 --mock-inference --beacon-port 0 \
    --peers 127.0.0.1:7101 --data-dir data/n2
python3 cli.py serve --node-id n3 --port 7103 --mock-inference --beacon-port 0 \
    --peers 127.0.0.1:7101 --data-dir data/n3

python3 cli.py status --url http://127.0.0.1:7101
```

### 3.2 Réseau Wi-Fi réel (PC + Android)

1. Brancher un point d'accès (ou utiliser le **mode point d'accès mobile**
   d'une tablette — aucun routeur requis). Repérer les IP (`ip addr` /
   Paramètres Wi-Fi Android).
2. Sur le PC du professeur :

   ```bash
   python3 cli.py serve --node-id pc-prof --port 7001 --mode full \
       --whitelist-file whitelist.json \
       --gguf-url http://127.0.0.1:11434 --gguf-models qwen2.5:0.5b \
       --advertise-ip 192.168.4.10
   ```

3. Sur les tablettes : application SwarmGlu → ⚙ Réglages (node-id, seeds
   `192.168.4.10:7001`, mode `follower`) → interrupteur « Nœud ».
   Sur un PC supplémentaire :

   ```bash
   python3 cli.py serve --node-id pc-eleve --port 7001 --mode follower \
       --peers 192.168.4.10:7001 --advertise-ip 192.168.4.11
   ```

4. Vérifier : `python3 cli.py status --url http://192.168.4.10:7001`

**Découverte** : les nœuds s'annoncent par beacon UDP broadcast
(port 47479, `MulticastLock` acquis sur Android). Si le routeur filtre le
broadcast, les seeds `--peers ip:port` prennent le relais.

## 4. Utiliser l'essaim

```bash
# Requête d'élève (routée vers le meilleur fournisseur, pare-feu OSP)
python3 cli.py ask --url http://192.168.4.10:7001 \
    --prompt "Quelle est la capitale de la Tunisie ?" --student eleve-1

# Requête FÉDÉRÉE vers l'essaim de la salle voisine
python3 cli.py ask --url http://192.168.4.10:7001 --swarm salle-2 \
    --prompt "Question pour la classe 2" --student eleve-1
```

Tâche agent HarnessDroid avec validation humaine (HITL) :

```bash
curl -X POST http://192.168.4.10:7001/agent/task -d '{
  "student_id": "eleve-1",
  "mcp": {"server": "harnessdroid", "tool": "rechercher_web_local",
          "args": {"requete": "volcans de Djerba"}}}'
# → {"task": {"task_id": "a1b2...", "status": "pending_validation", ...}}
# L'enseignant lève la barrière :
curl -X POST http://192.168.4.10:7001/agent/task/a1b2.../validate \
     -d '{"approved": true, "by": "professeur"}'
```

## 5. Protocole de test de résilience

### Automatisé (recommandé)

```bash
python3 cli.py demo --nodes 3
```

Déroulé : 3 nœuds locaux → leader élu (epoch 1) → **`kill -9` du leader**
(équivalent tablette hors tension) → **réélection automatique** (epoch 2,
nœud d'IP/ID le plus bas parmi les vivants) → requête d'élève servie par
l'essaim survivant → **audit des 3 chaînes de hachage** (INTACTES, y
compris celle du nœud tué — figée mais vérifiable). Sortie attendue :
`RÉSILIENCE VALIDÉE ✔`.

### Manuel (sur le vrai Wi-Fi)

1. Démarrer 3 nœuds ; noter le leader (`cli.py status`).
2. **Débrancher brutalement** la machine leader (ou `kill -9 <pid>`).
3. Observer sur un survivant : pendant ≤ 6 s, `role: candidate`
   (élection), puis `role: follower/leader` avec `epoch` incrémenté et un
   nouveau `leader_id` — sans aucune intervention humaine.
4. `python3 cli.py audit verify --data-dir data/<node>` sur chaque
   machine : toutes les chaînes restent INTACTES.

## 6. Auditabilité (chaîne « Scorona-like »)

Chaque événement (requête, routage, enregistrement, élection,
synchronisation, échange de passerelle, validation humaine) est scellé :

```
hash = sha256(canonical(event_data) + previous_hash + str(request_time))
```

avec `request_time` (arrivée), `processing_time` (durée), `timestamp`
(ISO-8601 UTC du scellement), `seq` (anti-trou) et `previous_hash`
(chaînage). Toute modification/suppression/réordonnancement casse la
chaîne :

```bash
python3 cli.py audit verify --data-dir data/pc-prof   # ✓ INTACTE — N blocs
python3 cli.py audit tail --data-dir data/pc-prof -n 20
```

Vie privée : les prompts des élèves sont **hashés** par défaut dans le
journal (`--log-raw-prompts` pour les conserver en clair, déconseillé).

## 7. Passerelle multi-essaims

Relier deux salles (chaque essaim a sa propre whitelist et son leader) :

```bash
# Salle 2 (par exemple)
python3 cli.py serve --node-id salle2-pc --port 7001 --swarm-id salle-2 ...

# Salle 1 : un nœud ambassadeur pointe vers l'ambassadeur de la salle 2
python3 cli.py serve --node-id salle1-pc2 --port 7001 --swarm-id salle-1 \
    --mode ambassador --neighbor 192.168.5.20:7001 ...
```

Toutes les 10 s, les ambassadeurs échangent leurs **digests
cryptographiques** (`/gateway/sync` : epoch, hash d'état, nœuds, modèles,
digest de whitelist), événements scellés `gateway_exchange`. Une requête
`--swarm salle-2` est alors routée via l'ambassadeur connu (garde
anti-boucle : 2 sauts max).

## 8. Architecture et modules

| Fichier | Rôle |
|---|---|
| `security.py` | `SecurityManager` : whitelist dynamique, horodatage précis, chaîne de hachage immuable + `verify_chain()` |
| `consensus.py` | `ConsensusManager` : élection par bail (heartbeats 3 s, bail 6 s), déclaration/résolution de conflits déterministe (IP la plus basse, puis node_id), failover automatique |
| `state_manager.py` | `StateManager` + `SwarmSynchronizer` : état global versionné par `epoch`, fusion LWW commutative validée par hash, anti-entropie périodique |
| `gateway.py` | `SwarmGateway` : digest exchange inter-essaims, registre de voisins (TTL 90 s), routage fédéré borné |
| `swarm_glu.py` | Serveur HTTP d'orchestration, routage d'inférence (pont GGUF Ollama/OpenAI), pare-feu OSP v0.6 (g ≥ 0.30), tâches MCP HarnessDroid avec HITL, beacon UDP |
| `cli.py` | Interface complète : `serve`, `status`, `ask`, `audit`, `whitelist`, `demo` |
| `android_bridge.py` | Pont Chaquopy : le nœud complet piloté depuis l'UI Kotlin |
| `android/` | Application `com.tree4five.swarmglu` (tableau de bord, écran élève, audit) |

### API HTTP (résumé)

| Route | Description |
|---|---|
| `GET /health`, `GET /status` | état du nœud / instantané complet |
| `POST /join` | enregistrement (whitelist ou approbation leader) |
| `POST /heartbeat`, `/leader-beat`, `/leader-announce` | consensus par bail |
| `GET /registry`, `/state/digest` · `POST /state/sync` | synchronisation LWW |
| `POST /ask` | requête d'élève (routage + OSP) ; `{"swarm_id": "…"}` pour fédérer |
| `POST /infer` · `POST /provider/register` | pont GGUF local |
| `POST /agent/task` · `/agent/task/{id}/validate` · `/agent/dispatch` | MCP + HITL |
| `POST /whitelist/proposal` · `/approve` | approbation par le leader |
| `POST /gateway/sync` · `/gateway/ask` | passerelle inter-essaims |
| `GET /audit/verify` · `/audit/tail?n=` | chaîne de hachage |

## 9. Modèle de sécurité (et limites assumées du PoC)

- **Participation** : whitelist pré-partagée hors-ligne (USB) ou
  approbation leader journalisée — l'union des deux se propage par
  synchronisation d'état.
- **Auditabilité** : chaîne de hachage locale, vérifiable par tout
  contrôleur (`cli.py audit verify`). *Perspective PoC suivante : gossip
  des derniers hashes entre pairs pour un audit répliqué.*
- **Limites assumées** : le LAN d'école roule en HTTP clair (aucune PKI
  hors-ligne) ; l'identité déclarée (`node_id`) n'est pas encore signée
  par secret partagé (extension : HMAC par nœud via champ `secret` de la
  whitelist). Ne pas exposer l'essaim au-delà du Wi-Fi de confiance.

## 10. OSP v0.6 — pare-feu anti-hallucination

Chaque réponse d'inférence reçoit un indice de véracité ancrée :
`g` = valeur fournie par le modèle, sinon heuristique des sources citées
(0 source → 0.0 ; n sources → `min(1, 0.4 + 0.2(n−1))`). Si
`g ≥ 0.30` : **VERIFIED**, la réponse est éligible au RAG distribué ;
sinon **UNVERIFIED** — rendue à l'élève mais exclue du pool partagé
(transparence pédagogique, événement `request_routed` scellé avec le
verdict).

---

*Tree4Five Tools — IA souveraine, locale, décentralisée. SwarmGlu v0.1.0.*
