# -*- coding: utf-8 -*-
"""
SwarmGlu — android_bridge.py
============================
Pont entre l'application Android (Kotlin, package com.tree4five.swarmglu)
et le noyau Python SwarmGlu, via l'intégrateur Chaquopy.

Pourquoi ce pont : le même code Python (security.py, consensus.py,
state_manager.py, gateway.py, swarm_glu.py) tourne À L'IDENTIQUE sur les
PC et dans l'application Android — le protocole ne peut jamais diverger
entre plateformes. L'UI Kotlin appelle directement ces fonctions (process
unique, pas de boucle HTTP locale) :

    start(config_json)      -> démarre le nœud complet (non bloquant)
    stop()                  -> arrêt propre (journalise node_shutdown)
    is_running()            -> booléen
    status_json()           -> instantané complet (rôle, epoch, pairs…)
    ask_json(prompt, ...)   -> requête d'élève routée (pare-feu OSP inclus)
    audit_verify_json()     -> vérification de la chaîne de hachage
    audit_tail_json(n)      -> n derniers blocs du journal
    whitelist_add(node_id)  -> administration locale de la whitelist

Chaque fonction retourne une chaîne JSON (jamais d'exception vers Kotlin :
les erreurs sont sérialisées dans {"error": ...}).
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict

import swarm_glu

log = logging.getLogger("swarmglu.android")

_node = None            # swarm_glu.SwarmNode unique de l'application
_lock = threading.RLock()


def start(config_json: str) -> str:
    """Démarre le nœud (idempotent). `config_json` = dictionnaire NodeConfig."""
    global _node
    with _lock:
        if _node is not None:
            return json.dumps({"ok": True, "already_running": True,
                               "node_id": _node.node_id})
        kwargs: Dict[str, Any] = json.loads(config_json)
        # Sur Android, la découverte UDP peut être bloquée par le routeur
        # Wi-Fi : 0 = désactivée par défaut, les seeds --peers prennent le
        # relais (l'UI les transmet depuis les préférences).
        kwargs.setdefault("beacon_port", 47479)
        config = swarm_glu.NodeConfig(**kwargs)
        try:
            node = swarm_glu.serve(config, block=False)
        except OSError as exc:
            return json.dumps({"ok": False, "error": "port %s injoignable : %s"
                               % (config.port, exc)})
        _node = node
        return json.dumps({"ok": True, "node_id": node.node_id,
                           "ip": node.identity["ip"], "port": config.port,
                           "swarm_id": config.swarm_id})


def stop() -> str:
    global _node
    with _lock:
        node, _node = _node, None
    if node is not None:
        node.stop()
    return json.dumps({"ok": True})


def is_running() -> bool:
    return _node is not None


def _node_or_error() -> Any:
    if _node is None:
        raise RuntimeError("nœud SwarmGlu non démarré (activez-le sur le "
                           "tableau de bord)")
    return _node


def _safe(func):
    """Décorateur : toute exception devient {"error": ...} sérialisable."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # délibéré : l'UI doit toujours obtenir du JSON
            log.exception("erreur pont Android")
            return json.dumps({"error": str(exc)}, default=str)
    return wrapper


@_safe
def status_json() -> str:
    """Instantané complet du nœud (mêmes données que GET /status)."""
    node = _node_or_error()
    _status, payload = node.h_status({}, {}, [], {})
    return json.dumps(payload, default=str)


@_safe
def ask_json(prompt: str, student_id: str = "", model_hint: str = "",
             swarm_id: str = "") -> str:
    """Requête d'élève : routage local/leader/fédéré + pare-feu OSP."""
    node = _node_or_error()
    body = {"prompt": prompt, "student_id": student_id or "eleve-android"}
    if model_hint:
        body["model_hint"] = model_hint
    if swarm_id:
        body["swarm_id"] = swarm_id
    status, payload = node.ask(body)
    payload["http_status"] = status
    return json.dumps(payload, default=str)


@_safe
def audit_verify_json() -> str:
    node = _node_or_error()
    return json.dumps(node.security.verify_chain(), default=str)


@_safe
def audit_tail_json(n: int = 20) -> str:
    node = _node_or_error()
    return json.dumps({"events": node.security.tail(int(n))}, default=str)


@_safe
def whitelist_add(node_id: str) -> str:
    """Administration : ajoute un nœud à la whitelist locale (copie USB)."""
    node = _node_or_error()
    added = node.security.add_node(node_id, approved_by="android-admin")
    node.state.set_whitelist_nodes(node.security.nodes())
    return json.dumps({"ok": True, "added": added,
                       "nodes": node.security.nodes()}, default=str)


@_safe
def agent_task_json(body_json: str) -> str:
    """
    Création d'une tâche agent HarnessDroid (barrière HITL).
    `body_json` : {"student_id", "mcp": {"server","tool","args"}}.
    Retourne {"http_status", "task": {...}} — la tâche part en
    pending_validation : AUCUN dispatch sans validation humaine.
    """
    node = _node_or_error()
    body = json.loads(body_json or "{}")
    origin = "%s:%s" % (node.identity["ip"], node.config.port)
    task = node.tasks.create(body, origin)
    return json.dumps({"http_status": 200, "task": task}, default=str)


@_safe
def agent_task_validate_json(task_id: str, approved: bool, by: str = "") -> str:
    """Validation humaine d'une tâche (approuve/rejette puis dispatch)."""
    node = _node_or_error()
    status, payload = node.tasks.validate(task_id, bool(approved), by or "enseignant")
    payload = dict(payload or {})
    payload["http_status"] = status
    if status == 200 and payload.get("task_id"):
        # Toujours joindre l'état complet de la tâche pour l'UI/les tests.
        payload["task"] = node.tasks.get(task_id)
    return json.dumps(payload, default=str)


@_safe
def whitelist_list_json() -> str:
    node = _node_or_error()
    return json.dumps({"nodes": node.security.nodes(),
                       "digest": node.security.whitelist_digest()}, default=str)
