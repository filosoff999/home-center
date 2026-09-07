"""Release qualification for the Home Center 0.12 credential-safety line.

This verifier consumes bounded, secret-free evidence only. It cannot rotate or
recover credentials, contact peers, invoke privileged helpers, or deploy code.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from .util import canonical_json


MAX_QUALIFICATION_BYTES = 64 * 1024
MAX_EVIDENCE_AGE_SECONDS = 24 * 60 * 60
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
TRANSACTION_ID = re.compile(r"^la-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}$")
EXPECTED_WORKSTREAMS = (
    "password-rotation",
    "console-recovery",
    "two-node-transaction",
    "recovery-acceptance",
)
FORBIDDEN_KEY_PARTS = ("password", "salt", "verifier", "token", "private_key", "secret_material")


class LocalAdminReleaseQualificationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LocalAdminReleaseQualificationError("qualification_duplicate_key")
        result[key] = value
    return result


def _reject_float(_: str) -> None:
    raise LocalAdminReleaseQualificationError("qualification_float_rejected")


def _reject_constant(_: str) -> None:
    raise LocalAdminReleaseQualificationError("qualification_constant_rejected")


def load_local_admin_release_qualification(data: bytes) -> dict[str, Any]:
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_QUALIFICATION_BYTES:
        raise LocalAdminReleaseQualificationError("qualification_document_size_rejected")
    if data.startswith(b"\xef\xbb\xbf"):
        raise LocalAdminReleaseQualificationError("qualification_document_bom_rejected")
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except LocalAdminReleaseQualificationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise LocalAdminReleaseQualificationError("qualification_document_json_rejected") from exc
    if not isinstance(value, dict):
        raise LocalAdminReleaseQualificationError("qualification_document_shape_rejected")
    return value


def _exact(value: object, keys: set[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise LocalAdminReleaseQualificationError(code)
    return value


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _digest_value(value: object) -> str:
    if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
        raise LocalAdminReleaseQualificationError("qualification_evidence_digest_rejected")
    return value


def _reject_secret_material_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).casefold()
            if any(part in normalized for part in FORBIDDEN_KEY_PARTS):
                raise LocalAdminReleaseQualificationError("qualification_secret_field_rejected")
            _reject_secret_material_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_secret_material_keys(nested)


def verify_local_admin_release_qualification(
    value: Mapping[str, Any],
    *,
    now_epoch: int,
) -> dict[str, Any]:
    document = _exact(
        value,
        {"schema", "release_version", "observed_at_epoch", "workstreams", "recovery_acceptance", "safety"},
        "qualification_document_shape_rejected",
    )
    if document["schema"] != "home-center.local-admin-release-qualification.v1":
        raise LocalAdminReleaseQualificationError("qualification_schema_rejected")
    if document["release_version"] != "0.12.0":
        raise LocalAdminReleaseQualificationError("qualification_release_rejected")
    observed = document["observed_at_epoch"]
    if not isinstance(observed, int) or isinstance(observed, bool) or observed <= 0:
        raise LocalAdminReleaseQualificationError("qualification_time_rejected")
    if not isinstance(now_epoch, int) or isinstance(now_epoch, bool) or now_epoch <= 0:
        raise LocalAdminReleaseQualificationError("qualification_clock_rejected")
    if observed > now_epoch or now_epoch - observed > MAX_EVIDENCE_AGE_SECONDS:
        raise LocalAdminReleaseQualificationError("qualification_evidence_stale")
    _reject_secret_material_keys(document)

    raw_workstreams = document["workstreams"]
    if not isinstance(raw_workstreams, list) or len(raw_workstreams) != len(EXPECTED_WORKSTREAMS):
        raise LocalAdminReleaseQualificationError("qualification_workstream_set_rejected")
    normalized: list[dict[str, str]] = []
    for item in raw_workstreams:
        entry = _exact(item, {"id", "evidence_sha256"}, "qualification_workstream_shape_rejected")
        if not isinstance(entry["id"], str):
            raise LocalAdminReleaseQualificationError("qualification_workstream_set_rejected")
        normalized.append({"id": entry["id"], "evidence_sha256": _digest_value(entry["evidence_sha256"])})
    if tuple(item["id"] for item in normalized) != EXPECTED_WORKSTREAMS:
        raise LocalAdminReleaseQualificationError("qualification_workstream_set_rejected")
    if len({item["evidence_sha256"] for item in normalized}) != len(normalized):
        raise LocalAdminReleaseQualificationError("qualification_evidence_equivocation")

    acceptance = _exact(
        document["recovery_acceptance"],
        {
            "schema",
            "status",
            "transaction_id",
            "credential_record_set_sha256",
            "rollback_verified",
            "ambiguous_recovery_verified",
            "protected_recovery_verified",
            "production_mutation_enabled",
            "evidence_sha256",
        },
        "qualification_recovery_acceptance_shape_rejected",
    )
    if (
        acceptance["schema"] != "home-center.local-admin-recovery-acceptance-result.v1"
        or acceptance["status"] != "accepted"
        or not isinstance(acceptance["transaction_id"], str)
        or TRANSACTION_ID.fullmatch(acceptance["transaction_id"]) is None
        or acceptance["rollback_verified"] is not True
        or acceptance["ambiguous_recovery_verified"] is not True
        or acceptance["protected_recovery_verified"] is not True
        or acceptance["production_mutation_enabled"] is not False
    ):
        raise LocalAdminReleaseQualificationError("qualification_recovery_acceptance_rejected")
    _digest_value(acceptance["credential_record_set_sha256"])
    _digest_value(acceptance["evidence_sha256"])

    safety = _exact(
        document["safety"],
        {
            "credential_mutation_enabled",
            "remote_recovery_enabled",
            "automatic_retry_after_ambiguous",
            "local_console_recovery_required",
            "secret_scan",
        },
        "qualification_safety_shape_rejected",
    )
    if (
        safety["credential_mutation_enabled"] is not False
        or safety["remote_recovery_enabled"] is not False
        or safety["automatic_retry_after_ambiguous"] is not False
        or safety["local_console_recovery_required"] is not True
    ):
        raise LocalAdminReleaseQualificationError("qualification_safety_boundary_rejected")
    if safety["secret_scan"] != "passed":
        raise LocalAdminReleaseQualificationError("qualification_secret_scan_rejected")

    return {
        "schema": "home-center.local-admin-release-qualification-result.v1",
        "status": "qualified",
        "release_version": "0.12.0",
        "transaction_id": acceptance["transaction_id"],
        "workstream_set_sha256": _digest(normalized),
        "recovery_acceptance_sha256": _digest(acceptance),
        "evidence_sha256": _digest(document),
        "credential_mutation_enabled": False,
        "remote_recovery_enabled": False,
        "production_mutation_enabled": False,
    }
