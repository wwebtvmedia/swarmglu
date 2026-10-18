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

### 3.3 Étendre la zone : point d'accès + routage applicatif

Sur **Android**, le bouton « 📡 Étendre l'essaim » du tableau de bord ouvre
un [hotspot local-only](https://developer.android.com/develop/connectivity/wifi/localonlyhotspot)
(`startLocalOnlyHotspot`, permissions localisation/Wi-Fi). La tablette
devient **routeur de l'essaim** : les appareils qui rejoignent le hotspot
(autres tablettes, agents HarnessDroid, ponts GGUF/LLMProvider — voir
`../harnessDroid`, `../LLMProvider`) atteignent le réseau principal par le
**relais applicatif** de ce nœud :

```bash
curl -X POST http://<routeur>:7001/relay -d '{
  "host": "192.168.1.249", "port": 3000,
  "path": "/osp/endpoint.json"}'   # → swarmknowledge, via le routeur
```

Le relais n'accepte que les destinations **privées** (RFC1918/loopback) —
jamais de proxy ouvert — et chaque relais est scellé dans l'audit
(événement `relayed`). Sur **PC Linux**, la même topologie se monte avec
NetworkManager : `nmcli device wifi hotspot ssid SwarmGlu password …`
(le nœud bind 0.0.0.0 et sert de routeur à l'identique). Scénario de test
complet (routeur + client hotspot + 192.168.1.249) :
`python3 tests/test_hotspot_scenario.py --deploy-249 youyou`.

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

## 7 bis. Authentification asymétrique (ML-KEM + ECDSA) et re-signature décentralisée

Pour renforcer la sécurité contre l'usurpation et garantir un système **totalement décentralisé** sans autorité centrale permanente, SwarmGlu intègre une couche d'authentification asymétrique hybride post-quantique et un consensus 1/8 :

1. **Connexion sécurisée hybride (ML-KEM + ECDSA)** :
   - **ECDSA** (SECP256R1 / NIST P-256) : signature numérique des requêtes, preuves de consensus et whitelists.
   - **ML-KEM** (ML-KEM-768 / NIST FIPS 203) : mécanisme d'encapsulation de clé post-quantique (KEM) assurant la confidentialité persistante face à l'ordinateur quantique.
   - Poignée de main mutuelle (`/secure/handshake`) et dérivation d'une clé de session symétrique de 256 bits via HKDF-SHA256 pour les échanges chiffrés (`/secure/rpc`).

2. **Sélection aléatoire pondérée (Ancienneté + Volume de requêtes)** :
   - Périodiquement ou à la demande, un nœud est sélectionné aléatoirement pour vérifier et re-signer l'intégralité de la whitelist.
   - La probabilité de sélection d'un nœud $i$ est directement proportionnelle à son temps dans l'essaim ($T_i$) et à son nombre de requêtes traitées ($R_i$) :
     $$W_i = \max(T_i, 1.0) \times (R_i + 1)$$
     $$P(i) = \frac{W_i}{\sum_k W_k}$$
   - Les nœuds les plus anciens et les plus actifs ont une probabilité accrue d'être choisis, garantissant la légitimité tout en conservant une sélection ouverte et probabiliste.

3. **Consensus 1/8** :
   - Avant de pouvoir re-signer la whitelist, le nœud sélectionné doit obtenir l'approbation d'au moins **1/8 (12,5 %)** des nœuds actifs de l'essaim :
     $$\text{Quorum} = \max\left(1, \left\lceil \frac{N}{8} \right\rceil\right)$$
   - Chaque accord fait l'objet d'un vote signé numériquement par le pair (`POST /whitelist/consensus-select`).
   - Une fois le quorum 1/8 obtenu, le nœud vérifie les pairs, génère la nouvelle whitelist, la scelle avec sa signature ECDSA et sa preuve de consensus 1/8, et la diffuse à tout l'essaim (`POST /whitelist/resign`).

```bash
# Vérifier l'état de la whitelist signée
python3 cli.py whitelist verify-signed

# Déclencher manuellement un cycle de re-signature décentralisée
python3 cli.py whitelist resign --url http://127.0.0.1:7001

# Tester le handshake asymétrique ML-KEM + ECDSA
python3 cli.py secure-auth --url http://127.0.0.1:7001
```

## 8. Architecture et modules

| Fichier | Rôle |
|---|---|
| `crypto_auth.py` | `NodeCrypto`, `WeightedLottery`, `Consensus18Verifier`, `AuthenticatedWhitelist` : ML-KEM-768, ECDSA P-256, HKDF, AES-GCM, tirage pondéré, consensus 1/8 |
| `decentralized_resigner.py` | `DecentralizedResigner` : coordination de la sélection aléatoire, sollicitation du consensus 1/8, vérification des nœuds et re-signature |
| `decentralized_resigner.py` | Re-signature décentralisée de la whitelist : loterie pondérée (ancienneté × requêtes), consensus 1/8 signé |
| `security.py` | `SecurityManager` : whitelist dynamique et signée, horodatage précis, chaîne de hachage immuable + `verify_chain()` |
| `consensus.py` | `ConsensusManager` : élection par bail (heartbeats 3 s, bail 6 s), déclaration/résolution de conflits déterministe (IP la plus basse, puis node_id), failover automatique |
| `state_manager.py` | `StateManager` + `SwarmSynchronizer` : état global versionné par `epoch`, fusion LWW commutative validée par hash, anti-entropie périodique |
| `gateway.py` | `SwarmGateway` : digest exchange inter-essaims, registre de voisins (TTL 90 s), routage fédéré borné |
| `swarm_glu.py` | Serveur HTTP d'orchestration, endpoints sécurisés ML-KEM/ECDSA, re-signature décentralisée, pare-feu OSP v0.6, tâches MCP, beacon UDP |
| `cli.py` | Interface complète : `serve`, `status`, `ask`, `audit`, `whitelist`, `secure-auth`, `demo` |
| `android_bridge.py` | Pont Chaquopy : le nœud complet piloté depuis l'UI Kotlin |
| `android/` | Application `com.tree4five.swarmglu` (tableau de bord, écran élève, audit) |

### API HTTP (résumé)

| Route | Description |
|---|---|
| `GET /health`, `GET /status` | état du nœud / instantané complet (incluant métriques crypto et requêtes) |
| `POST /join` | enregistrement (whitelist ou approbation leader) |
| `POST /heartbeat`, `/leader-beat`, `/leader-announce` | consensus par bail |
| `GET /registry`, `/state/digest` · `POST /state/sync` | synchronisation LWW |
| `POST /ask` | requête d'élève (routage + OSP) ; `{"swarm_id": "…"}` pour fédérer |
| `POST /infer` · `POST /provider/register` | pont GGUF local |
| `POST /agent/task` · `/agent/task/{id}/validate` · `/agent/dispatch` | MCP + HITL |
| `POST /whitelist/proposal` · `/approve` | approbation par le leader |
| `POST /secure/handshake` | poignée de main asymétrique hybride post-quantique (ML-KEM + ECDSA) |
| `POST /secure/rpc` | appel RPC authentifié et chiffré via la clé de session ML-KEM |
| `POST /whitelist/consensus-select` | sollicitation du vote d'accord pour le consensus 1/8 |
| `POST /whitelist/resign` | diffusion et application de la whitelist re-signée avec consensus 1/8 |
| `POST /whitelist/trigger-resign` | déclenchement du cycle de sélection et re-signature décentralisée |
| `GET /whitelist/signed` | consultation de la whitelist signée actuelle et de sa preuve de consensus |
| `POST /gateway/sync` · `/gateway/ask` | passerelle inter-essaims |
| `POST /relay` | **routage du hotspot** : le nœud routeur relaie les clients vers les services privés du réseau principal (RFC1918/loopback uniquement) |
| `GET /knowledge` | **savoir commun** : réponses VERIFIED partagées à tout l'essaim (count + digest) |
| `GET /audit/verify` · `/audit/tail?n=` | chaîne de hachage |

## 8 bis. Publication Google Play

Tout le dossier de soumission est préparé dans [`google-play/`](google-play/) :
checklist pas-à-pas, fiches magasin FR/EN, réponses « Sécurité des
données » (aucune donnée collectée), classification 3+, visuels
(icône 512, bannière 1024×500 — régénérables via `make_assets.py`), et
signature release (`android/keystore.properties` + keystore locaux, non
versionnés). Build de l'AAB : `cd android && ./gradlew bundleRelease`.

## 8 ter. Tests

Tout-en-un (non-régression complète, voir `run_all.py`) :

```bash
python3 run_all.py                        # rapide : noyau + résilience (~1 min)
python3 run_all.py --full --ssh-249 youyou  # + tous les scénarios réseau (~25 min)
python3 run_all.py --android              # + tests dans Android (ADB)
```

Les niveaux, unitaires :

```bash
# 1) Suite noyau (64 tests, < 25 s, sans réseau externe) :
#    whitelist/chaîne de hachage (altération, suppression, réordonnancement),
#    élection déterministe, baux et abdications, fusion LWW commutative,
#    passerelle/garde anti-boucle, pare-feu OSP, + intégration en processus
#    (élection, routage /ask, basculement après mort du leader, HITL,
#    fédération de deux essaims).
python3 -m unittest discover -s tests   # 125 tests (dont crypto 1/8 + repli pur-Python)

# 2) Essaim réel MULTI-ÉMULATEURS (2 Android + 1 PC, ~5 min) :
#    crée et boote deux AVD, forme un essaim aux timings du cahier des
#    charges (3 s/6 s), vérifie convergence, requête d'élève OSP, TUE le
#    leader, vérifie la réélection (~8 s) et l'intégrité des chaînes.
python3 tests/test_multi_emulator_swarm.py

# 3) Interop swarmknowledge + partage du savoir (« share all ») :
#    vérifie le nœud OSP vivant sur 192.168.1.249, ancre une réponse sur
#    son endpoint, et confirme que TOUTES les connaissances VERIFIED sont
#    propagées à tous les nœuds (PC + émulateurs) — même digest partout,
#    pare-feu, failover, audit. Déploiement distant si clé SSH :
#    python3 tests/test_swarmknowledge_sharing.py --deploy-249 <user>
python3 tests/test_swarmknowledge_sharing.py

# 4) MULTI-ESSAIMS : deux salles fédérées sur machines réelles (2 émulateurs
#    + 2 PC, + .249 dans la salle-B avec --deploy-249) : convergence
#    indépendante de chaque salle, Digest Exchange, savoir CLOISONNÉ par
#    essaim, requêtes fédérées croisées, mort du leader de salle-A
#    (réélection ~9 s) SANS impact sur salle-B, audit intègre partout.
python3 tests/test_multi_swarm_federation.py --deploy-249 youyou

# 5) Boucle complète DANS Android (émulateur ou tablette USB) :
cd android && ./gradlew connectedDebugAndroidTest
```

Ces suites ont déjà corrigé deux vrais bugs de production : le 503
fournisseur pendant la fenêtre de basculement (soi-même doit toujours
compter comme vivant) et le gel du compteur de pairs vivants (rafraîchis-
sement volatile bloqué par la porte LWW logique).

## 8 quater. Génération des clés (Android & nœuds)

### A. Clé de signature Android (obligatoire pour Google Play)

Deux méthodes **équivalentes** (vérifiées toutes deux) — PKCS#12 dans les
deux cas, le fichier va dans `android/` avec `keystore.properties` :

**Méthode 1 — keytool (fourni avec le JDK, recommandée) :**

```bash
PASS=$(openssl rand -base64 24)        # mot de passe fort, à conserver !
keytool -genkeypair -v \
    -keystore android/swarmglu-release.keystore -alias swarmglu \
    -keyalg RSA -keysize 2048 -validity 10000 -storetype PKCS12 \
    -storepass "$PASS" -keypass "$PASS" \
    -dname "CN=SwarmGlu, OU=Tree4Five Tools, O=Tree4Five, L=Djerba, C=TN"
```

**Méthode 2 — openssl (alternative) :**

```bash
openssl genrsa -out cle.pem 2048
openssl req -new -x509 -key cle.pem -out cert.pem -days 10000 \
    -subj "/CN=SwarmGlu/O=Tree4Five/L=Djerba/C=TN"
openssl pkcs12 -export -out android/swarmglu-release.keystore \
    -inkey cle.pem -in cert.pem -name swarmglu -passout pass:"$PASS"
rm cle.pem                              # la clé nue ne doit pas traîner
```

Puis renseigner `android/keystore.properties` :

```properties
storeFile=swarmglu-release.keystore
storePassword=<le mot de passe>
keyAlias=swarmglu
keyPassword=<le mot de passe>
```

⚠️ **Sauvegarder le keystore + le mot de passe hors machine** (clé USB) :
jamais versionnés (déjà exclus par .gitignore), et perdus = plus AUCUNE
mise à jour possible de l'app sur Play. Vérification :
`keytool -list -keystore android/swarmglu-release.keystore`.

### B. Clés des nœuds (ECDSA P-256 + ML-KEM-768) — automatiques

Les clés cryptographiques des nœuds (authentification hybride,
re-signature 1/8) sont **générées automatiquement** au premier démarrage
et persistées dans `<data_dir>/keys_<node_id>.json` — rien à faire. Pour
régénérer l'identité d'un nœud : arrêter le nœud, supprimer ce fichier,
redémarrer. Clé publique d'un nœud (à partager pour vérification) :

```bash
python3 -c "from crypto_auth import NodeCrypto; \
    import sys; c=NodeCrypto('mon-noeud', key_dir='data/mon-noeud'); \
    print('ECDSA:', c.ecdsa_pub_hex)"
```

## 8 quinquies. Flotte complexe — LLM réels multi-sources

`python3 tests/test_fleet_full.py --model fastmodel:latest` : le jour
d'école complet — **8 à 9 nœuds** (leader Kotlin JVM, nœud Python
fournisseur de l'**Ollama de l'école 192.168.1.194** — meilleur modèle
chargeable auto-sélectionné, `bestmodel:latest` documenté GPU-OOM —,
nœud SSH 192.168.1.249, nœud SSH 192.168.1.149 si réveillée, 2
émulateurs, Note 10, tablette) avec **DEUX LLM réels** :

* l'Ollama 27B de l'école (réponse réelle mesurée : « La capitale de la
  Tunisie, c'est Tunis. » en 7,7 s via far-249) ;
* **l'inférence llama.cpp ON-DEVICE de la tablette** : son pont OSP local
  (Tree4Five LLM Provider, `/v1` + token lu sur l'appareil) est détecté,
  déclaré au registre, et une question `model_hint=llmprovider` est
  routée vers elle (« Tunisie », 31 s mesurées) ;
* le pont OSP de l'écosystème est exercé en direct : PROPOSE →
  `/osp/teach` → **BID compétence 0,733** (quorum q=2 documenté) ;
* savoir commun au même digest sur tous les nœuds, failover après
  `kill -9` du leader Kotlin, audits intègres.

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

## 10. OSP v0.6 — pare-feu anti-hallucination et RAG distribué

Toute réponse VERIFIED entre automatiquement dans le **savoir commun**
(meta `knowledge:*` de l'état distribué, propagé à tous les nœuds par la
fusion LWW — événement `knowledge_shared` scellé). `GET /knowledge` liste
ce savoir commun ; interopérable avec l'écosystème swarmknowledge (les
sources d'ancrage `osp://…` des nœuds OSP réels, p.ex.
`192.168.1.249:3000/osp/endpoint.json`).

Chaque réponse d'inférence reçoit un indice de véracité ancrée :
`g` = valeur fournie par le modèle, sinon heuristique des sources citées
(0 source → 0.0 ; n sources → `min(1, 0.4 + 0.2(n−1))`). Si
`g ≥ 0.30` : **VERIFIED**, la réponse est éligible au RAG distribué ;
sinon **UNVERIFIED** — rendue à l'élève mais exclue du pool partagé
(transparence pédagogique, événement `request_routed` scellé avec le
verdict).

---

*Tree4Five Tools — IA souveraine, locale, décentralisée. SwarmGlu v0.1.0.*
