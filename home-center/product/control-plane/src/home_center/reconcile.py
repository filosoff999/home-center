"""Read-only node reconcile loop and mutually authenticated peer discovery."""

from __future__ import annotations

import json
import logging
import ssl
import threading
import urllib.error
import urllib.request
from typing import Any

from .config import Config
from .inventory import collect
from .store import StateStore


LOG = logging.getLogger("home_center.reconcile")


class Reconciler:
    def __init__(self, config: Config, store: StateStore) -> None:
        self.config = config
        self.store = store
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="home-center-reconcile", daemon=True)
        self._latest_local: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._peer_state = "unknown"

    def start(self) -> None:
        self.reconcile_once()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def local_capability(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._latest_local))

    def _run(self) -> None:
        while not self._stop.wait(self.config.reconcile_interval_seconds):
            try:
                self.reconcile_once()
            except Exception:
                LOG.exception("reconcile iteration failed")

    def reconcile_once(self) -> None:
        local = collect(
            self.config.node_id,
            self.config.node_name,
            self.config.role,
            self.config.management_address,
        )
        with self._lock:
            self._latest_local = local
        self.store.upsert_node(local, "ready")
        try:
            peer = self._fetch_peer()
            self._validate_peer(peer)
            self.store.upsert_node(peer, "ready")
            self._transition_peer("ready", None)
        except Exception as exc:
            self.store.mark_node(self.config.peer.node_id, "unreachable")
            self._transition_peer("unreachable", self._failure_class(exc))

    def _fetch_peer(self) -> dict[str, Any]:
        context = ssl.create_default_context(cafile=str(self.config.cluster_ca))
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(str(self.config.tls_certificate), str(self.config.tls_private_key))
        request = urllib.request.Request(
            f"{self.config.peer.url}/internal/v1/node",
            headers={"Accept": "application/json", "User-Agent": "home-center-peer/0.1"},
        )
        with urllib.request.urlopen(request, timeout=self.config.peer_timeout_seconds, context=context) as response:
            if response.status != 200:
                raise RuntimeError(f"peer returned HTTP {response.status}")
            if response.headers.get_content_type() != "application/json":
                raise RuntimeError("peer returned unexpected content type")
            body = response.read(256 * 1024 + 1)
            if len(body) > 256 * 1024:
                raise RuntimeError("peer response is too large")
        envelope = json.loads(body)
        if envelope.get("schema") != "home-center.peer-node.v1":
            raise ValueError("unsupported peer envelope")
        if envelope.get("cluster_id") != self.config.cluster_id:
            raise ValueError("peer cluster identity mismatch")
        capability = envelope.get("capability")
        if not isinstance(capability, dict):
            raise ValueError("missing peer capability")
        return capability

    def _validate_peer(self, capability: dict[str, Any]) -> None:
        if capability.get("schema") != "home-center.node-capability.v1":
            raise ValueError("unsupported capability schema")
        node = capability.get("node")
        if not isinstance(node, dict):
            raise ValueError("missing node identity")
        expected = {
            "id": self.config.peer.node_id,
            "name": self.config.peer.name,
            "address": self.config.peer.address,
        }
        for key, value in expected.items():
            if node.get(key) != value:
                raise ValueError(f"peer {key} mismatch")

    def _transition_peer(self, state: str, reason: str | None) -> None:
        if state == self._peer_state:
            return
        old = self._peer_state
        self._peer_state = state
        self.store.audit(
            actor="system:reconciler",
            action="peer.health.transition",
            target=self.config.peer.node_id,
            outcome=state,
            correlation_id=f"peer-{self.config.peer.node_id}",
            details={"from": old, "to": state, "reason_class": reason},
        )

    @staticmethod
    def _failure_class(exc: Exception) -> str:
        """Return actionable transport evidence without logging addresses or secrets."""

        if isinstance(exc, urllib.error.HTTPError):
            return f"http_{exc.code}"
        if isinstance(exc, urllib.error.URLError):
            reason = exc.reason
            if isinstance(reason, ssl.SSLCertVerificationError):
                return f"tls_verify_{reason.verify_code}"
            if isinstance(reason, ssl.SSLError):
                return "tls_handshake"
            if isinstance(reason, OSError) and reason.errno is not None:
                return f"transport_errno_{reason.errno}"
            return f"transport_{type(reason).__name__}"
        if isinstance(exc, json.JSONDecodeError):
            return "peer_json_invalid"
        return type(exc).__name__
