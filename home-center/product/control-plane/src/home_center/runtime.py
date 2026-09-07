"""Composition root for API, state, auth, inventory and cluster health."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from . import __version__
from .actions import ActionRegistry
from .ad_auth import AdAuthenticator
from .auth import LoginRateLimiter, SessionManager
from .config import Config
from .external_access import ExternalAccessPolicy, ExternalRequestRateLimiter
from .helper_client import HelperClientError, rotate_local_admin_password
from .intent_service import IntentPlanningService
from .local_admin_auth import LocalAdminCredentialStore
from .reconcile import Reconciler
from .resource_snapshot import build_resource_snapshot
from .store import StateStore
from .util import sha256_file, utc_now


LOG = logging.getLogger("home_center.runtime")


class Runtime:
    def __init__(self, config: Config, *, local_admin_expected_uid: int = 0) -> None:
        self.config = config
        self.profile = self._load_profile(config.deployment_profile)
        self.store = StateStore(config.state_db, config.audit_key_file.read_bytes(), config.cluster_id)
        self.local_admin = LocalAdminCredentialStore(
            config.local_admin_credentials_file,
            expected_uid=local_admin_expected_uid,
            expected_gid=os.getegid(),
            expected_mode=0o640,
        )
        self.local_admin_password_rotator = rotate_local_admin_password
        self.sessions = SessionManager(config.session_key_file)
        self.login_limiter = LoginRateLimiter()
        self.ad_auth = AdAuthenticator(config.ad_auth)
        self.external_access = ExternalAccessPolicy(
            configured_enabled=config.external_access.enabled,
            public_hostname=config.external_access.public_hostname,
            trusted_proxy_addresses=config.external_access.trusted_proxy_addresses,
            authentication_ready=True,
        )
        self.external_request_limiter = ExternalRequestRateLimiter()
        self.actions = ActionRegistry(config.node_id, self.store)
        self.intents = IntentPlanningService()
        self.reconciler = Reconciler(config, self.store)

    def change_local_admin_password(self, current_password: str, new_password: str) -> None:
        """Rotate through the root helper, then reload only validated local state."""

        result = self.local_admin_password_rotator(
            self.local_admin.username,
            current_password,
            new_password,
        )
        if result.get("status") != "succeeded":
            reason = result.get("reason")
            raise HelperClientError(reason if isinstance(reason, str) else "credential_rotation_failed")
        self.local_admin = LocalAdminCredentialStore(
            self.config.local_admin_credentials_file,
            expected_uid=self.local_admin.expected_uid,
            expected_gid=self.local_admin.expected_gid,
            expected_mode=self.local_admin.expected_mode,
        )

    def authenticate_local_admin(self, username: str, password: str) -> str | None:
        """Reload the atomic verifier before every local authentication.

        This makes an offline, local-console recovery effective without a
        service restart while retaining exact metadata validation.
        """

        current = LocalAdminCredentialStore(
            self.config.local_admin_credentials_file,
            expected_uid=self.local_admin.expected_uid,
            expected_gid=self.local_admin.expected_gid,
            expected_mode=self.local_admin.expected_mode,
        )
        self.local_admin = current
        return current.authenticate(username, password)

    def start(self) -> None:
        self.store.audit(
            actor="system:runtime",
            action="runtime.start",
            target=self.config.node_id,
            outcome="accepted",
            correlation_id=f"runtime-{self.config.node_id}",
            details={"version": __version__, "role": self.config.role},
        )
        self.reconciler.start()

    def stop(self) -> None:
        self.reconciler.stop()
        self.store.audit(
            actor="system:runtime",
            action="runtime.stop",
            target=self.config.node_id,
            outcome="accepted",
            correlation_id=f"runtime-{self.config.node_id}",
            details={"version": __version__},
        )
        self.store.close()

    def ready(self) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        try:
            if not self.store.integrity_check():
                reasons.append("database_integrity")
            self.store.verify_audit_chain()
        except Exception:
            LOG.exception("readiness integrity validation failed")
            reasons.append("audit_integrity")
        if not self.reconciler.local_capability():
            reasons.append("inventory_unavailable")
        return not reasons, reasons

    def overview(self) -> dict[str, Any]:
        nodes = self.store.nodes()
        expected_nodes = len(self.profile["spec"]["nodes"])
        ready_nodes = sum(node["status"] == "ready" for node in nodes)
        status = "healthy" if ready_nodes == expected_nodes and len(nodes) == expected_nodes else "degraded"
        return {
            "schema": "home-center.overview.v1",
            "observed_at": utc_now(),
            "version": __version__,
            "cluster": {
                "id": self.config.cluster_id,
                "status": status,
                "profile": self.profile["metadata"]["name"],
                "profile_version": self.profile["metadata"]["version"],
                "local_role": self.config.role,
                "ready_nodes": ready_nodes,
                "expected_nodes": expected_nodes,
                "automatic_failover": False,
                "split_brain_policy": "single-writer-manual-failover",
            },
            "nodes": nodes,
            "jobs": self.store.jobs(10),
            "audit_head": self.store.verify_audit_chain(),
        }

    def resource_snapshot(self) -> dict[str, Any]:
        """Return validated capacity facts from persisted node observations only."""

        return build_resource_snapshot(
            cluster_id=self.config.cluster_id,
            expected_nodes=len(self.profile["spec"]["nodes"]),
            nodes=self.store.nodes(),
        )

    def backup_inventory(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if not self.config.backup_dir.exists():
            return items
        for manifest in sorted(self.config.backup_dir.glob("home-center-*.manifest.json"), reverse=True)[:100]:
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                data["manifest_sha256"] = sha256_file(manifest)
                items.append(data)
            except (OSError, json.JSONDecodeError):
                continue
        return items

    @staticmethod
    def _load_profile(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema") != "home-center.deployment-profile.v1":
            raise ValueError("unsupported deployment profile")
        nodes = value.get("spec", {}).get("nodes", [])
        if not isinstance(nodes, list) or len(nodes) != 2:
            raise ValueError("HM.DM profile must contain exactly two nodes")
        return value
