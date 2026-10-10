# -*- coding: utf-8 -*-
"""
SwarmGlu — gateway.py
=====================
Passerelle multi-essaims (*Swarm-to-Swarm Gateway*).

Un nœud lancé en mode ``ambassador`` (ou tout nœud connaissant l'adresse
d'un essaim voisin via ``--neighbor``) expose la route ``/gateway/sync``
et échange périodiquement des RÉSUMÉS D'ÉTAT CRYPTOGRAPHIQUES
(*Digest Exchange*) avec les essaims voisins — typiquement pour relier
deux salles de classe dotées chacune de leur essaim Wi-Fi local.

Chaque digest d'essaim contient :

* ``swarm_id``         : identifiant de l'essaim (ex. « salle-1 ») ;
* ``epoch`` / ``leader``: époque et leader courants ;
* ``state_hash``       : SHA-256 de l'état logique (cf. StateManager) ;
* ``node_count`` / ``models`` : capacités mutualisées ;
* ``whitelist_digest`` : empreinte de la whitelist de l'essaim.

Le routage FÉDÉRÉ fonctionne ensuite naturellement : une requête
``/ask {"swarm_id": "salle-2", ...}`` reçue sur n'importe quel nœud est
transmise à l'ambassadeur de « salle-2 », qui la traite dans son propre
essaim. Un compteur de sauts (``hops``) borne la récursion et interdit
les boucles inter-essaims.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("swarmglu.gateway")

# Durée de validité d'un voisin sans nouvel échange (ensuite : purgé).
NEIGHBOR_TTL = 90.0

# Nombre maximal de sauts inter-essaims (anti-boucle de routage fédéré).
MAX_FEDERATION_HOPS = 2


class SwarmGateway:
    """
    Service de passerelle : registre d'essaims voisins, échange de digests
    et routage fédéré des requêtes.
    """

    def __init__(self, node: Any, swarm_id: str, neighbor_urls: Optional[List[str]] = None):
        self.node = node           # SwarmNode (swarm_glu.py)
        self.swarm_id = swarm_id
        self.neighbor_urls = list(neighbor_urls or [])  # ex. "192.168.4.10:7101"
        self._lock = threading.RLock()
        self._neighbors: Dict[str, Dict[str, Any]] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Digest local (ce que nous annonçons aux essaims voisins)
    # ------------------------------------------------------------------

    def local_digest(self) -> Dict[str, Any]:
        """Résumé cryptographique de NOTRE essaim ( Digest Exchange )."""
        state = self.node.state
        peers = state.alive_peers(timeout=15.0)
        models = sorted({m for _pid, p in peers for m in p.get("models", [])})
        return {
            "swarm_id": self.swarm_id,
            "epoch": state.epoch,
            "leader_id": state._doc["leader_id"],
            "state_hash": state.digest(),
            "node_count": len(peers),
            "models": models,
            "whitelist_digest": self.node.security.whitelist_digest(),
            "ambassador_addr": "%s:%s" % (self.node.identity["ip"], self.node.identity["port"]),
            "ts": round(time.time(), 3),
        }

    # ------------------------------------------------------------------
    # Registre des essaims voisins
    # ------------------------------------------------------------------

    def handle_sync(self, remote_digest: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """
        POST /gateway/sync — enregistre le digest d'un essaim voisin et
        retourne le nôtre. Chaque échange est scellé dans la chaîne
        d'audit (événement ``gateway_exchange``).
        """
        remote_swarm = remote_digest.get("swarm_id", "")
        if not remote_swarm:
            return 400, {"error": "swarm_id requis"}
        if remote_swarm == self.swarm_id:
            # Deux ambassadeurs du même essaim : rien à fédérer.
            return 200, {"ok": True, "self": True, "digest": self.local_digest()}

        with self._lock:
            self._neighbors[remote_swarm] = {
                "swarm_id": remote_swarm,
                "epoch": remote_digest.get("epoch", 0),
                "leader_id": remote_digest.get("leader_id"),
                "state_hash": remote_digest.get("state_hash"),
                "node_count": remote_digest.get("node_count", 0),
                "models": remote_digest.get("models", []),
                "whitelist_digest": remote_digest.get("whitelist_digest"),
                "ambassador_addr": remote_digest.get("ambassador_addr"),
                "last_exchange": round(time.time(), 3),
            }

        # PROPAGATION : l'essaim voisin est publié dans l'état distribué
        # (une clé meta par voisin) — ainsi TOUT nœud de l'essaim peut
        # router une requête fédérée, même sans être l'ambassadeur.
        self.node.state.set_meta("gateway:%s" % remote_swarm, {
            "swarm_id": remote_swarm,
            "epoch": remote_digest.get("epoch", 0),
            "state_hash": remote_digest.get("state_hash"),
            "node_count": remote_digest.get("node_count", 0),
            "ambassador_addr": remote_digest.get("ambassador_addr"),
            "ts": round(time.time(), 3),
        })

        self.node.security.record_event(
            "gateway_exchange", self.node.node_id,
            {"remote_swarm": remote_swarm,
             "remote_state_hash": remote_digest.get("state_hash"),
             "remote_epoch": remote_digest.get("epoch", 0),
             "remote_nodes": remote_digest.get("node_count", 0)},
        )
        log.info("Digest échangé avec l'essaim '%s' — epoch %s, %s nœud(s)",
                 remote_swarm, remote_digest.get("epoch"),
                 remote_digest.get("node_count"))
        return 200, {"ok": True, "digest": self.local_digest()}

    def neighbors(self) -> List[Dict[str, Any]]:
        """Voisins connus et frais (pour /status et le routage fédéré)."""
        self._prune()
        with self._lock:
            return [dict(v) for v in self._neighbors.values()]

    def _prune(self) -> None:
        now = time.time()
        with self._lock:
            stale = [sid for sid, v in self._neighbors.items()
                     if now - v.get("last_exchange", 0) > NEIGHBOR_TTL]
            for sid in stale:
                del self._neighbors[sid]
                log.info("Essaim voisin '%s' expiré (TTL %.0fs)", sid, NEIGHBOR_TTL)
        # Les entrées meta propagées expirent aussi (sinon : routage vers
        # un ambassadeur mort).
        for sid in list(self.node.state.get_meta_keys() or []):
            if not sid.startswith("gateway:"):
                continue
            entry = self.node.state.get_meta(sid) or {}
            if now - float(entry.get("ts", 0)) > NEIGHBOR_TTL:
                self.node.state.del_meta(sid)

    def _find_neighbor(self, target: str) -> Optional[Dict[str, Any]]:
        """Voisin frais local d'abord, sinon l'entrée propagée dans l'état."""
        with self._lock:
            if target in self._neighbors:
                return self._neighbors[target]
        return self.node.state.get_meta("gateway:%s" % target)

    # ------------------------------------------------------------------
    # Boucle ambassadeur : pousser notre digest aux essaims configurés
    # ------------------------------------------------------------------

    def start(self) -> None:
        if not self.neighbor_urls:
            return  # simple nœud : la route /gateway/sync reste ouverte
        self._thread = threading.Thread(target=self._run, name="gateway", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        """Pousse le digest vers chaque voisin : tôt (3 s), puis toutes les 10 s."""
        self._stop.wait(3.0)  # laisser l'essaim local se former d'abord
        while not self._stop.is_set():
            for url in list(self.neighbor_urls):
                addr = self._parse_addr(url)
                if not addr:
                    log.warning("Adresse voisine invalide: %s", url)
                    continue
                try:
                    status, reply = self.node.transport.post(
                        addr[0], addr[1], "/gateway/sync", self.local_digest(), timeout=3.0)
                    if status == 200 and reply and reply.get("digest"):
                        self.handle_sync(reply["digest"])
                except Exception as exc:
                    log.debug("voisin %s injoignable (%s)", url, exc)
            self._stop.wait(10.0)

    @staticmethod
    def _parse_addr(url: str) -> Optional[Tuple[str, int]]:
        try:
            host, port = url.rsplit(":", 1)
            return (host, int(port))
        except (ValueError, AttributeError):
            return None

    # ------------------------------------------------------------------
    # Routage fédéré
    # ------------------------------------------------------------------

    def route_federated(self, ask_body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """
        Route une requête d'élève vers l'essaim demandé
        (``ask_body["swarm_id"]``), via son ambassadeur connu.

        La garde ``hops`` (max 2) empêche les boucles salle-A -> salle-B ->
        salle-A : au-delà, la requête est refusée avec une erreur explicite.
        """
        target = ask_body.get("swarm_id", "")
        hops = int(ask_body.get("hops", 0)) + 1
        if hops > MAX_FEDERATION_HOPS:
            return 508, {"error": "limite de sauts inter-essaims atteinte "
                                  "(boucle ?)", "hops": hops}
        if not target:
            return 400, {"error": "swarm_id cible requis"}

        neighbor = self._find_neighbor(target)
        if not neighbor or not neighbor.get("ambassador_addr"):
            return 404, {
                "error": "essaim '%s' inconnu de la passerelle" % target,
                "known_swarms": [n["swarm_id"] for n in self.neighbors()],
            }

        addr = self._parse_addr(neighbor["ambassador_addr"])
        if not addr:
            return 502, {"error": "adresse d'ambassadeur invalide"}

        forwarded = dict(ask_body)
        forwarded["hops"] = hops
        forwarded["origin_swarm"] = forwarded.get("origin_swarm") or self.swarm_id
        self.node.security.record_event(
            "gateway_federated_ask", self.node.node_id,
            {"target_swarm": target, "hops": hops,
             "student_id": ask_body.get("student_id")},
        )
        try:
            status, reply = self.node.transport.post(
                addr[0], addr[1], "/gateway/ask", forwarded, timeout=90.0)
            return status, reply or {"error": "réponse vide de l'essaim distant"}
        except Exception as exc:
            return 502, {"error": "essaim '%s' injoignable: %s" % (target, exc)}
