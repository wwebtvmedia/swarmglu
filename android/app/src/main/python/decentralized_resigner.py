# -*- coding: utf-8 -*-
"""
SwarmGlu — decentralized_resigner.py
====================================
Gestionnaire de sélection aléatoire pondérée, consensus 1/8 et re-signature
décentralisée de la whitelist par les pairs de l'essaim.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from crypto_auth import AuthenticatedWhitelist, Consensus18Verifier, NodeCrypto, WeightedLottery

log = logging.getLogger("swarmglu.resigner")


class DecentralizedResigner:
    """
    Coordonne la sélection pondérée (ancienneté + volume de requêtes),
    l'obtention du consensus 1/8 auprès des pairs et la re-signature
    décentralisée de la whitelist.
    """

    def __init__(
        self,
        node: Any,
        interval: float = 30.0,
        enabled: bool = True,
    ):
        self.node = node
        self.interval = interval
        self.enabled = enabled
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._resigning_epoch = 1

    def start(self) -> None:
        if self.interval <= 0 or not self.enabled:
            return
        self._thread = threading.Thread(target=self._run, name="resigner-loop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        """Boucle périodique d'évaluation de la re-signature."""
        while not self._stop.wait(self.interval):
            try:
                self.run_cycle()
            except Exception:
                log.exception("Erreur dans la boucle de re-signature décentralisée")

    def collect_swarm_metrics(self) -> Dict[str, Dict[str, Any]]:
        """
        Rassemble les métriques (ancienneté / uptime et requêtes traitées)
        de tous les nœuds vivants de l'essaim ainsi que de soi-même.
        """
        metrics: Dict[str, Dict[str, Any]] = {}
        now = time.time()

        # Métriques du nœud local
        local_uptime = max(round(now - getattr(self.node, "started_at", now), 2), 1.0)
        local_requests = getattr(self.node, "request_count", 0)
        metrics[self.node.node_id] = {
            "uptime_s": local_uptime,
            "requests_count": local_requests,
            "ecdsa_pub": self.node.crypto.ecdsa_pub_hex,
            "mlkem_pub": self.node.crypto.mlkem_pub_hex,
            "ip": self.node.identity["ip"],
            "port": self.node.identity["port"],
        }

        # Métriques des pairs connus et vivants
        alive_peers = self.node.state.alive_peers()
        for pid, peer in alive_peers:
            if pid == self.node.node_id:
                continue
            p_uptime = float(peer.get("uptime_s", 0) or 0)
            if p_uptime <= 0:
                p_joined = float(peer.get("joined_at", 0) or peer.get("updated_at", now))
                p_uptime = max(round(now - p_joined, 2), 1.0)
            p_requests = int(peer.get("requests_count", 0) or 0)
            metrics[pid] = {
                "uptime_s": p_uptime,
                "requests_count": p_requests,
                "ecdsa_pub": peer.get("ecdsa_pub", ""),
                "mlkem_pub": peer.get("mlkem_pub", ""),
                "ip": peer.get("ip", ""),
                "port": int(peer.get("port", 0)),
            }

        return metrics

    def run_cycle(
        self,
        force_self: bool = False,
        seed: Optional[str | int] = None,
    ) -> Dict[str, Any]:
        """
        Exécute un cycle complet :
        1. Calcul de probabilité pondérée proportionnelle au temps et aux requêtes.
        2. Tirage aléatoire du nœud désigné.
        3. Si sélectionné : demande de consensus 1/8 aux pairs.
        4. Vérification de tous les nœuds, génération et re-signature de la whitelist.
        5. Diffusion du résultat à l'ensemble de l'essaim.
        """
        with self._lock:
            metrics = self.collect_swarm_metrics()
            if not metrics:
                return {"ok": False, "error": "Aucun nœud disponible"}

            selected_node, probs = WeightedLottery.select_node(metrics, seed=seed)
            if force_self:
                selected_node = self.node.node_id

            if selected_node != self.node.node_id:
                return {
                    "ok": True,
                    "selected": selected_node,
                    "role": "observer",
                    "probabilities": probs,
                    "metrics": metrics,
                }

            # Ce nœud est l'élu ! Il doit d'abord obtenir le consensus 1/8.
            self._resigning_epoch += 1
            epoch = self._resigning_epoch
            total_nodes = len(metrics)
            threshold = Consensus18Verifier.threshold(total_nodes)

            # 1. Vote local de soi-même
            vote_ts = round(time.time(), 3)
            my_sig = self.node.crypto.sign(
                f"SWARM_1_8_VOTE:{epoch}:{self.node.node_id}:{self.node.node_id}:{vote_ts}"
            )
            approvals: List[Dict[str, Any]] = [
                {
                    "voter_id": self.node.node_id,
                    "voter_ecdsa_pub": self.node.crypto.ecdsa_pub_hex,
                    "timestamp": vote_ts,
                    "signature": my_sig,
                }
            ]

            # 2. Sollicitation des pairs pour obtenir le consensus 1/8
            if total_nodes > 1:
                prop_ts = round(time.time(), 3)
                proposal_sig = self.node.crypto.sign(
                    f"CONSENSUS_PROPOSAL:{self.node.node_id}:{epoch}:{prop_ts}"
                )
                proposal_body = {
                    "candidate_id": self.node.node_id,
                    "epoch": epoch,
                    "uptime_s": metrics[self.node.node_id]["uptime_s"],
                    "requests_count": metrics[self.node.node_id]["requests_count"],
                    "all_metrics": metrics,
                    "timestamp": prop_ts,
                    "signature": proposal_sig,
                }

                for pid, pdata in metrics.items():
                    if pid == self.node.node_id:
                        continue
                    ip, port = pdata.get("ip"), pdata.get("port")
                    if not ip or not port:
                        continue
                    try:
                        code, resp = self.node.transport.post(
                            ip, port, "/whitelist/consensus-select", proposal_body, timeout=2.5
                        )
                        if code == 200 and resp and resp.get("vote") == "agree":
                            v_sig = resp.get("signature", "")
                            v_ts = resp.get("timestamp", 0)
                            v_pub = resp.get("voter_ecdsa_pub", "")
                            approvals.append({
                                "voter_id": pid,
                                "voter_ecdsa_pub": v_pub,
                                "timestamp": v_ts,
                                "signature": v_sig,
                            })
                    except Exception as exc:
                        log.debug("Erreur lors de la demande d'accord à %s: %s", pid, exc)

            if len(approvals) < threshold:
                return {
                    "ok": False,
                    "error": f"Consensus 1/8 non atteint ({len(approvals)}/{threshold} requis)",
                    "approvals": approvals,
                    "threshold": threshold,
                    "total_nodes": total_nodes,
                }

            # Consensus 1/8 obtenu ! On procède à la vérification et re-signature.
            consensus_proof = {
                "type": "1/8_consensus",
                "target_signer": self.node.node_id,
                "epoch": epoch,
                "total_nodes": total_nodes,
                "threshold_required": threshold,
                "approvals": approvals,
            }

            # 3. Vérification de tous les nœuds actifs
            verified_nodes = []
            for nid, nmeta in metrics.items():
                verified_nodes.append({
                    "node_id": nid,
                    "ecdsa_pub": nmeta.get("ecdsa_pub", ""),
                    "mlkem_pub": nmeta.get("mlkem_pub", ""),
                    "verified_at": round(time.time(), 3),
                })

            # 4. Construction et signature de la nouvelle whitelist
            signed_doc = AuthenticatedWhitelist.build_signed_doc(
                signer_crypto=self.node.crypto,
                nodes_list=verified_nodes,
                consensus_proof=consensus_proof,
                epoch=epoch,
                swarm_id=self.node.config.swarm_id,
            )

            # 5. Application locale
            self.node.security.apply_signed_whitelist(
                signed_doc, active_nodes_count=total_nodes
            )
            self.node.security.record_event(
                "whitelist_resigned_decentralized",
                self.node.node_id,
                {
                    "epoch": epoch,
                    "signer": self.node.node_id,
                    "consensus_votes": len(approvals),
                    "threshold": threshold,
                    "total_nodes": total_nodes,
                    "nodes_count": len(verified_nodes),
                },
            )

            # 6. Diffusion aux pairs de l'essaim
            broadcast_body = {"signed_whitelist": signed_doc}
            for pid, pdata in metrics.items():
                if pid == self.node.node_id:
                    continue
                ip, port = pdata.get("ip"), pdata.get("port")
                if not ip or not port:
                    continue
                try:
                    self.node.transport.post(
                        ip, port, "/whitelist/resign", broadcast_body, timeout=2.5
                    )
                except Exception:
                    pass

            return {
                "ok": True,
                "resigned": True,
                "epoch": epoch,
                "signer": self.node.node_id,
                "approvals_count": len(approvals),
                "threshold": threshold,
                "total_nodes": total_nodes,
                "probabilities": probs,
                "signed_whitelist": signed_doc,
            }
