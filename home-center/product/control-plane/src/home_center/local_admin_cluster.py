"""Secret-safe two-node local-administrator password transaction.

Password material is accepted only in memory, sent to the configured peer over
the existing mutually authenticated TLS channel, and forwarded to the fixed
local privileged helper operation. Durable state and audit evidence contain
only transaction identities, phases, node identities and closed reason codes.
"""

from __future__ import annotations

import json
import re
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from .helper_client import HelperClientError
from .store import IdempotencyConflict, StateStore


COMMAND_SCHEMA = "home-center.local-admin-cluster-command.v1"
RESULT_SCHEMA = "home-center.local-admin-cluster-result.v1"
CHANGE_RESULT_SCHEMA = "home-center.local-admin-password-change-result.v2"
PEER_PATH = "/internal/v1/local-admin-transaction"
MAX_PEER_BODY_BYTES = 8 * 1024
CANARY_ATTEMPTS = 3
CANARY_INTERVAL_SECONDS = 1.0
TRANSACTION_ID = re.compile(r"^la-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}$")
INTENT_TOKEN = re.compile(r"^[0-9a-f]{32}$")
NODE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
POLICY_REASONS = frozenset(
    {
        "current_password_invalid",
        "password_too_short",
        "password_letter_required",
        "password_digit_required",
        "password_rejected",
        "password_unchanged",
    }
)
SAFE_REASONS = frozenset(
    {
        *POLICY_REASONS,
        "cluster_identity_mismatch",
        "cluster_leader_required",
        "cluster_rotation_recovery_required",
        "cluster_rotation_rolled_back",
        "cluster_transaction_active",
        "cluster_transaction_conflict",
        "credential_operation_failed",
        "credential_validation_failed",
        "invalid_cluster_command",
        "invalid_cluster_response",
        "local_canary_failed",
        "peer_canary_failed",
        "peer_response_reconciled",
        "peer_transport_unavailable",
        "process_restart",
        "transaction_finalized",
        "transaction_not_found",
        "transaction_phase_conflict",
    }
)
TERMINAL_PHASES = frozenset({"completed", "rolled_back", "failed"})


class LocalAdminClusterError(RuntimeError):
    def __init__(self, code: str) -> None:
        safe = code if code in SAFE_REASONS else "credential_operation_failed"
        super().__init__(safe)
        self.code = safe


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def decode_cluster_command(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise LocalAdminClusterError("invalid_cluster_command") from exc
    return validate_cluster_command(value)


def _password_field(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return 1 <= len(encoded) <= 1024


def validate_cluster_command(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LocalAdminClusterError("invalid_cluster_command")
    common = {"schema", "cluster_id", "transaction_id", "intent_token", "action"}
    action = value.get("action")
    if action in {"prepare", "commit", "rollback"}:
        required = common | {"username", "current_password", "new_password"}
    elif action == "canary":
        required = common | {"username", "password"}
    elif action in {"status", "finalize"}:
        required = common
    else:
        raise LocalAdminClusterError("invalid_cluster_command")
    if set(value) != required or value.get("schema") != COMMAND_SCHEMA:
        raise LocalAdminClusterError("invalid_cluster_command")
    if not isinstance(value.get("cluster_id"), str) or not 3 <= len(value["cluster_id"]) <= 64:
        raise LocalAdminClusterError("invalid_cluster_command")
    if TRANSACTION_ID.fullmatch(value.get("transaction_id", "")) is None:
        raise LocalAdminClusterError("invalid_cluster_command")
    if INTENT_TOKEN.fullmatch(value.get("intent_token", "")) is None:
        raise LocalAdminClusterError("invalid_cluster_command")
    if "username" in value and value["username"] != "admin":
        raise LocalAdminClusterError("invalid_cluster_command")
    for name in ("current_password", "new_password", "password"):
        if name in value and not _password_field(value[name]):
            raise LocalAdminClusterError("invalid_cluster_command")
    return value


def validate_cluster_result(value: Any, transaction_id: str) -> dict[str, Any]:
    required = {"schema", "transaction_id", "node_id", "status", "phase", "reason", "observed_at"}
    if not isinstance(value, dict) or set(value) != required:
        raise LocalAdminClusterError("invalid_cluster_response")
    if value.get("schema") != RESULT_SCHEMA or value.get("transaction_id") != transaction_id:
        raise LocalAdminClusterError("invalid_cluster_response")
    if NODE_ID.fullmatch(value.get("node_id", "")) is None:
        raise LocalAdminClusterError("invalid_cluster_response")
    if value.get("status") not in {"succeeded", "rejected", "failed", "unknown"}:
        raise LocalAdminClusterError("invalid_cluster_response")
    reason = value.get("reason")
    if reason is not None and reason not in SAFE_REASONS:
        raise LocalAdminClusterError("invalid_cluster_response")
    if not isinstance(value.get("phase"), str) or not isinstance(value.get("observed_at"), str):
        raise LocalAdminClusterError("invalid_cluster_response")
    return value


class LocalAdminTransactionParticipant:
    """Execute one node-local part of the cluster saga."""

    def __init__(
        self,
        *,
        cluster_id: str,
        node_id: str,
        store: StateStore,
        validator: Callable[[str, str, str], dict[str, Any]],
        rotator: Callable[[str, str, str], dict[str, Any]],
        authenticator: Callable[[str, str], str | None],
    ) -> None:
        self.cluster_id = cluster_id
        self.node_id = node_id
        self.store = store
        self.validator = validator
        self.rotator = rotator
        self.authenticator = authenticator
        self._lock = threading.Lock()

    def _result(
        self,
        transaction_id: str,
        *,
        status: str,
        phase: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        if reason is not None and reason not in SAFE_REASONS:
            reason = "credential_operation_failed"
        return {
            "schema": RESULT_SCHEMA,
            "transaction_id": transaction_id,
            "node_id": self.node_id,
            "status": status,
            "phase": phase,
            "reason": reason,
            "observed_at": _utc_now(),
        }

    def _audit(self, transaction: dict[str, Any]) -> None:
        self.store.audit(
            actor=f"system:local-admin-transaction:{transaction['coordinator_node_id']}",
            action="local-admin.password.transaction",
            target=self.node_id,
            outcome=transaction["outcome"],
            correlation_id=transaction["transaction_id"],
            details={
                "transaction_id": transaction["transaction_id"],
                "phase": transaction["phase"],
                "reason": transaction["reason"],
                "policy": "local-admin-password-v1",
            },
        )

    def _transition(
        self,
        transaction_id: str,
        intent_token: str,
        expected_phases: tuple[str, ...],
        new_phase: str,
        outcome: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        before = self.store.local_admin_transaction(transaction_id)
        try:
            transaction = self.store.transition_local_admin_transaction(
                transaction_id,
                intent_token=intent_token,
                expected_phases=expected_phases,
                new_phase=new_phase,
                outcome=outcome,
                reason=reason,
            )
        except (IdempotencyConflict, KeyError, RuntimeError) as exc:
            raise LocalAdminClusterError("transaction_phase_conflict") from exc
        if before is None or before["phase"] != transaction["phase"]:
            self._audit(transaction)
        return transaction

    def begin_coordination(self, transaction_id: str, intent_token: str) -> dict[str, Any]:
        try:
            transaction, created = self.store.begin_local_admin_transaction(
                transaction_id=transaction_id,
                intent_token=intent_token,
                coordinator_node_id=self.node_id,
                phase="coordinating",
            )
        except IdempotencyConflict as exc:
            code = "cluster_transaction_active" if str(exc) == "local_admin_transaction_active" else "cluster_transaction_conflict"
            raise LocalAdminClusterError(code) from exc
        if created:
            self._audit(transaction)
        return transaction

    def _transaction(self, transaction_id: str, intent_token: str, coordinator_node_id: str) -> dict[str, Any]:
        transaction = self.store.local_admin_transaction(transaction_id)
        if transaction is None:
            raise LocalAdminClusterError("transaction_not_found")
        if (
            transaction["intent_token"] != intent_token
            or transaction["coordinator_node_id"] != coordinator_node_id
        ):
            raise LocalAdminClusterError("cluster_transaction_conflict")
        return transaction

    @staticmethod
    def _helper_call(operation: Callable[[str, str, str], dict[str, Any]], *passwords: str) -> None:
        try:
            result = operation(*passwords)
        except HelperClientError as exc:
            raise LocalAdminClusterError(str(exc)) from exc
        if not isinstance(result, dict) or result.get("status") != "succeeded":
            reason = result.get("reason") if isinstance(result, dict) else None
            raise LocalAdminClusterError(reason if isinstance(reason, str) else "credential_operation_failed")

    def _authenticates(self, username: str, password: str) -> bool:
        try:
            return self.authenticator(username, password) == username
        except Exception:
            return False

    def handle(self, command: dict[str, Any], *, coordinator_node_id: str) -> dict[str, Any]:
        command = validate_cluster_command(command)
        transaction_id = command["transaction_id"]
        if command["cluster_id"] != self.cluster_id or NODE_ID.fullmatch(coordinator_node_id) is None:
            return self._result(
                transaction_id,
                status="rejected",
                phase="absent",
                reason="cluster_identity_mismatch",
            )
        with self._lock:
            try:
                action = command["action"]
                if action == "status":
                    return self._status(command, coordinator_node_id)
                if action == "prepare":
                    return self._prepare(command, coordinator_node_id)
                transaction = self._transaction(command["transaction_id"], command["intent_token"], coordinator_node_id)
                if action == "commit":
                    return self._commit(command, transaction)
                if action == "canary":
                    return self._canary(command, transaction)
                if action == "rollback":
                    return self._rollback(command, transaction)
                if action == "finalize":
                    return self._finalize(command, transaction)
            except LocalAdminClusterError as exc:
                transaction = self.store.local_admin_transaction(transaction_id)
                return self._result(
                    transaction_id,
                    status="unknown" if exc.code == "cluster_rotation_recovery_required" else "rejected",
                    phase=transaction["phase"] if transaction else "absent",
                    reason=exc.code,
                )
        return self._result(transaction_id, status="rejected", phase="absent", reason="invalid_cluster_command")

    def _status(self, command: dict[str, Any], coordinator_node_id: str) -> dict[str, Any]:
        try:
            transaction = self._transaction(command["transaction_id"], command["intent_token"], coordinator_node_id)
        except LocalAdminClusterError as exc:
            return self._result(command["transaction_id"], status="rejected", phase="absent", reason=exc.code)
        status = "unknown" if transaction["phase"] == "recovery_required" else "succeeded"
        return self._result(
            command["transaction_id"],
            status=status,
            phase=transaction["phase"],
            reason=transaction["reason"],
        )

    def _prepare(self, command: dict[str, Any], coordinator_node_id: str) -> dict[str, Any]:
        try:
            transaction, created = self.store.begin_local_admin_transaction(
                transaction_id=command["transaction_id"],
                intent_token=command["intent_token"],
                coordinator_node_id=coordinator_node_id,
                phase="preparing",
            )
        except IdempotencyConflict as exc:
            code = "cluster_transaction_active" if str(exc) == "local_admin_transaction_active" else "cluster_transaction_conflict"
            raise LocalAdminClusterError(code) from exc
        if created:
            self._audit(transaction)
        if transaction["phase"] == "prepared":
            return self._result(command["transaction_id"], status="succeeded", phase="prepared")
        if transaction["phase"] == "coordinating":
            transaction = self._transition(
                command["transaction_id"], command["intent_token"], ("coordinating",), "preparing", "pending"
            )
        if transaction["phase"] != "preparing":
            raise LocalAdminClusterError("transaction_phase_conflict")
        try:
            self._helper_call(
                self.validator,
                command["username"],
                command["current_password"],
                command["new_password"],
            )
        except LocalAdminClusterError as exc:
            self._transition(
                command["transaction_id"], command["intent_token"], ("preparing",), "failed", "rejected", exc.code
            )
            raise
        self._transition(
            command["transaction_id"], command["intent_token"], ("preparing",), "prepared", "accepted"
        )
        return self._result(command["transaction_id"], status="succeeded", phase="prepared")

    def _commit(self, command: dict[str, Any], transaction: dict[str, Any]) -> dict[str, Any]:
        if transaction["phase"] in {"committed", "verified", "completed"}:
            return self._result(command["transaction_id"], status="succeeded", phase=transaction["phase"])
        if transaction["phase"] != "prepared":
            raise LocalAdminClusterError("transaction_phase_conflict")
        self._transition(
            command["transaction_id"], command["intent_token"], ("prepared",), "commit_started", "pending"
        )
        try:
            self._helper_call(
                self.rotator,
                command["username"],
                command["current_password"],
                command["new_password"],
            )
        except LocalAdminClusterError:
            old_valid = self._authenticates(command["username"], command["current_password"])
            new_valid = self._authenticates(command["username"], command["new_password"])
            if new_valid and not old_valid:
                self._transition(
                    command["transaction_id"], command["intent_token"], ("commit_started",), "committed", "accepted", "peer_response_reconciled"
                )
                return self._result(command["transaction_id"], status="succeeded", phase="committed", reason="peer_response_reconciled")
            if old_valid and not new_valid:
                self._transition(
                    command["transaction_id"], command["intent_token"], ("commit_started",), "failed", "failed", "credential_operation_failed"
                )
                raise LocalAdminClusterError("credential_operation_failed")
            self._transition(
                command["transaction_id"], command["intent_token"], ("commit_started",), "recovery_required", "unknown", "cluster_rotation_recovery_required"
            )
            raise LocalAdminClusterError("cluster_rotation_recovery_required")
        if not self._authenticates(command["username"], command["new_password"]):
            self._transition(
                command["transaction_id"], command["intent_token"], ("commit_started",), "recovery_required", "unknown", "credential_validation_failed"
            )
            raise LocalAdminClusterError("cluster_rotation_recovery_required")
        self._transition(
            command["transaction_id"], command["intent_token"], ("commit_started",), "committed", "accepted"
        )
        return self._result(command["transaction_id"], status="succeeded", phase="committed")

    def _canary(self, command: dict[str, Any], transaction: dict[str, Any]) -> dict[str, Any]:
        if transaction["phase"] not in {"committed", "verified"}:
            raise LocalAdminClusterError("transaction_phase_conflict")
        if not self._authenticates(command["username"], command["password"]):
            self._transition(
                command["transaction_id"], command["intent_token"], (transaction["phase"],), "recovery_required", "unknown", "local_canary_failed"
            )
            raise LocalAdminClusterError("cluster_rotation_recovery_required")
        if transaction["phase"] == "committed":
            self._transition(
                command["transaction_id"], command["intent_token"], ("committed",), "verified", "accepted"
            )
        return self._result(command["transaction_id"], status="succeeded", phase="verified")

    def _rollback(self, command: dict[str, Any], transaction: dict[str, Any]) -> dict[str, Any]:
        if transaction["phase"] == "rolled_back":
            return self._result(command["transaction_id"], status="succeeded", phase="rolled_back")
        if transaction["phase"] == "completed":
            raise LocalAdminClusterError("transaction_finalized")
        allowed = (
            "coordinating", "preparing", "prepared", "commit_started", "committed", "verified",
            "failed", "recovery_required", "rollback_started",
        )
        if transaction["phase"] != "rollback_started":
            self._transition(
                command["transaction_id"], command["intent_token"], allowed, "rollback_started", "pending"
            )
        old_valid = self._authenticates(command["username"], command["current_password"])
        new_valid = self._authenticates(command["username"], command["new_password"])
        if new_valid and not old_valid:
            try:
                self._helper_call(
                    self.rotator,
                    command["username"],
                    command["new_password"],
                    command["current_password"],
                )
            except LocalAdminClusterError:
                self._transition(
                    command["transaction_id"], command["intent_token"], ("rollback_started",), "recovery_required", "unknown", "cluster_rotation_recovery_required"
                )
                raise LocalAdminClusterError("cluster_rotation_recovery_required")
            old_valid = self._authenticates(command["username"], command["current_password"])
            new_valid = self._authenticates(command["username"], command["new_password"])
        if not old_valid or new_valid:
            self._transition(
                command["transaction_id"], command["intent_token"], ("rollback_started",), "recovery_required", "unknown", "cluster_rotation_recovery_required"
            )
            raise LocalAdminClusterError("cluster_rotation_recovery_required")
        self._transition(
            command["transaction_id"], command["intent_token"], ("rollback_started",), "rolled_back", "rolled_back", "cluster_rotation_rolled_back"
        )
        return self._result(command["transaction_id"], status="succeeded", phase="rolled_back", reason="cluster_rotation_rolled_back")

    def _finalize(self, command: dict[str, Any], transaction: dict[str, Any]) -> dict[str, Any]:
        if transaction["phase"] == "completed":
            return self._result(command["transaction_id"], status="succeeded", phase="completed")
        if transaction["phase"] != "verified":
            raise LocalAdminClusterError("transaction_phase_conflict")
        self._transition(
            command["transaction_id"], command["intent_token"], ("verified",), "completed", "accepted"
        )
        return self._result(command["transaction_id"], status="succeeded", phase="completed")


class LocalAdminPeerClient:
    """Send one closed command over the configured TLS 1.3 peer channel."""

    def __init__(self, config: Any) -> None:
        self.config = config

    def execute(self, command: dict[str, Any]) -> dict[str, Any]:
        command = validate_cluster_command(command)
        payload = json.dumps(command, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(payload) > MAX_PEER_BODY_BYTES:
            raise LocalAdminClusterError("invalid_cluster_command")
        context = ssl.create_default_context(cafile=str(self.config.cluster_ca))
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(str(self.config.tls_certificate), str(self.config.tls_private_key))
        request = urllib.request.Request(
            f"{self.config.peer.url}{PEER_PATH}",
            data=payload,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "home-center-local-admin-cluster/0.12",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.peer_timeout_seconds, context=context) as response:
                if response.status != 200 or response.headers.get_content_type() != "application/json":
                    raise LocalAdminClusterError("invalid_cluster_response")
                body = response.read(MAX_PEER_BODY_BYTES + 1)
        except LocalAdminClusterError:
            raise
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise LocalAdminClusterError("peer_transport_unavailable") from exc
        if len(body) > MAX_PEER_BODY_BYTES:
            raise LocalAdminClusterError("invalid_cluster_response")
        try:
            value = json.loads(body, object_pairs_hook=_strict_object)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise LocalAdminClusterError("invalid_cluster_response") from exc
        result = validate_cluster_result(value, command["transaction_id"])
        if result["node_id"] != self.config.peer.node_id:
            raise LocalAdminClusterError("invalid_cluster_response")
        return result


class LocalAdminClusterCoordinator:
    """Coordinate standby-first commit, soak, leader commit and compensation."""

    def __init__(
        self,
        *,
        config: Any,
        store: StateStore,
        participant: LocalAdminTransactionParticipant,
        peer_client: LocalAdminPeerClient,
        sleep: Callable[[float], None] = time.sleep,
        canary_attempts: int = CANARY_ATTEMPTS,
        canary_interval_seconds: float = CANARY_INTERVAL_SECONDS,
    ) -> None:
        self.config = config
        self.store = store
        self.participant = participant
        self.peer_client = peer_client
        self.sleep = sleep
        self.canary_attempts = canary_attempts
        self.canary_interval_seconds = canary_interval_seconds
        self._lock = threading.Lock()

    def _command(self, transaction_id: str, intent_token: str, action: str, **secrets_in_memory: str) -> dict[str, Any]:
        return {
            "schema": COMMAND_SCHEMA,
            "cluster_id": self.config.cluster_id,
            "transaction_id": transaction_id,
            "intent_token": intent_token,
            "action": action,
            **secrets_in_memory,
        }

    @staticmethod
    def _expect(result: dict[str, Any], phase: str) -> dict[str, Any]:
        validate_cluster_result(result, result.get("transaction_id", ""))
        if result["status"] != "succeeded" or result["phase"] != phase:
            raise LocalAdminClusterError(result.get("reason") or "credential_operation_failed")
        return result

    def _audit(self, transaction_id: str, outcome: str, phase: str, reason: str | None = None) -> None:
        self.store.audit(
            actor=f"system:local-admin-coordinator:{self.config.node_id}",
            action="local-admin.password.cluster",
            target=self.config.cluster_id,
            outcome=outcome,
            correlation_id=transaction_id,
            details={
                "transaction_id": transaction_id,
                "phase": phase,
                "reason": reason,
                "commit_order": [self.config.peer.node_id, self.config.node_id],
                "policy": "local-admin-password-v1",
            },
        )

    def _local(self, command: dict[str, Any]) -> dict[str, Any]:
        return self.participant.handle(command, coordinator_node_id=self.config.node_id)

    def _remote(self, command: dict[str, Any]) -> dict[str, Any]:
        return self.peer_client.execute(command)

    def _rollback(
        self,
        transaction_id: str,
        intent_token: str,
        username: str,
        current_password: str,
        new_password: str,
        *,
        local_started: bool,
        remote_started: bool,
    ) -> bool:
        command = self._command(
            transaction_id,
            intent_token,
            "rollback",
            username=username,
            current_password=current_password,
            new_password=new_password,
        )
        local_safe = not local_started
        remote_safe = not remote_started
        if local_started:
            try:
                local = self._local(command)
                local_safe = local["status"] == "succeeded" and local["phase"] == "rolled_back"
            except Exception:
                local_safe = False
        if remote_started:
            try:
                remote = self._remote(command)
                remote_safe = (
                    remote["status"] == "succeeded" and remote["phase"] == "rolled_back"
                ) or (
                    remote["status"] == "rejected"
                    and remote["phase"] == "absent"
                    and remote["reason"] == "transaction_not_found"
                )
            except Exception:
                remote_safe = False
        return local_safe and remote_safe

    def change(self, username: str, current_password: str, new_password: str) -> dict[str, Any]:
        if self.config.role != "leader":
            raise LocalAdminClusterError("cluster_leader_required")
        with self._lock:
            transaction_id = f"la-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(8)}"
            intent_token = secrets.token_hex(16)
            local_started = False
            remote_started = False
            both_nodes_verified = False
            failure: LocalAdminClusterError | None = None
            try:
                self.participant.begin_coordination(transaction_id, intent_token)
                local_started = True
                self._audit(transaction_id, "pending", "standby_prepare")
                prepare = self._command(
                    transaction_id,
                    intent_token,
                    "prepare",
                    username=username,
                    current_password=current_password,
                    new_password=new_password,
                )
                remote_started = True
                self._expect(self._remote(prepare), "prepared")
                self._expect(self._local(prepare), "prepared")

                commit = self._command(
                    transaction_id,
                    intent_token,
                    "commit",
                    username=username,
                    current_password=current_password,
                    new_password=new_password,
                )
                self._audit(transaction_id, "pending", "standby_commit")
                self._expect(self._remote(commit), "committed")

                canary = self._command(
                    transaction_id,
                    intent_token,
                    "canary",
                    username=username,
                    password=new_password,
                )
                for attempt in range(self.canary_attempts):
                    self._expect(self._remote(canary), "verified")
                    self._audit(transaction_id, "accepted", f"standby_canary_{attempt + 1}")
                    if attempt + 1 < self.canary_attempts:
                        self.sleep(self.canary_interval_seconds)

                self._audit(transaction_id, "pending", "leader_commit")
                self._expect(self._local(commit), "committed")
                self._expect(self._local(canary), "verified")
                both_nodes_verified = True

                finalize = self._command(transaction_id, intent_token, "finalize")
                self._expect(self._remote(finalize), "completed")
                self._expect(self._local(finalize), "completed")
            except LocalAdminClusterError as exc:
                failure = exc
            except Exception:
                failure = LocalAdminClusterError("credential_operation_failed")

            if failure is not None:
                if both_nodes_verified:
                    self._audit(
                        transaction_id,
                        "unknown",
                        "recovery_required",
                        "cluster_rotation_recovery_required",
                    )
                    raise LocalAdminClusterError("cluster_rotation_recovery_required") from failure
                rolled_back = self._rollback(
                    transaction_id,
                    intent_token,
                    username,
                    current_password,
                    new_password,
                    local_started=local_started,
                    remote_started=remote_started,
                )
                if rolled_back:
                    self._audit(transaction_id, "rolled_back", "compensated", failure.code)
                    if failure.code in POLICY_REASONS:
                        raise failure
                    raise LocalAdminClusterError("cluster_rotation_rolled_back") from failure
                self._audit(transaction_id, "unknown", "recovery_required", "cluster_rotation_recovery_required")
                raise LocalAdminClusterError("cluster_rotation_recovery_required") from failure

            self._audit(transaction_id, "accepted", "completed")
            return {
                "schema": CHANGE_RESULT_SCHEMA,
                "status": "changed",
                "transaction_id": transaction_id,
                "nodes": [self.config.peer.node_id, self.config.node_id],
                "commit_order": [self.config.peer.node_id, self.config.node_id],
                "canary_attempts": self.canary_attempts,
            }
