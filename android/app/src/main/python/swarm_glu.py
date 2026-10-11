# -*- coding: utf-8 -*-
"""
SwarmGlu — swarm_glu.py
=======================
Serveur d'orchestration HTTP de l'essaim : la « glu » qui unit les briques
de Tree4Five Tools.

Responsabilités :

* **API HTTP** (stdlib pur, aucun pip — déploiement hors-ligne identique
  sur PC et Android) : enregistrement, heartbeats, élection, registre,
  synchronisation d'état, requêtes des élèves, passerelle.
* **Routage d'inférence** vers le Pont GGUF local (`com.tree4five.gguf`) :
  sélection dynamique du nœud fournisseur (modèles Qwen/Llama/Phi déclarés
  par les tablettes/PC), via API compatible OpenAI ou Ollama, ou mock
  intégré pour les tests hors-ligne.
* **Pare-feu anti-hallucination OSP v0.6** : chaque réponse reçoit un
  indice de véracité ancrée `groundedness` ; si `g < 0.30`, la réponse est
  marquée UNVERIFIED et exclue du RAG distribué (mais rendue à l'élève,
  pédagogiquement transparente).
* **Tâches MCP HarnessDroid** (`com.ai.harnessdroid`) avec barrière de
  validation humaine (*Human-in-the-Loop*) : aucune tâche ne part vers
  l'agent sans approbation explicite (enseignant).
* **Découverte UDP** (*beacon*) : un nœud qui démarre se fait connaître
  par broadcast — zéro configuration centrale, zéro SPOF.

Le serveur utilise `ThreadingHTTPServer` (un thread par requête) : les
battements de cœur ne bloquent jamais les inférences longues.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from consensus import ConsensusConfig, ConsensusManager
from crypto_auth import NodeCrypto, WeightedLottery
from decentralized_resigner import DecentralizedResigner
from gateway import SwarmGateway
from security import SecurityManager, canonical_json, sha256_hex
from state_manager import StateManager, SwarmSynchronizer

log = logging.getLogger("swarmglu.server")

# Port UDP par défaut du beacon de découverte (0 = désactivé).
DEFAULT_BEACON_PORT = 47479


# ---------------------------------------------------------------------------
# Configuration du nœud
# ---------------------------------------------------------------------------

class NodeConfig:
    """Tous les paramètres d'exécution d'un nœud SwarmGlu."""

    def __init__(
        self,
        node_id: str = "",
        port: int = 7001,
        mode: str = "full",                 # full | follower | ambassador | provider
        whitelist_file: str = "whitelist.json",
        data_dir: str = "data",
        swarm_id: str = "swarm-local",
        peers: Optional[List[str]] = None,  # seeds "ip:port" (fallback si UDP bloqué)
        neighbors: Optional[List[str]] = None,  # essaims voisins (ambassadeur)
        beacon_port: int = DEFAULT_BEACON_PORT,
        advertise_ip: str = "",
        advertise_port: int = 0,   # port annoncé aux pairs (≠ port d'écoute : émulateurs adb)
        bind: str = "0.0.0.0",
        heartbeat_interval: float = 3.0,
        lease_timeout: float = 6.0,
        allow_join: bool = True,
        mock_inference: bool = False,
        gguf_url: str = "",                 # ex. http://127.0.0.1:11434 (Ollama)
        gguf_models: Optional[List[str]] = None,
        api_flavor: str = "ollama",         # ollama | openai
        log_raw_prompts: bool = False,      # vie privée : prompt hashé par défaut
        task_ttl: float = 300.0,
        infer_timeout: float = 90.0,
        sync_interval: float = 5.0,
        resign_interval: float = 30.0,
        enable_resigning: bool = True,
        log_level: str = "INFO",
    ):
        self.node_id = node_id
        self.port = port
        self.mode = mode
        self.whitelist_file = whitelist_file
        self.data_dir = data_dir
        self.swarm_id = swarm_id
        self.peers = peers or []
        self.neighbors = neighbors or []
        self.beacon_port = beacon_port
        self.advertise_ip = advertise_ip
        # Port annoncé aux pairs : par défaut le port d'écoute. Distinct
        # quand le nœud est joignable via un transfert (adb forward pour
        # les émulateurs, NAT symétrique…) : on ÉCOUTE sur `port` mais on
        # déclare `advertise_port` aux pairs.
        self.advertise_port = advertise_port or port
        self.bind = bind
        self.heartbeat_interval = heartbeat_interval
        self.lease_timeout = lease_timeout
        self.allow_join = allow_join
        self.mock_inference = mock_inference
        self.gguf_url = gguf_url
        self.gguf_models = gguf_models or []
        self.api_flavor = api_flavor
        self.log_raw_prompts = log_raw_prompts
        self.task_ttl = task_ttl
        self.infer_timeout = infer_timeout
        self.sync_interval = sync_interval
        self.resign_interval = resign_interval
        self.enable_resigning = enable_resigning
        self.log_level = log_level


def detect_local_ip() -> str:
    """
    Détecte l'IP LAN du nœud SANS envoyer de paquet (astuce connect UDP).

    Indispensable hors-ligne : pas de DNS, pas de route par défaut garantie.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("10.255.255.255", 1))  # n'émet rien : force le routage
            return sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except socket.gaierror:
            return "127.0.0.1"


# ---------------------------------------------------------------------------
# Transport HTTP inter-nœuds (stdlib urllib)
# ---------------------------------------------------------------------------

class HttpTransport:
    """
    Client HTTP minimaliste injecté dans ConsensusManager / synchroniseur.
    Retourne toujours un tuple (code, dict|None) — jamais d'exception :
    les pannes réseau font partie de la vie d'un essaim.
    """

    def post(self, ip: str, port: int, path: str, body: Dict[str, Any],
             timeout: float = 5.0) -> Tuple[int, Optional[Dict[str, Any]]]:
        url = "http://%s:%s%s" % (ip, port, path)
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, self._parse(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, self._parse(exc.read())
        except Exception:
            return 0, None  # injoignable / timeout

    def get(self, ip: str, port: int, path: str,
            timeout: float = 5.0) -> Tuple[int, Optional[Dict[str, Any]]]:
        url = "http://%s:%s%s" % (ip, port, path)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.status, self._parse(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, self._parse(exc.read())
        except Exception:
            return 0, None

    @staticmethod
    def _parse(raw: bytes) -> Optional[Dict[str, Any]]:
        try:
            text = raw.decode("utf-8")
            return json.loads(text) if text else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None


# ---------------------------------------------------------------------------
# Pare-feu OSP v0.6 — indice de véracité ancrée (groundedness)
# ---------------------------------------------------------------------------

class GroundednessFirewall:
    """
    Pare-feu anti-hallucination du protocole OSP v0.6.

    Règle du cahier des charges : une réponse n'entre dans le RAG distribué
    que si son indice de véracité ancrée `g >= 0.30`.

    Calcul (heuristique PoC, extensible) :
    * si le fournisseur fournit un `groundedness` numérique, on le prend ;
    * sinon, il dépend du nombre de sources ANCRES citées :
      0 source -> g=0.0 ; n sources -> min(1.0, 0.4 + 0.2*(n-1)).
    """

    THRESHOLD = 0.30

    @classmethod
    def compute(cls, provider_response: Dict[str, Any]) -> float:
        g = provider_response.get("groundedness")
        if isinstance(g, (int, float)):
            return max(0.0, min(1.0, float(g)))
        sources = provider_response.get("sources") or []
        n = len(sources)
        if n <= 0:
            return 0.0
        return min(1.0, 0.4 + 0.2 * (n - 1))

    @classmethod
    def verdict(cls, g: float) -> Dict[str, Any]:
        verified = g >= cls.THRESHOLD
        return {
            "groundedness": round(g, 3),
            "threshold": cls.THRESHOLD,
            "verdict": "VERIFIED" if verified else "UNVERIFIED",
            "rag_eligible": verified,  # indexable dans le RAG mutualisé ?
            "note_osp": (
                "Réponse ancrée admise dans le RAG distribué."
                if verified else
                "Pare-feu OSP : véracité ancrée insuffisante (g < %.2f). "
                "Réponse délivrée mais EXCLUE du RAG distribué." % cls.THRESHOLD),
        }


# ---------------------------------------------------------------------------
# Pont MCP HarnessDroid — exécution de tâches agent
# ---------------------------------------------------------------------------

class McpDispatcher:
    """
    Exécuteur de tâches MCP (Model Context Protocol) à destination des
    agents autonomes HarnessDroid (`com.ai.harnessdroid`).

    PoC : mode « echo » — la tâche est journalisée et simulée.
    Point d'extension documenté : brancher ici l'envoi réel vers
    HarnessDroid (Intent/AIDL ou serveur MCP JSON-RPC local).
    """

    def __init__(self, config: NodeConfig):
        self.config = config

    def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        mcp = task.get("mcp", {})
        time.sleep(0.05)  # simulation d'exécution agent
        return {
            "status": "executed",
            "mode": "echo",
            "mcp_server": mcp.get("server", "default"),
            "tool": mcp.get("tool", ""),
            "args": mcp.get("args", {}),
            "transcript": [
                "[HITL] validation humaine obtenue: %s" % task.get("approved_by", "?"),
                "[MCP ] appel simulé de '%s' sur '%s'" % (
                    mcp.get("tool", "?"), mcp.get("server", "?")),
                "[EXT ] brancher ici com.tree4five / AIDL pour exécution réelle",
            ],
        }


# ---------------------------------------------------------------------------
# Découverte UDP (beacon broadcast)
# ---------------------------------------------------------------------------

class BeaconService:
    """
    Beacon UDP : chaque nœud annonce « swarm_id | node_id | port | epoch »
    en broadcast toutes les 3 s. Les nouveaux arrivants découvrent ainsi
    l'essaim SANS serveur d'annuaire. (Sur Android, le service acquiert un
    MulticastLock pour autoriser la réception des broadcasts.)

    Ce thread assure aussi deux tâches ménagères : rafraîchissement de sa
    propre entrée `last_seen` (registre) et rechargement à chaud de la
    whitelist (mise à jour par clé USB).
    """

    def __init__(self, node: Any, port: int, interval: float = 3.0):
        self.node = node
        self.port = port
        self.interval = interval
        self._stop = threading.Event()

    def start(self) -> None:
        if self.port <= 0:
            return
        threading.Thread(target=self._listen, name="beacon-rx", daemon=True).start()
        threading.Thread(target=self._send, name="beacon-tx", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def payload(self) -> Dict[str, Any]:
        return {
            "swarm_id": self.node.config.swarm_id,
            "node_id": self.node.node_id,
            "http_port": self.node.config.port,
            "mode": self.node.config.mode,
            "epoch": self.node.state.epoch,
            "leader_id": self.node.state._doc.get("leader_id"),
        }

    def _send(self) -> None:
        counter = 0
        while not self._stop.wait(self.interval):
            try:
                # Ménage : maintenir SA propre vitalité au registre.
                self.node.state.touch_peer(self.node.node_id, alive=True)
                # Ménage : whitelist rechargée à chaud toutes les ~15 s.
                counter += 1
                if counter % 5 == 0 and self.node.security.reload_whitelist_if_changed():
                    self.node.security.record_event(
                        "whitelist_reloaded", self.node.node_id,
                        {"digest": self.node.security.whitelist_digest()})

                payload = json.dumps(self.payload()).encode("utf-8")
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                try:
                    sock.sendto(payload, ("255.255.255.255", self.port))
                finally:
                    sock.close()
            except Exception:
                pass  # broadcast indisponible (réseau restreint) : --peers prend le relais

    def _listen(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", self.port))
            sock.settimeout(1.0)
        except OSError as exc:
            log.warning("Beacon UDP désactivé (bind impossible: %s)", exc)
            return
        while not self._stop.is_set():
            try:
                data, (ip, _port) = sock.recvfrom(8192)
                msg = json.loads(data.decode("utf-8"))
                if msg.get("swarm_id") != self.node.config.swarm_id:
                    continue  # un autre essaim ( passerelle), pas notre LAN logique
                if msg.get("node_id") in (None, self.node.node_id):
                    continue
                # Découverte : enregistrer le pair vu au broadcast.
                self.node.state.update_peer(msg["node_id"], {
                    "ip": ip, "port": int(msg.get("http_port", 0)),
                    "mode": msg.get("mode", "full"),
                })
                self.node.state.touch_peer(msg["node_id"], alive=True)
            except socket.timeout:
                continue
            except Exception:
                continue


# ---------------------------------------------------------------------------
# Gestionnaire des tâches agents (HITL : Human-in-the-Loop)
# ---------------------------------------------------------------------------

class TaskManager:
    """
    Cycle de vie d'une tâche agent HarnessDroid :

        pending_validation -> (approbation enseignant)
        -> approved -> dispatched -> done
        ou bien : rejected / expired (TTL)

    AUCUN dispatch vers l'agent tant que la barrière humaine n'est pas
    levée : c'est l'exigence Human-in-the-Loop du cahier des charges.
    """

    def __init__(self, node: Any, ttl: float = 300.0):
        self.node = node
        self.ttl = ttl
        self._lock = threading.RLock()
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._sweep_loop, name="tasks", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _sweep_loop(self) -> None:
        while not self._stop.wait(10.0):
            with self._lock:
                now = time.time()
                for tid, task in list(self._tasks.items()):
                    if task["status"] == "pending_validation" and now > task["deadline"]:
                        task["status"] = "expired"
                        self.node.security.record_event(
                            "agent_task_expired", self.node.node_id,
                            {"task_id": tid, "reason": "ttl"})

    def create(self, body: Dict[str, Any], origin_addr: str) -> Dict[str, Any]:
        task_id = sha256_hex("%s|%s|%f" % (
            self.node.node_id, body.get("student_id", "?"), time.time()))[:16]
        task = {
            "task_id": task_id,
            "student_id": body.get("student_id", ""),
            "requested_by": body.get("requested_by", ""),
            "target_node": body.get("target_node"),  # optionnel : auto-sélection
            "mcp": body.get("mcp", {"server": "harnessdroid", "tool": "", "args": {}}),
            "status": "pending_validation",
            "created_at": time.time(),
            "deadline": time.time() + self.ttl,
            "origin_addr": origin_addr,
        }
        with self._lock:
            self._tasks[task_id] = task
        self.node.security.record_event(
            "agent_task_created", task.get("student_id") or self.node.node_id,
            {"task_id": task_id, "mcp": task["mcp"], "target": task["target_node"]})
        return task

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(t) for t in sorted(
                self._tasks.values(), key=lambda t: -t["created_at"])]

    def validate(self, task_id: str, approved: bool, by: str) -> Tuple[int, Dict[str, Any]]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return 404, {"error": "tâche inconnue"}
            if task["status"] != "pending_validation":
                return 409, {"error": "tâche déjà traitée", "status": task["status"]}
            task["approved_by"] = by or "enseignant"
            task["status"] = "approved" if approved else "rejected"
        self.node.security.record_event(
            "agent_task_validated", self.node.node_id,
            {"task_id": task_id, "approved": approved, "by": by})
        if not approved:
            return 200, {"task_id": task_id, "status": "rejected"}
        return self.dispatch(task)

    def dispatch(self, task: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """Envoie la tâche approuvée vers un nœud agent (capacité harnessdroid)."""
        target = task.get("target_node") or self.node.pick_agent_node()
        if not target:
            return 503, {"error": "aucun nœud agent HarnessDroid disponible",
                         "task_id": task["task_id"]}
        task["target_node"] = target

        if target == self.node.node_id:
            result = self.node.mcp.execute(task)
            return self.node.tasks.complete(task["task_id"], result)

        peer = self.node.state.peers.get(target)
        if not peer:
            return 503, {"error": "nœud cible inconnu", "target": target}
        body = {"task": task, "origin_addr": "%s:%s" % (
            self.node.identity["ip"], self.node.config.port)}
        status, reply = self.node.transport.post(
            peer["ip"], int(peer["port"]), "/agent/dispatch", body,
            timeout=self.node.config.infer_timeout)
        with self._lock:
            if status == 200:
                t = self._tasks.get(task["task_id"])
                if t:
                    t["status"] = "dispatched"
        if status != 200:
            return status, reply or {"error": "dispatch impossible"}
        return 200, {"task_id": task["task_id"], "status": "dispatched",
                     "target": target, "reply": reply}

    def complete(self, task_id: str, result: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task["status"] = "done"
                task["result"] = result
        self.node.security.record_event(
            "agent_task_result", self.node.node_id,
            {"task_id": task_id, "result": result})
        return 200, {"task_id": task_id, "status": "done", "result": result}

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            counts: Dict[str, int] = {}
            for t in self._tasks.values():
                counts[t["status"]] = counts.get(t["status"], 0) + 1
            return {"total": len(self._tasks), "by_status": counts}


# ---------------------------------------------------------------------------
# Le nœud SwarmGlu : câblage de tous les services
# ---------------------------------------------------------------------------

class SwarmNode:
    """
    Assemble SecurityManager + StateManager + ConsensusManager + SwarmGateway
    + TaskManager + Beacon, et expose chaque route HTTP comme méthode
    `h_*(body, headers, params, query) -> (status, dict)`.
    """

    def __init__(self, config: NodeConfig):
        self.config = config
        self.node_id = config.node_id or "%s-%d" % (socket.gethostname(), config.port)
        ip = config.advertise_ip or detect_local_ip()
        # Identité annoncée : ip/port PUBLICS (peuvent différer du port
        # d'écoute — cf. advertise_port pour les émulateurs via adb forward).
        self.identity = {"node_id": self.node_id, "ip": ip,
                         "port": config.advertise_port, "mode": config.mode}

        os.makedirs(config.data_dir, exist_ok=True)
        # La whitelist vit dans data_dir pour être copiable/échangeable.
        self.security = SecurityManager(
            whitelist_path=config.whitelist_file,
            log_path=os.path.join(config.data_dir, "swarm_audit.jsonl"))

        # Authentification asymétrique hybride post-quantique (ML-KEM + ECDSA)
        self.started_at = time.time()
        self.request_count = 0
        self.crypto = NodeCrypto(self.node_id, key_dir=os.path.join(config.data_dir, "keys"))
        self.identity["ecdsa_pub"] = self.crypto.ecdsa_pub_hex
        self.identity["mlkem_pub"] = self.crypto.mlkem_pub_hex
        self.identity["uptime_s"] = 0.0
        self.identity["requests_count"] = 0
        self.secure_sessions: Dict[str, bytes] = {}

        # Auto-enrôlement : sans whitelist préchargée, un premier nœud doit
        # bien pouvoir démarrer (zéro SPOF) — trace explicite dans l'audit.
        if not self.security.is_node_allowed(self.node_id):
            if config.allow_join:
                self.security.add_node(
                    self.node_id,
                    approved_by="self-bootstrap",
                    ecdsa_pub=self.crypto.ecdsa_pub_hex,
                    mlkem_pub=self.crypto.mlkem_pub_hex,
                )
            # (sinon : le nœud tourne mais refusera tout pair non whitelisté ;
            #  il attend une whitelist copiée par USB.)

        self.state = StateManager(self.node_id, security=self.security)
        self.transport = HttpTransport()
        self.consensus = ConsensusManager(
            self.identity, self.state, self.transport, self.security,
            ConsensusConfig(config.heartbeat_interval, config.lease_timeout))
        self.consensus.allow_join = config.allow_join
        self.synchronizer = SwarmSynchronizer(self, interval=config.sync_interval)
        self.gateway = SwarmGateway(self, config.swarm_id, config.neighbors)
        self.mcp = McpDispatcher(config)
        self.tasks = TaskManager(self, ttl=config.task_ttl)
        self.beacon = BeaconService(self, config.beacon_port,
                                    interval=config.heartbeat_interval)
        self.resigner = DecentralizedResigner(
            self, interval=config.resign_interval, enabled=config.enable_resigning
        )

        # Déclaration de capacités locales (pont GGUF / agent MCP).
        capabilities: List[str] = ["node"]
        if config.mock_inference or config.gguf_url:
            capabilities.append("inference")
        if config.mode in ("full", "ambassador"):
            capabilities.append("harnessdroid")
        models = list(config.gguf_models)
        if config.mock_inference:
            models = models or ["mock-qwen-0.5b"]
        self.state.declare_self(capabilities, models,
                                api_flavor=config.api_flavor if config.gguf_url else
                                ("mock" if config.mock_inference else None),
                                base_url=config.gguf_url or None)
        self.state.update_peer(self.node_id, {
            "ip": ip, "port": config.advertise_port, "mode": config.mode,
            "capabilities": capabilities, "models": models,
            "api_flavor": self.state._self_info.get("api_flavor"),
            "base_url": self.state._self_info.get("base_url"),
            "ecdsa_pub": self.crypto.ecdsa_pub_hex,
            "mlkem_pub": self.crypto.mlkem_pub_hex,
            "uptime_s": 0.0,
            "requests_count": 0,
            "joined_at": self.started_at,
        })
        self.state.set_whitelist_nodes(self.security.nodes())

        # Seeds statiques (« ip:port ») : fallback si broadcast UDP bloqué.
        # Marqués vivants pour que la boucle de bootstrap les contacte
        # (l'entrée expire d'elle-même si l'adresse ne répond jamais).
        for seed in config.peers:
            try:
                host, p = seed.rsplit(":", 1)
                seed_id = "seed-%s:%s" % (host, p)
                self.state.update_peer(seed_id, {"ip": host, "port": int(p)})
                self.state.touch_peer(seed_id, alive=True)
            except ValueError:
                log.warning("seed invalide ignoré: %s", seed)

        self.server: Optional[ThreadingHTTPServer] = None

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------

    def start_services(self) -> None:
        self.security.record_event(
            "node_start", self.node_id,
            {"ip": self.identity["ip"], "port": self.config.advertise_port,
             "mode": self.config.mode, "swarm_id": self.config.swarm_id,
             "capabilities": self.state.self_capabilities(),
             "models": self.state.self_models(),
             "whitelist_digest": self.security.whitelist_digest()})
        self.consensus.start()
        self.synchronizer.start()
        self.gateway.start()
        self.tasks.start()
        self.beacon.start()
        self.resigner.start()
        log.info("Nœud '%s' démarré sur %s:%d (mode %s, essaim '%s')",
                 self.node_id, self.identity["ip"], self.config.port,
                 self.config.mode, self.config.swarm_id)

    def stop(self) -> None:
        self.security.record_event("node_shutdown", self.node_id,
                                   {"uptime_s": round(time.time() - self.started_at, 1)})
        for svc in (self.beacon, self.tasks, self.gateway, self.synchronizer, self.consensus, self.resigner):
            try:
                svc.stop()
            except Exception:
                pass
        if self.server:
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        log.info("Nœud '%s' arrêté", self.node_id)

    # ------------------------------------------------------------------
    # Sélection de fournisseurs / agents
    # ------------------------------------------------------------------

    def _provider_candidates(self, model_hint: str = "") -> List[Tuple[str, Dict[str, Any]]]:
        """Pairs vivants offrant l'inférence, triés (correspondance modèle, charge)."""
        window = max(2.0, 3 * self.config.heartbeat_interval)
        candidates = []
        for pid, peer in self.state.alive_peers(window):
            caps = peer.get("capabilities", [])
            if "inference" not in caps:
                continue
            models = " ".join(peer.get("models", [])).lower()
            match = 0 if (model_hint and model_hint.lower() in models) else 1
            candidates.append((match, peer.get("inflight", 0), pid, peer))
        candidates.sort(key=lambda c: (c[0], c[1], c[2]))
        return [(pid, peer) for _m, _i, pid, peer in candidates]

    def pick_agent_node(self) -> Optional[str]:
        """Nœud agent HarnessDroid vivant (soi-même si capable)."""
        if "harnessdroid" in self.state.self_capabilities():
            return self.node_id
        for pid, peer in self.state.alive_peers(max(6.0, 2 * self.config.heartbeat_interval)):
            if "harnessdroid" in peer.get("capabilities", []):
                return pid
        return None

    # ------------------------------------------------------------------
    # Inférence (pont GGUF local ou distant)
    # ------------------------------------------------------------------

    def local_infer(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """
        Point de terminaison /infer : le nœud EST le fournisseur.

        * mock_inference : réponse factice sourcée (tests/démos hors-ligne) ;
        * gguf_url       : transfert vers le Pont GGUF local de la machine
                           (Ollama http://…:11434/api/generate, ou API
                           OpenAI /v1/chat/completions) — com.tree4five.gguf.
        """
        prompt = body.get("prompt", "")
        model_hint = body.get("model_hint", "")
        t0 = time.time()
        self.request_count += 1
        self.identity["requests_count"] = self.request_count
        self.identity["uptime_s"] = round(time.time() - self.started_at, 1)
        self.state.update_peer(self.node_id, {
            "requests_count": self.request_count,
            "uptime_s": self.identity["uptime_s"],
        })

        if self.config.mock_inference:
            answer = ("[mock] Voici une réponse ancrée de démonstration à : « %s ». "
                      "Le modèle compact local (mock-qwen) fonctionne hors-ligne." % prompt[:120])
            return 200, {
                "answer": answer,
                "model": model_hint or "mock-qwen-0.5b",
                # Sources ancrées : celles transmises par la requête (p.ex.
                # ancre OSP swarmknowledge 192.168.1.249:3000), sinon le
                # corpus local de démonstration.
                "sources": (body.get("sources") if body.get("sources") is not None
                            else ["osp://local/djerba-wiki", "osp://local/manuel-svt"]),
                "provider": self.node_id,
            }

        if not self.config.gguf_url:
            return 503, {"error": "ce nœud n'héberge aucun modèle GGUF"}

        flavor = self.config.api_flavor
        model = model_hint or (self.config.gguf_models[0] if self.config.gguf_models else "default")
        if flavor == "openai":
            path = "/v1/chat/completions"
            payload = {"model": model, "stream": False,
                       "messages": [{"role": "user", "content": prompt}]}
        else:  # ollama
            path = "/api/generate"
            payload = {"model": model, "prompt": prompt, "stream": False}

        # Le pont GGUF tourne sur CETTE machine : appel direct de son URL locale.
        status, reply = self._post_url(self.config.gguf_url + path, payload)
        if status != 200 or not reply:
            return 502, {"error": "pont GGUF injoignable (%s)" % self.config.gguf_url}

        if flavor == "openai":
            answer = (reply.get("choices") or [{}])[0].get("message", {}).get("content", "")
        else:
            answer = reply.get("response", "")
        return 200, {"answer": answer, "model": reply.get("model", model),
                     "sources": body.get("sources", []), "provider": self.node_id}

    def _post_url(self, url: str, payload: Dict[str, Any]) -> Tuple[int, Optional[Dict[str, Any]]]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=self.config.infer_timeout) as resp:
                return resp.status, HttpTransport._parse(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, HttpTransport._parse(exc.read())
        except Exception:
            return 0, None

    def forward_infer(self, pid: str, peer: Dict[str, Any], body: Dict[str, Any]
                      ) -> Tuple[int, Dict[str, Any]]:
        """Transfère /infer vers un pair fournisseur (avec compteur de charge)."""
        peer["inflight"] = peer.get("inflight", 0) + 1
        try:
            return self.transport.post(
                peer["ip"], int(peer["port"]), "/infer", body,
                timeout=self.config.infer_timeout)
        finally:
            peer["inflight"] = max(0, peer.get("inflight", 0) - 1)

    # ------------------------------------------------------------------
    # Requête d'élève : routage + pare-feu OSP
    # ------------------------------------------------------------------

    def ask(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """
        POST /ask — requête d'élève, routée dynamiquement.

        Ordre de résolution :
        1. `swarm_id` distant  -> routage fédéré via la passerelle ;
        2. je suis follower    -> transfert au leader (coordinateur) ;
        3. traitement local    : choix du fournisseur (modèle + charge),
           inférence, calcul du groundedness, pare-feu OSP, audit scellé.
        """
        t0 = time.time()
        request_time = t0
        self.request_count += 1
        self.identity["requests_count"] = self.request_count
        self.identity["uptime_s"] = round(time.time() - self.started_at, 1)
        self.state.update_peer(self.node_id, {
            "requests_count": self.request_count,
            "uptime_s": self.identity["uptime_s"],
        })

        # 1) Fédération inter-essaims (sauf si la requête nous est déjà
        #    transférée : marqueur _fwd anti-boucle).
        target_swarm = body.get("swarm_id")
        if target_swarm and target_swarm != self.config.swarm_id and not body.get("_fwd"):
            return self.gateway.route_federated(body)

        # 2) Délégation au leader si je ne suis pas coordinateur.
        if not body.get("_fwd") and self.consensus.role.value != "leader" \
                and self.consensus.leader_id and self.consensus.leader_id != self.node_id:
            peer = self.state.peers.get(self.consensus.leader_id)
            if peer:
                forwarded = dict(body)
                forwarded["_fwd"] = 1
                status, reply = self.transport.post(
                    peer["ip"], int(peer["port"]), "/ask", forwarded,
                    timeout=self.config.infer_timeout + 10)
                if status == 200 and reply:
                    reply["served_by"] = self.consensus.leader_id
                    return status, reply
            # Leader injoignable (élection en cours) : on sert quand même
            # en local — résilience avant tout.

        # 3) Traitage local.
        model_hint = body.get("model_hint", "")
        candidates = self._provider_candidates(model_hint)
        if not candidates:
            return 503, {"error": "aucun fournisseur GGUF disponible dans l'essaim",
                         "student_id": body.get("student_id")}

        errors = []
        for pid, peer in candidates:
            if pid == self.node_id:
                status, resp = self.local_infer(body)
            else:
                status, resp = self.forward_infer(pid, peer, body)
            if status == 200 and resp and isinstance(resp.get("answer"), str):
                elapsed = time.time() - t0
                osp = GroundednessFirewall.verdict(GroundednessFirewall.compute(resp))
                # Vie privée (école) : le prompt est hashé dans l'audit,
                # sauf opt-in explicite --log-raw-prompts.
                prompt_field = (body.get("prompt", "") if self.config.log_raw_prompts
                                else None)
                payload = {
                    "student_id": body.get("student_id", ""),
                    "prompt": prompt_field,
                    "prompt_sha256": sha256_hex(body.get("prompt", "")),
                    "model_hint": model_hint,
                    "provider": pid,
                    "model": resp.get("model", ""),
                    "osp": osp,
                }
                self.security.record_event(
                    "request_routed", body.get("student_id") or self.node_id,
                    payload, processing_time=elapsed, request_time=request_time)
                # ----- RAG DISTRIBUÉ OSP v0.6 : « share all » -----
                # Toute réponse VERIFIED (g >= 0.30) entre dans le savoir
                # commun de l'essaim : stockée dans l'état distribué
                # (meta, fusion LWW), elle se propage à TOUS les nœuds —
                # PC comme tablettes/émulateurs — sans action supplémentaire.
                partage = None
                if osp["rag_eligible"]:
                    cle = "knowledge:%s" % sha256_hex(body.get("prompt", ""))[:12]
                    self.state.set_meta(cle, {
                        "q_sha256": payload["prompt_sha256"],
                        "student_id": body.get("student_id", ""),
                        "sources": resp.get("sources", []),
                        "groundedness": osp["groundedness"],
                        "provider": pid,
                        "model": resp.get("model", ""),
                        "epoch": self.state.epoch,
                        "ts": round(time.time(), 3),
                    })
                    partage = {"knowledge_key": cle,
                               "shared": True}
                    self.security.record_event(
                        "knowledge_shared", body.get("student_id") or self.node_id,
                        {"key": cle, "sources": resp.get("sources", []),
                         "groundedness": osp["groundedness"], "provider": pid})
                return 200, {
                    "answer": resp["answer"],
                    "model": resp.get("model", ""),
                    "provider": pid,
                    "sources": resp.get("sources", []),
                    "processing_time": round(elapsed, 3),
                    "request_time": request_time,
                    "osp": osp,
                    "epoch": self.state.epoch,
                    "knowledge": partage,   # null si pare-feu OSP refusé
                }
            errors.append({"provider": pid, "status": status,
                           "error": (resp or {}).get("error", "échec")})
        return 502, {"error": "tous les fournisseurs ont échoué", "details": errors}

    # ------------------------------------------------------------------
    # Helpers HTTP
    # ------------------------------------------------------------------

    def forward_to_leader(self, path: str, body: Dict[str, Any], method: str = "POST"
                          ) -> Optional[Tuple[int, Dict[str, Any]]]:
        """Transfère une requête d'administration au leader (whitelist...)."""
        leader = self.consensus.leader_id
        if not leader or leader == self.node_id:
            return None
        peer = self.state.peers.get(leader)
        if not peer:
            return None
        if method == "GET":
            return self.transport.get(peer["ip"], int(peer["port"]), path, timeout=10.0)
        return self.transport.post(peer["ip"], int(peer["port"]), path, body, timeout=10.0)

    # ------------------------------------------------------------------
    # Handlers HTTP (signature uniforme)
    # ------------------------------------------------------------------

    def h_health(self, body, headers, params, query):
        return 200, {"ok": True, "node_id": self.node_id,
                     "role": self.consensus.role.value, "epoch": self.state.epoch}

    def h_status(self, body, headers, params, query):
        c = self.consensus.snapshot()
        peers = []
        for pid, peer in sorted(self.state.peers.items()):
            peers.append({
                "node_id": pid, "addr": "%s:%s" % (peer.get("ip"), peer.get("port")),
                "mode": peer.get("mode", "full"),
                "capabilities": peer.get("capabilities", []),
                "models": peer.get("models", []),
                "alive": peer.get("alive", False),
                "last_seen": peer.get("last_seen", 0),
            })
        return 200, {
            "node_id": self.node_id,
            "addr": "%s:%s" % (self.identity["ip"], self.config.advertise_port),
            "swarm_id": self.config.swarm_id,
            "mode": self.config.mode,
            "consensus": c,
            "state": {
                "epoch": self.state.epoch,
                "version": self.state._doc["version"],
                "digest": self.state.digest(),
                "peer_count": len(peers),
                "alive_count": sum(1 for p in peers if p["alive"]),
            },
            "whitelist_digest": self.security.whitelist_digest(),
            "whitelist_count": len(self.security.nodes()),
            "crypto_mode": self.crypto.mode,
            "audit": self.security.verify_chain(),
            "peers": peers,
            "gateway_neighbors": self.gateway.neighbors(),
            "tasks": self.tasks.summary(),
            "uptime_s": round(time.time() - self.started_at, 1),
            "requests_count": self.request_count,
            "crypto": {
                "ecdsa_pub": self.crypto.ecdsa_pub_hex,
                "mlkem_pub": self.crypto.mlkem_pub_hex,
            },
            "signed_whitelist": self.security.get_signed_whitelist() is not None,
        }

    def h_join(self, body, headers, params, query):
        sender_ip = headers.get("X-Forwarded-For", "").split(",")[0].strip() \
            or self.client_address_ip(headers)
        return self.consensus.handle_join(body, sender_ip)

    def h_heartbeat(self, body, headers, params, query):
        return self.consensus.handle_heartbeat(body, self.client_address_ip(headers))

    def h_leader_beat(self, body, headers, params, query):
        return self.consensus.handle_leader_beat(body, self.client_address_ip(headers))

    def h_leader_announce(self, body, headers, params, query):
        return self.consensus.handle_leader_announce(body, self.client_address_ip(headers))

    def h_registry(self, body, headers, params, query):
        return 200, {"state": self.state.to_dict(), "from": self.node_id}

    def h_state_digest(self, body, headers, params, query):
        return 200, {"epoch": self.state.epoch, "digest": self.state.digest(),
                     "node_id": self.node_id}

    def h_state_sync(self, body, headers, params, query):
        remote = body.get("state") or {}
        report = self.state.merge(remote)
        added = self.state.apply_merged_whitelist()
        for nid in added:
            self.security.record_event("whitelist_propagated", self.node.node_id,
                                       {"new_node": nid, "source": "state_sync"})
        self.consensus.observe_state_leader(
            remote.get("leader_id"), remote.get("leader_key") or "",
            int(remote.get("epoch", 0)))
        if report["changed"]:
            self.security.record_event(
                "state_synced", self.node_id,
                {"source": body.get("from", "?"), "epoch": self.state.epoch,
                 "digest": self.state.digest()})
        return 200, {"ok": True, "digest": self.state.digest(),
                     "epoch": self.state.epoch, "changed": report["changed"]}

    def h_ask(self, body, headers, params, query):
        if not body.get("prompt"):
            return 400, {"error": "champ 'prompt' requis"}
        return self.ask(body)

    def h_infer(self, body, headers, params, query):
        if not body.get("prompt"):
            return 400, {"error": "champ 'prompt' requis"}
        t0 = time.time()
        status, resp = self.local_infer(body)
        self.security.record_event(
            "inference_local", self.node_id,
            {"prompt_sha256": sha256_hex(body.get("prompt", "")),
             "status": status, "model": (resp or {}).get("model", "")},
            processing_time=time.time() - t0, request_time=t0)
        return status, resp

    def h_provider_register(self, body, headers, params, query):
        """Un nœud déclare/modifie ses capacités de fournisseur (pont GGUF)."""
        self.state.declare_self(
            body.get("capabilities", ["inference"]),
            body.get("models", []),
            api_flavor=body.get("api_flavor"),
            base_url=body.get("base_url"))
        info = self.state._self_info
        self.state.update_peer(self.node_id, {
            "capabilities": info["capabilities"], "models": info["models"],
            "api_flavor": info["api_flavor"], "base_url": info["base_url"]})
        self.security.record_event(
            "provider_registered", self.node_id,
            {"models": info["models"], "api_flavor": info["api_flavor"],
             "base_url": info["base_url"]})
        return 200, {"ok": True, "capabilities": info["capabilities"],
                     "models": info["models"]}

    def h_whitelist_proposal(self, body, headers, params, query):
        """Proposition d'ajout à la whitelist : seul le Leader tranche."""
        node_id = body.get("node_id", "")
        if not node_id:
            return 400, {"error": "node_id requis"}
        if self.consensus.role.value != "leader":
            fwd = self.forward_to_leader("/whitelist/proposal", body)
            if fwd:
                return fwd
            return 503, {"error": "aucun leader disponible pour approuver"}
        self.security.record_event("whitelist_proposal", node_id,
                                   {"proposed_by": body.get("requested_by", "?")})
        # Consensus du Leader : approbation (PoC mono-leader ; l'événement
        # reste scellé dans la chaîne pour audit).
        self.security.add_node(node_id, approved_by="leader:%s" % self.node_id)
        self.state.set_whitelist_nodes(self.security.nodes())
        self.security.record_event("whitelist_approved", self.node_id,
                                   {"new_node": node_id, "by": self.node_id})
        return 200, {"ok": True, "approved": node_id,
                     "whitelist_digest": self.security.whitelist_digest()}

    def h_whitelist_approve(self, body, headers, params, query):
        return self.h_whitelist_proposal(body, headers, params, query)

    def h_whitelist_consensus_select(self, body, headers, params, query):
        """
        Sollicitation pour le consensus 1/8 :
        Le nœud désigné par la sélection pondérée demande l'accord du pair
        pour vérifier et re-signer la whitelist.
        """
        candidate_id = body.get("candidate_id", "")
        epoch = body.get("epoch", 1)
        uptime_s = body.get("uptime_s", 1.0)
        requests_count = body.get("requests_count", 0)
        ts = body.get("timestamp", 0)
        sig = body.get("signature", "")
        all_metrics = body.get("all_metrics", {})

        if not candidate_id:
            return 400, {"error": "candidate_id requis"}

        # Vérifier que le candidat est autorisé dans la whitelist
        if not self.security.is_node_allowed(candidate_id):
            return 403, {"error": "Nœud candidat non autorisé dans la whitelist"}

        # Clé publique du candidat
        cand_pub = (all_metrics.get(candidate_id, {}).get("ecdsa_pub")
                    or self.state.peers.get(candidate_id, {}).get("ecdsa_pub"))
        if cand_pub:
            msg = "CONSENSUS_PROPOSAL:%s:%s:%s" % (candidate_id, epoch, ts)
            if not NodeCrypto.verify_signature(msg, sig, cand_pub):
                return 400, {"error": "Signature de proposition invalide"}

        # Vérifier la validité des probabilités pondérées si métriques fournies
        if all_metrics:
            probs = WeightedLottery.compute_probabilities(all_metrics)
            if candidate_id not in probs:
                return 400, {"error": "Candidat absent des probabilités pondérées"}

        # Émission du vote d'accord 1/8 signé avec notre clé ECDSA
        voter_ts = round(time.time(), 3)
        vote_msg = "SWARM_1_8_VOTE:%s:%s:%s:%s" % (epoch, candidate_id, self.node_id, voter_ts)
        vote_sig = self.crypto.sign(vote_msg)

        self.security.record_event(
            "whitelist_consensus_vote_granted", self.node_id,
            {"candidate": candidate_id, "epoch": epoch}
        )

        return 200, {
            "ok": True,
            "vote": "agree",
            "voter_id": self.node_id,
            "voter_ecdsa_pub": self.crypto.ecdsa_pub_hex,
            "timestamp": voter_ts,
            "signature": vote_sig,
        }

    def h_whitelist_resign(self, body, headers, params, query):
        """
        Diffusion d'une nouvelle whitelist re-signée avec preuve de consensus 1/8.
        """
        signed_doc = body.get("signed_whitelist")
        if not signed_doc:
            return 400, {"error": "signed_whitelist requis"}

        known_keys = {self.node_id: self.crypto.ecdsa_pub_hex}
        for pid, p in self.state.peers.items():
            if p.get("ecdsa_pub"):
                known_keys[pid] = p["ecdsa_pub"]

        total_nodes = len(self.state.alive_peers())
        ok, reason = self.security.apply_signed_whitelist(
            signed_doc, known_keys=known_keys, active_nodes_count=total_nodes
        )
        if not ok:
            return 400, {"error": "Rejet de la whitelist re-signée: %s" % reason}

        self.security.record_event(
            "whitelist_resigned_accepted", self.node_id,
            {"signer": signed_doc.get("signer_node_id"),
             "epoch": signed_doc.get("epoch"),
             "nodes_count": len(self.security.nodes())}
        )
        return 200, {"ok": True, "digest": self.security.whitelist_digest()}

    def h_whitelist_trigger_resign(self, body, headers, params, query):
        """Déclenche manuellement un cycle de sélection, consensus 1/8 et re-signature."""
        force_self = bool(body.get("force_self", True))
        seed = body.get("seed")
        result = self.resigner.run_cycle(force_self=force_self, seed=seed)
        return 200, result

    def h_whitelist_signed(self, body, headers, params, query):
        """Retourne la version signée actuelle de la whitelist."""
        signed = self.security.get_signed_whitelist()
        if not signed:
            return 404, {"error": "Aucune whitelist signée présente"}
        return 200, {
            "ok": True,
            "signed_whitelist": signed,
            "digest": self.security.whitelist_digest(),
        }

    def h_secure_handshake(self, body, headers, params, query):
        """
        Poignée de main asymétrique hybride post-quantique (ML-KEM-768 + ECDSA).
        Permet l'authentification mutuelle et la négociation d'une clé de session partagée.
        """
        msg_type = body.get("type")
        if msg_type == "handshake_init":
            sender_id = body.get("sender_id", "")
            try:
                resp_payload, session_key = self.crypto.respond_handshake(body)
                self.secure_sessions[sender_id] = session_key
                self.security.record_event(
                    "secure_handshake_established", self.node_id,
                    {"peer": sender_id, "post_quantum": "ML-KEM-768", "auth": "ECDSA-P256"}
                )
                return 200, resp_payload
            except Exception as exc:
                return 400, {"error": "Handshake refusé : %s" % exc}
        return 400, {"error": "Type de handshake invalide"}

    def h_secure_rpc(self, body, headers, params, query):
        """
        Exécution d'une commande via le canal sécurisé ML-KEM + ECDSA.
        """
        sender_id = body.get("sender_id", "")
        encrypted_payload = body.get("payload", "")
        session_key = self.secure_sessions.get(sender_id)
        if not session_key:
            return 401, {"error": "Session non authentifiée (faire handshake au préalable)"}
        try:
            raw_bytes = self.crypto.decrypt_payload(session_key, encrypted_payload)
            req = json.loads(raw_bytes.decode("utf-8"))
            action = req.get("action", "ping")
            if action == "ping":
                reply_data = {"status": "ok", "action": "pong", "ts": time.time()}
            elif action == "get_whitelist":
                reply_data = {
                    "nodes": self.security.nodes(),
                    "digest": self.security.whitelist_digest(),
                    "signed": self.security.get_signed_whitelist() is not None,
                }
            else:
                reply_data = {"status": "ok", "action": action, "echo": req}
            enc_reply = self.crypto.encrypt_payload(session_key, json.dumps(reply_data))
            return 200, {"status": "ok", "sender_id": self.node_id, "payload": enc_reply}
        except Exception as exc:
            return 400, {"error": "Erreur RPC sécurisé : %s" % exc}

    def h_agent_task(self, body, headers, params, query):
        """Création d'une tâche agent — stockée chez le leader."""
        if self.consensus.role.value != "leader":
            fwd = self.forward_to_leader("/agent/task", body)
            if fwd:
                return fwd
        origin = "%s:%s" % (self.identity["ip"], self.config.port)
        task = self.tasks.create(body, origin)
        return 200, {"task": task,
                     "hint": "POST /agent/task/%s/validate {\"approved\":true,"
                             "\"by\":\"enseignant\"} pour lever la barrière HITL"
                             % task["task_id"]}

    def h_agent_task_get(self, body, headers, params, query):
        # Les tâches vivent chez le leader : déléguer si inconnue ici.
        if self.tasks.get(params[0]) is None:
            fwd = self.forward_to_leader("/agent/task/%s" % params[0], {}, method="GET")
            if fwd:
                return fwd
        task = self.tasks.get(params[0])
        return (200, {"task": task}) if task else (404, {"error": "tâche inconnue"})

    def h_agent_task_validate(self, body, headers, params, query):
        """Barrière Human-in-the-Loop : approbation enseignant."""
        # La validation peut arriver sur N'IMPORTE QUEL nœud (la tablette
        # de l'enseignant) : si la tâche vit chez le leader, déléguer.
        if self.tasks.get(params[0]) is None:
            fwd = self.forward_to_leader("/agent/task/%s/validate" % params[0], body)
            if fwd:
                return fwd
        approved = bool(body.get("approved", False))
        return self.tasks.validate(params[0], approved, body.get("by", "enseignant"))

    def h_agent_dispatch(self, body, headers, params, query):
        """Côté nœud agent : exécute une tâche déjà validée par un humain."""
        task = body.get("task") or {}
        if task.get("status") not in ("approved",):
            return 409, {"error": "tâche non approuvée (barrière HITL)"}
        t0 = time.time()
        result = self.mcp.execute(task)
        # Retour du résultat à l'origine (best effort).
        origin = body.get("origin_addr", "")
        if origin:
            try:
                host, p = origin.rsplit(":", 1)
                self.transport.post(host, int(p), "/agent/result",
                                    {"task_id": task.get("task_id"),
                                     "result": result,
                                     "processing_time": time.time() - t0},
                                    timeout=10.0)
            except Exception:
                pass
        return 200, {"ok": True, "task_id": task.get("task_id"), "result": result}

    def h_agent_result(self, body, headers, params, query):
        return self.tasks.complete(body.get("task_id", ""), body.get("result", {}))

    def h_tasks(self, body, headers, params, query):
        return 200, {"tasks": self.tasks.list(), "summary": self.tasks.summary()}

    def h_providers(self, body, headers, params, query):
        providers = []
        for pid, peer in self._provider_candidates():
            providers.append({"node_id": pid,
                              "addr": "%s:%s" % (peer.get("ip"), peer.get("port")),
                              "models": peer.get("models", []),
                              "api_flavor": peer.get("api_flavor")})
        return 200, {"providers": providers}

    def h_knowledge(self, body, headers, params, query):
        """
        GET /knowledge — le SAVOIR COMMUN de l'essaim (« share all ») :
        toutes les réponses VERIFIED (OSP v0.6, g >= 0.30) partagées par
        n'importe quel nœud, propagées à tous par la fusion d'état.
        Le digest permet de vérifier d'un coup que tout le monde a TOUT.
        """
        entrees = sorted(
            ({"key": cle, **val} for cle, val in self.state._doc["meta"].items()
             if cle.startswith("knowledge:")),
            key=lambda e: e.get("ts", 0), reverse=True)
        return 200, {
            "count": len(entrees),
            "digest": sha256_hex(canonical_json(entrees)),
            "entries": entrees[:100],
        }

    def h_audit_verify(self, body, headers, params, query):
        return 200, self.security.verify_chain()

    def h_audit_tail(self, body, headers, params, query):
        n = int((query.get("n") or ["20"])[0])
        return 200, {"events": self.security.tail(min(n, 500))}

    def h_relay(self, body, headers, params, query):
        """
        POST /relay — ROUTAGE APPLICATIF DU POINT D'ACCÈS (« étendre la
        zone »).

        Sur Android, un nœud peut ouvrir un hotspot local-only
        (WifiManager.startLocalOnlyHotspot — bouton « Étendre l'essaim » de
        l'application) : les clients de ce hotspot (tablettes d'élèves,
        agents HarnessDroid, ponts GGUF/LLMProvider…) n'ont PAS accès au
        LAN de l'école. Ce nœud, membre des DEUX réseaux, relaie leurs
        requêtes HTTP vers les services du réseau principal.

        Sécurité : destinations limitées aux adresses PRIVÉES (RFC1918 +
        loopback) — jamais de proxy ouvert vers Internet ; chaque relais
        est scellé dans la chaîne d'audit.
        """
        import ipaddress
        host = str(body.get("host", ""))
        try:
            port = int(body.get("port", 80) or 80)
        except (TypeError, ValueError):
            return 400, {"error": "port invalide", "port": body.get("port")}
        chemin = str(body.get("path", "/") or "/")
        methode = str(body.get("method", "GET") or "GET").upper()
        timeout = min(float(body.get("timeout", 30) or 30), 90.0)
        try:
            ip = ipaddress.ip_address(host)
            if not (ip.is_private or ip.is_loopback):
                return 403, {"error": "relais limité aux réseaux privés "
                                      "(RFC1918/loopback)", "host": host}
        except ValueError:
            return 400, {"error": "adresse IP littérale requise", "host": host}

        t0 = time.time()
        if methode == "POST":
            status, reponse = self.transport.post(
                host, port, chemin, body.get("body") or {}, timeout=timeout)
        elif methode == "GET":
            status, reponse = self.transport.get(host, port, chemin, timeout=timeout)
        else:
            return 400, {"error": "methode GET ou POST uniquement", "method": methode}
        self.security.record_event(
            "relayed", self.node_id,
            {"host": host, "port": port, "path": chemin, "method": methode,
             "status": status},
            processing_time=time.time() - t0, request_time=t0)
        return 200, {"status": status, "data": reponse,
                     "relayed_by": self.node_id,
                     "processing_time": round(time.time() - t0, 3)}

    def h_gateway_sync(self, body, headers, params, query):
        return self.gateway.handle_sync(body)

    def h_gateway_ask(self, body, headers, params, query):
        target = body.get("swarm_id")
        if target and target != self.config.swarm_id:
            return self.gateway.route_federated(body)
        local = dict(body)
        local["_fwd"] = 1  # nous SOMMES l'essaim cible
        status, payload = self.ask(local)
        payload.setdefault("federated", True)
        payload["served_by_swarm"] = self.config.swarm_id
        return status, payload

    def client_address_ip(self, headers) -> str:
        """IP du client HTTP (renseignée par le handler)."""
        return getattr(headers, "client_ip", "") or ""


# ---------------------------------------------------------------------------
# Serveur HTTP (routing minimal, zéro dépendance)
# ---------------------------------------------------------------------------

ROUTES: Dict[Tuple[str, str], str] = {
    ("GET", "/health"): "h_health",
    ("GET", "/status"): "h_status",
    ("GET", "/registry"): "h_registry",
    ("GET", "/state/digest"): "h_state_digest",
    ("GET", "/providers"): "h_providers",
    ("GET", "/knowledge"): "h_knowledge",
    ("GET", "/tasks"): "h_tasks",
    ("GET", "/audit/verify"): "h_audit_verify",
    ("GET", "/audit/tail"): "h_audit_tail",
    ("POST", "/join"): "h_join",
    ("POST", "/heartbeat"): "h_heartbeat",
    ("POST", "/leader-beat"): "h_leader_beat",
    ("POST", "/leader-announce"): "h_leader_announce",
    ("POST", "/state/sync"): "h_state_sync",
    ("POST", "/ask"): "h_ask",
    ("POST", "/infer"): "h_infer",
    ("POST", "/provider/register"): "h_provider_register",
    ("POST", "/whitelist/proposal"): "h_whitelist_proposal",
    ("POST", "/whitelist/approve"): "h_whitelist_approve",
    ("POST", "/agent/task"): "h_agent_task",
    ("POST", "/agent/dispatch"): "h_agent_dispatch",
    ("POST", "/agent/result"): "h_agent_result",
    ("POST", "/relay"): "h_relay",
    ("POST", "/gateway/sync"): "h_gateway_sync",
    ("POST", "/gateway/ask"): "h_gateway_ask",
    ("POST", "/secure/handshake"): "h_secure_handshake",
    ("POST", "/secure/rpc"): "h_secure_rpc",
    ("POST", "/whitelist/consensus-select"): "h_whitelist_consensus_select",
    ("POST", "/whitelist/resign"): "h_whitelist_resign",
    ("POST", "/whitelist/trigger-resign"): "h_whitelist_trigger_resign",
    ("GET", "/whitelist/signed"): "h_whitelist_signed",
}

REGEX_ROUTES = [
    ("POST", re.compile(r"^/agent/task/([^/]+)/validate$"), "h_agent_task_validate"),
    ("GET", re.compile(r"^/agent/task/([^/]+)$"), "h_agent_task_get"),
]


class SwarmHTTPHandler(BaseHTTPRequestHandler):
    """Répartiteur HTTP -> méthodes SwarmNode.h_*. CORS ouvert (tablettes)."""

    protocol_version = "HTTP/1.1"
    server_version = "SwarmGlu/0.1"

    @property
    def node(self) -> SwarmNode:
        return self.server.node  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    # -- plomberie ----------------------------------------------------------

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self) -> Optional[Dict[str, Any]]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if len(path) > 1:
            path = path.rstrip("/")  # tolérance /status/ == /status
        query = parse_qs(parsed.query)

        handler_name = ROUTES.get((method, path))
        params: List[str] = []
        if not handler_name:
            for m, pattern, name in REGEX_ROUTES:
                if m != method:
                    continue
                match = pattern.match(path)
                if match:
                    handler_name, params = name, list(match.groups())
                    break
        if not handler_name:
            self._send_json(404, {"error": "route inconnue", "path": path})
            return

        body: Dict[str, Any] = {}
        if method == "POST":
            maybe_body = self._read_body()
            if maybe_body is None:
                self._send_json(400, {"error": "corps JSON invalide"})
                return
            body = maybe_body

        # L'IP client est exposée aux handlers (join, heartbeats).
        self.headers.client_ip = self.client_address[0]  # type: ignore[attr-defined]
        handler = getattr(self.node, handler_name)
        try:
            status, payload = handler(body, self.headers, params, query)
        except Exception as exc:  # pragma: no cover
            log.exception("erreur interne sur %s", path)
            status, payload = 500, {"error": "erreur interne: %s" % exc}
        self._send_json(status, payload if isinstance(payload, dict) else {"data": payload})

    def do_GET(self) -> None:  # noqa: N802 (API BaseHTTPRequestHandler)
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def make_server(node: SwarmNode) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((node.config.bind, node.config.port), SwarmHTTPHandler)
    server.daemon_threads = True
    server.node = node  # type: ignore[attr-defined]
    return server


def serve(config: NodeConfig, block: bool = True) -> SwarmNode:
    """
    Démarre un nœud complet (HTTP + consensus + sync + gateway + beacon).

    * block=True  : bloque jusqu'à SIGINT/SIGTERM (CLI) ;
    * block=False : rend immédiatement la main ( pont Android Chaquopy).
    """
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s")

    node = SwarmNode(config)
    node.server = make_server(node)
    threading.Thread(target=node.server.serve_forever, name="http",
                     kwargs={"poll_interval": 0.5}, daemon=True).start()
    node.start_services()

    if not block:
        return node

    stop_event = threading.Event()

    def _handle_signal(signum, frame):
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        while not stop_event.is_set():
            time.sleep(0.5)
    finally:
        node.stop()
    return node


if __name__ == "__main__":
    # Démarrage direct minimal : `python swarm_glu.py` (la CLI complète est
    # dans cli.py).
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7001
    node_id = sys.argv[2] if len(sys.argv) > 2 else ""
    serve(NodeConfig(port=port, node_id=node_id))
