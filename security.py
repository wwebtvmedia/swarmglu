# -*- coding: utf-8 -*-
"""
SwarmGlu — security.py
======================
Whitelist cryptographique et journal d'événements immuable
(chaîne de hachage « Scorona-like »).

Deux responsabilités, conformément au cahier des charges Tree4Five :

1. **Whitelist** (`whitelist.json`) : aucun nœud ne peut s'enregistrer ni
   participer sans y figurer, ou sans approbation par le consensus du
   Leader (flux `/join` -> `add_node`).

2. **Journal immuable** (`swarm_audit.jsonl`) : chaque événement de
   l'essaim (requête d'élève, routage, enregistrement de pair, élection,
   synchronisation, échange de passerelle...) est consigné avec un
   horodatage rigoureux (`request_time`, `processing_time`, `timestamp`)
   et scellé par chaînage cryptographique :

       hash = sha256(event_data + previous_hash + timestamp)

   `verify_chain()` rejoue l'intégralité du journal et détecte toute
   altération (modification, suppression, réordonnancement, trou de
   séquence). Exposé via la CLI : `python cli.py audit verify`.

Bibliothèque standard uniquement : le même fichier tourne sans pip ni
Internet sur PC (Windows/Linux) et Android (Termux / Chaquopy).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Hash « genèse » amorçant la chaîne (aucun bloc précédent).
GENESIS_HASH = "0" * 64


def now_iso() -> str:
    """Horodatage ISO-8601 UTC (précision ms, suffixe Z)."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def canonical_json(obj: Any) -> str:
    """
    Sérialisation canonique (clés triées, séparateurs compacts).

    Indispensable : le même événement doit produire le même hash sur toutes
    les machines de l'essaim, sinon la vérification de chaîne échouerait
    de façon non déterministe entre un PC et une tablette.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ip_to_sort_int(ip: str) -> int:
    """
    IPv4 -> entier ordonnable (192.168.1.9 < 192.168.1.10).

    La comparaison lexicographique « 1.10 < 1.9 » serait fausse ; l'entier
    garantit l'ordre numérique réel exigé par l'élection déterministe
    (« l'adresse IP la plus basse l'emporte »).
    """
    import socket
    import struct

    try:
        return struct.unpack("!I", socket.inet_aton(ip))[0]
    except OSError:
        return 0xFFFFFFFF  # adresse non IPv4 : l'égalité se joue sur le node_id


def deterministic_key(ip: str, node_id: str) -> Tuple[int, str]:
    """
    Clé de tri déterministe : (IP la plus basse, puis node_id).

    Partagée par l'élection (consensus.py) et la fusion d'état
    (state_manager.py) — d'où sa place dans ce module sans dépendance.
    """
    return (ip_to_sort_int(ip), node_id)


class SecurityManager:
    """
    Gère la liste blanche cryptographique et le journal d'événements
    immuable basé sur une chaîne de hachage (type Scorona).
    """

    def __init__(
        self,
        whitelist_path: str = "whitelist.json",
        log_path: str = "swarm_audit.jsonl",
    ):
        self.whitelist_path = whitelist_path
        self.log_path = log_path
        self._lock = threading.RLock()
        self._whitelist_mtime: Optional[float] = None
        self.whitelist_doc = self._load_whitelist()
        # Queue de chaîne mise en cache : (seq, dernier_hash).
        # Le brouillon initial relisait tout le fichier à chaque écriture
        # (O(n²)) ; on ne scanne qu'au démarrage.
        self._tail_seq, self._tail_hash = self._scan_tail()

    # ------------------------------------------------------------------
    # Whitelist
    # ------------------------------------------------------------------

    def _load_whitelist(self) -> Dict[str, Any]:
        """Charge `whitelist.json` (format {"version": 1, "nodes": [...]})."""
        if os.path.exists(self.whitelist_path):
            try:
                with open(self.whitelist_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    data.setdefault("nodes", [])
                    data.setdefault("version", 1)
                    self._whitelist_mtime = os.path.getmtime(self.whitelist_path)
                    return data
            except (OSError, json.JSONDecodeError) as exc:
                print("[Security] Erreur de lecture de la whitelist: %s" % exc)
        return {"version": 1, "nodes": []}

    def reload_whitelist_if_changed(self) -> bool:
        """
        Recharge à chaud si le fichier a changé (mise à jour par clé USB,
        sans redémarrer le nœud — contrainte salle de classe).
        """
        try:
            mtime = os.path.getmtime(self.whitelist_path)
        except OSError:
            return False
        if mtime != self._whitelist_mtime:
            self.whitelist_doc = self._load_whitelist()
            return True
        return False

    def _save_whitelist(self) -> None:
        """Écriture atomique (tmp + renommage) pour éviter les corruptions."""
        with self._lock:
            tmp = self.whitelist_path + ".tmp"
            directory = os.path.dirname(os.path.abspath(self.whitelist_path))
            os.makedirs(directory, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.whitelist_doc, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.whitelist_path)
            self._whitelist_mtime = os.path.getmtime(self.whitelist_path)

    def is_node_allowed(self, node_id: str) -> bool:
        """Vérifie si un nœud figure dans la liste blanche active."""
        return node_id in self.nodes()

    def nodes(self) -> List[str]:
        raw = self.whitelist_doc.get("nodes", [])
        out: List[str] = []
        for n in raw:
            if isinstance(n, str):
                out.append(n)
            elif isinstance(n, dict) and "node_id" in n:
                out.append(n["node_id"])
        return out

    def add_node(
        self,
        node_id: str,
        approved_by: str = "local-admin",
        ecdsa_pub: str = "",
        mlkem_pub: str = "",
    ) -> bool:
        """
        Ajoute un nœud à la whitelist (approbation locale ou consensus du
        Leader). Retourne True si la liste a changé.
        """
        with self._lock:
            nodes = self.whitelist_doc.setdefault("nodes", [])
            current_nodes = self.nodes()
            if node_id in current_nodes:
                if ecdsa_pub or mlkem_pub:
                    info = self.whitelist_doc.setdefault("approved", {}).setdefault(node_id, {})
                    if ecdsa_pub:
                        info["ecdsa_pub"] = ecdsa_pub
                    if mlkem_pub:
                        info["mlkem_pub"] = mlkem_pub
                    self._save_whitelist()
                return False
            nodes.append(node_id)
            info = {
                "by": approved_by,
                "at": now_iso(),
            }
            if ecdsa_pub:
                info["ecdsa_pub"] = ecdsa_pub
            if mlkem_pub:
                info["mlkem_pub"] = mlkem_pub
            self.whitelist_doc.setdefault("approved", {})[node_id] = info
            self._save_whitelist()
            return True

    def remove_node(self, node_id: str) -> bool:
        with self._lock:
            nodes = self.whitelist_doc.get("nodes", [])
            current_nodes = self.nodes()
            if node_id not in current_nodes:
                return False
            # Supprime de la liste (que ce soit une chaîne ou un dict)
            self.whitelist_doc["nodes"] = [
                n for n in nodes
                if (n != node_id if isinstance(n, str) else n.get("node_id") != node_id)
            ]
            self.whitelist_doc.get("approved", {}).pop(node_id, None)
            self._save_whitelist()
            return True

    def sync_from_swarm(self, remote_nodes: List[str]) -> List[str]:
        """
        Fusion (union) avec la whitelist propagée par l'état synchronisé.
        Retourne les node_id ajoutés — journalisés par l'appelant.
        """
        added: List[str] = []
        for nid in remote_nodes or []:
            if self.add_node(nid, approved_by="swarm-sync"):
                added.append(nid)
        return added

    def whitelist_digest(self) -> str:
        """Empreinte SHA-256 de la liste triée (comparaison de digests)."""
        return sha256_hex(canonical_json(sorted(self.nodes())))

    def get_signed_whitelist(self) -> Optional[Dict[str, Any]]:
        """Retourne la whitelist cryptographiquement signée si présente."""
        return self.whitelist_doc.get("signed_whitelist")

    def apply_signed_whitelist(
        self,
        signed_doc: Dict[str, Any],
        known_keys: Optional[Dict[str, str]] = None,
        active_nodes_count: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """
        Valide et applique une whitelist signée avec preuve de consensus 1/8.
        """
        from crypto_auth import AuthenticatedWhitelist

        ok, reason = AuthenticatedWhitelist.verify_signed_doc(
            signed_doc, known_keys, active_nodes_count
        )
        if not ok:
            return False, reason

        with self._lock:
            new_nodes: List[str] = []
            for n in signed_doc.get("nodes", []):
                nid = n if isinstance(n, str) else n.get("node_id")
                if nid and nid not in new_nodes:
                    new_nodes.append(nid)
                    if isinstance(n, dict):
                        info = self.whitelist_doc.setdefault("approved", {}).setdefault(nid, {})
                        if n.get("ecdsa_pub"):
                            info["ecdsa_pub"] = n["ecdsa_pub"]
                        if n.get("mlkem_pub"):
                            info["mlkem_pub"] = n["mlkem_pub"]
                        info.setdefault("by", f"resigner:{signed_doc.get('signer_node_id')}")
                        info.setdefault("at", signed_doc.get("resigned_at", now_iso()))

            self.whitelist_doc["version"] = signed_doc.get("version", 2)
            self.whitelist_doc["nodes"] = new_nodes
            self.whitelist_doc["signed_whitelist"] = signed_doc
            self._save_whitelist()

        return True, "Whitelist re-signée appliquée avec succès"

    def verify_current_signed_whitelist(
        self,
        known_keys: Optional[Dict[str, str]] = None,
        active_nodes_count: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """Vérifie la validité de la whitelist signée actuelle."""
        signed = self.get_signed_whitelist()
        if not signed:
            return False, "Aucune whitelist signée présente"
        from crypto_auth import AuthenticatedWhitelist
        return AuthenticatedWhitelist.verify_signed_doc(signed, known_keys, active_nodes_count)

    # ------------------------------------------------------------------
    # Chaîne de hachage immuable
    # ------------------------------------------------------------------

    def _scan_tail(self) -> Tuple[int, str]:
        """
        Détermine (seq, dernier_hash) depuis le journal existant.

        Parcours complet au démarrage (volume PoC faible) : si le journal
        est corrompu, on REFUSE d'écrire par-dessus — comportement
        « fail-safe » plutôt que d'étouffer une altération.
        """
        report = self.verify_chain()
        if not report["ok"]:
            raise RuntimeError(
                "Journal '%s' corrompu (ligne %s : %s). Lancez "
                "`python cli.py audit verify` pour diagnostiquer. "
                "Écriture bloquée." % (self.log_path, report.get("line"), report.get("error"))
            )
        return report["entries"], report["last_hash"]

    def get_last_hash(self) -> str:
        """Dernier hash de la chaîne (« 0 »*64 si journal vide)."""
        with self._lock:
            return self._tail_hash

    def record_event(
        self,
        event_type: str,
        node_id: str,
        payload: Optional[Dict[str, Any]] = None,
        processing_time: float = 0.0,
        request_time: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Enregistre un événement dans le journal immuable avec horodatage
        et hachage cryptographique :

            hash = sha256(canonical(event_data) + previous_hash + str(request_time))

        * ``request_time``    : horodatage d'arrivée de la requête (epoch, s).
        * ``processing_time`` : durée de traitement en secondes.
        * ``timestamp``       : scellement lisible ISO-8601 UTC.
        """
        payload = payload or {}
        with self._lock:
            seq = self._tail_seq + 1
            rt = round(float(request_time if request_time is not None else time.time()), 3)
            pt = round(float(processing_time), 3)
            timestamp = now_iso()
            previous_hash = self._tail_hash

            event_data = {
                "seq": seq,
                "event_type": event_type,
                "node_id": node_id,
                "payload": payload,
                "request_time": rt,
                "processing_time": pt,
            }
            raw_string = canonical_json(event_data) + previous_hash + str(rt)
            block_hash = hashlib.sha256(raw_string.encode("utf-8")).hexdigest()

            block = {
                "seq": seq,
                "timestamp": timestamp,
                "request_time": rt,
                "processing_time": pt,
                "previous_hash": previous_hash,
                "hash": block_hash,
                "event": event_data,
            }

            directory = os.path.dirname(os.path.abspath(self.log_path))
            os.makedirs(directory, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(block, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())  # durabilité : essentiel pour l'audit

            self._tail_seq, self._tail_hash = seq, block_hash
            return block

    def verify_chain(self) -> Dict[str, Any]:
        """
        Rejoue toute la chaîne de hachage et retourne un rapport :

            {"ok": True,  "entries": 412, "first_hash": "...", "last_hash": "..."}
            {"ok": False, "line": 57, "error": "hash invalide — bloc altéré", ...}

        Contrôles : liaison previous_hash, continuité de séquence (aucun
        trou => aucune suppression), recalcul du scellement de chaque bloc.
        """
        report: Dict[str, Any] = {
            "ok": True,
            "entries": 0,
            "first_hash": GENESIS_HASH,
            "last_hash": GENESIS_HASH,
            "line": None,
            "error": None,
        }
        if not os.path.exists(self.log_path):
            return report

        prev = GENESIS_HASH
        expected_seq = 1
        try:
            with open(self.log_path, "r", encoding="utf-8") as fh:
                for line_no, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        block = json.loads(line)
                    except json.JSONDecodeError as exc:
                        report.update(ok=False, line=line_no, error="JSON illisible: %s" % exc)
                        return report

                    if block.get("previous_hash") != prev:
                        report.update(ok=False, line=line_no,
                                      error="previous_hash non conforme (chaîne rompue ou bloc inséré)")
                        return report
                    if block.get("seq") != expected_seq:
                        report.update(ok=False, line=line_no,
                                      error="séquence attendue %d, trouvée %s (bloc supprimé ?)"
                                            % (expected_seq, block.get("seq")))
                        return report

                    # Recalcul du scellement : sha256(event_data + prev + request_time)
                    raw = canonical_json(block.get("event", {})) + prev + str(block.get("request_time"))
                    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != block.get("hash"):
                        report.update(ok=False, line=line_no, error="hash invalide — bloc altéré")
                        return report

                    if report["entries"] == 0:
                        report["first_hash"] = block.get("hash", GENESIS_HASH)
                    prev = block.get("hash", GENESIS_HASH)
                    report["last_hash"] = prev
                    report["entries"] = expected_seq
                    expected_seq += 1
        except OSError as exc:
            report.update(ok=False, error="lecture impossible: %s" % exc)
        return report

    def tail(self, n: int = 20) -> List[Dict[str, Any]]:
        """Les `n` derniers blocs (inspection humaine / CLI `audit tail`)."""
        if not os.path.exists(self.log_path):
            return []
        with open(self.log_path, "r", encoding="utf-8") as fh:
            lines = [l for l in fh if l.strip()]
        out: List[Dict[str, Any]] = []
        for line in lines[-max(1, n):]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
