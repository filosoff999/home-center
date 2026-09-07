"""Bounded, non-authorizing acknowledgement of an exact module permission review."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from home_center.module_admission import MAX_ADMISSION_REQUEST_BYTES
from home_center.module_permission_review import (
    ModulePermissionReviewError,
    load_and_build_module_permission_review,
)
from home_center.util import canonical_json


MAX_PERMISSION_ACKNOWLEDGEMENT_REQUEST_BYTES = MAX_ADMISSION_REQUEST_BYTES + 16 * 1024
ACKNOWLEDGEMENT_TTL_SECONDS = 15 * 60
ACKNOWLEDGEMENT_PERSISTENCE_ENABLED = True
AUTHORIZATION_DECISION_PERSISTED = False
PERMISSION_GRANTS_APPLIED = False
LIFECYCLE_EXECUTION_ENABLED = False
PRODUCTION_ACTIVATION_ENABLED = False

REVIEW_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
ACKNOWLEDGEMENT_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class ModulePermissionAcknowledgementError(ValueError):
    """A bounded acknowledgement failure that never contains caller input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ModulePermissionAcknowledgementDraft:
    actor: str
    idempotency_key: str
    request_hash: str
    review_id: str
    scope_id: str
    review: dict[str, Any]
    created_at: str
    expires_at: str


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ModulePermissionAcknowledgementError("acknowledgement_duplicate_key")
        result[key] = value
    return result


def _reject_float(_: str) -> None:
    raise ModulePermissionAcknowledgementError("acknowledgement_float_rejected")


def _reject_constant(_: str) -> None:
    raise ModulePermissionAcknowledgementError("acknowledgement_constant_rejected")


def _canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _actor(value: str) -> str:
    if not isinstance(value, str) or not 3 <= len(value) <= 256:
        raise ModulePermissionAcknowledgementError("acknowledgement_actor_rejected")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ModulePermissionAcknowledgementError("acknowledgement_actor_rejected")
    return value


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ModulePermissionAcknowledgementError("acknowledgement_clock_rejected")
    return current.astimezone(UTC).replace(microsecond=0)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ModulePermissionAcknowledgementError("acknowledgement_record_rejected")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ModulePermissionAcknowledgementError("acknowledgement_record_rejected") from exc
    return parsed.astimezone(UTC)


def load_and_prepare_module_permission_acknowledgement(
    payload: bytes,
    *,
    actor: str,
    now: datetime | None = None,
) -> ModulePermissionAcknowledgementDraft:
    """Recompute and bind a review before producing persistence material."""

    if (
        not isinstance(payload, bytes)
        or not 0 < len(payload) <= MAX_PERMISSION_ACKNOWLEDGEMENT_REQUEST_BYTES
    ):
        raise ModulePermissionAcknowledgementError("acknowledgement_request_size_rejected")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ModulePermissionAcknowledgementError("acknowledgement_request_bom_rejected")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_duplicate_rejecting_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except ModulePermissionAcknowledgementError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ModulePermissionAcknowledgementError("acknowledgement_request_json_rejected") from exc
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "review_id",
        "acknowledgement",
        "idempotency_key",
        "admission_request",
    }:
        raise ModulePermissionAcknowledgementError("acknowledgement_request_fields_rejected")
    if value["schema"] != "home-center.module-permission-acknowledgement-request.v1":
        raise ModulePermissionAcknowledgementError("acknowledgement_request_schema_rejected")
    if value["acknowledgement"] != "permissions-reviewed":
        raise ModulePermissionAcknowledgementError("acknowledgement_intent_rejected")
    review_id = value["review_id"]
    if not isinstance(review_id, str) or not REVIEW_ID.fullmatch(review_id):
        raise ModulePermissionAcknowledgementError("acknowledgement_review_id_rejected")
    idempotency_key = value["idempotency_key"]
    if not isinstance(idempotency_key, str) or not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        raise ModulePermissionAcknowledgementError("acknowledgement_idempotency_key_rejected")
    admission_request = value["admission_request"]
    if not isinstance(admission_request, dict):
        raise ModulePermissionAcknowledgementError("acknowledgement_admission_request_rejected")
    admission_payload = canonical_json(admission_request).encode("utf-8")
    if len(admission_payload) > MAX_ADMISSION_REQUEST_BYTES:
        raise ModulePermissionAcknowledgementError("acknowledgement_admission_request_rejected")
    try:
        review = load_and_build_module_permission_review(admission_payload).to_dict()
    except ModulePermissionReviewError as exc:
        raise ModulePermissionAcknowledgementError("acknowledgement_review_rejected") from exc
    if not hmac.compare_digest(review_id, review["review_id"]):
        raise ModulePermissionAcknowledgementError("acknowledgement_review_mismatch")

    canonical_actor = _actor(actor)
    created = _now(now)
    requested_modules = sorted(
        review["requested_modules"], key=lambda item: (item["id"], item["version"])
    )
    scope_id = _canonical_digest(requested_modules)
    request_hash = _canonical_digest(
        {
            "schema": value["schema"],
            "review_id": review_id,
            "acknowledgement": value["acknowledgement"],
            "admission_request": admission_request,
        }
    )
    return ModulePermissionAcknowledgementDraft(
        actor=canonical_actor,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        review_id=review_id,
        scope_id=scope_id,
        review=review,
        created_at=_timestamp(created),
        expires_at=_timestamp(created + timedelta(seconds=ACKNOWLEDGEMENT_TTL_SECONDS)),
    )


def render_module_permission_acknowledgement(
    record: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Render a stored record with an effective, fail-closed expiry state."""

    required = {
        "acknowledgement_id",
        "review_id",
        "scope_id",
        "actor",
        "state",
        "created_at",
        "expires_at",
        "review",
    }
    if not isinstance(record, dict) or not required <= set(record):
        raise ModulePermissionAcknowledgementError("acknowledgement_record_rejected")
    acknowledgement_id = record["acknowledgement_id"]
    if not isinstance(acknowledgement_id, str) or not ACKNOWLEDGEMENT_ID.fullmatch(acknowledgement_id):
        raise ModulePermissionAcknowledgementError("acknowledgement_record_rejected")
    review_id = record["review_id"]
    scope_id = record["scope_id"]
    if (
        not isinstance(review_id, str)
        or REVIEW_ID.fullmatch(review_id) is None
        or not isinstance(scope_id, str)
        or REVIEW_ID.fullmatch(scope_id) is None
    ):
        raise ModulePermissionAcknowledgementError("acknowledgement_record_rejected")
    review = record["review"]
    review_fields = {
        "schema",
        "status",
        "review_id",
        "requested_modules",
        "modules",
        "permissions",
        "summary",
        "acknowledgement_required",
        "decision_persistence_enabled",
        "permission_grants_applied",
        "production_activation_enabled",
    }
    if not isinstance(review, dict) or set(review) != review_fields:
        raise ModulePermissionAcknowledgementError("acknowledgement_record_rejected")
    if (
        review["schema"] != "home-center.module-permission-review.v1"
        or review["status"] != "review-required"
        or review["review_id"] != review_id
        or review["acknowledgement_required"] is not True
        or review["decision_persistence_enabled"] is not False
        or review["permission_grants_applied"] is not False
        or review["production_activation_enabled"] is not False
    ):
        raise ModulePermissionAcknowledgementError("acknowledgement_record_rejected")
    review_without_id = {key: value for key, value in review.items() if key != "review_id"}
    if not hmac.compare_digest(_canonical_digest(review_without_id), review_id):
        raise ModulePermissionAcknowledgementError("acknowledgement_record_rejected")
    requested_modules = review["requested_modules"]
    if not isinstance(requested_modules, list) or not requested_modules:
        raise ModulePermissionAcknowledgementError("acknowledgement_record_rejected")
    if any(
        not isinstance(item, dict)
        or set(item) != {"id", "version"}
        or not isinstance(item["id"], str)
        or not isinstance(item["version"], str)
        for item in requested_modules
    ):
        raise ModulePermissionAcknowledgementError("acknowledgement_record_rejected")
    canonical_scope = sorted(
        requested_modules, key=lambda item: (item["id"], item["version"])
    )
    if not hmac.compare_digest(_canonical_digest(canonical_scope), scope_id):
        raise ModulePermissionAcknowledgementError("acknowledgement_record_rejected")
    if record["state"] not in {"recorded", "superseded"}:
        raise ModulePermissionAcknowledgementError("acknowledgement_record_rejected")
    created_at = _parse_timestamp(record["created_at"])
    expires_at = _parse_timestamp(record["expires_at"])
    if expires_at - created_at != timedelta(seconds=ACKNOWLEDGEMENT_TTL_SECONDS):
        raise ModulePermissionAcknowledgementError("acknowledgement_record_rejected")
    effective_state = record["state"]
    if effective_state == "recorded" and _now(now) >= expires_at:
        effective_state = "expired"
    handoff_status = "precondition-recorded" if effective_state == "recorded" else "blocked"
    return {
        "schema": "home-center.module-permission-acknowledgement.v1",
        "acknowledgement_id": acknowledgement_id,
        "status": effective_state,
        "review_id": review_id,
        "scope_id": scope_id,
        "actor": _actor(record["actor"]),
        "created_at": record["created_at"],
        "expires_at": record["expires_at"],
        "review": review,
        "lifecycle_handoff": {
            "reference": f"module-acknowledgement:{acknowledgement_id}",
            "status": handoff_status,
            "consumable": False,
        },
        "acknowledgement_persistence_enabled": ACKNOWLEDGEMENT_PERSISTENCE_ENABLED,
        "authorization_decision_persisted": AUTHORIZATION_DECISION_PERSISTED,
        "permission_grants_applied": PERMISSION_GRANTS_APPLIED,
        "lifecycle_execution_enabled": LIFECYCLE_EXECUTION_ENABLED,
        "production_activation_enabled": PRODUCTION_ACTIVATION_ENABLED,
    }


def acknowledgement_list(
    records: list[dict[str, Any]], *, now: datetime | None = None
) -> dict[str, Any]:
    return {
        "schema": "home-center.module-permission-acknowledgement-list.v1",
        "items": [render_module_permission_acknowledgement(record, now=now) for record in records],
        "acknowledgement_persistence_enabled": ACKNOWLEDGEMENT_PERSISTENCE_ENABLED,
        "authorization_decisions_enabled": False,
        "permission_grants_applied": PERMISSION_GRANTS_APPLIED,
        "lifecycle_execution_enabled": LIFECYCLE_EXECUTION_ENABLED,
        "production_activation_enabled": PRODUCTION_ACTIVATION_ENABLED,
    }
