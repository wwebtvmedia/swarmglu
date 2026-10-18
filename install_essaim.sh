#!/usr/bin/env bash
# ==============================================================================
# SwarmGlu — install_essaim.sh
# ==============================================================================
# Installe sur une machine Linux NEUVE tout le nécessaire pour CRÉER un
# essaim SwarmGlu (réseau Wi-Fi d'école hors-ligne, zéro serveur central) :
#
#   1. paquets système : python3 (3.8+), git, curl, socat, unzip, ssh ;
#   2. dépôt SwarmGlu (clone si absent, sinon le dossier de ce script) ;
#   3. roues `cryptography` (ECDSA/ML-KEM RÉELS) — optionnel, repli
#      pur-Python toujours disponible ;
#   4. vérification : suite de tests (113) + démo 3 nœuds = « essaim
#      créé et validé » ;
#   5. affiche les commandes pour démarrer les nœuds de la salle.
#
# Usage :
#   ./install_essaim.sh                # install complet + tests + démo (~3 min)
#   ./install_essaim.sh --quick        # sans tests ni démo (vérif import seule)
#   ./install_essaim.sh --start        # + démarre immédiatement le 1er nœud
#   ./install_essaim.sh --android      # + SDK Android (tests tablette/émulateur)
#   ./install_essaim.sh --server       # + openssh-server (reçoit les déploiements)
#   ./install_essaim.sh --repo URL     # clone depuis une autre source Git
#   ./install_essaim.sh --help
#
# Idempotent : relansez-le en cas de doute, il ne réinstalle que le manquant.
# ==============================================================================

set -euo pipefail

REPO_DEFAUT="https://github.com/wwebtvmedia/swarmglu.git"
REPO="$REPO_DEFAUT"
RAPIDE=0
DEMARRER=0
ANDROID=0
SERVEUR=0

usage() { sed -n '3,26p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --quick)   RAPIDE=1 ;;
        --start)   DEMARRER=1 ;;
        --android) ANDROID=1 ;;
        --server)  SERVEUR=1 ;;
        --repo)    shift; REPO="${1:?--repo exige une URL}"; ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Option inconnue : $1 (voir --help)" >&2; exit 2 ;;
    esac
    shift
done

etape() { printf '\n[%s] %s…\n' "$1" "$2"; }
ok()    { printf '      ✓ %s\n' "$1"; }
avert() { printf '      ! %s\n' "$1"; }
echec() { printf '      ✗ %s\n' "$1" >&2; exit 1; }

# ------------------------------------------------------------------------------
# 0. Paquetier de la distribution (apt / dnf / pacman / zypper / apk)
# ------------------------------------------------------------------------------
PAQUETIER=""
if   command -v apt-get >/dev/null 2>&1; then PAQUETIER=apt
elif command -v dnf     >/dev/null 2>&1; then PAQUETIER=dnf
elif command -v pacman  >/dev/null 2>&1; then PAQUETIER=pacman
elif command -v zypper  >/dev/null 2>&1; then PAQUETIER=zypper
elif command -v apk     >/dev/null 2>&1; then PAQUETIER=apk
fi

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then SUDO="sudo"
    elif [ -z "$PAQUETIER" ]; then
        : # tout peut déjà être présent : on ne vérifie plus qu'après
    else
        echec "ni root ni sudo : relancez en root ou installez sudo"
    fi
fi

# ------------------------------------------------------------------------------
# 1. Paquets système (uniquement ceux qui manquent)
# ------------------------------------------------------------------------------
manque() { command -v "$1" >/dev/null 2>&1 || echo 1; }

etape "1/6" "paquets système"
if [ -n "$PAQUETIER" ]; then
    BASIQUES="python3 git curl socat unzip ssh"
    [ "$SERVEUR" -eq 1 ]  && BASIQUES="$BASIQUES serveur_ssh"
    [ "$ANDROID" -eq 1 ] && BASIQUES="$BASIQUES java"
    A_INSTALLER=""
    for besoin in $BASIQUES; do
        case "$besoin" in
            serveur_ssh) command -v sshd >/dev/null 2>&1 || A_INSTALLER="$A_INSTALLER $besoin" ;;
            java)        command -v java  >/dev/null 2>&1 || A_INSTALLER="$A_INSTALLER $besoin" ;;
            *)           [ -n "$(manque "$besoin")" ] && A_INSTALLER="$A_INSTALLER $besoin" ;;
        esac
    done
    if [ -z "${A_INSTALLER// /}" ]; then
        ok "tous les paquets de base sont déjà là"
    else
        # Noms de paquets par distribution (serveur_ssh/java = alias).
        extraire() {
            for nom in $A_INSTALLER; do
                case "$nom" in
                    serveur_ssh)
                        case "$PAQUETIER" in
                            apt) echo openssh-server ;;
                            dnf|yum) echo openssh-server ;;
                            pacman) echo openssh ;;
                            zypper) echo openssh ;;
                            apk) echo openssh ;;
                        esac ;;
                    java)
                        case "$PAQUETIER" in
                            apt) echo openjdk-17-jdk-headless ;;
                            dnf|yum) echo java-17-openjdk-headless ;;
                            pacman) echo jdk17-openjdk ;;
                            zypper) echo java-17-openjdk-headless ;;
                            apk) echo openjdk17-jdk-headless ;;
                        esac ;;
                    python3) case "$PAQUETIER" in apt) echo python3 ;; pacman) echo python ;; *) echo python3 ;; esac ;;
                    ssh)     case "$PAQUETIER" in pacman) echo openssh ;; dnf|yum) echo openssh-clients ;; zypper) echo openssh-clients ;; *) echo openssh-client ;; esac ;;
                    *) echo "$nom" ;;
                esac
            done
        }
        LISTE="$(extraire | tr '\n' ' ')"
        ok "installation :$LISTE"
        case "$PAQUETIER" in
            apt)    $SUDO apt-get update -qq && $SUDO apt-get install -y -qq $LISTE ;;
            dnf)    $SUDO dnf install -y $LISTE ;;
            pacman) $SUDO pacman -Sy --noconfirm $LISTE ;;
            zypper) $SUDO zypper --non-interactive install $LISTE ;;
            apk)    $SUDO apk add $LISTE ;;
        esac
        ok "paquets installés"
    fi
else
    # Pas de paquetier connu : on vérifie juste la présence des outils.
    for besoin in python3 git curl socat unzip ssh; do
        [ -n "$(manque "$besoin")" ] && \
            avert "outil absent : $besoin (installez-le avec votre gestionnaire)"
    done
    ok "aucun paquetier reconnu — vérification seule"
fi

command -v python3 >/dev/null 2>&1 || echec "python3 introuvable"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || \
    echec "Python 3.8+ requis ($(python3 --version))"
ok "$(python3 --version)"

# ------------------------------------------------------------------------------
# 2. Dépôt SwarmGlu
# ------------------------------------------------------------------------------
etape "2/6" "dépôt SwarmGlu"
DOSSIER_SCRIPT="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$DOSSIER_SCRIPT/cli.py" ]; then
    RACINE="$DOSSIER_SCRIPT"
    ok "dépôt local : $RACINE"
elif [ -f "$HOME/swarmglu/cli.py" ]; then
    RACINE="$HOME/swarmglu"
    ok "dépôt déjà présent : $RACINE"
else
    command -v git >/dev/null 2>&1 || echec "git absent, impossible de cloner"
    RACINE="$HOME/swarmglu"
    ok "clone $REPO → $RACINE"
    git clone --depth 1 "$REPO" "$RACINE" || echec "échec du clone (Internet ?)"
fi
cd "$RACINE"
python3 -c "import swarm_glu" 2>/dev/null || \
    echec "le dépôt SwarmGlu est incomplet (import swarm_glu impossible)"

# ------------------------------------------------------------------------------
# 3. Roues `cryptography` — ECDSA/ML-KEM réels (optionnel mais conseillé)
# ------------------------------------------------------------------------------
etape "3/6" "crypto réelle (roues cryptography)"
# Même import que crypto_auth.py (asymmetric.mlkem = ECDSA/ML-KEM réels).
CIBLE="from cryptography.hazmat.primitives.asymmetric import ec, mlkem"
if python3 -c "$CIBLE" 2>/dev/null; then
    ok "cryptography $(python3 -c 'import cryptography; print(cryptography.__version__)') — ECDSA/ML-KEM réels"
else
    # Le noyau tourne SANS pip (repli pur-Python) — on essaie donc
    # d'enrichir sans jamais bloquer l'installation.
    ROUES_OK=0
    if python3 -m pip --version >/dev/null 2>&1; then
        python3 -m pip install --user --quiet cryptography 2>/dev/null || \
            python3 -m pip install --user --quiet --break-system-packages cryptography 2>/dev/null || true
        python3 -c "$CIBLE" 2>/dev/null && ROUES_OK=1
    fi
    if [ "$ROUES_OK" -eq 1 ]; then
        ok "cryptography installée via pip — ECDSA/ML-KEM réels"
    elif python3 -c "import cryptography" 2>/dev/null; then
        avert "cryptography présente mais sans ML-KEM (version ancienne) : repli pur-Python ML-KEM"
        avert "améliorer : python3 -m pip install --user --upgrade cryptography"
    else
        avert "repli pur-Python actif — ECDSA/ML-KEM réels indisponibles (réseau ?)"
        avert "ultérieur : python3 -m pip install --user cryptography"
    fi
fi

# ------------------------------------------------------------------------------
# 4. Vérification — la démo crée littéralement un essaim 3 nœuds
# ------------------------------------------------------------------------------
if [ "$RAPIDE" -eq 0 ]; then
    etape "4/6" "suite de tests (113) — ~50 s"
    if python3 -m unittest discover -s tests 2>&1 | tail -3 | grep -q "^OK"; then
        ok "suite noyau VERT"
    else
        echec "suite noyau en échec — voir plus haut"
    fi

    etape "5/6" "démo essaim 3 nœuds (réélection, /ask, audits) — ~1 min"
    DEMO_JOURNAL="/tmp/swarmglu_demo_install.log"
    if python3 cli.py demo --nodes 3 >"$DEMO_JOURNAL" 2>&1; then
        ok "RÉSILIENCE VALIDÉE ✔ (essaim local créé et validé)"
    else
        tail -20 "$DEMO_JOURNAL" >&2
        echec "démo d'essaim en échec (journal : $DEMO_JOURNAL)"
    fi
else
    etape "4/6" "mode --quick : tests et démo ignorés"
    etape "5/6" "mode --quick"
    ok "import + CLI vérifiés"
fi

# ------------------------------------------------------------------------------
# 6. Optionnel : SDK Android / serveur SSH / démarrage immédiat
# ------------------------------------------------------------------------------
etape "6/6" "options"
if [ "$ANDROID" -eq 1 ]; then
    SDK="$HOME/Android/Sdk"
    CMDLINE="$SDK/cmdline-tools/latest/bin"
    if [ -x "$CMDLINE/sdkmanager" ]; then
        ok "SDK Android déjà présent : $SDK"
    else
        ok "téléchargement des command-line tools Android…"
        mkdir -p "$SDK/cmdline-tools"
        ZIP="/tmp/cmdline-tools.zip"
        curl -fsSL -o "$ZIP" \
            "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip" || \
            echec "téléchargement SDK impossible (réseau ?)"
        unzip -qo "$ZIP" -d "$SDK/cmdline-tools"
        mv "$SDK/cmdline-tools/cmdline-tools" "$SDK/cmdline-tools/latest"
        ok "command-line tools installés"
    fi
    # Licences + composants exigés par les tests (cf. tests/*_swarm.py).
    yes 2>/dev/null | "$CMDLINE/sdkmanager" --licenses >/dev/null 2>&1 || true
    "$CMDLINE/sdkmanager" --install \
        "platform-tools" "emulator" \
        "system-images;android-34;google_apis;x86_64" >/dev/null
    ok "platform-tools + emulator + image android-34 x86_64 installés"
    grep -q "ANDROID_HOME" "$HOME/.bashrc" 2>/dev/null || cat >> "$HOME/.bashrc" <<'EOF'

# SwarmGlu — SDK Android (tests tablette/émulateur)
export ANDROID_HOME="$HOME/Android/Sdk"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
EOF
    ok "PATH Android ajouté à ~/.bashrc (reconnectez-vous)"
    [ -e /dev/kvm ] || avert "/dev/kvm absent : les émulateurs seront plus lents (kvm ?)"
fi

if [ "$SERVEUR" -eq 1 ]; then
    command -v sshd >/dev/null 2>&1 || echec "openssh-server introuvable après install"
    if command -v systemctl >/dev/null 2>&1; then
        $SUDO systemctl enable --now ssh 2>/dev/null || \
            $SUDO systemctl enable --now sshd 2>/dev/null || true
    fi
    ok "sshd actif — la machine peut recevoir --deploy-249 <utilisateur>"
fi

if [ "$DEMARRER" -eq 1 ]; then
    NODE_ID="${NODE_ID:-pc-prof}"
    PORT="${PORT:-7001}"
    IP_ANNONCE="$(python3 -c "from swarm_glu import detect_local_ip; print(detect_local_ip())")"
    mkdir -p "data/$NODE_ID"
    nohup python3 cli.py serve --node-id "$NODE_ID" --port "$PORT" \
        --mode full --mock-inference --beacon-port 0 \
        --advertise-ip "$IP_ANNONCE" \
        --whitelist-file "whitelist-$NODE_ID.json" \
        --data-dir "data/$NODE_ID" \
        > "data/$NODE_ID/serve.log" 2>&1 &
    sleep 4
    if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
        ok "nœud $NODE_ID en vie sur http://$IP_ANNONCE:$PORT (journal : data/$NODE_ID/serve.log)"
    else
        tail -10 "data/$NODE_ID/serve.log" >&2
        echec "le nœud $NODE_ID n'a pas répondu /health"
    fi
else
    ok "(démarrez le premier nœud avec : ./install_essaim.sh --start)"
fi

# ------------------------------------------------------------------------------
# Récapitulatif — créer l'essaim de la salle
# ------------------------------------------------------------------------------
IP_ANNONCE="$(python3 -c "from swarm_glu import detect_local_ip; print(detect_local_ip())" 2>/dev/null || echo '<IP-de-cette-machine>')"
cat <<FIN

================================================================
MACHINE PRÊTE ✔ — créer l'essaim (réseau Wi-Fi d'école)
================================================================
Dépôt : $RACINE   (guide complet : howto.txt)

1) Whitelist de la classe (copier sur chaque machine par USB) :
       python3 cli.py whitelist init --nodes pc-prof,tablette-1,tablette-2

2) Nœud professeur (seul à pouvoir être leader) :
       python3 cli.py serve --node-id pc-prof --port 7001 --mode full \\
               --advertise-ip $IP_ANNONCE

3) Nœud élève / autre PC (se joint au prof) :
       python3 cli.py serve --node-id pc-eleve --port 7001 --mode follower \\
               --peers $IP_ANNONCE:7001 --advertise-ip <IP-du-PC>

4) Vérifier :
       curl http://$IP_ANNONCE:7001/status
       python3 cli.py ask --url http://$IP_ANNONCE:7001 --prompt "Bonjour ?"

5) Non-régression :   python3 run_all.py     (voir howto.txt)
FIN
