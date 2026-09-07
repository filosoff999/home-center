"""Deterministic, secret-free acceptance verifier for the 0.12 credential workflow.

The verifier consumes bounded evidence only.  It cannot rotate credentials,
contact cluster peers, invoke the privileged helper, or perform recovery.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from .util import canonical_json


MAX_ACCEPTANCE_BYTES = 64 * 1024
TRANSACTION_ID = re.compile(r"^la-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}$")
NODE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
FORBIDDEN_KEY_PARTS = ("password", "secret", "salt", "verifier", "token", "private_key")


class LocalAdminAcceptanceError(ValueError):
    """Stable acceptance failure code without echoing supplied evidence."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LocalAdminAcceptanceError("acceptance_duplicate_key")
        result[key] = value
    return result


def _reject_float(_: str) -> None:
    raise LocalAdminAcceptanceError("acceptance_float_rejected")


def _reject_constant(_: str) -> None:
    raise LocalAdminAcceptanceError("acceptance_constant_rejected")


def load_local_admin_acceptance(data: bytes) -> dict[str, Any]:
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_ACCEPTANCE_BYTES:
        raise LocalAdminAcceptanceError("acceptance_document_size_rejected")
    if data.startswith(b"\xef\xbb\xbf"):
        raise LocalAdminAcceptanceError("acceptance_document_bom_rejected")
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except LocalAdminAcceptanceError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise LocalAdminAcceptanceError("acceptance_document_json_rejected") from exc
    if not isinstance(value, dict):
        raise LocalAdminAcceptanceError("acceptance_document_shape_rejected")
    return value


def _exact(value: object, keys: set[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise LocalAdminAcceptanceError(code)
    return value


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _reject_secret_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).casefold()
            if any(part in normalized for part in FORBIDDEN_KEY_PARTS):
                raise LocalAdminAcceptanceError("acceptance_secret_field_rejected")
            _reject_secret_fields(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_secret_fields(nested)


def _validate_success(value: object) -> tuple[str, str]:
    success = _exact(
        value,
        {"transaction_id", "commit_order", "nodes", "terminal_phase", "outcome"},
        "acceptance_success_shape_rejected",
    )
    transaction_id = success["transaction_id"]
    if not isinstance(transaction_id, str) or TRANSACTION_ID.fullmatch(transaction_id) is None:
        raise LocalAdminAcceptanceError("acceptance_transaction_identity_rejected")
    if success["terminal_phase"] != "completed" or success["outcome"] != "accepted":
        raise LocalAdminAcceptanceError("acceptance_success_terminal_rejected")
    nodes = success["nodes"]
    if not isinstance(nodes, list) or len(nodes) != 2:
        raise LocalAdminAcceptanceError("acceptance_success_nodes_rejected")
    normalized: list[Mapping[str, Any]] = []
    for item in nodes:
        node = _exact(
            item,
            {"node_id", "role", "commit_sequence", "new_credential_canary", "credential_record_sha256"},
            "acceptance_success_node_shape_rejected",
        )
        if NODE_ID.fullmatch(str(node["node_id"])) is None:
            raise LocalAdminAcceptanceError("acceptance_node_identity_rejected")
        if node["role"] not in {"standby", "leader"}:
            raise LocalAdminAcceptanceError("acceptance_node_role_rejected")
        if node["new_credential_canary"] != "passed":
            raise LocalAdminAcceptanceError("acceptance_new_credential_canary_rejected")
        if not isinstance(node["credential_record_sha256"], str) or DIGEST.fullmatch(node["credential_record_sha256"]) is None:
            raise LocalAdminAcceptanceError("acceptance_credential_record_digest_rejected")
        normalized.append(node)
    normalized.sort(key=lambda item: item["commit_sequence"] if isinstance(item["commit_sequence"], int) else 999)
    if [item["commit_sequence"] for item in normalized] != [1, 2]:
        raise LocalAdminAcceptanceError("acceptance_commit_sequence_rejected")
    if [item["role"] for item in normalized] != ["standby", "leader"]:
        raise LocalAdminAcceptanceError("acceptance_commit_role_order_rejected")
    node_ids = [str(item["node_id"]) for item in normalized]
    if len(set(node_ids)) != 2 or success["commit_order"] != node_ids:
        raise LocalAdminAcceptanceError("acceptance_commit_order_rejected")
    record_digests = [str(item["credential_record_sha256"]) for item in normalized]
    if len(set(record_digests)) != 2:
        raise LocalAdminAcceptanceError("acceptance_independent_credential_state_rejected")
    return transaction_id, _digest(record_digests)


def _validate_rollback(value: object) -> None:
    rollback = _exact(
        value,
        {"terminal_phase", "outcome", "old_credential_canary", "new_credential_canary", "automatic_retry"},
        "acceptance_rollback_shape_rejected",
    )
    if (
        rollback["terminal_phase"] != "rolled_back"
        or rollback["outcome"] != "rolled_back"
        or rollback["old_credential_canary"] != "passed"
        or rollback["new_credential_canary"] != "rejected"
        or rollback["automatic_retry"] is not False
    ):
        raise LocalAdminAcceptanceError("acceptance_rollback_rejected")


def _validate_ambiguous(value: object) -> None:
    ambiguous = _exact(
        value,
        {"terminal_phase", "outcome", "recovery_required", "automatic_retry", "remote_recovery_enabled"},
        "acceptance_ambiguous_shape_rejected",
    )
    if (
        ambiguous["terminal_phase"] != "recovery_required"
        or ambiguous["outcome"] != "unknown"
        or ambiguous["recovery_required"] is not True
        or ambiguous["automatic_retry"] is not False
        or ambiguous["remote_recovery_enabled"] is not False
    ):
        raise LocalAdminAcceptanceError("acceptance_ambiguous_recovery_rejected")


def _validate_recovery_boundary(value: object) -> None:
    boundary = _exact(
        value,
        {"local_console_only", "root_required", "ssh_pty_rejected", "evidence_chain"},
        "acceptance_recovery_boundary_shape_rejected",
    )
    if (
        boundary["local_console_only"] is not True
        or boundary["root_required"] is not True
        or boundary["ssh_pty_rejected"] is not True
        or boundary["evidence_chain"] != "passed"
    ):
        raise LocalAdminAcceptanceError("acceptance_recovery_boundary_rejected")


def verify_local_admin_recovery_acceptance(value: Mapping[str, Any]) -> dict[str, Any]:
    document = _exact(
        value,
        {"schema", "cluster_id", "success", "rollback", "ambiguous", "recovery_boundary", "audit"},
        "acceptance_document_shape_rejected",
    )
    if document["schema"] != "home-center.local-admin-recovery-acceptance.v1":
        raise LocalAdminAcceptanceError("acceptance_schema_rejected")
    cluster_id = document["cluster_id"]
    if not isinstance(cluster_id, str) or not 3 <= len(cluster_id) <= 64:
        raise LocalAdminAcceptanceError("acceptance_cluster_identity_rejected")
    _reject_secret_fields(document)
    transaction_id, record_set_digest = _validate_success(document["success"])
    _validate_rollback(document["rollback"])
    _validate_ambiguous(document["ambiguous"])
    _validate_recovery_boundary(document["recovery_boundary"])
    audit = _exact(document["audit"], {"audit_chain", "secret_scan"}, "acceptance_audit_shape_rejected")
    if audit["audit_chain"] != "passed" or audit["secret_scan"] != "passed":
        raise LocalAdminAcceptanceError("acceptance_audit_rejected")

    evidence_sha256 = _digest(document)
    return {
        "schema": "home-center.local-admin-recovery-acceptance-result.v1",
        "status": "accepted",
        "transaction_id": transaction_id,
        "credential_record_set_sha256": record_set_digest,
        "rollback_verified": True,
        "ambiguous_recovery_verified": True,
        "protected_recovery_verified": True,
        "production_mutation_enabled": False,
        "evidence_sha256": evidence_sha256,
    }
