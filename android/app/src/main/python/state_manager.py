# -*- coding: utf-8 -*-
"""
SwarmGlu — state_manager.py
===========================
Gestionnaire d'état distribué : versioning par epoch, fusion
Last-Write-Wins (LWW) validée par le hash de l'état, et synchronisation
périodique de l'essaim (anti-entropie).

L'état global replicué sur tous les nœuds contient :

* ``epoch``          : numéro d'époque (incrémenté à CHAQUE changement de
                       leader — c'est la borne temporelle logique du
                       consensus) ;
* ``leader_id``      : leader courant (et sa clé de tri déterministe) ;
* ``peers``          : registre des pairs (ip, port, mode, capacités,
                       modèles, last_seen, alive) ;
* ``whitelist_nodes``: whitelist propagée (approbations du leader) ;
* ``version``        : compteur de modifications locales (informationnel).

Règle de consensus légère (cahier des charges) : en cas de divergence,
chaque entrée de pair est résolue par *Last-Write-Wins* — l'écriture la
plus récente l'emporte, départagée par le node_id en cas d'égalité
d'horodatage. La fusion est COMMUTATIVE : quel que soit l'ordre des
échanges, tous les nœuds convergent vers le même état, dont le
``digest()`` (SHA-256 de la projection logique) sera identique — c'est ce
hash qui valide la synchronisation.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from security import canonical_json, now_iso, sha256_hex

log = logging.getLogger("swarmglu.state")

# Champs volatils (exclus du digest : ils bougent à chaque battement et
# n'ont pas de sens hors du nœud qui les observe).
VOLATILE_FIELDS = {"last_seen", "alive"}

# Champs logiques (inclus dans le digest et les échanges d'état).
LOGICAL_PEER_FIELDS = (
    "ip", "port", "mode", "capabilities", "models", "api_flavor",
    "base_url", "updated_at", "writer",
    "uptime_s", "requests_count", "joined_at", "ecdsa_pub", "mlkem_pub",
)


class StateManager:
    """Détient l'état global replicué et sa politique de fusion LWW."""

    def __init__(self, node_id: str, security: Any = None):
        self.node_id = node_id
        self.security = security  # pour appliquer la whitelist propagée
        self._lock = threading.RLock()
        self._doc: Dict[str, Any] = {
            "epoch": 0,
            "leader_id": None,
            "leader_key": None,
            "version": 0,
            "updated_at": now_iso(),
            "whitelist_nodes": [],
            "peers": {},  # node_id -> entrée de registre
            "meta": {},
        }
        # Auto-déclaration : capacités/modèles du nœud local (fournisseur
        # GGUF, agent HarnessDroid...), mis à jour via declare_self().
        self._self_info: Dict[str, Any] = {
            "capabilities": [],
            "models": [],
            "api_flavor": None,
            "base_url": None,
        }

    # ------------------------------------------------------------------
    # Accès locaux
    # ------------------------------------------------------------------

    @property
    def peers(self) -> Dict[str, Dict[str, Any]]:
        return self._doc["peers"]

    @property
    def epoch(self) -> int:
        return self._doc["epoch"]

    def self_capabilities(self) -> List[str]:
        return list(self._self_info.get("capabilities", []))

    def self_models(self) -> List[str]:
        return list(self._self_info.get("models", []))

    def declare_self(self, capabilities: List[str], models: List[str],
                     api_flavor: Optional[str] = None,
                     base_url: Optional[str] = None) -> None:
        """Déclare les capacités locales (pont GGUF, agent MCP...)."""
        with self._lock:
            self._self_info = {
                "capabilities": sorted(set(capabilities or [])),
                "models": sorted(set(models or [])),
                "api_flavor": api_flavor,
                "base_url": base_url,
            }

    # ------------------------------------------------------------------
    # Mutations du registre
    # ------------------------------------------------------------------

    def update_peer(self, node_id: str, fields: Dict[str, Any]) -> bool:
        """
        Fusionne des champs LOGIQUES dans l'entrée d'un pair (ip, port,
        capacités, modèles...). Retourne True si l'état logique a changé.

        Chaque écriture est estampillée ``writer`` + ``updated_at`` :
        c'est ce couple qui arbitre le LWW lors des fusions distantes.
        """
        with self._lock:
            logical = {k: v for k, v in fields.items() if k not in VOLATILE_FIELDS}
            peer = self._doc["peers"].setdefault(node_id, {})
            changed = any(peer.get(k) != v for k, v in logical.items())
            if changed:
                peer.update(logical)
                peer["writer"] = self.node_id
                peer["updated_at"] = round(time.time(), 3)
                self._bump()
            return changed

    def touch_peer(self, node_id: str, alive: bool = True) -> None:
        """Mise à jour VOLATILE (vue locale de vivacité) — sans bump."""
        with self._lock:
            peer = self._doc["peers"].setdefault(node_id, {})
            peer["last_seen"] = round(time.time(), 3)
            peer["alive"] = alive

    def mark_peer_dead(self, node_id: str) -> None:
        with self._lock:
            peer = self._doc["peers"].get(node_id)
            if peer:
                peer["alive"] = False

    def drop_peer(self, node_id: str) -> None:
        with self._lock:
            if self._doc["peers"].pop(node_id, None) is not None:
                self._bump()

    def alive_peers(self, timeout: float = 9.0) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Pairs vus depuis moins de `timeout` secondes.

        SOI-MÊME est toujours considéré vivant : la lenteur des ticks de
        consensus (battements pendant 2 s sur le socket d'un leader qui
        meurt) ne doit jamais faire expirer sa propre entrée — sinon le
        nœud se refuse à lui-même comme fournisseur pendant la fenêtre
        de basculement (bug réel observé : /ask 503 juste après failover).
        """
        now = time.time()
        out = []
        with self._lock:
            for pid, peer in self._doc["peers"].items():
                if pid == self.node_id and peer:
                    out.append((pid, dict(peer)))
                    continue
                if peer.get("alive", False) and now - peer.get("last_seen", 0) <= timeout:
                    out.append((pid, dict(peer)))
        return out

    def set_leader(self, leader_id: Optional[str], key: Optional[str], epoch: int) -> None:
        with self._lock:
            if epoch >= self._doc["epoch"]:
                self._doc["epoch"] = epoch
                self._doc["leader_id"] = leader_id
                self._doc["leader_key"] = key
                self._bump()

    def set_whitelist_nodes(self, nodes: List[str]) -> None:
        with self._lock:
            merged = sorted(set(self._doc["whitelist_nodes"]) | set(nodes or []))
            if merged != self._doc["whitelist_nodes"]:
                self._doc["whitelist_nodes"] = merged
                self._bump()

    def set_meta(self, key: str, value: Any) -> None:
        with self._lock:
            if self._doc["meta"].get(key) != value:
                self._doc["meta"][key] = value
                self._bump()

    def del_meta(self, key: str) -> None:
        with self._lock:
            if self._doc["meta"].pop(key, None) is not None:
                self._bump()

    def get_meta(self, key: str) -> Any:
        with self._lock:
            return self._doc["meta"].get(key)

    def get_meta_keys(self) -> List[str]:
        with self._lock:
            return list(self._doc["meta"].keys())

    def _bump(self) -> None:
        self._doc["version"] += 1
        self._doc["updated_at"] = now_iso()

    # ------------------------------------------------------------------
    # Sérialisation & digest
    # ------------------------------------------------------------------

    def to_dict(self, include_volatile: bool = True) -> Dict[str, Any]:
        """Copie profonde (réponse /registry, fusion distante)."""
        with self._lock:
            import copy
            doc = copy.deepcopy(self._doc)
            if not include_volatile:
                for peer in doc["peers"].values():
                    for f in VOLATILE_FIELDS:
                        peer.pop(f, None)
            return doc

    def logical_projection(self) -> Dict[str, Any]:
        """Vue logique stable — base du digest (les battements ne la changent pas)."""
        doc = self.to_dict(include_volatile=False)
        doc.pop("version", None)
        doc.pop("updated_at", None)
        return doc

    def digest(self) -> str:
        """SHA-256 de la projection logique : identique sur tout l'essaim
        synchronisé — c'est LUI qui valide le consensus LWW."""
        return sha256_hex(canonical_json(self.logical_projection()))

    # ------------------------------------------------------------------
    # Fusion LWW (consensus de synchronisation)
    # ------------------------------------------------------------------

    @staticmethod
    def _peer_beats(local: Dict[str, Any], remote: Dict[str, Any]) -> bool:
        """
        True si l'entrée distante gagne le LWW :
        (updated_at, writer) lexicographiquement supérieur — comparaison
        totale => fusion commutative => convergence garantie.
        """
        lkey = (local.get("updated_at", 0), local.get("writer", ""))
        rkey = (remote.get("updated_at", 0), remote.get("writer", ""))
        return rkey > lkey

    def merge(self, remote_doc: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fusionne un état distant dans l'état local (LWW par entrée).

        * epoch : la plus haute gagne ; à égalité, la plus petite
          ``leader_key`` gagne (même règle déterministe que l'élection).
        * peers : LWW commutatif par entrée.
        * whitelist_nodes : union.

        Retourne un rapport {"changed": bool, "changes": {...}} — le
        SecurityManager applique ensuite la whitelist propagée.
        """
        changes: Dict[str, Any] = {"peers": [], "whitelist_added": [], "leader": False}
        if not remote_doc:
            return {"changed": False, **changes}

        with self._lock:
            # 1) Époque / leader
            r_epoch = int(remote_doc.get("epoch", 0))
            r_leader = remote_doc.get("leader_id")
            r_key = remote_doc.get("leader_key") or ""
            if r_epoch > self._doc["epoch"]:
                self._doc["epoch"] = r_epoch
                self._doc["leader_id"] = r_leader
                self._doc["leader_key"] = remote_doc.get("leader_key")
                changes["leader"] = True
                self._bump()
            elif (
                r_epoch == self._doc["epoch"]
                and r_leader
                and r_leader != self._doc["leader_id"]
                and r_key
                and (self._doc.get("leader_key") is None or r_key < self._doc["leader_key"])
            ):
                self._doc["leader_id"] = r_leader
                self._doc["leader_key"] = remote_doc.get("leader_key")
                changes["leader"] = True
                self._bump()

            # 2) Registre des pairs : LWW logique + volatil toujours frais
            for pid, rpeer in (remote_doc.get("peers") or {}).items():
                rlogical = {k: rpeer.get(k) for k in LOGICAL_PEER_FIELDS if k in rpeer}
                lpeer = self._doc["peers"].get(pid)
                if lpeer is None:
                    entry = dict(rlogical)
                    entry["writer"] = rpeer.get("writer", pid)
                    entry["updated_at"] = rpeer.get("updated_at", 0)
                    # La vivacité distante est adoptée telle quelle : sans
                    # elle, un follower ne connaîtrait JAMAIS l'état des
                    # autres followers (il ne les contacte pas directement)
                    # et ne pourrait pas calculer les candidats à l'élection.
                    entry["last_seen"] = rpeer.get("last_seen", 0)
                    entry["alive"] = bool(rpeer.get("alive"))
                    self._doc["peers"][pid] = entry
                    changes["peers"].append(pid)
                    self._bump()
                else:
                    if self._peer_beats(lpeer, rpeer):
                        merged = dict(lpeer)
                        merged.update(rlogical)
                        merged["writer"] = rpeer.get("writer", lpeer.get("writer"))
                        merged["updated_at"] = rpeer.get("updated_at", lpeer.get("updated_at"))
                        self._doc["peers"][pid] = merged
                        changes["peers"].append(pid)
                        self._bump()
                    # VOLATIL INDEPENDANT du LWW logique : même si l'état
                    # logique distant ne gagne pas (inchangé), la vivacité
                    # plus récente DOIT être adoptée — sinon un registre
                    # tiré du leader ne rafraîchit jamais les followers
                    # entre eux (bug observé : alive_count figé à 2).
                    # Silencieux : le volatil ne compte pas comme changement
                    # logique (digest inchangé, pas d'événement d'audit).
                    courante = self._doc["peers"][pid]
                    if (rpeer.get("last_seen") or 0) > (courante.get("last_seen") or 0):
                        rafraichie = dict(courante)
                        rafraichie["last_seen"] = rpeer.get("last_seen")
                        rafraichie["alive"] = bool(rpeer.get("alive"))
                        self._doc["peers"][pid] = rafraichie

            # 3) Whitelist propagée : union
            r_nodes = remote_doc.get("whitelist_nodes") or []
            merged_nodes = sorted(set(self._doc["whitelist_nodes"]) | set(r_nodes))
            if merged_nodes != self._doc["whitelist_nodes"]:
                changes["whitelist_added"] = [
                    n for n in merged_nodes if n not in self._doc["whitelist_nodes"]
                ]
                self._doc["whitelist_nodes"] = merged_nodes
                self._bump()

            # 4) Meta : LWW par clé (valeur + même taille => simple écrasement)
            for k, v in (remote_doc.get("meta") or {}).items():
                if self._doc["meta"].get(k) != v:
                    self._doc["meta"][k] = v
                    self._bump()

        changed = bool(changes["peers"] or changes["whitelist_added"] or changes["leader"])
        return {"changed": changed, **changes}

    def apply_merged_whitelist(self) -> List[str]:
        """Applique la whitelist propagée au SecurityManager local."""
        if self.security is None:
            return []
        with self._lock:
            nodes = list(self._doc["whitelist_nodes"])
        return self.security.sync_from_swarm(nodes)


class SwarmSynchronizer:
    """
    Thread de synchronisation de l'essaim (anti-entropie légère) :

    * FOLLOWER : tire l'état complet du Leader toutes les `interval` s
      (GET /registry) et le fusionne (LWW) ;
    * LEADER / orphelin : compare son digest avec un pair au hasard ;
      en cas d'écart, échange complet (POST /state/sync).

    Toute divergence détectée est journalisée dans la chaîne d'audit
    (événement ``state_synced``).
    """

    def __init__(self, node: Any, interval: float = 5.0):
        self.node = node          # SwarmNode (swarm_glu.py) — accès aux services
        self.interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self._tick()
            except Exception:
                log.exception("erreur de synchronisation")

    def _tick(self) -> None:
        node = self.node
        state: StateManager = node.state
        consensus = node.consensus

        # 1) Le follower se met à jour auprès du leader.
        if consensus.leader_id and consensus.leader_id != node.node_id:
            addr = self._peer_addr(consensus.leader_id)
            if addr:
                status, reply = node.transport.get(addr[0], addr[1], "/registry", timeout=3.0)
                if status == 200 and reply and reply.get("state"):
                    self._apply(reply["state"], source="leader_pull")
                    return

        # 2) Anti-entropie : un pair au hasard, comparaison de digests.
        others = [
            (pid, p) for pid, p in state.peers.items()
            if pid != node.node_id and p.get("alive", False)
        ]
        if not others:
            return
        pid, peer = random.choice(others)
        try:
            status, reply = node.transport.get(
                peer["ip"], int(peer["port"]), "/state/digest", timeout=2.0)
            if status != 200 or not reply:
                return
            if reply.get("digest") != state.digest() or reply.get("epoch", 0) != state.epoch:
                status2, full = node.transport.get(
                    peer["ip"], int(peer["port"]), "/registry", timeout=3.0)
                if status2 == 200 and full and full.get("state"):
                    self._apply(full["state"], source="peer_exchange:%s" % pid)
        except Exception:
            pass  # pair momentanément injoignable : la prochaine passe réessaie

    def _peer_addr(self, node_id: str) -> Optional[Tuple[str, int]]:
        peer = self.node.state.peers.get(node_id)
        if not peer:
            return None
        return (peer.get("ip"), int(peer.get("port", 0)))

    def _apply(self, remote_doc: Dict[str, Any], source: str) -> None:
        state: StateManager = self.node.state
        report = state.merge(remote_doc)
        # Les approbations de whitelist propagées s'appliquent localement.
        added = state.apply_merged_whitelist()
        for nid in added:
            self.node.security.record_event(
                "whitelist_propagated", self.node.node_id,
                {"new_node": nid, "source": source})
        # Un leader d'epoch supérieure qui émerge de la fusion est noté
        # (le bail réel reste validé par les battements).
        self.node.consensus.observe_state_leader(
            remote_doc.get("leader_id"), remote_doc.get("leader_key") or "",
            int(remote_doc.get("epoch", 0)))
        if report["changed"]:
            self.node.security.record_event(
                "state_synced", self.node.node_id,
                {"source": source, "epoch": state.epoch,
                 "digest": state.digest(),
                 "changes": {k: v for k, v in report.items() if k != "changed"}},
            )
            log.info("État synchronisé (%s) — epoch %d, digest %.12s…",
                     source, state.epoch, state.digest())
