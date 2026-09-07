"""Closed, deterministic authorization handoff for a blocked module lifecycle plan.

This module can prove that the authorization precondition is satisfied for one
exact plan.  It cannot consume acknowledgement evidence, persist lifecycle
state, place workloads, execute module actions, or activate anything in
production.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime
from typing import Any, Mapping

from .module_permission_acknowledgement import (
    ModulePermissionAcknowledgementError,
    render_module_permission_acknowledgement,
)
from .util import canonical_json


AUTHORIZATION_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
EXPECTED_BLOCKERS = (
    "authorization_contract_unavailable",
    "lifecycle_executor_unavailable",
    "placement_unresolved",
)
REMAINING_BLOCKERS = (
    "lifecycle_executor_unavailable",
    "placement_unresolved",
)


class ModuleLifecycleAuthorizationError(ValueError):
    """Stable, non-secret failure code for authorization binding failures."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _plan_digest(plan: Mapping[str, Any]) -> str:
    material = dict(plan)
    plan_id = material.pop("plan_id", None)
    if not isinstance(plan_id, str) or AUTHORIZATION_ID.fullmatch(plan_id) is None:
        raise ModuleLifecycleAuthorizationError("authorization_plan_identity_rejected")
    calculated = _digest(material)
    if not hmac.compare_digest(calculated, plan_id):
        raise ModuleLifecycleAuthorizationError("authorization_plan_identity_rejected")
    return plan_id


def _actor_binding(actor: str) -> str:
    if not isinstance(actor, str) or not 3 <= len(actor.encode("utf-8")) <= 320:
        raise ModuleLifecycleAuthorizationError("authorization_actor_rejected")
    return "sha256:" + hashlib.sha256(
        b"home-center.module-lifecycle-authorization.actor.v1\x00" + actor.encode("utf-8")
    ).hexdigest()


def authorize_module_install_lifecycle(
    plan: Mapping[str, Any],
    *,
    acknowledgement_record: Mapping[str, Any],
    actor: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Authorize one exact blocked plan without creating execution authority."""

    if not isinstance(plan, Mapping):
        raise ModuleLifecycleAuthorizationError("authorization_plan_rejected")
    if (
        plan.get("schema") != "home-center.module-install-lifecycle-plan.v1"
        or plan.get("status") != "blocked"
        or plan.get("operation") != "install"
        or plan.get("authorization_decisions_enabled") is not False
        or plan.get("acknowledgement_consumption_enabled") is not False
        or plan.get("lifecycle_persistence_enabled") is not False
        or plan.get("lifecycle_execution_enabled") is not False
        or plan.get("production_activation_enabled") is not False
    ):
        raise ModuleLifecycleAuthorizationError("authorization_plan_rejected")
    plan_id = _plan_digest(plan)

    blockers = plan.get("blockers")
    if not isinstance(blockers, list) or tuple(blockers) != EXPECTED_BLOCKERS:
        raise ModuleLifecycleAuthorizationError("authorization_preconditions_incomplete")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ModuleLifecycleAuthorizationError("authorization_preconditions_incomplete")
    if any(not isinstance(step, Mapping) or step.get("publication") is None for step in steps):
        raise ModuleLifecycleAuthorizationError("authorization_preconditions_incomplete")

    try:
        acknowledgement = render_module_permission_acknowledgement(
            dict(acknowledgement_record), now=now
        )
    except ModulePermissionAcknowledgementError as exc:
        raise ModuleLifecycleAuthorizationError("authorization_acknowledgement_rejected") from exc

    plan_ack = plan.get("acknowledgement")
    if not isinstance(plan_ack, Mapping):
        raise ModuleLifecycleAuthorizationError("authorization_acknowledgement_rejected")
    if (
        acknowledgement.get("status") != "recorded"
        or acknowledgement.get("lifecycle_handoff", {}).get("consumable") is not False
        or not hmac.compare_digest(str(acknowledgement.get("actor", "")), actor)
        or not hmac.compare_digest(str(acknowledgement.get("acknowledgement_id", "")), str(plan_ack.get("id", "")))
        or not hmac.compare_digest(str(acknowledgement.get("review_id", "")), str(plan.get("review_id", "")))
        or not hmac.compare_digest(str(acknowledgement.get("scope_id", "")), str(plan.get("scope_id", "")))
    ):
        raise ModuleLifecycleAuthorizationError("authorization_acknowledgement_rejected")

    publication_ids: list[str] = []
    for step in steps:
        publication = step["publication"]
        publication_id = publication.get("publication_id") if isinstance(publication, Mapping) else None
        if not isinstance(publication_id, str) or AUTHORIZATION_ID.fullmatch(publication_id) is None:
            raise ModuleLifecycleAuthorizationError("authorization_publication_rejected")
        publication_ids.append(publication_id)
    if len(set(publication_ids)) != len(publication_ids):
        raise ModuleLifecycleAuthorizationError("authorization_publication_rejected")

    document = {
        "schema": "home-center.module-lifecycle-authorization.v1",
        "plan_id": plan_id,
        "actor_binding_sha256": _actor_binding(actor),
        "acknowledgement_id": acknowledgement["acknowledgement_id"],
        "review_id": plan["review_id"],
        "scope_id": plan["scope_id"],
        "publication_ids": publication_ids,
        "decision": "authorized",
        "remaining_blockers": list(REMAINING_BLOCKERS),
        "acknowledgement_consumed": False,
        "lifecycle_persistence_enabled": False,
        "lifecycle_execution_enabled": False,
        "production_activation_enabled": False,
    }
    return {**document, "authorization_id": _digest(document)}
