#!/usr/bin/env bash
# ==============================================================================
# SwarmGlu — essaim_natif.sh
# ==============================================================================
# Crée et VALIDE un essaim SwarmGlu RÉSILIENT en pur NATIF :
#   AUCUN podman, AUCUN émulateur Android — N processus Python ordinaires
#   sur cette machine (macOS/Linux, réseau local de la salle).
#
# Scénario complet vérifié de bout en bout :
#   [1/7] préflight (python3, curl, ports libres)
#   [2/7] whitelist partagée de la classe
#   [3/7] démarrage des N nœuds (graines en maillage complet → un nœud
#         quelconque peut REDÉMARRER et REJOINDRE même si le leader est mort)
#   [4/7] convergence : exactement UN leader
#   [5/7] requête d'élève /ask + savoir partagé IDENTIQUE partout (digest)
#   [6/7] RÉSILIENCE : kill -9 du leader → réélection → /ask de survie →
#         REDÉMARRAGE du leader mort → il rejoint sans partition
#   [7/7] audits : chaîne de hachage INTACTE sur CHAQUE nœud
#
# Usage :
#   ./essaim_natif.sh                 # 3 nœuds, validation complète, arrêt
#   ./essaim_natif.sh --nodes 5       # essaim plus grand (min 3)
#   ./essaim_natif.sh --base-port 7201
#   ./essaim_natif.sh --laisser       # laisse l'essaim tournant à la fin
#   ./essaim_natif.sh --stop          # arrête l'essaim laissé par --laisser
#   ./essaim_natif.sh --help
#
# Résultat attendu : "ESSAIM NATIF RÉSILIENT VALIDÉ ✔"
# Données : data/natif/ (whitelist, journaux, pids) — ignorées par git.
# (Variante SANS ce scénario : install_essaim.sh installe et démarre ;
#  variante conteneurs : cf. howto.txt §0 — test podman 2 instances.)
# ==============================================================================

set -euo pipefail

RACINE="$(cd "$(dirname "$0")" && pwd)"
cd "$RACINE"

N=3
BASE=7101
LAISSER=0
STOP=0

usage() { sed -n '3,32p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --nodes)     shift; N="${1:?--nodes exige un nombre}"; ;;
        --base-port) shift; BASE="${1:?--base-port exige un port}"; ;;
        --laisser)   LAISSER=1 ;;
        --stop)      STOP=1 ;;
        -h|--help)   usage; exit 0 ;;
        *) echo "Option inconnue : $1 (voir --help)" >&2; exit 2 ;;
    esac
    shift
done

DIR="data/natif"
WL="$DIR/whitelist.json"

ok()    { printf '      ✓ %s\n' "$1"; }
avert() { printf '      ! %s\n' "$1"; }
etape() { printf '\n[%s] %s…\n' "$1" "$2"; }

# Échec : journal de chaque nœud + arrêt propre (trap EXIT).
echec() {
    printf '      ✗ %s\n' "$1" >&2
    for nid in "${IDS[@]:-}"; do
        if [ -f "$DIR/$nid/serve.log" ]; then
            echo "--- $nid/serve.log (fin) ---" >&2
            tail -8 "$DIR/$nid/serve.log" >&2 || true
        fi
    done
    exit 1
}

# --- identités et ports -------------------------------------------------------
IDS=()
for i in $(seq 0 $((N - 1))); do IDS+=("natif-$i"); done
port_de() { echo $((BASE + $1)); }

peers_pour() { # indice du nœud → liste ip:port des AUTRES (maillage complet)
    local self="$1" out="" j
    for j in $(seq 0 $((N - 1))); do
        [ "$j" = "$self" ] && continue
        out="${out:+$out,}127.0.0.1:$(port_de "$j")"
    done
    echo "$out"
}

# --- HTTP ---------------------------------------------------------------------
# consensus port → "role leader epoch" (ou DOWN)
consensus() {
    curl -fsS --max-time 3 "http://127.0.0.1:$1/status" 2>/dev/null | python3 -c "
import sys, json
try:
    c = json.load(sys.stdin)['consensus']
    print(c.get('role', '?'), c.get('leader_id') or '-', c.get('epoch', 0))
except Exception:
    print('DOWN - -')
" || echo "DOWN - -"
}

vivant() { # port →0 si /health répond
    curl -fsS --max-time 2 "http://127.0.0.1:$1/health" >/dev/null 2>&1
}

# --- processus ----------------------------------------------------------------
demarrer() { # indice
    local idx="$1" nid port peers
    nid="${IDS[$idx]}"; port="$(port_de "$idx")"; peers="$(peers_pour "$idx")"
    mkdir -p "$DIR/$nid"
    nohup python3 -u cli.py serve --node-id "$nid" --port "$port" \
        --swarm-id salle-natif --whitelist-file "$WL" \
        --data-dir "$DIR/$nid" --advertise-ip 127.0.0.1 \
        --mode full --mock-inference --beacon-port 0 \
        --heartbeat-interval 1.0 --lease-timeout 2.0 \
        --peers "$peers" --log-level WARNING \
        >> "$DIR/$nid/serve.log" 2>&1 &
    echo $! > "$DIR/$nid.pid"
}

tuer() { # indice
    local pidf="$DIR/${IDS[$1]}.pid"
    [ -f "$pidf" ] || return 0
    kill -9 "$(cat "$pidf")" 2>/dev/null || true
}

# --- nettoyage (trap) ---------------------------------------------------------
nettoyer() {
    local code=$?
    if [ "$LAISSER" -eq 1 ] && [ "$code" -eq 0 ]; then
        echo ""
        echo "Essaim NATIF laissé actif :"
        for idx in "${!IDS[@]}"; do
            echo "    http://127.0.0.1:$(port_de "$idx")   (${IDS[$idx]})"
        done
        echo "    Arrêt : ./essaim_natif.sh --stop"
        return 0
    fi
    for idx in "${!IDS[@]}"; do
        local pidf="$DIR/${IDS[$idx]}.pid"
        [ -f "$pidf" ] && kill "$(cat "$pidf")" 2>/dev/null || true
    done
    sleep 1
    for idx in "${!IDS[@]}"; do
        local pidf="$DIR/${IDS[$idx]}.pid"
        [ -f "$pidf" ] && kill -9 "$(cat "$pidf")" 2>/dev/null || true
        rm -f "$pidf"
    done
    if [ "$code" -eq 0 ]; then echo ""; echo "Nœuds natifs arrêtés (data conservée : $DIR)."; fi
}
trap nettoyer EXIT

# --stop : arrête un essaim laissé tournant puis sort --------------------------
if [ "$STOP" -eq 1 ]; then
    for idx in "${!IDS[@]}"; do
        pidf="$DIR/${IDS[$idx]}.pid"
        if [ -f "$pidf" ]; then
            kill "$(cat "$pidf")" 2>/dev/null || true
            echo "arrêt ${IDS[$idx]}"
        fi
    done
    sleep 1
    rm -f "$DIR"/*.pid 2>/dev/null || true
    echo "Essaim natif arrêté."
    exit 0
fi

# ------------------------------------------------------------------------------
# [1/7] Préflight
# ------------------------------------------------------------------------------
etape "1/7" "préflight"
command -v python3 >/dev/null 2>&1 || echec "python3 introuvable (./install_essaim.sh)"
command -v curl     >/dev/null 2>&1 || echec "curl introuvable (./install_essaim.sh)"
[ "$N" -ge 3 ] || echec "--nodes ≥ 3 requis (la réélection a besoin de survivants)"
for idx in "${!IDS[@]}"; do
    p="$(port_de "$idx")"
    if python3 -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1', int(sys.argv[1]))) == 0 else 1)" "$p"; then
        echec "port $p déjà occupé (démo en cours ? --base-port pour changer)"
    fi
done
ok "python3 + curl — ${IDS[*]} sur les ports $BASE…$((BASE + N - 1)) libres"

# ------------------------------------------------------------------------------
# [2/7] Whitelist partagée
# ------------------------------------------------------------------------------
etape "2/7" "whitelist partagée"
if [ -f "$WL" ]; then
    ok "déjà présente : $WL"
else
    mkdir -p "$DIR"
    ids_csv="$(IFS=,; echo "${IDS[*]}")"
    python3 cli.py whitelist init --nodes "$ids_csv" \
        --whitelist-file "$WL" --data-dir "$DIR" >/dev/null
    ok "whitelist créée : $ids_csv"
fi

# ------------------------------------------------------------------------------
# [3/7] Démarrage des nœuds
# ------------------------------------------------------------------------------
etape "3/7" "démarrage de ${IDS[*]}"
for idx in "${!IDS[@]}"; do demarrer "$idx"; done
deadline=$((SECONDS + 25))
tous=0
while [ $SECONDS -lt $deadline ]; do
    tous=1
    for idx in "${!IDS[@]}"; do
        vivant "$(port_de "$idx")" || { tous=0; break; }
    done
    [ "$tous" = 1 ] && break
    sleep 1
done
[ "$tous" = 1 ] || echec "tous les nœuds ne répondent pas /health"
ok "tous les nœuds en vie (maillage de graines complet)"

# ------------------------------------------------------------------------------
# [4/7] Convergence : UN leader
# ------------------------------------------------------------------------------
etape "4/7" "convergence"
leader_id=""; epoch1=0
deadline=$((SECONDS + 40))
while [ $SECONDS -lt $deadline ]; do
    leaders=0; l=""; e=0; pret=1
    for idx in "${!IDS[@]}"; do
        read -r role lid ep <<< "$(consensus "$(port_de "$idx")")"
        if [ "$role" = "DOWN" ]; then pret=0; break; fi
        if [ "$role" = "leader" ]; then leaders=$((leaders + 1)); l="$lid"; e="$ep"; fi
    done
    if [ "$pret" = 1 ] && [ "$leaders" = 1 ]; then leader_id="$l"; epoch1="$e"; break; fi
    sleep 1
done
[ -n "$leader_id" ] || echec "aucun leader unique élu (co-proclamations persistantes ?)"
for idx in "${!IDS[@]}"; do
    read -r role lid ep <<< "$(consensus "$(port_de "$idx")")"
    echo "         ${IDS[$idx]} → rôle $role"
done
ok "leader élu : $leader_id (epoch $epoch1)"

# ------------------------------------------------------------------------------
# [5/7] Requête d'élève + savoir partagé
# ------------------------------------------------------------------------------
etape "5/7" "requête /ask + savoir partagé"
rep=""
poser_ask() { # port
    curl -fsS --max-time 60 -X POST -H 'Content-Type: application/json' \
        -d '{"student_id":"eleve-natif","prompt":"Quelle est la capitale de la Tunisie ?"}' \
        "http://127.0.0.1:$1/ask" 2>/dev/null
}
idx_cible=1
rep="$(poser_ask "$(port_de "$idx_cible")")" || rep=""
cle="$(echo "$rep" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    k = (d.get('knowledge') or {}).get('knowledge_key')
    print(k or '')
except Exception:
    print('')
")" || cle=""
[ -n "$cle" ] || echec "/ask sans réponse valide ou savoir non partageable (réponse : ${rep:0:200})"
ok "/ask servi par ${IDS[$idx_cible]} — savoir partagé : $cle"
# Tous les nœuds doivent converger vers le MÊME digest de savoir.
deadline=$((SECONDS + 25))
partout=0
while [ $SECONDS -lt $deadline ]; do
    partout=1; ref=""
    for idx in "${!IDS[@]}"; do
        kd="$(curl -fsS --max-time 3 "http://127.0.0.1:$(port_de "$idx")/knowledge" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('count', 0), d.get('digest', ''))
except Exception:
    print(0, '')
")" || kd="0"
        set -- $kd
        if [ "${1:-0}" -lt 1 ]; then partout=0; break; fi
        if [ -z "$ref" ]; then ref="$2"; elif [ "$2" != "$ref" ]; then partout=0; break; fi
    done
    [ "$partout" = 1 ] && break
    sleep 1
done
[ "$partout" = 1 ] || echec "savoir non convergé sur tous les nœuds (digest différents)"
ok "savoir identique sur ${IDS[*]} (digest ${ref:0:16}…)"

# ------------------------------------------------------------------------------
# [6/7] Résilience : mort du leader, réélection, survie, redémarrage
# ------------------------------------------------------------------------------
etape "6/7" "mort du leader $leader_id (kill -9) → réélection → redémarrage"
idx_leader=-1
for idx in "${!IDS[@]}"; do [ "${IDS[$idx]}" = "$leader_id" ] && idx_leader=$idx; done
[ "$idx_leader" -ge 0 ] || echec "leader $leader_id inconnu des identités locales"

tuer "$idx_leader"
deadline=$((SECONDS + 8))
while [ $SECONDS -lt $deadline ]; do vivant "$(port_de "$idx_leader")" || break; sleep 1; done
vivant "$(port_de "$idx_leader")" && echec "le leader n'est pas tombé"

# Réélection : UN seul leader d'epoch STRICTEMENT supérieure, stable2 relevés.
new_leader=""; epoch2=0; stable=0
deadline=$((SECONDS + 30))
while [ $SECONDS -lt $deadline ]; do
    leaders=0; l=""; e=0
    for idx in "${!IDS[@]}"; do
        [ "$idx" = "$idx_leader" ] && continue
        read -r role lid ep <<< "$(consensus "$(port_de "$idx")")"
        if [ "$role" = "leader" ] && [ "$ep" -gt "$epoch1" ]; then
            leaders=$((leaders + 1)); l="$lid"; e="$ep"
        fi
    done
    if [ "$leaders" = 1 ]; then
        if [ "$l" = "$new_leader" ]; then stable=$((stable + 1)); else new_leader="$l"; stable=1; fi
        if [ "$stable" -ge 2 ]; then epoch2="$e"; break; fi
    else
        new_leader=""; stable=0
    fi
    sleep 1
done
[ "$epoch2" -gt 0 ] || echec "aucune réélection stable (epoch $epoch1 → ?)"
ok "réélection en ${epoch1}→${epoch2} : $new_leader"

# L'essaim survit : /ask sur un survivant.
idx_survivant=-1
for idx in "${!IDS[@]}"; do
    [ "$idx" = "$idx_leader" ] && continue
    vivant "$(port_de "$idx")" && { idx_survivant=$idx; break; }
done
rep="$(poser_ask "$(port_de "$idx_survivant")")" || rep=""
echo "$rep" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('answer') else 1)" \
    || echec "/ask impossible après la mort du leader"
ok "/ask servi par ${IDS[$idx_survivant]} après basculement"

# Redémarrage du leader mort : il doit REJOINDRE sans former de partition.
demarrer "$idx_leader"
deadline=$((SECONDS + 20))
rejoint=0
while [ $SECONDS -lt $deadline ]; do
    if vivant "$(port_de "$idx_leader")"; then
        # Partout : exactement UN leader et un leader_id unique.
        leaders=0; cibles=0; lid_ref=""
        pret=1
        for idx in "${!IDS[@]}"; do
            read -r role lid ep <<< "$(consensus "$(port_de "$idx")")"
            if [ "$role" = "DOWN" ]; then pret=0; break; fi
            cibles=$((cibles + 1))
            [ "$role" = "leader" ] && leaders=$((leaders + 1))
            if [ -z "$lid_ref" ]; then lid_ref="$lid"; elif [ "$lid" != "$lid_ref" ]; then pret=0; break; fi
        done
        if [ "$pret" = 1 ] && [ "$leaders" = 1 ]; then rejoint=1; break; fi
    fi
    sleep 1
done
[ "$rejoint" = 1 ] || echec "le nœud redémarré n'a pas rejoign l'essaim (partition ?)"
ok "leader redémarré → REJOINT : ${IDS[$idx_leader]} en vie ; un seul leader ($lid_ref)"

# ------------------------------------------------------------------------------
# [7/7] Audits de chaque nœud
# ------------------------------------------------------------------------------
etape "7/7" "audits des chaînes de hachage"
audits_ok=1
for idx in "${!IDS[@]}"; do
    verif="$(curl -fsS --max-time 5 "http://127.0.0.1:$(port_de "$idx")/audit/verify" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print('1' if d.get('ok') else '0', d.get('entries', 0))
except Exception:
    print('0', 0)
")" || verif="0 0"
    set -- $verif
    if [ "$1" = "1" ]; then
        printf '      ✓ audit %-8s : INTACTE — %s bloc(s)\n' "${IDS[$idx]}" "$2"
    else
        printf '      ✗ audit %-8s : CORROMPUE\n' "${IDS[$idx]}" >&2
        audits_ok=0
    fi
done
[ "$audits_ok" = 1 ] || echec "chaîne d'audit corrompue"

echo ""
echo "================================================================"
echo "ESSAIM NATIF RÉSILIENT VALIDÉ ✔ — ${IDS[$idx_leader]} a été tué,"
echo "réélu ($epoch1→$epoch2), redémarré et a rejoint ; savoir identique"
echo "partout ; ${#IDS[@]} chaînes d'audit intactes."
echo "================================================================"
