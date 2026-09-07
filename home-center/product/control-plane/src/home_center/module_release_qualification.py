"""Deterministic release qualification for the Home Center 0.11 planning line.

Qualification proves that all planned module-management workstreams have exact
bounded evidence and that execution/placement remain blocked.  It cannot
install modules, consume acknowledgements, persist lifecycle state, or activate
production behavior.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .util import canonical_json


MAX_QUALIFICATION_BYTES = 64 * 1024
MAX_EVIDENCE_AGE_SECONDS = 24 * 60 * 60
EXPECTED_WORKSTREAMS = (
    "module-supply-chain",
    "admission-planner",
    "permission-review",
    "permission-ack",
    "lifecycle-planning",
    "artifact-binding",
    "authorization-execution",
)
EXPECTED_BLOCKERS = (
    "lifecycle_executor_unavailable",
    "placement_unresolved",
)


class ModuleReleaseQualificationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ModuleReleaseQualificationError("qualification_duplicate_key")
        result[key] = value
    return result


def _reject_float(_: str) -> None:
    raise ModuleReleaseQualificationError("qualification_float_rejected")


def _reject_constant(_: str) -> None:
    raise ModuleReleaseQualificationError("qualification_constant_rejected")


def load_module_release_qualification(data: bytes) -> dict[str, Any]:
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_QUALIFICATION_BYTES:
        raise ModuleReleaseQualificationError("qualification_document_size_rejected")
    if data.startswith(b"\xef\xbb\xbf"):
        raise ModuleReleaseQualificationError("qualification_document_bom_rejected")
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except ModuleReleaseQualificationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ModuleReleaseQualificationError("qualification_document_json_rejected") from exc
    if not isinstance(value, dict):
        raise ModuleReleaseQualificationError("qualification_document_shape_rejected")
    return value


def _exact(value: object, keys: set[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ModuleReleaseQualificationError(code)
    return value


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validate_digest(value: object) -> str:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        raise ModuleReleaseQualificationError("qualification_evidence_digest_rejected")
    try:
        bytes.fromhex(value[7:])
    except ValueError as exc:
        raise ModuleReleaseQualificationError("qualification_evidence_digest_rejected") from exc
    return value


def verify_module_release_qualification(
    value: Mapping[str, Any],
    *,
    now_epoch: int,
) -> dict[str, Any]:
    document = _exact(
        value,
        {"schema", "release_version", "observed_at_epoch", "workstreams", "safety"},
        "qualification_document_shape_rejected",
    )
    if document["schema"] != "home-center.module-release-qualification.v1":
        raise ModuleReleaseQualificationError("qualification_schema_rejected")
    if document["release_version"] != "0.11.0":
        raise ModuleReleaseQualificationError("qualification_release_rejected")
    observed = document["observed_at_epoch"]
    if not isinstance(observed, int) or isinstance(observed, bool) or observed <= 0:
        raise ModuleReleaseQualificationError("qualification_time_rejected")
    if not isinstance(now_epoch, int) or isinstance(now_epoch, bool) or now_epoch <= 0:
        raise ModuleReleaseQualificationError("qualification_clock_rejected")
    if observed > now_epoch or now_epoch - observed > MAX_EVIDENCE_AGE_SECONDS:
        raise ModuleReleaseQualificationError("qualification_evidence_stale")

    raw_workstreams = document["workstreams"]
    if not isinstance(raw_workstreams, list) or len(raw_workstreams) != len(EXPECTED_WORKSTREAMS):
        raise ModuleReleaseQualificationError("qualification_workstream_set_rejected")
    normalized: list[dict[str, str]] = []
    for item in raw_workstreams:
        entry = _exact(item, {"id", "evidence_sha256"}, "qualification_workstream_shape_rejected")
        workstream_id = entry["id"]
        if not isinstance(workstream_id, str):
            raise ModuleReleaseQualificationError("qualification_workstream_set_rejected")
        normalized.append({"id": workstream_id, "evidence_sha256": _validate_digest(entry["evidence_sha256"])})
    if tuple(item["id"] for item in normalized) != EXPECTED_WORKSTREAMS:
        raise ModuleReleaseQualificationError("qualification_workstream_set_rejected")
    if len({item["evidence_sha256"] for item in normalized}) != len(normalized):
        raise ModuleReleaseQualificationError("qualification_evidence_equivocation")

    safety = _exact(
        document["safety"],
        {
            "artifact_binding",
            "authorization_binding",
            "acknowledgement_consumed",
            "remaining_blockers",
            "lifecycle_persistence_enabled",
            "lifecycle_execution_enabled",
            "production_activation_enabled",
            "secret_scan",
        },
        "qualification_safety_shape_rejected",
    )
    if safety["artifact_binding"] != "exact" or safety["authorization_binding"] != "exact":
        raise ModuleReleaseQualificationError("qualification_binding_rejected")
    if safety["acknowledgement_consumed"] is not False:
        raise ModuleReleaseQualificationError("qualification_acknowledgement_consumption_rejected")
    if safety["remaining_blockers"] != list(EXPECTED_BLOCKERS):
        raise ModuleReleaseQualificationError("qualification_execution_blockers_rejected")
    if (
        safety["lifecycle_persistence_enabled"] is not False
        or safety["lifecycle_execution_enabled"] is not False
        or safety["production_activation_enabled"] is not False
    ):
        raise ModuleReleaseQualificationError("qualification_mutation_boundary_rejected")
    if safety["secret_scan"] != "passed":
        raise ModuleReleaseQualificationError("qualification_secret_scan_rejected")

    workstream_set_sha256 = _digest(normalized)
    evidence_sha256 = _digest(document)
    return {
        "schema": "home-center.module-release-qualification-result.v1",
        "status": "qualified",
        "release_version": "0.11.0",
        "workstream_set_sha256": workstream_set_sha256,
        "evidence_sha256": evidence_sha256,
        "remaining_blockers": list(EXPECTED_BLOCKERS),
        "acknowledgement_consumed": False,
        "lifecycle_persistence_enabled": False,
        "lifecycle_execution_enabled": False,
        "production_activation_enabled": False,
    }
