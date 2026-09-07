"""Deterministic, production-inert planning for a module install lifecycle."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from typing import Any

from home_center.module_admission import (
    MAX_ADMISSION_REQUEST_BYTES,
    ModuleAdmissionError,
    plan_module_admission,
)
from home_center.module_permission_acknowledgement import (
    ACKNOWLEDGEMENT_ID,
    ModulePermissionAcknowledgementError,
    render_module_permission_acknowledgement,
)
from home_center.module_permission_review import (
    ModulePermissionReviewError,
    build_module_permission_review,
)
from home_center.util import canonical_json


MAX_MODULE_LIFECYCLE_REQUEST_BYTES = MAX_ADMISSION_REQUEST_BYTES + 8 * 1024
ACKNOWLEDGEMENT_CONSUMPTION_ENABLED = False
AUTHORIZATION_DECISIONS_ENABLED = False
ARTIFACT_MUTATION_ENABLED = False
LIFECYCLE_PERSISTENCE_ENABLED = False
LIFECYCLE_EXECUTION_ENABLED = False
PRODUCTION_ACTIVATION_ENABLED = False

BLOCKERS = (
    "artifact_publication_unverified",
    "authorization_contract_unavailable",
    "lifecycle_executor_unavailable",
    "placement_unresolved",
)


class ModuleLifecycleError(ValueError):
    """A bounded lifecycle planning failure that never contains caller input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ModuleLifecycleError("lifecycle_duplicate_key")
        result[key] = value
    return result


def _reject_float(_: str) -> None:
    raise ModuleLifecycleError("lifecycle_float_rejected")


def _reject_constant(_: str) -> None:
    raise ModuleLifecycleError("lifecycle_constant_rejected")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_module_install_lifecycle_request(payload: bytes) -> dict[str, Any]:
    """Decode a closed request without accepting ambiguous JSON representations."""

    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_MODULE_LIFECYCLE_REQUEST_BYTES:
        raise ModuleLifecycleError("lifecycle_request_size_rejected")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ModuleLifecycleError("lifecycle_request_bom_rejected")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_duplicate_rejecting_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except ModuleLifecycleError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ModuleLifecycleError("lifecycle_request_json_rejected") from exc
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "operation",
        "acknowledgement_id",
        "admission_request",
    }:
        raise ModuleLifecycleError("lifecycle_request_fields_rejected")
    if value["schema"] != "home-center.module-install-lifecycle-request.v1":
        raise ModuleLifecycleError("lifecycle_request_schema_rejected")
    if value["operation"] != "install":
        raise ModuleLifecycleError("lifecycle_operation_rejected")
    acknowledgement_id = value["acknowledgement_id"]
    if not isinstance(acknowledgement_id, str) or ACKNOWLEDGEMENT_ID.fullmatch(
        acknowledgement_id
    ) is None:
        raise ModuleLifecycleError("lifecycle_acknowledgement_id_rejected")
    if not isinstance(value["admission_request"], dict):
        raise ModuleLifecycleError("lifecycle_admission_request_rejected")
    if len(canonical_json(value["admission_request"]).encode("utf-8")) > MAX_ADMISSION_REQUEST_BYTES:
        raise ModuleLifecycleError("lifecycle_admission_request_rejected")
    return value


def _action(manifest: dict[str, Any], action_id: str) -> dict[str, Any]:
    for action in manifest["actions"]:
        if action["id"] == action_id:
            return {
                "id": action["id"],
                "permission": action["permission"],
                "risk": action["risk"],
                "timeout_seconds": action["timeout_seconds"],
                "idempotent": action["idempotent"],
            }
    raise ModuleLifecycleError("lifecycle_action_binding_rejected")


def _postconditions(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": probe["id"],
            "kind": probe["kind"],
            "interval_seconds": probe["interval_seconds"],
            "timeout_seconds": probe["timeout_seconds"],
            "status": "pending",
        }
        for probe in manifest["health"]
    ]


def plan_module_install_lifecycle(
    request: dict[str, Any],
    *,
    acknowledgement_record: dict[str, Any],
    actor: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a blocked lifecycle plan; no transition can execute in this increment."""

    if (
        not isinstance(request, dict)
        or set(request)
        != {"schema", "operation", "acknowledgement_id", "admission_request"}
        or request.get("schema") != "home-center.module-install-lifecycle-request.v1"
        or request.get("operation") != "install"
        or not isinstance(request.get("acknowledgement_id"), str)
        or ACKNOWLEDGEMENT_ID.fullmatch(request["acknowledgement_id"]) is None
    ):
        raise ModuleLifecycleError("lifecycle_request_rejected")
    try:
        acknowledgement = render_module_permission_acknowledgement(
            acknowledgement_record, now=now
        )
    except ModulePermissionAcknowledgementError as exc:
        raise ModuleLifecycleError("lifecycle_acknowledgement_rejected") from exc
    if (
        acknowledgement["acknowledgement_id"] != request.get("acknowledgement_id")
        or not hmac.compare_digest(acknowledgement["actor"], actor)
        or acknowledgement["status"] != "recorded"
        or acknowledgement["lifecycle_handoff"]["status"] != "precondition-recorded"
        or acknowledgement["lifecycle_handoff"]["consumable"] is not False
    ):
        raise ModuleLifecycleError("lifecycle_acknowledgement_rejected")

    admission_request = request.get("admission_request")
    if not isinstance(admission_request, dict):
        raise ModuleLifecycleError("lifecycle_request_rejected")
    try:
        admission = plan_module_admission(admission_request)
        review = build_module_permission_review(admission_request).to_dict()
    except (ModuleAdmissionError, ModulePermissionReviewError) as exc:
        raise ModuleLifecycleError("lifecycle_admission_rejected") from exc
    acknowledgement_request_hash = _digest(
        {
            "schema": "home-center.module-permission-acknowledgement-request.v1",
            "review_id": review["review_id"],
            "acknowledgement": "permissions-reviewed",
            "admission_request": admission_request,
        }
    )
    stored_request_hash = acknowledgement_record.get("request_hash")
    if not isinstance(stored_request_hash, str) or not hmac.compare_digest(
        acknowledgement_request_hash, stored_request_hash
    ):
        raise ModuleLifecycleError("lifecycle_admission_binding_rejected")
    if not hmac.compare_digest(review["review_id"], acknowledgement["review_id"]):
        raise ModuleLifecycleError("lifecycle_review_binding_rejected")
    scope_id = _digest(
        sorted(review["requested_modules"], key=lambda item: (item["id"], item["version"]))
    )
    if not hmac.compare_digest(scope_id, acknowledgement["scope_id"]):
        raise ModuleLifecycleError("lifecycle_scope_binding_rejected")

    manifests = {
        (item["module"]["id"], item["module"]["version"]): item
        for item in admission_request["candidates"]
    }
    install_steps: list[dict[str, Any]] = []
    recovery_steps: list[dict[str, Any]] = []
    for index, module in enumerate(admission.install_order, start=1):
        manifest = manifests.get((module.module_id, module.version))
        if manifest is None:
            raise ModuleLifecycleError("lifecycle_manifest_binding_rejected")
        lifecycle = manifest["lifecycle"]["install"]
        install_steps.append(
            {
                "id": f"install-{index:03d}",
                "module": {
                    "id": module.module_id,
                    "version": module.version,
                    "artifact_sha256": module.artifact_sha256,
                },
                "action": _action(manifest, lifecycle["action"]),
                "state": "blocked",
                "postconditions": _postconditions(manifest),
                "backup": {
                    "mode": manifest["backup"]["mode"],
                    "data_ids": list(manifest["backup"]["data_ids"]),
                    "restore_required": manifest["backup"]["restore_required"],
                },
            }
        )
    for index, step in enumerate(reversed(install_steps), start=1):
        module = step["module"]
        manifest = manifests[(module["id"], module["version"])]
        rollback_id = manifest["lifecycle"]["install"]["rollback_action"]
        recovery_steps.append(
            {
                "id": f"rollback-{index:03d}",
                "module": {"id": module["id"], "version": module["version"]},
                "action": _action(manifest, rollback_id),
                "state": "planned",
                "data_policy": "preserve",
            }
        )

    preflight = [
        {"id": "admission", "status": "pass", "reason": None},
        {"id": "permission-review", "status": "pass", "reason": None},
        {"id": "permission-acknowledgement", "status": "pass", "reason": None},
        {
            "id": "artifact-publication",
            "status": "blocked",
            "reason": "artifact_publication_unverified",
        },
        {
            "id": "authorization",
            "status": "blocked",
            "reason": "authorization_contract_unavailable",
        },
        {
            "id": "executor",
            "status": "blocked",
            "reason": "lifecycle_executor_unavailable",
        },
        {"id": "placement", "status": "blocked", "reason": "placement_unresolved"},
    ]
    document = {
        "schema": "home-center.module-install-lifecycle-plan.v1",
        "status": "blocked",
        "operation": "install",
        "acknowledgement": {
            "id": acknowledgement["acknowledgement_id"],
            "reference": acknowledgement["lifecycle_handoff"]["reference"],
            "status": "evidence-validated",
            "consumable": False,
        },
        "review_id": review["review_id"],
        "scope_id": scope_id,
        "preflight": preflight,
        "blockers": list(BLOCKERS),
        "steps": install_steps,
        "recovery": {
            "strategy": "reverse-order-rollback",
            "state": "planned",
            "data_policy": "preserve",
            "steps": recovery_steps,
        },
        "acknowledgement_consumption_enabled": ACKNOWLEDGEMENT_CONSUMPTION_ENABLED,
        "authorization_decisions_enabled": AUTHORIZATION_DECISIONS_ENABLED,
        "artifact_mutation_enabled": ARTIFACT_MUTATION_ENABLED,
        "lifecycle_persistence_enabled": LIFECYCLE_PERSISTENCE_ENABLED,
        "lifecycle_execution_enabled": LIFECYCLE_EXECUTION_ENABLED,
        "production_activation_enabled": PRODUCTION_ACTIVATION_ENABLED,
    }
    return {**document, "plan_id": _digest(document)}


def empty_module_lifecycle_status() -> dict[str, Any]:
    """Return the honest runtime state before a Market lifecycle request exists."""

    return {
        "schema": "home-center.module-lifecycle-status.v1",
        "status": "no-pending-lifecycle",
        "plan": None,
        "acknowledgement_consumption_enabled": ACKNOWLEDGEMENT_CONSUMPTION_ENABLED,
        "authorization_decisions_enabled": AUTHORIZATION_DECISIONS_ENABLED,
        "artifact_mutation_enabled": ARTIFACT_MUTATION_ENABLED,
        "lifecycle_persistence_enabled": LIFECYCLE_PERSISTENCE_ENABLED,
        "lifecycle_execution_enabled": LIFECYCLE_EXECUTION_ENABLED,
        "production_activation_enabled": PRODUCTION_ACTIVATION_ENABLED,
    }
