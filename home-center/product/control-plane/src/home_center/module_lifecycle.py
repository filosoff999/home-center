"""Deterministic, production-inert planning for a module install lifecycle."""

from __future__ import annotations

import copy
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
from home_center.module_artifact_publication import (
    ModuleArtifactPublicationError,
    render_module_artifact_publication,
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
REMAINING_BLOCKERS = (
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


def _manifest_binding_sha256(manifest: dict[str, Any]) -> str:
    normalized = copy.deepcopy(manifest)
    try:
        normalized["artifact"]["provenance"]["statement_sha256"] = "0" * 64
    except (KeyError, TypeError) as exc:
        raise ModuleLifecycleError("lifecycle_manifest_binding_rejected") from exc
    return "sha256:" + hashlib.sha256(
        canonical_json(normalized).encode("utf-8")
    ).hexdigest()


def _publications(records: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    if not isinstance(records, list) or len(records) > 128:
        raise ModuleLifecycleError("lifecycle_artifact_publication_rejected")
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    try:
        for record in records:
            publication = render_module_artifact_publication(record)
            key = (
                publication["module"]["id"],
                publication["module"]["version"],
                publication["artifact"]["sha256"],
            )
            if key in result:
                raise ModuleLifecycleError("lifecycle_artifact_publication_ambiguous")
            result[key] = publication
    except ModuleArtifactPublicationError as exc:
        raise ModuleLifecycleError("lifecycle_artifact_publication_rejected") from exc
    return result


def _bind_publication(
    manifest: dict[str, Any], publication: dict[str, Any] | None
) -> dict[str, Any] | None:
    if publication is None:
        return None
    artifact = manifest["artifact"]
    provenance = artifact["provenance"]
    declared_signers = set(provenance["signer_key_ids"])
    verified_signers = publication["verification"]["signing_key_ids"]
    exact = (
        hmac.compare_digest(publication["module"]["publisher"], manifest["module"]["publisher"])
        and publication["artifact"]["size_bytes"] == artifact["size_bytes"]
        and hmac.compare_digest(
            publication["artifact"]["content_address"],
            f"sha256:{artifact['sha256']}",
        )
        and hmac.compare_digest(
            publication["verification"]["manifest_binding_sha256"],
            _manifest_binding_sha256(manifest),
        )
        and hmac.compare_digest(
            publication["verification"]["statement_sha256"],
            f"sha256:{provenance['statement_sha256']}",
        )
        and len(verified_signers) >= provenance["threshold"]
        and set(verified_signers).issubset(declared_signers)
        and publication["installation_authority"] is False
        and publication["lifecycle_execution_enabled"] is False
        and publication["production_activation_enabled"] is False
    )
    if not exact:
        raise ModuleLifecycleError("lifecycle_artifact_publication_binding_rejected")
    return publication


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


def module_install_artifact_identities(
    request: dict[str, Any],
) -> list[tuple[str, str, str]]:
    """Return only the admitted install set used for server-side publication lookup."""

    if not isinstance(request, dict) or not isinstance(request.get("admission_request"), dict):
        raise ModuleLifecycleError("lifecycle_request_rejected")
    try:
        admission = plan_module_admission(request["admission_request"])
    except ModuleAdmissionError as exc:
        raise ModuleLifecycleError("lifecycle_admission_rejected") from exc
    return [
        (module.module_id, module.version, module.artifact_sha256)
        for module in admission.install_order
    ]


def plan_module_install_lifecycle(
    request: dict[str, Any],
    *,
    acknowledgement_record: dict[str, Any],
    actor: str,
    publication_records: list[dict[str, Any]] | None = None,
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
    publications = _publications(publication_records or [])
    install_steps: list[dict[str, Any]] = []
    recovery_steps: list[dict[str, Any]] = []
    for index, module in enumerate(admission.install_order, start=1):
        manifest = manifests.get((module.module_id, module.version))
        if manifest is None:
            raise ModuleLifecycleError("lifecycle_manifest_binding_rejected")
        lifecycle = manifest["lifecycle"]["install"]
        publication = _bind_publication(
            manifest,
            publications.get((module.module_id, module.version, module.artifact_sha256)),
        )
        install_steps.append(
            {
                "id": f"install-{index:03d}",
                "module": {
                    "id": module.module_id,
                    "version": module.version,
                    "artifact_sha256": module.artifact_sha256,
                },
                "publication": publication,
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

    publication_complete = all(step["publication"] is not None for step in install_steps)
    blockers = list(REMAINING_BLOCKERS if publication_complete else BLOCKERS)
    preflight = [
        {"id": "admission", "status": "pass", "reason": None},
        {"id": "permission-review", "status": "pass", "reason": None},
        {"id": "permission-acknowledgement", "status": "pass", "reason": None},
        {
            "id": "artifact-publication",
            "status": "pass" if publication_complete else "blocked",
            "reason": None if publication_complete else "artifact_publication_unverified",
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
        "blockers": blockers,
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
