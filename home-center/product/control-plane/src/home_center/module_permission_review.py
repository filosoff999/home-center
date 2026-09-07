"""Read-only permission review over an admitted Home Center module plan."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from home_center.module_admission import (
    ModuleAdmissionError,
    ModuleVersion,
    load_admission_request,
    plan_module_admission,
)


PERMISSION_GRANTS_APPLIED = False
DECISION_PERSISTENCE_ENABLED = False
PRODUCTION_ACTIVATION_ENABLED = False
MAX_REVIEW_PERMISSIONS = 128
MAX_REVIEW_ACTIONS = 2048
RISK_ORDER = {"read-only": 0, "mutation": 1, "destructive": 2, "unclassified": 3}


class ModulePermissionReviewError(ValueError):
    """A bounded review failure that never includes untrusted input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, order=True)
class PermissionAction:
    module_id: str
    action_id: str
    risk: str


@dataclass(frozen=True)
class ReviewedModule:
    module_id: str
    version: str
    artifact_sha256: str
    permissions: tuple[str, ...]


@dataclass(frozen=True)
class ReviewedPermission:
    permission_id: str
    risk: str
    modules: tuple[str, ...]
    actions: tuple[PermissionAction, ...]


@dataclass(frozen=True)
class ModulePermissionReview:
    review_id: str
    requested_modules: tuple[ModuleVersion, ...]
    modules: tuple[ReviewedModule, ...]
    permissions: tuple[ReviewedPermission, ...]

    def to_dict(self) -> dict[str, Any]:
        counts = {risk: 0 for risk in RISK_ORDER}
        for permission in self.permissions:
            counts[permission.risk] += 1
        return {
            "schema": "home-center.module-permission-review.v1",
            "status": "review-required",
            "review_id": self.review_id,
            "requested_modules": [
                {"id": item.module_id, "version": item.version} for item in self.requested_modules
            ],
            "modules": [
                {
                    "id": item.module_id,
                    "version": item.version,
                    "artifact_sha256": item.artifact_sha256,
                    "permissions": list(item.permissions),
                }
                for item in self.modules
            ],
            "permissions": [
                {
                    "id": item.permission_id,
                    "risk": item.risk,
                    "modules": list(item.modules),
                    "actions": [
                        {
                            "module_id": action.module_id,
                            "action_id": action.action_id,
                            "risk": action.risk,
                        }
                        for action in item.actions
                    ],
                }
                for item in self.permissions
            ],
            "summary": {
                "total": len(self.permissions),
                "read_only": counts["read-only"],
                "mutation": counts["mutation"],
                "destructive": counts["destructive"],
                "unclassified": counts["unclassified"],
            },
            "acknowledgement_required": True,
            "decision_persistence_enabled": DECISION_PERSISTENCE_ENABLED,
            "permission_grants_applied": PERMISSION_GRANTS_APPLIED,
            "production_activation_enabled": PRODUCTION_ACTIVATION_ENABLED,
        }


def empty_permission_review() -> dict[str, Any]:
    """Return the honest runtime state before a Market selection exists."""

    return {
        "schema": "home-center.module-permission-review-status.v1",
        "status": "no-pending-review",
        "review": None,
        "decision_persistence_enabled": DECISION_PERSISTENCE_ENABLED,
        "permission_grants_applied": PERMISSION_GRANTS_APPLIED,
        "production_activation_enabled": PRODUCTION_ACTIVATION_ENABLED,
    }


def _review_identity(document: dict[str, Any]) -> str:
    canonical = json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def build_module_permission_review(request: dict[str, Any]) -> ModulePermissionReview:
    """Create a deterministic authority preview without granting or persisting it."""

    try:
        plan = plan_module_admission(request)
    except ModuleAdmissionError as exc:
        code = "review_complexity_rejected" if exc.code == "planner_permissions_rejected" else "review_admission_rejected"
        raise ModulePermissionReviewError(code) from exc

    selected = {(item.module_id, item.version): item for item in plan.install_order}
    manifests: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in request["candidates"]:
        key = (candidate["module"]["id"], candidate["module"]["version"])
        if key in selected:
            manifests[key] = candidate
    if set(manifests) != set(selected):
        raise ModulePermissionReviewError("review_candidate_binding_rejected")

    reviewed_modules: list[ReviewedModule] = []
    declarations: dict[str, set[str]] = {}
    actions: dict[str, list[PermissionAction]] = {}
    action_modules: dict[str, set[str]] = {}
    for step in plan.install_order:
        manifest = manifests[(step.module_id, step.version)]
        module_permissions = tuple(sorted(manifest["permissions"]))
        reviewed_modules.append(
            ReviewedModule(step.module_id, step.version, step.artifact_sha256, module_permissions)
        )
        for permission in module_permissions:
            declarations.setdefault(permission, set()).add(step.module_id)
        for action in manifest["actions"]:
            permission = action["permission"]
            actions.setdefault(permission, []).append(
                PermissionAction(step.module_id, action["id"], action["risk"])
            )
            action_modules.setdefault(permission, set()).add(step.module_id)

    if set(declarations) != set(plan.requested_permissions):
        raise ModulePermissionReviewError("review_permission_binding_rejected")
    if len(declarations) > MAX_REVIEW_PERMISSIONS or sum(len(value) for value in actions.values()) > MAX_REVIEW_ACTIONS:
        raise ModulePermissionReviewError("review_complexity_rejected")

    reviewed_permissions: list[ReviewedPermission] = []
    for permission_id in sorted(declarations):
        permission_actions = tuple(sorted(actions.get(permission_id, [])))
        declaring_modules = declarations[permission_id]
        if action_modules.get(permission_id, set()) != declaring_modules:
            risk = "unclassified"
        else:
            risk = max(
                (action.risk for action in permission_actions),
                key=lambda value: RISK_ORDER[value],
            )
        reviewed_permissions.append(
            ReviewedPermission(
                permission_id,
                risk,
                tuple(sorted(declaring_modules)),
                permission_actions,
            )
        )

    provisional = ModulePermissionReview(
        review_id="sha256:" + "0" * 64,
        requested_modules=plan.requested_modules,
        modules=tuple(reviewed_modules),
        permissions=tuple(reviewed_permissions),
    )
    document = provisional.to_dict()
    document.pop("review_id")
    return ModulePermissionReview(
        review_id=_review_identity(document),
        requested_modules=provisional.requested_modules,
        modules=provisional.modules,
        permissions=provisional.permissions,
    )


def load_and_build_module_permission_review(payload: bytes) -> ModulePermissionReview:
    try:
        request = load_admission_request(payload)
    except ModuleAdmissionError as exc:
        raise ModulePermissionReviewError("review_request_rejected") from exc
    return build_module_permission_review(request)
