# -*- coding: utf-8 -*-
"""
SwarmGlu — consensus.py
=======================
Élection de Leader par bail (*Heartbeat & Lease Election*), détection de
défaillance et basculement automatique (*failover*) sans intervention
humaine — le mécanisme anti-SPOF du cahier des charges.

Principes
---------
* **Un unique Leader actif** coordonne le registre des pairs. N'importe
  quel nœud peut démarrer seul, s'auto-configurer et devenir Leader
  (bootstrap) : il n'existe AUCUN serveur central obligatoire.

* **Heartbeats toutes les 3 secondes** :
  - les FOLLOWERS envoient leur battement au Leader (et mettent à jour
    leurs capacités/modèles dans le registre) ;
  - le Leader diffuse son propre battement (`/leader-beat`) à tous les
    pairs, prolongeant ainsi son « bail ».

* **Bail de 6 secondes** : si un follower n'entend plus le Leader pendant
  `lease_timeout` (2 battements manqués), il déclenche l'élection.

* **Élection déterministe** : les candidats vivants sont triés par clé
  `(IP la plus basse, node_id)`. Le candidat de rang 0 se proclame
  immédiatement ; les autres attendent `délai * rang` — ainsi, en absence
  de partition, le nœud d'IP la plus basse gagne SANS conflit. Chaque
  proclamation incrémente l'`epoch` (numéro d'époque de l'état global).

* **Résolution de conflit** (partition réseau qui guérit, deux Leaders
  coexistants) : à epoch égale, la clé la plus petite l'emporte ; le
  Leader « perdant » abdique proprement (`leader_stepdown` journalisé).

Rôles possibles (`--mode` de la CLI) : `full` (éligible Leader),
`ambassador` (éligible + passerelle inter-essaims), `follower` et
`provider` (jamais Leader — typiquement les tablettes des élèves).
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from security import deterministic_key

log = logging.getLogger("swarmglu.consensus")


class Role(enum.Enum):
    FOLLOWER = "follower"
    CANDIDATE = "candidate"
    LEADER = "leader"


# Modes éligibles à la fonction de Leader (les tablettes élèves/fournisseurs
# s'excluent d'elles-mêmes de l'élection).
LEADER_ELIGIBLE_MODES = {"full", "ambassador"}


class ConsensusConfig:
    """Paramètres temporels du consensus (valeurs du cahier des charges)."""

    def __init__(
        self,
        heartbeat_interval: float = 3.0,   # battement toutes les 3 s
        lease_timeout: float = 6.0,        # silence du leader > 6 s -> élection
        peer_timeout: float = 9.0,         # pair muet > 9 s -> présumé mort
        election_delay_per_rank: float = 0.3,  # délai de proclamation par rang
        tick_interval: float = 0.5,        # granularité de la boucle
    ):
        self.heartbeat_interval = heartbeat_interval
        self.lease_timeout = lease_timeout
        self.peer_timeout = peer_timeout
        self.election_delay_per_rank = election_delay_per_rank
        self.tick_interval = tick_interval
        if self.lease_timeout < 2 * self.heartbeat_interval:
            # Garde-fou : un bail inférieur à 2 battements élit par spasmes.
            self.lease_timeout = 2 * self.heartbeat_interval


class ConsensusManager:
    """
    Machine à états FOLLOWER -> CANDIDATE -> LEADER pilotée par les baux.

    Dépendances injectées (aucune importation croisée) :

    * ``state``      : le StateManager (registre des pairs partagé).
    * ``transport``  : couche réseau (duck-typing) —
                       ``transport.post(ip, port, path, body, timeout)``
                       retourne ``(status_code, dict|None)``.
    * ``security``   : SecurityManager (whitelist + chaîne d'audit).
    * ``identity``   : {"node_id", "ip", "port", "mode"} du nœud local.
    """

    def __init__(
        self,
        identity: Dict[str, str],
        state: Any,
        transport: Any,
        security: Any,
        config: Optional[ConsensusConfig] = None,
    ):
        self.identity = identity
        self.state = state
        self.transport = transport
        self.security = security
        self.config = config or ConsensusConfig()
        self.allow_join = True  # approbation « consensus du Leader » activée

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.role = Role.FOLLOWER
        self.epoch = 0
        self.leader_id: Optional[str] = None
        self._lease_deadline = time.time() + self.config.lease_timeout
        self._next_heartbeat = 0.0
        self._next_beat = 0.0
        self._next_join_attempt = 0.0
        self._proclaim_at: Optional[float] = None
        self._next_hermit_probe = 0.0
        self._started_at = time.time()

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------

    @property
    def node_id(self) -> str:
        return self.identity["node_id"]

    @property
    def my_key(self) -> Tuple[int, str]:
        return deterministic_key(self.identity["ip"], self.node_id)

    def my_key_str(self) -> str:
        return "%010d|%s" % self.my_key

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="consensus", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        """
        Boucle principale : un tick toutes les `tick_interval` secondes.

        Le verrou ne couvre PLUS l'intégralité du tick : il ne protège
        que les mutations d'état (start_election, _proclaim,
        _accept_leader, _step_down). Les appels réseau (beats, annonces,
        joins) partent SANS verrou — sinon deux nœuds qui s'annoncent
        mutuellement pendant que chacun détient son propre verrou (le
        handler HTTP du pair est alors bloqué sur ce verrou) formaient
        une impasse garantie : chaque POST expirait à 2,0 s — constaté
        en démo (abdication décalée d'exactement 2,00 s, puis essaim
        scindé faute d'échange de carnet d'adresses).
        """
        while not self._stop.wait(self.config.tick_interval):
            try:
                now = time.time()
                if self.role == Role.LEADER:
                    self._leader_tick(now)
                else:
                    self._follower_tick(now)
            except Exception:  # pragma: no cover — robustesse boucle
                log.exception("erreur dans la boucle de consensus")

    # ------------------------------------------------------------------
    # Comportement par rôle
    # ------------------------------------------------------------------

    def _leader_tick(self, now: float) -> None:
        # Le Leader rafraîchit sa propre entrée (volatile) dans le registre.
        self.state.touch_peer(self.node_id, alive=True)

        # Diffusion du battement de bail à tous les followers.
        if now >= self._next_beat:
            self._next_beat = now + self.config.heartbeat_interval
            self._broadcast_leader_beat()

        # ANTI-HERMITE : un leader sans AUCUN follower connu re-probe ses
        # graines/pairs ; s'il découvre un leader légitime, il abdique.
        # Guérit les partitions nées d'une race de bootstrap (auto-élection
        # avant la réussite du join) sans intervention humaine.
        if self._next_hermit_probe <= 0:
            self._next_hermit_probe = now + max(3.0, 3 * self.config.lease_timeout)
        if now >= self._next_hermit_probe and not self._alive_peer_ids():
            self._next_hermit_probe = now + max(3.0, 3 * self.config.lease_timeout)
            self._probe_as_hermit()

        # Fauchage des followers muets (présumés morts -> journalisés).
        self._reap_dead_peers(now)

    def _probe_as_hermit(self) -> None:
        """Leader isolé : frappe les adresses connues ; si un leader répond,
        il est plus légitime (essaim existant) -> abdication."""
        for pid, peer in list(self.state.peers.items()):
            if pid == self.node_id:
                continue
            ip, port = peer.get("ip"), int(peer.get("port", 0) or 0)
            if not ip or not port:
                continue
            body = {"node_id": self.node_id, "ip": self.identity["ip"],
                    "port": self.identity["port"], "mode": self.identity.get("mode", "full"),
                    "capabilities": self.state.self_capabilities(),
                    "models": self.state.self_models()}
            try:
                status, reply = self.transport.post(ip, port, "/join", body, timeout=2.0)
            except Exception:
                continue
            if status == 200 and reply and reply.get("leader_id")                     and reply["leader_id"] != self.node_id:
                self.security.record_event(
                    "hermit_rejoined", self.node_id,
                    {"discovered_leader": reply["leader_id"], "epoch": self.epoch})
                self._step_down(reply)
                return

    def _follower_tick(self, now: float) -> None:
        # Rafraîchit SA PROPRE vivacité au registre local : sans cela, un
        # follower (sans beacon UDP) verrait sa propre entrée expirer et
        # ne pourrait pas se servir de fournisseur pendant une fenêtre
        # sans leader (élection en cours).
        self.state.touch_peer(self.node_id, alive=True)

        # Candidat dont le délai de rang est échu : se proclamer.
        if self.role == Role.CANDIDATE and self._proclaim_at is not None:
            if now >= self._proclaim_at or now >= self._lease_deadline:
                # La seconde condition est un filet de sécurité : si
                # l'élection s'enlise (partition), le candidat se proclame
                # quand même — le conflit éventuel sera tranché par la clé.
                self._proclaim("rank_delay_elapsed")
                return

        if self.leader_id:
            if now >= self._lease_deadline:
                # Silence du Leader pendant `lease_timeout` -> élection.
                self.start_election("leader_silence_%.0fs" % self.config.lease_timeout)
                return
            if now >= self._next_heartbeat:
                self._next_heartbeat = now + self.config.heartbeat_interval
                self._send_heartbeat()
        else:
            # Bootstrap : aucun leader connu. Si personne ne répond avant
            # l'expiration du bail initial, le nœud s'auto-élit (premier
            # démarré de l'essaim => il devient Leader de l'epoch 1).
            if now >= self._lease_deadline:
                self.start_election("bootstrap_silence")
                return
            if now >= self._next_join_attempt:
                self._next_join_attempt = now + self.config.heartbeat_interval
                self._try_join_known_peers()

    # ------------------------------------------------------------------
    # Échange réseau
    # ------------------------------------------------------------------

    def _peer_addr(self, node_id: str) -> Optional[Tuple[str, int]]:
        peer = self.state.peers.get(node_id)
        if not peer:
            return None
        return (peer.get("ip"), int(peer.get("port", 0)))

    def _broadcast_leader_beat(self) -> None:
        with self._lock:
            if self.role != Role.LEADER:
                # Être démis entre le relevé de rôle et ce tick est
                # possible (le verrou ne couvre plus le tick entier) :
                # un ex-leader ne doit plus diffuser de battement.
                return
            body = self._leader_message()
        vivants = set(self._alive_peer_ids())
        for pid in vivants:
            addr = self._peer_addr(pid)
            if not addr:
                continue
            try:
                self.transport.post(addr[0], addr[1], "/leader-beat", body, timeout=2.0)
            except Exception:
                pass  # le bail du follower expirera de lui-même
        # Anti-partition : les adresses MÉMORISÉES même présumées mortes
        # reçoivent aussi le battement (thread d'arrière-plan, court délai).
        # Une vue réciproquement périmée — précisément ce qui mène à deux
        # co-proclamations — ne serait jamais rafraîchie sinon : si
        # l'annonce unique de _proclaim est perdue, la partition resterait
        # ouverte jusqu'à la purge du registre (5 * peer_timeout).
        oublies = [(pid, peer) for pid, peer in list(self.state.peers.items())
                   if pid != self.node_id and pid not in vivants
                   and peer.get("ip") and peer.get("port")]
        if oublies:
            threading.Thread(target=self._beat_oublies,
                             args=(body, oublies), daemon=True).start()

    def _beat_oublies(self, body: Dict[str, Any],
                      pairs: List[Tuple[str, Dict[str, Any]]]) -> None:
        """Battement aux adresses connues hors liste vivante (anti-partition)."""
        for _pid, peer in pairs:
            try:
                self.transport.post(peer["ip"], int(peer["port"]),
                                    "/leader-beat", body, timeout=1.0)
            except Exception:
                pass

    def _marquer_emetteur(self, body: Dict[str, Any]) -> None:
        """Rafraîchit l'émetteur d'un battement/annonce de leader, même
        lorsque son epoch/clé est en conflit avec le nôtre."""
        leader_id = body.get("leader_id", "")
        ip, port = body.get("ip"), int(body.get("port", 0) or 0)
        if leader_id and ip and port:
            self.state.update_peer(leader_id, {"ip": ip, "port": port,
                                               "mode": "full"})
        if leader_id:
            self.state.touch_peer(leader_id, alive=True)

    def _leader_message(self) -> Dict[str, Any]:
        # Le carnet d'adresses voyage AVEC les messages du leader : chaque
        # pair connaît ainsi tout l'essaim en un battement (1 s), même
        # avant le premier tirage complet du registre (5 s) — critique
        # pour que les annonces d'élection atteignent tout le monde.
        return {
            "leader_id": self.node_id,
            "ip": self.identity["ip"],
            "port": self.identity["port"],
            "epoch": self.epoch,
            "key": self.my_key_str(),
            "state_digest": self.state.digest(),
            "peers": self._address_book(),
        }

    def _address_book(self) -> List[Dict[str, Any]]:
        """Adresses de tous les pairs connus (transporteur de découverte)."""
        book = []
        for pid, peer in self.state.peers.items():
            if pid == self.node_id:
                continue
            if peer.get("ip") and peer.get("port"):
                book.append({"node_id": pid, "ip": peer["ip"],
                             "port": peer["port"],
                             "mode": peer.get("mode", "full")})
        return book

    def _merge_address_book(self, peers: Optional[List[Dict[str, Any]]]) -> None:
        """Apprend les pairs INCONNUS du carnet reçu (découverte passive)."""
        for entry in peers or []:
            nid = entry.get("node_id")
            if not nid or nid == self.node_id:
                continue
            if not entry.get("ip") or not entry.get("port"):
                continue
            if nid not in self.state.peers:
                self.state.update_peer(nid, {
                    "ip": entry["ip"], "port": int(entry["port"]),
                    "mode": entry.get("mode", "full")})
                self.state.touch_peer(nid, alive=True)

    def _send_heartbeat(self) -> None:
        """Follower -> Leader : battement + rafraîchissement du registre."""
        addr = self._peer_addr(self.leader_id) if self.leader_id else None
        if not addr:
            self.leader_id = None  # leader inconnu au registre : re-bootstrap
            return
        body = {
            "node_id": self.node_id,
            "ip": self.identity["ip"],
            "port": self.identity["port"],
            "ts": time.time(),
            "mode": self.identity.get("mode", "full"),
            "capabilities": self.state.self_capabilities(),
            "models": self.state.self_models(),
        }
        for k in ("ecdsa_pub", "mlkem_pub", "uptime_s", "requests_count"):
            if k in self.identity:
                body[k] = self.identity[k]
        try:
            status, reply = self.transport.post(addr[0], addr[1], "/heartbeat", body, timeout=2.0)
            if status == 200 and reply:
                # Découverte passive : le leader retourne le carnet
                # d'adresses de l'essaim tout entier.
                self._merge_address_book(reply.get("peers"))
                # Le leader répond avec son identité : contact direct =
                # preuve de vie -> le bail est renouvelé.
                if reply.get("leader_id") == self.leader_id:
                    with self._lock:
                        self._lease_deadline = time.time() + self.config.lease_timeout
                        if reply.get("epoch", self.epoch) > self.epoch:
                            self.epoch = reply["epoch"]
                elif reply.get("leader_id"):
                    # Redirection : le leader a changé entre-temps.
                    self._adopt_leader_from_message(reply, source="heartbeat_redirect")
        except Exception:
            # Leader injoignable : on ne fait rien ici, c'est le bail
            # (`lease_deadline`) qui déclenchera l'élection au bout de 6 s.
            pass

    def _try_join_known_peers(self) -> None:
        """
        Bootstrap : frapper aux portes de TOUS les pairs connus (seeds
        statiques, pairs vus en UDP), vivants ou simplement mémorisés —
        c'est le but du bootstrap de découvrir qui répond.
        """
        for pid, peer in list(self.state.peers.items()):
            if pid == self.node_id:
                continue
            ip, port = peer.get("ip"), int(peer.get("port", 0))
            if not ip or not port:
                continue
            body = {
                "node_id": self.node_id,
                "ip": self.identity["ip"],
                "port": self.identity["port"],
                "mode": self.identity.get("mode", "full"),
                "capabilities": self.state.self_capabilities(),
                "models": self.state.self_models(),
            }
            try:
                status, reply = self.transport.post(ip, port, "/join", body, timeout=2.0)
                if status == 200 and reply:
                    # Le pair répond : il est vivant.
                    self.state.touch_peer(pid, alive=True)
                    if pid.startswith("seed-"):
                        # Le seed a joué son rôle : remplacé par le vrai
                        # node_id appris via le registre synchronisé.
                        self.state.drop_peer(pid)
                    if reply.get("leader_id") and reply["leader_id"] != self.node_id:
                        self._adopt_leader_from_message(reply, source="join_reply")
                        return
            except Exception:
                continue

    # ------------------------------------------------------------------
    # Élection
    # ------------------------------------------------------------------

    def _alive_peer_ids(self) -> List[str]:
        """Pairs présumés vivants (vus récemment), hors soi-même."""
        return [
            pid for pid, peer in self.state.alive_peers(self.config.peer_timeout)
            if pid != self.node_id
        ]

    def _eligible_candidates(self) -> List[Tuple[Tuple[int, str], str]]:
        """
        Candidats à l'élection : soi-même + pairs vivants dont le mode est
        éligible (full/ambassador). Tri par clé déterministe — TOUS les
        nœuds calculent le même classement.
        """
        candidates: List[Tuple[Tuple[int, str], str]] = []
        if self.identity.get("mode", "full") in LEADER_ELIGIBLE_MODES:
            candidates.append((self.my_key, self.node_id))
        for pid, peer in self.state.alive_peers(self.config.peer_timeout):
            if pid == self.node_id:
                continue
            if peer.get("mode", "full") not in LEADER_ELIGIBLE_MODES:
                continue
            candidates.append((deterministic_key(peer.get("ip", ""), pid), pid))
        return sorted(candidates, key=lambda item: item[0])

    def start_election(self, reason: str) -> None:
        """
        Déclenche une élection. Le rang du nœud dans le classement
        déterministe fixe son délai de proclamation : rang 0 = immédiat,
        rang n = n * 0.3 s. En réseau sain, seul le rang 0 parle — les
        autres l'entendent et s'inclinent avant leur propre tour.
        """
        with self._lock:
            if self.leader_id and time.time() < self._lease_deadline:
                # Un battement est arrivé entre le relevé du bail et la
                # prise de verrou : le leader est vivant, élection annulée
                # (sans verrou global autour du tick, cette course existe).
                return
            candidates = self._eligible_candidates()
            rank = next((i for i, (_k, nid) in enumerate(candidates) if nid == self.node_id), None)
            if rank is None:
                # Mode non éligible (follower/provider) : on attend le
                # vainqueur ; le bail est relancé pour re-vérifier ensuite.
                self.role = Role.FOLLOWER
                self._lease_deadline = time.time() + self.config.lease_timeout
                return

            self.role = Role.CANDIDATE
            self.leader_id = None
            self._proclaim_at = time.time() + self.config.election_delay_per_rank * rank
            # Filet de sécurité : au plus tard au bail suivant, proclamation.
            self._lease_deadline = time.time() + self.config.lease_timeout
            self.security.record_event(
                "election_started",
                self.node_id,
                {"reason": reason, "epoch": self.epoch, "rank": rank,
                 "candidates": [nid for _k, nid in candidates]},
            )
            log.warning("ÉLECTION déclenchée (%s) — epoch %d, rang %d/%d",
                        reason, self.epoch, rank, len(candidates))

    def _proclaim(self, trigger: str) -> None:
        """Se proclame Leader pour une nouvelle epoch et l'annonce à tous."""
        with self._lock:
            if self.role == Role.LEADER or self.leader_id:
                # Déjà leader, OU une annonce d'élection est arrivée
                # pendant notre délai de rang : nous avons adopté un
                # leader plus légitime — candidature abandonnée (le tick
                # n'étant plus couvert par le verrou, cette course
                # existe ; elle est tranchée ici, sous verrou).
                return
            self.epoch += 1
            self.role = Role.LEADER
            self.leader_id = self.node_id
            self._proclaim_at = None
            self._next_beat = 0.0  # battre immédiatement
            self._lease_deadline = float("inf")
            self.state.set_leader(self.node_id, self.my_key_str(), self.epoch)
            self.security.record_event(
                "leader_elected",
                self.node_id,
                {"leader": self.node_id, "epoch": self.epoch,
                 "trigger": trigger, "method": "lease_failover"},
            )
            log.warning("JE SUIS LE NOUVEAU LEADER — epoch %d (déclencheur: %s)",
                        self.epoch, trigger)

        # Annonce hors verrou (appels réseau). On frappe à TOUTES les
        # adresses connues du registre — pas seulement les pairs vus
        # vivants : en cas de vues réciproquement périmées (celles qui
        # mènent à deux co-proclamations), c'est précisément l'annonce à
        # l'« autre » qui permet au plus grand de céder devant le plus
        # petit (règle déterministe) et de refermer la partition.
        body = self._leader_message()
        for pid, peer in list(self.state.peers.items()):
            if pid == self.node_id:
                continue
            ip, port = peer.get("ip"), int(peer.get("port", 0))
            if not ip or not port:
                continue
            try:
                self.transport.post(ip, port, "/leader-announce", body, timeout=2.0)
            except Exception:
                pass

    def _adopt_leader_from_message(self, msg: Dict[str, Any], source: str) -> None:
        """Adopte le leader annoncé par un message (beat/annonce/redirection)."""
        leader_id = msg.get("leader_id")
        epoch = int(msg.get("epoch", 0))
        key = msg.get("key") or ""
        addr = (msg.get("ip"), int(msg.get("port", 0)))
        if not leader_id or not addr[0]:
            return
        self._accept_leader(leader_id, epoch, key, addr, source)

    def _accept_leader(
        self,
        leader_id: str,
        epoch: int,
        key: str,
        addr: Tuple[str, int],
        source: str,
    ) -> None:
        with self._lock:
            changed = (self.leader_id != leader_id) or (epoch > self.epoch) or self.role != Role.FOLLOWER
            self.role = Role.FOLLOWER
            self.leader_id = leader_id
            self.epoch = max(self.epoch, epoch)
            self._proclaim_at = None
            self._lease_deadline = time.time() + self.config.lease_timeout
            self._next_heartbeat = 0.0  # battre immédiatement
            self.state.set_leader(leader_id, key, self.epoch)
            # On mémorise l'adresse du leader dans le registre local.
            self.state.update_peer(leader_id, {"ip": addr[0], "port": addr[1], "mode": "full"})
            if changed:
                self.security.record_event(
                    "leader_accepted",
                    self.node_id,
                    {"leader": leader_id, "epoch": self.epoch, "source": source},
                )
                log.info("Leader accepté: %s (epoch %d, via %s)", leader_id, self.epoch, source)

    def _step_down(self, winner: Dict[str, Any]) -> None:
        """
        Abdication : à epoch égale, la clé la plus petite gagne. Appelé
        quand un Leader coexistant de clé inférieure se manifeste
        (guérison de partition réseau).
        """
        with self._lock:
            self.security.record_event(
                "leader_stepdown",
                self.node_id,
                {"winner": winner.get("leader_id"), "epoch": self.epoch},
            )
            log.warning("Abdication au profit de %s (clé plus petite, epoch %d)",
                        winner.get("leader_id"), winner.get("epoch", 0))
        self._accept_leader(
            winner.get("leader_id"), int(winner.get("epoch", 0)),
            winner.get("key", ""), (winner.get("ip"), int(winner.get("port", 0))),
            source="stepdown",
        )

    def _reap_dead_peers(self, now: float) -> None:
        """Le Leader marque morts les followers muets puis les purge."""
        for pid, peer in list(self.state.peers.items()):
            last_seen = peer.get("last_seen", 0)
            if pid == self.node_id:
                continue
            if peer.get("alive", False) and now - last_seen > self.config.peer_timeout:
                self.state.mark_peer_dead(pid)
                self.security.record_event(
                    "peer_timeout", self.node_id,
                    {"peer": pid, "silence_s": round(now - last_seen, 1)},
                )
                log.warning("Pair présumé mort: %s (silence %.1fs)", pid, now - last_seen)
            elif not peer.get("alive", False) and now - last_seen > 5 * self.config.peer_timeout:
                # Muet depuis très longtemps : purge du registre.
                self.state.drop_peer(pid)
                self.security.record_event("peer_removed", self.node_id, {"peer": pid})

    # ------------------------------------------------------------------
    # Handlers appelés par la couche HTTP (swarm_glu.py)
    # ------------------------------------------------------------------

    def handle_join(self, body: Dict[str, Any], sender_ip: str) -> Tuple[int, Dict[str, Any]]:
        """
        Requête d'enregistrement d'un pair (POST /join).

        Devenir membre exige soit de figurer dans la whitelist, soit
        l'approbation par le consensus du Leader (allow_join).
        """
        node_id = body.get("node_id", "")
        ip = body.get("ip") or sender_ip
        port = int(body.get("port", 0))
        if not node_id or not port:
            return 400, {"error": "node_id et port requis"}

        enrolled = False
        if not self.security.is_node_allowed(node_id):
            if self.allow_join and self.role == Role.LEADER:
                # Consensus du Leader : approbation immédiate, journalisée,
                # puis propagée à tout l'essaim via l'état synchronisé.
                self.security.add_node(node_id, approved_by="leader:%s" % self.node_id)
                self.state.set_whitelist_nodes(self.security.nodes())
                self.security.record_event(
                    "whitelist_approved", self.node_id,
                    {"new_node": node_id, "approved_by": self.node_id, "ip": ip},
                )
                enrolled = True
            else:
                return 403, {
                    "error": "nœud non whitelisté — approbation requise",
                    "leader_id": self.leader_id,
                    "leader_addr": self._leader_addr_str(),
                }

        # Enregistrement dans le registre partagé (champs logiques).
        peer_data = {
            "ip": ip, "port": port,
            "mode": body.get("mode", "full"),
            "capabilities": body.get("capabilities", []),
            "models": body.get("models", []),
        }
        for k in ("ecdsa_pub", "mlkem_pub", "uptime_s", "requests_count", "joined_at"):
            if k in body:
                peer_data[k] = body[k]
        self.state.update_peer(node_id, peer_data)
        self.state.touch_peer(node_id, alive=True)
        self.security.record_event(
            "peer_registered", node_id,
            {"ip": ip, "port": port, "mode": body.get("mode", "full"),
             "enrolled_now": enrolled, "by": self.node_id},
        )
        return 200, self._join_reply()

    def _join_reply(self) -> Dict[str, Any]:
        """
        Réponse uniforme à /join : adresse COMPLÈTE du leader (ip, port,
        clé déterministe) au format compris par _adopt_leader_from_message.
        """
        reply = {
            "ok": True,
            "leader_id": self.leader_id,
            "leader_addr": self._leader_addr_str(),
            "epoch": self.epoch,
            "node_id": self.node_id,
            "role": self.role.value,
        }
        if self.leader_id == self.node_id:
            reply["ip"] = self.identity["ip"]
            reply["port"] = int(self.identity["port"])
            reply["key"] = self.my_key_str()
        elif self.leader_id:
            peer = self.state.peers.get(self.leader_id) or {}
            if peer.get("ip"):
                reply["ip"] = peer["ip"]
                reply["port"] = int(peer.get("port", 0))
                reply["key"] = self.state._doc.get("leader_key") or ""
        return reply

    def _leader_addr_str(self) -> Optional[str]:
        if not self.leader_id:
            return None
        if self.leader_id == self.node_id:
            return "%s:%s" % (self.identity["ip"], self.identity["port"])
        addr = self._peer_addr(self.leader_id)
        return "%s:%s" % addr if addr else None

    def handle_heartbeat(self, body: Dict[str, Any], sender_ip: str) -> Tuple[int, Dict[str, Any]]:
        """
        Battement d'un follower (POST /heartbeat) — réservé au Leader ;
        un follower répond par une redirection vers le leader courant.
        """
        node_id = body.get("node_id", "")
        if not node_id:
            return 400, {"error": "node_id requis"}
        if not self.security.is_node_allowed(node_id):
            return 403, {"error": "nœud non whitelisté",
                         "leader_addr": self._leader_addr_str()}

        peer_data = {
            "ip": body.get("ip") or sender_ip,
            "port": int(body.get("port", 0)),
            "mode": body.get("mode", "full"),
            "capabilities": body.get("capabilities", []),
            "models": body.get("models", []),
        }
        for k in ("ecdsa_pub", "mlkem_pub", "uptime_s", "requests_count"):
            if k in body:
                peer_data[k] = body[k]
        self.state.update_peer(node_id, peer_data)
        self.state.touch_peer(node_id, alive=True)

        if self.role == Role.LEADER:
            return 200, {
                "ok": True,
                "leader_id": self.node_id,
                "epoch": self.epoch,
                "lease_deadline": time.time() + self.config.lease_timeout,
                "state_digest": self.state.digest(),
                "peers": self._address_book(),  # découverte passive
            }
        # Je ne suis pas leader : redirection.
        return 200, self._join_reply()

    def handle_leader_beat(self, body: Dict[str, Any], sender_ip: str) -> Tuple[int, Dict[str, Any]]:
        """Battement de bail du Leader (POST /leader-beat) — côté followers."""
        leader_id = body.get("leader_id", "")
        if not leader_id or not self.security.is_node_allowed(leader_id):
            return 403, {"error": "leader non whitelisté"}

        # Même en conflit (deux leaders coexistants) : sans ce rafraîchissement,
        # chacun marque l'autre « mort » après peer_timeout, les battements
        # cessent et la partition ne peut plus se refermer.
        self._marquer_emetteur(body)

        # Découverte passive AVANT toute décision de conflit : le carnet
        # voyage avec le battement MÊME d'un leader rival. Sans ça, un
        # leader abdiquant n'apprendrait jamais les autres survivants et
        # son élection suivante ne s'annoncerait qu'au cadavre du leader
        # (essaim scindé constaté en démo).
        self._merge_address_book(body.get("peers"))

        if self.role == Role.LEADER and leader_id != self.node_id:
            epoch = int(body.get("epoch", 0))
            if epoch > self.epoch or (epoch == self.epoch and body.get("key", "") < self.my_key_str()):
                # Conflit de leaders : la règle de la plus petite clé tranche.
                self._step_down(body)
                return 200, {"ok": True, "stood_down": True}
            return 200, {"ok": True, "conflict": "ignoré (ma clé est plus petite)"}

        self._adopt_leader_from_message(body, source="leader_beat")
        return 200, {"ok": True, "follower": self.node_id}

    def handle_leader_announce(self, body: Dict[str, Any], sender_ip: str) -> Tuple[int, Dict[str, Any]]:
        """
        Proclamation d'élection (POST /leader-announce).

        Acceptée si : epoch strictement supérieure, ou epoch égale avec
        clé plus petite (départage déterministe des co-proclamations).
        """
        leader_id = body.get("leader_id", "")
        epoch = int(body.get("epoch", 0))
        if not leader_id or not self.security.is_node_allowed(leader_id):
            return 403, {"error": "leader non whitelisté"}

        # Même en conflit : garde l'émetteur rafraîchi (cf. handle_leader_beat).
        self._marquer_emetteur(body)

        # L'annonce transporte aussi le carnet d'adresses (cf.
        # _leader_message) : c'est LE moment où un leader abdiquant
        # apprend les autres survivants — sans fusion, son élection
        # suivante ne s'annoncerait qu'aux adresses périmées et la
        # partition ne se refermerait jamais (essaim scindé).
        self._merge_address_book(body.get("peers"))

        with self._lock:
            if leader_id == self.node_id:
                return 200, {"ok": True, "self": True}
            if self.role == Role.LEADER:
                if epoch > self.epoch or (epoch == self.epoch and body.get("key", "") < self.my_key_str()):
                    self._step_down(body)
                    return 200, {"ok": True, "stood_down": True}
                return 200, {"ok": True, "conflict": "ignoré"}
            if epoch < self.epoch:
                return 200, {"ok": True, "stale": True}

        self._adopt_leader_from_message(body, source="leader_announce")
        return 200, {"ok": True}

    def observe_state_leader(self, leader_id: Optional[str], key: str, epoch: int) -> None:
        """
        Notifié par le StateManager quand une fusion d'état fait émerger un
        leader d'epoch supérieure (information, pas preuve de vie : le bail
        continuera d'être validé par les battements réels).
        """
        with self._lock:
            if leader_id and epoch > self.epoch and self.role != Role.LEADER:
                addr = self._peer_addr(leader_id)
                if addr:
                    self._accept_leader(leader_id, epoch, key, addr, source="state_merge")

    # ------------------------------------------------------------------
    # Introspection (endpoint /status)
    # ------------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "role": self.role.value,
                "leader_id": self.leader_id,
                "leader_addr": self._leader_addr_str(),
                "epoch": self.epoch,
                "heartbeat_interval": self.config.heartbeat_interval,
                "lease_timeout": self.config.lease_timeout,
                "uptime_s": round(time.time() - self._started_at, 1),
            }
