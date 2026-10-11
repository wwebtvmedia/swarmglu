# -*- coding: utf-8 -*-
"""
SwarmGlu — crypto_auth.py
=========================
Authentification asymétrique hybride post-quantique (ML-KEM + ECDSA),
sélection probabiliste décentralisée et consensus 1/8 pour la génération,
la vérification et la re-signature de la whitelist de l'essaim.

Spécifications :
1. **Authentification asymétrique hybride** :
   - **ECDSA** (SECP256R1 / NIST P-256) : signature numérique et identité vérifiable.
   - **ML-KEM** (ML-KEM-768 / NIST FIPS 203) : mécanisme d'encapsulation de clé
     post-quantique (Key Encapsulation Mechanism).
   - Dérivation de clé de session partagée via HKDF-SHA256.
   - Connexion sécurisée avec poignée de main (handshake) mutuellement authentifiée.

2. **Système totalement décentralisé** :
   - Aucun nœud maître ni autorité centrale permanente pour la whitelist.
   - Périodiquement et aléatoirement, un nœud est sélectionné pour vérifier et
     re-signer l'intégralité de la whitelist.

3. **Sélection pondérée (temps + requêtes)** :
   - La probabilité de sélection d'un nœud est proportionnelle à son ancienneté
     (temps dans l'essaim) et à son volume de requêtes traitées :
       W_i = max(T_i, 1.0) * (requests_i + 1)
       P_i = W_i / sum(W_k)
   - Les nœuds les plus anciens et les plus actifs ont une probabilité accrue,
     mais le tirage reste aléatoire et ouvert à tous les nœuds de l'essaim.

4. **Consensus 1/8** :
   - Avant de pouvoir vérifier et re-signer la whitelist, le nœud candidat doit
     obtenir l'accord d'au moins 1/8 (12,5 %) des nœuds actifs de l'essaim :
       quorum = max(1, ceil(total_nodes / 8))
   - Chaque accord est attesté par la signature numérique ECDSA du nœud votant.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from cryptography.hazmat.primitives.asymmetric import ec, mlkem
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAS_CRYPTOGRAPHY = True
except ImportError:  # pragma: no cover
    HAS_CRYPTOGRAPHY = False


def canonical_json(obj: Any) -> str:
    """Sérialisation JSON canonique déterministe."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes | str) -> str:
    """Empreinte SHA-256 en hexadécimal."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Module cryptographique asymétrique : ECDSA + ML-KEM
# ---------------------------------------------------------------------------

class NodeCrypto:
    """
    Gère les clés asymétriques ECDSA (signatures) et ML-KEM-768 (KEM post-quantique),
    ainsi que la négociation de clés de session et le chiffrement authentifié.
    """

    def __init__(self, node_id: str, key_dir: Optional[str] = None):
        self.node_id = node_id
        self.key_dir = key_dir
        self._ecdsa_priv: Any = None
        self._ecdsa_pub: Any = None
        self._mlkem_priv: Any = None
        self._mlkem_pub: Any = None
        self._mock_mode = not HAS_CRYPTOGRAPHY
        self._load_or_generate_keys()

    @property
    def mode(self) -> str:
        """Mode cryptographique effectif : « cryptography » (ECDSA/ML-KEM
        réels) ou « mock » (HMAC — REPLI sans interopérabilité)."""
        return "mock" if self._mock_mode else "cryptography"

    def _keys_filepath(self) -> Optional[str]:
        if not self.key_dir:
            return None
        return os.path.join(self.key_dir, f"keys_{self.node_id}.json")

    def _load_or_generate_keys(self) -> None:
        """Charge les clés existantes depuis key_dir ou en génère de nouvelles."""
        path = self._keys_filepath()
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if self._init_from_serialized(data):
                    return
            except Exception:
                pass

        self._generate_new_keys()
        self._save_keys()

    def _generate_new_keys(self) -> None:
        if not self._mock_mode:
            # Clé ECDSA (NIST P-256 / SECP256R1)
            self._ecdsa_priv = ec.generate_private_key(ec.SECP256R1())
            self._ecdsa_pub = self._ecdsa_priv.public_key()
            # Clé ML-KEM (ML-KEM-768, standard FIPS 203)
            self._mlkem_priv = mlkem.MLKEM768PrivateKey.generate()
            self._mlkem_pub = self._mlkem_priv.public_key()
        else:
            # Fallback simulé si la librairie cryptography est absente
            seed = hashlib.sha256(f"mock:{self.node_id}:{time.time()}".encode()).digest()
            self._ecdsa_priv = seed
            self._ecdsa_pub = hashlib.sha256(seed + b":pub").hexdigest()
            self._mlkem_priv = seed + b":mlkem_priv"
            self._mlkem_pub = hashlib.sha256(seed + b":mlkem_pub").hexdigest()

    def _init_from_serialized(self, data: Dict[str, Any]) -> bool:
        if not HAS_CRYPTOGRAPHY:
            self._ecdsa_priv = bytes.fromhex(data.get("ecdsa_priv_hex", ""))
            self._ecdsa_pub = data.get("ecdsa_pub_hex", "")
            self._mlkem_priv = bytes.fromhex(data.get("mlkem_priv_hex", ""))
            self._mlkem_pub = data.get("mlkem_pub_hex", "")
            return True

        try:
            ecdsa_pem = data.get("ecdsa_priv_pem", "").encode("utf-8")
            mlkem_pem = data.get("mlkem_priv_pem", "").encode("utf-8")
            self._ecdsa_priv = serialization.load_pem_private_key(ecdsa_pem, password=None)
            self._ecdsa_pub = self._ecdsa_priv.public_key()
            self._mlkem_priv = serialization.load_pem_private_key(mlkem_pem, password=None)
            self._mlkem_pub = self._mlkem_priv.public_key()
            return True
        except Exception:
            return False

    def _save_keys(self) -> None:
        path = self._keys_filepath()
        if not path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if not self._mock_mode:
            ecdsa_pem = self._ecdsa_priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ).decode("utf-8")
            mlkem_pem = self._mlkem_priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ).decode("utf-8")
            data = {
                "node_id": self.node_id,
                "ecdsa_priv_pem": ecdsa_pem,
                "ecdsa_pub_hex": self.ecdsa_pub_hex,
                "mlkem_priv_pem": mlkem_pem,
                "mlkem_pub_hex": self.mlkem_pub_hex,
            }
        else:
            data = {
                "node_id": self.node_id,
                "ecdsa_priv_hex": self._ecdsa_priv.hex(),
                "ecdsa_pub_hex": self._ecdsa_pub,
                "mlkem_priv_hex": self._mlkem_priv.hex(),
                "mlkem_pub_hex": self._mlkem_pub,
            }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)

    # --- Propriétés publiques des clés ---

    @property
    def ecdsa_pub_hex(self) -> str:
        """Clé publique ECDSA en hexadécimal (point non compressé X9.62)."""
        if self._mock_mode:
            return str(self._ecdsa_pub)
        raw = self._ecdsa_pub.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint
        )
        return raw.hex()

    @property
    def mlkem_pub_hex(self) -> str:
        """Clé publique ML-KEM-768 en hexadécimal (bytes bruts NIST)."""
        if self._mock_mode:
            return str(self._mlkem_pub)
        return self._mlkem_pub.public_bytes_raw().hex()

    # --- Signatures numériques ECDSA ---

    def sign(self, message: bytes | str) -> str:
        """Signe un message avec la clé privée ECDSA (retourne signature hex)."""
        if isinstance(message, str):
            message = message.encode("utf-8")
        if self._mock_mode:
            return hmac.new(self._ecdsa_priv, message, hashlib.sha256).hexdigest()
        sig_bytes = self._ecdsa_priv.sign(message, ec.ECDSA(hashes.SHA256()))
        return sig_bytes.hex()

    @staticmethod
    def verify_signature(message: bytes | str, signature_hex: str, ecdsa_pub_hex: str) -> bool:
        """Vérifie une signature ECDSA avec la clé publique fournie."""
        if isinstance(message, str):
            message = message.encode("utf-8")
        if not HAS_CRYPTOGRAPHY:
            expected = hmac.new(bytes.fromhex(ecdsa_pub_hex[:32]), message, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, signature_hex)
        try:
            pub_bytes = bytes.fromhex(ecdsa_pub_hex)
            pub_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub_bytes)
            sig_bytes = bytes.fromhex(signature_hex)
            pub_key.verify(sig_bytes, message, ec.ECDSA(hashes.SHA256()))
            return True
        except Exception:
            return False

    # --- Encapsulation / Décapsulation ML-KEM ---

    def encapsulate(self, peer_mlkem_pub_hex: str) -> Tuple[bytes, str]:
        """
        Encapsule un secret partagé pour le destinataire ML-KEM.
        Retourne (shared_secret_bytes, ciphertext_hex).
        """
        if self._mock_mode:
            secret = hashlib.sha256(f"shared:{peer_mlkem_pub_hex}:{os.urandom(16)}".encode()).digest()
            ct = hashlib.sha256(secret + b":ct").hexdigest()
            return secret, ct

        peer_pub_bytes = bytes.fromhex(peer_mlkem_pub_hex)
        peer_pub = mlkem.MLKEM768PublicKey.from_public_bytes(peer_pub_bytes)
        shared_secret, ciphertext = peer_pub.encapsulate()
        return shared_secret, ciphertext.hex()

    def decapsulate(self, ciphertext_hex: str) -> bytes:
        """
        Décapsule le ciphertext reçu avec la clé privée ML-KEM.
        Retourne shared_secret_bytes.
        """
        if self._mock_mode:
            return hashlib.sha256(f"shared:{self.mlkem_pub_hex}:{ciphertext_hex}".encode()).digest()

        ct_bytes = bytes.fromhex(ciphertext_hex)
        return self._mlkem_priv.decapsulate(ct_bytes)

    # --- Dérivation de session et chiffrement ---

    @staticmethod
    def derive_session_key(shared_secret: bytes, salt: bytes, context: str = "SwarmGlu-Auth-v1") -> bytes:
        """Dérive une clé symétrique de 256 bits via HKDF-SHA256."""
        if not HAS_CRYPTOGRAPHY:
            return hashlib.sha256(shared_secret + salt + context.encode()).digest()
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            info=context.encode("utf-8"),
        )
        return hkdf.derive(shared_secret)

    @staticmethod
    def encrypt_payload(session_key: bytes, plaintext: bytes | str) -> str:
        """Chiffre et authentifie un payload via AES-256-GCM (ou HMAC fallback)."""
        if isinstance(plaintext, str):
            plaintext = plaintext.encode("utf-8")
        if not HAS_CRYPTOGRAPHY:
            nonce = os.urandom(12).hex()
            mac = hmac.new(session_key, nonce.encode() + plaintext, hashlib.sha256).hexdigest()
            b64 = base64.b64encode(plaintext).decode()
            return json.dumps({"nonce": nonce, "mac": mac, "data": b64})

        aesgcm = AESGCM(session_key)
        nonce = os.urandom(12)
        ct = aesgcm.encrypt(nonce, plaintext, None)
        return json.dumps({"nonce": nonce.hex(), "ct": ct.hex()})

    @staticmethod
    def decrypt_payload(session_key: bytes, encrypted_str: str) -> bytes:
        """Déchiffre et vérifie l'intégrité d'un payload chiffré."""
        envelope = json.loads(encrypted_str)
        if not HAS_CRYPTOGRAPHY:
            nonce = envelope["nonce"]
            mac = envelope["mac"]
            data = base64.b64decode(envelope["data"])
            expected = hmac.new(session_key, nonce.encode() + data, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(mac, expected):
                raise ValueError("Authentification HMAC échouée")
            return data

        aesgcm = AESGCM(session_key)
        nonce = bytes.fromhex(envelope["nonce"])
        ct = bytes.fromhex(envelope["ct"])
        return aesgcm.decrypt(nonce, ct, None)

    # --- Poignée de main (Handshake) asymétrique hybride ---

    def initiate_handshake(self, target_node_id: str) -> Dict[str, Any]:
        """
        Étape 1 (Initiateur A) : prépare l'offre de poignée de main avec nonces,
        clés publiques ECDSA et ML-KEM, et signature d'authentification.
        """
        nonce = os.urandom(16).hex()
        timestamp = round(time.time(), 3)
        msg_to_sign = f"SWARM_AUTH_INIT:{self.node_id}:{target_node_id}:{self.mlkem_pub_hex}:{nonce}:{timestamp}"
        signature = self.sign(msg_to_sign)
        return {
            "type": "handshake_init",
            "sender_id": self.node_id,
            "target_id": target_node_id,
            "ecdsa_pub": self.ecdsa_pub_hex,
            "mlkem_pub": self.mlkem_pub_hex,
            "nonce": nonce,
            "timestamp": timestamp,
            "signature": signature,
        }

    def respond_handshake(self, init_msg: Dict[str, Any]) -> Tuple[Dict[str, Any], bytes]:
        """
        Étape 2 (Destinataire B) : vérifie l'identité ECDSA de A, encapsule un secret
        via ML-KEM, signe la réponse et dérive la clé de session.
        Retourne (message_reponse, session_key).
        """
        sender_id = init_msg.get("sender_id", "")
        peer_ecdsa_pub = init_msg.get("ecdsa_pub", "")
        peer_mlkem_pub = init_msg.get("mlkem_pub", "")
        peer_nonce = init_msg.get("nonce", "")
        ts = init_msg.get("timestamp", 0)
        sig = init_msg.get("signature", "")

        msg_to_verify = f"SWARM_AUTH_INIT:{sender_id}:{self.node_id}:{peer_mlkem_pub}:{peer_nonce}:{ts}"
        if not self.verify_signature(msg_to_verify, sig, peer_ecdsa_pub):
            raise ValueError("Signature d'initiation de poignée de main invalide")

        # Encapsulation ML-KEM
        shared_secret, ciphertext_hex = self.encapsulate(peer_mlkem_pub)

        resp_nonce = os.urandom(16).hex()
        resp_ts = round(time.time(), 3)
        resp_msg_to_sign = (
            f"SWARM_AUTH_RESP:{self.node_id}:{sender_id}:{ciphertext_hex}:"
            f"{peer_nonce}:{resp_nonce}:{resp_ts}"
        )
        resp_sig = self.sign(resp_msg_to_sign)

        salt = hashlib.sha256(f"{peer_nonce}:{resp_nonce}".encode()).digest()
        session_key = self.derive_session_key(shared_secret, salt)

        resp_payload = {
            "type": "handshake_resp",
            "sender_id": self.node_id,
            "target_id": sender_id,
            "ecdsa_pub": self.ecdsa_pub_hex,
            "ciphertext": ciphertext_hex,
            "nonce": resp_nonce,
            "timestamp": resp_ts,
            "signature": resp_sig,
        }
        return resp_payload, session_key

    def finalize_handshake(self, init_msg: Dict[str, Any], resp_msg: Dict[str, Any]) -> bytes:
        """
        Étape 3 (Initiateur A) : vérifie la signature de B, décapsule le secret
        ML-KEM et dérive la clé de session partagée identique.
        Retourne session_key.
        """
        resp_sender = resp_msg.get("sender_id", "")
        resp_ecdsa_pub = resp_msg.get("ecdsa_pub", "")
        ciphertext_hex = resp_msg.get("ciphertext", "")
        peer_nonce = resp_msg.get("nonce", "")
        resp_ts = resp_msg.get("timestamp", 0)
        sig = resp_msg.get("signature", "")

        msg_to_verify = (
            f"SWARM_AUTH_RESP:{resp_sender}:{self.node_id}:{ciphertext_hex}:"
            f"{init_msg['nonce']}:{peer_nonce}:{resp_ts}"
        )
        if not self.verify_signature(msg_to_verify, sig, resp_ecdsa_pub):
            raise ValueError("Signature de réponse de poignée de main invalide")

        shared_secret = self.decapsulate(ciphertext_hex)
        salt = hashlib.sha256(f"{init_msg['nonce']}:{peer_nonce}".encode()).digest()
        return self.derive_session_key(shared_secret, salt)


# ---------------------------------------------------------------------------
# Algorithme de sélection pondérée décentralisée (Temps + Requêtes)
# ---------------------------------------------------------------------------

class WeightedLottery:
    """
    Sélectionne aléatoirement un nœud vérificateur/re-signeur avec une probabilité
    proportionnelle à l'ancienneté (temps dans l'essaim) et au volume de requêtes.
    """

    @staticmethod
    def calculate_weight(uptime_s: float, requests_count: int) -> float:
        """
        Pondération : W_i = max(temps_i, 1.0) * (requetes_i + 1).
        Garantit une probabilité strictement positive pour tout nœud connecté,
        tout en favorisant fortement les nœuds anciens et actifs.
        """
        time_factor = max(float(uptime_s), 1.0)
        req_factor = max(int(requests_count), 0) + 1
        return round(time_factor * req_factor, 3)

    @classmethod
    def compute_probabilities(cls, nodes_metrics: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
        """
        Calcule les probabilités normalisées P_i pour chaque nœud :
        P_i = W_i / sum(W_k).
        """
        if not nodes_metrics:
            return {}
        weights = {}
        for nid, met in nodes_metrics.items():
            uptime = met.get("uptime_s", 1.0)
            reqs = met.get("requests_count", 0)
            weights[nid] = cls.calculate_weight(uptime, reqs)

        total_weight = sum(weights.values())
        if total_weight <= 0:
            p_uniform = 1.0 / len(weights)
            return {nid: p_uniform for nid in weights}

        return {nid: round(w / total_weight, 6) for nid, w in weights.items()}

    @classmethod
    def select_node(
        cls,
        nodes_metrics: Dict[str, Dict[str, Any]],
        seed: Optional[int | str] = None,
    ) -> Tuple[Optional[str], Dict[str, float]]:
        """
        Tire au sort un nœud selon la distribution de probabilité.
        Si un seed est fourni, le tirage est pseudo-aléatoire déterministe (reproductible).
        Retourne (selected_node_id, probabilités).
        """
        if not nodes_metrics:
            return None, {}

        probs = cls.compute_probabilities(nodes_metrics)
        candidates = sorted(probs.keys())
        weights = [probs[nid] for nid in candidates]

        if seed is not None:
            if isinstance(seed, str):
                seed_int = int(hashlib.sha256(seed.encode()).hexdigest()[:14], 16)
            else:
                seed_int = int(seed)
            rng = random.Random(seed_int)
            chosen = rng.choices(candidates, weights=weights, k=1)[0]
        else:
            chosen = random.choices(candidates, weights=weights, k=1)[0]

        return chosen, probs


# ---------------------------------------------------------------------------
# Vérification du Consensus 1/8
# ---------------------------------------------------------------------------

class Consensus18Verifier:
    """
    Règle de consensus décentralisée 1/8 :
    Au moins 1/8 (12.5 %) des nœuds actifs de l'essaim doivent valider et signer
    l'élection du nœud vérificateur avant qu'il ne puisse re-signer la whitelist.
    """

    @staticmethod
    def threshold(total_nodes: int) -> int:
        """Nombre minimal d'accords requis : max(1, ceil(total_nodes / 8))."""
        if total_nodes <= 0:
            return 1
        return max(1, math.ceil(total_nodes / 8.0))

    @classmethod
    def verify_proof(
        cls,
        proof: Dict[str, Any],
        known_ecdsa_keys: Dict[str, str],
        active_nodes_count: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """
        Vérifie la validité d'une preuve de consensus 1/8 :
        1. Quorum >= ceil(total_nodes / 8).
        2. Signatures ECDSA valides de nœuds distincts.
        3. Identité du candidat cohérente.
        """
        if not proof:
            return False, "Preuve de consensus absente"

        target_signer = proof.get("target_signer", "")
        epoch = proof.get("epoch", 0)
        approvals = proof.get("approvals", [])
        total_nodes = active_nodes_count if active_nodes_count is not None else proof.get("total_nodes", len(approvals))

        needed = cls.threshold(total_nodes)
        if len(approvals) < needed:
            return False, f"Quorum 1/8 insuffisant ({len(approvals)}/{needed} requis pour {total_nodes} nœuds)"

        seen_voters = set()
        valid_votes = 0

        for vote in approvals:
            voter_id = vote.get("voter_id", "")
            sig = vote.get("signature", "")
            ts = vote.get("timestamp", 0)
            voter_pub = vote.get("voter_ecdsa_pub") or known_ecdsa_keys.get(voter_id)

            if not voter_id or voter_id in seen_voters:
                continue
            if not voter_pub or not sig:
                continue

            msg = f"SWARM_1_8_VOTE:{epoch}:{target_signer}:{voter_id}:{ts}"
            if NodeCrypto.verify_signature(msg, sig, voter_pub):
                seen_voters.add(voter_id)
                valid_votes += 1

        if valid_votes < needed:
            return False, f"Accords valides insuffisants ({valid_votes}/{needed})"

        return True, "Consensus 1/8 validé avec succès"


# ---------------------------------------------------------------------------
# Whitelist signée et authentifiée
# ---------------------------------------------------------------------------

class AuthenticatedWhitelist:
    """
    Format et vérification d'une whitelist cryptographiquement signée avec
    preuve de consensus 1/8.
    """

    @staticmethod
    def build_signed_doc(
        signer_crypto: NodeCrypto,
        nodes_list: List[Dict[str, Any] | str],
        consensus_proof: Dict[str, Any],
        epoch: int,
        swarm_id: str = "swarm-local",
    ) -> Dict[str, Any]:
        """Construit et signe la whitelist canonique."""
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Formatage uniforme des nœuds
        formatted_nodes = []
        for n in nodes_list:
            if isinstance(n, str):
                formatted_nodes.append({"node_id": n})
            elif isinstance(n, dict):
                formatted_nodes.append(dict(n))

        formatted_nodes.sort(key=lambda x: x.get("node_id", ""))

        body_to_sign = {
            "version": 2,
            "swarm_id": swarm_id,
            "epoch": epoch,
            "nodes": formatted_nodes,
            "signer_node_id": signer_crypto.node_id,
            "signer_ecdsa_pub": signer_crypto.ecdsa_pub_hex,
            "resigned_at": timestamp,
            "consensus_proof": consensus_proof,
        }

        canonical_bytes = canonical_json(body_to_sign).encode("utf-8")
        signature = signer_crypto.sign(canonical_bytes)

        body_to_sign["signature"] = signature
        return body_to_sign

    @staticmethod
    def verify_signed_doc(
        doc: Dict[str, Any],
        known_ecdsa_keys: Optional[Dict[str, str]] = None,
        active_nodes_count: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """
        Vérifie intégralement la whitelist signée :
        1. Intégrité et signature ECDSA du nœud re-signeur.
        2. Preuve de consensus 1/8.
        """
        known_keys = known_ecdsa_keys or {}
        sig = doc.get("signature", "")
        if not sig:
            return False, "Signature de whitelist absente"

        signer_id = doc.get("signer_node_id", "")
        signer_pub = doc.get("signer_ecdsa_pub") or known_keys.get(signer_id)
        if not signer_pub:
            return False, f"Clé publique du signeur '{signer_id}' introuvable"

        # Vérification de la preuve de consensus 1/8
        proof = doc.get("consensus_proof", {})
        ok, reason = Consensus18Verifier.verify_proof(proof, known_keys, active_nodes_count)
        if not ok:
            return False, f"Échec consensus 1/8 : {reason}"

        # Recréation du corps signé pour vérification ECDSA
        body_to_verify = dict(doc)
        body_to_verify.pop("signature", None)
        canonical_bytes = canonical_json(body_to_verify).encode("utf-8")

        if not NodeCrypto.verify_signature(canonical_bytes, sig, signer_pub):
            return False, "Signature ECDSA de la whitelist invalide"

        return True, "Whitelist authentifiée et vérifiée"
