"""Fail-closed verification of Home Center 0.9 release-candidate evidence.

The evidence document records observations; it is deliberately not release
authority.  A separately signed stable-channel record is still required before
any deployment workflow may admit an artifact.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .util import canonical_json, sha256_bytes


ACCEPTANCE_SCHEMA = "home-center.release-candidate-acceptance.v1"
VERIFICATION_SCHEMA = "home-center.release-candidate-verification.v1"
TARGET_VERSION = "0.9.0"
PREDECESSOR_VERSION = "0.8.0"
EXPECTED_NODES = ("dc02", "dc01")
ROLLBACK_ORDER = ("dc01", "dc02")
EXPECTED_DOMAIN = "hm.dm"
EXPECTED_DOMAIN_SID = "S-1-5-21-483832520-828804035-215000592"
MAX_ACCEPTANCE_BYTES = 64 * 1024

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ACCEPTANCE_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")


class ReleaseCandidateAcceptanceError(ValueError):
    """A stable, non-secret rejection suitable for automation."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CandidateIdentity:
    version: str
    revision: str
    artifact_sha256: str

    def public(self) -> dict[str, str]:
        return {
            "version": self.version,
            "revision": self.revision,
            "artifact_sha256": self.artifact_sha256,
        }


@dataclass(frozen=True)
class VerifiedReleaseCandidate:
    acceptance_id: str
    candidate: CandidateIdentity
    predecessor: CandidateIdentity
    evidence_sha256: str

    def result(self) -> dict[str, Any]:
        return {
            "schema": VERIFICATION_SCHEMA,
            "status": "accepted",
            "reason_code": None,
            "acceptance_id": self.acceptance_id,
            "candidate": self.candidate.public(),
            "predecessor": self.predecessor.public(),
            "rollout_order": list(EXPECTED_NODES),
            "rollback_order": list(ROLLBACK_ORDER),
            "evidence_sha256": self.evidence_sha256,
        }


def _duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseCandidateAcceptanceError("acceptance_json_duplicate_key")
        result[key] = value
    return result


def load_acceptance_document(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_ACCEPTANCE_BYTES:
        raise ReleaseCandidateAcceptanceError("acceptance_document_size_rejected")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseCandidateAcceptanceError("acceptance_json_rejected") from exc
    if not isinstance(value, dict):
        raise ReleaseCandidateAcceptanceError("acceptance_document_rejected")
    return value


def _object(value: Any, keys: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ReleaseCandidateAcceptanceError(code)
    return value


def _passed(value: Any, code: str) -> None:
    if value != "passed":
        raise ReleaseCandidateAcceptanceError(code)


def _boolean(value: Any, expected: bool, code: str) -> None:
    if value is not expected:
        raise ReleaseCandidateAcceptanceError(code)


def _digest(value: Any, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ReleaseCandidateAcceptanceError(code)
    return value


def _identity(value: Any, code: str) -> CandidateIdentity:
    item = _object(value, {"version", "revision", "artifact_sha256"}, code)
    version = item["version"]
    if not isinstance(version, str):
        raise ReleaseCandidateAcceptanceError(code)
    return CandidateIdentity(
        version=version,
        revision=_digest(item["revision"], _HEX40, code),
        artifact_sha256=_digest(item["artifact_sha256"], _HEX64, code),
    )


def _pki(value: Any, code: str) -> dict[str, str]:
    item = _object(value, {"ca_sha256", "certificate_sha256", "public_key_sha256"}, code)
    return {name: _digest(item[name], _HEX64, code) for name in sorted(item)}


def _verify_rollout(
    rollout: Any,
    candidate: CandidateIdentity,
) -> None:
    if not isinstance(rollout, list) or len(rollout) != 2:
        raise ReleaseCandidateAcceptanceError("acceptance_rollout_shape_rejected")
    expected_keys = {
        "sequence",
        "node",
        "status",
        "observed_candidate",
        "backup",
        "health",
        "web_identity_before",
        "web_identity_after",
        "peer_identity_before",
        "peer_identity_after",
    }
    for sequence, expected_node in enumerate(EXPECTED_NODES, start=1):
        item = _object(rollout[sequence - 1], expected_keys, "acceptance_rollout_shape_rejected")
        if type(item["sequence"]) is not int or item["sequence"] != sequence or item["node"] != expected_node:
            raise ReleaseCandidateAcceptanceError("acceptance_rollout_order_rejected")
        _passed(item["status"], "acceptance_rollout_status_rejected")
        if _identity(item["observed_candidate"], "acceptance_observed_candidate_rejected") != candidate:
            raise ReleaseCandidateAcceptanceError("acceptance_candidate_parity_rejected")

        backup = _object(
            item["backup"],
            {"archive_sha256", "manifest_sha256", "verified", "restore_verified"},
            "acceptance_backup_shape_rejected",
        )
        _digest(backup["archive_sha256"], _HEX64, "acceptance_backup_digest_rejected")
        _digest(backup["manifest_sha256"], _HEX64, "acceptance_backup_digest_rejected")
        _boolean(backup["verified"], True, "acceptance_backup_verification_rejected")
        _boolean(backup["restore_verified"], True, "acceptance_restore_verification_rejected")

        health = _object(
            item["health"],
            {"readiness", "peer_mtls", "drs_replication"},
            "acceptance_health_shape_rejected",
        )
        for check in ("readiness", "peer_mtls", "drs_replication"):
            _passed(health[check], f"acceptance_{check}_rejected")

        for kind in ("web_identity", "peer_identity"):
            before = _pki(item[f"{kind}_before"], f"acceptance_{kind}_rejected")
            after = _pki(item[f"{kind}_after"], f"acceptance_{kind}_rejected")
            if before != after:
                raise ReleaseCandidateAcceptanceError(f"acceptance_{kind}_changed")


def _verify_rollback(value: Any, predecessor: CandidateIdentity) -> None:
    item = _object(
        value,
        {"status", "order", "restored_nodes", "state_restore", "audit_chain", "backups_reverified"},
        "acceptance_rollback_shape_rejected",
    )
    _passed(item["status"], "acceptance_rollback_status_rejected")
    if item["order"] != list(ROLLBACK_ORDER):
        raise ReleaseCandidateAcceptanceError("acceptance_rollback_order_rejected")
    restored = item["restored_nodes"]
    if not isinstance(restored, list) or len(restored) != 2:
        raise ReleaseCandidateAcceptanceError("acceptance_rollback_nodes_rejected")
    for index, node in enumerate(ROLLBACK_ORDER):
        restored_node = _object(restored[index], {"node", "observed_predecessor"}, "acceptance_rollback_nodes_rejected")
        if restored_node["node"] != node:
            raise ReleaseCandidateAcceptanceError("acceptance_rollback_order_rejected")
        if _identity(restored_node["observed_predecessor"], "acceptance_restored_predecessor_rejected") != predecessor:
            raise ReleaseCandidateAcceptanceError("acceptance_predecessor_parity_rejected")
    for check in ("state_restore", "audit_chain", "backups_reverified"):
        _passed(item[check], f"acceptance_rollback_{check}_rejected")


def _verify_cluster(value: Any) -> None:
    item = _object(
        value,
        {
            "domain_dns_name",
            "domain_sid",
            "nodes",
            "writer_count",
            "automatic_failover",
            "version_parity",
            "revision_parity",
            "artifact_parity",
            "drs_replication",
            "peer_mtls",
        },
        "acceptance_cluster_shape_rejected",
    )
    if item["domain_dns_name"] != EXPECTED_DOMAIN or item["domain_sid"] != EXPECTED_DOMAIN_SID:
        raise ReleaseCandidateAcceptanceError("acceptance_domain_identity_rejected")
    if (
        item["nodes"] != ["dc01", "dc02"]
        or type(item["writer_count"]) is not int
        or item["writer_count"] != 1
    ):
        raise ReleaseCandidateAcceptanceError("acceptance_cluster_topology_rejected")
    _boolean(item["automatic_failover"], False, "acceptance_automatic_failover_rejected")
    for check in ("version_parity", "revision_parity", "artifact_parity", "drs_replication", "peer_mtls"):
        _passed(item[check], f"acceptance_cluster_{check}_rejected")


def _verify_external_access(value: Any) -> None:
    item = _object(
        value,
        {
            "policy_default_disabled",
            "gateway_tls",
            "desktop_browser",
            "mobile_browser",
            "positive_path",
            "spoof_rejection",
            "internal_surfaces_hidden",
            "rate_limit",
        },
        "acceptance_external_access_shape_rejected",
    )
    _boolean(item["policy_default_disabled"], True, "acceptance_external_default_rejected")
    for check in (
        "gateway_tls",
        "desktop_browser",
        "mobile_browser",
        "positive_path",
        "spoof_rejection",
        "internal_surfaces_hidden",
        "rate_limit",
    ):
        _passed(item[check], f"acceptance_external_{check}_rejected")


def _verify_safety(value: Any) -> None:
    item = _object(
        value,
        {
            "ad_mutations",
            "dns_mutations",
            "dhcp_mutations",
            "gpo_mutations",
            "automatic_failover",
            "secret_scan",
        },
        "acceptance_safety_shape_rejected",
    )
    for kind in ("ad_mutations", "dns_mutations", "dhcp_mutations", "gpo_mutations"):
        if type(item[kind]) is not int or item[kind] != 0:
            raise ReleaseCandidateAcceptanceError(f"acceptance_{kind}_rejected")
    _boolean(item["automatic_failover"], False, "acceptance_safety_failover_rejected")
    _passed(item["secret_scan"], "acceptance_secret_scan_rejected")


def verify_release_candidate(
    document: dict[str, Any],
    *,
    expected_candidate_revision: str,
    expected_candidate_artifact_sha256: str,
    expected_predecessor_revision: str,
    expected_predecessor_artifact_sha256: str,
) -> VerifiedReleaseCandidate:
    root = _object(
        document,
        {
            "schema",
            "acceptance_id",
            "candidate",
            "predecessor",
            "rollout",
            "rollback_drill",
            "cluster",
            "external_access",
            "safety",
        },
        "acceptance_document_shape_rejected",
    )
    if root["schema"] != ACCEPTANCE_SCHEMA:
        raise ReleaseCandidateAcceptanceError("acceptance_schema_rejected")
    acceptance_id = root["acceptance_id"]
    if not isinstance(acceptance_id, str) or _ACCEPTANCE_ID.fullmatch(acceptance_id) is None:
        raise ReleaseCandidateAcceptanceError("acceptance_id_rejected")

    candidate = _identity(root["candidate"], "acceptance_candidate_rejected")
    predecessor = _identity(root["predecessor"], "acceptance_predecessor_rejected")
    expected_candidate = CandidateIdentity(
        TARGET_VERSION,
        _digest(expected_candidate_revision, _HEX40, "expected_candidate_revision_rejected"),
        _digest(expected_candidate_artifact_sha256, _HEX64, "expected_candidate_artifact_rejected"),
    )
    expected_predecessor = CandidateIdentity(
        PREDECESSOR_VERSION,
        _digest(expected_predecessor_revision, _HEX40, "expected_predecessor_revision_rejected"),
        _digest(expected_predecessor_artifact_sha256, _HEX64, "expected_predecessor_artifact_rejected"),
    )
    if candidate != expected_candidate:
        raise ReleaseCandidateAcceptanceError("acceptance_candidate_identity_rejected")
    if predecessor != expected_predecessor:
        raise ReleaseCandidateAcceptanceError("acceptance_predecessor_identity_rejected")

    _verify_rollout(root["rollout"], candidate)
    _verify_rollback(root["rollback_drill"], predecessor)
    _verify_cluster(root["cluster"])
    _verify_external_access(root["external_access"])
    _verify_safety(root["safety"])
    return VerifiedReleaseCandidate(
        acceptance_id=acceptance_id,
        candidate=candidate,
        predecessor=predecessor,
        evidence_sha256=sha256_bytes((canonical_json(root) + "\n").encode("utf-8")),
    )


def rejected_result(reason: str) -> dict[str, Any]:
    return {
        "schema": VERIFICATION_SCHEMA,
        "status": "rejected",
        "reason_code": reason,
        "acceptance_id": None,
        "candidate": None,
        "predecessor": None,
        "rollout_order": [],
        "rollback_order": [],
        "evidence_sha256": None,
    }
