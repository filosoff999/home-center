"""Authenticated, side-effect-free intent planning service for Home Center 0.13."""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Final

from .core.intent_engine import IntentEngine, IntentKind, IntentPlan, IntentRequest
from .core.policy_engine import AccessMode, PolicyEngine, PolicyRule


LOCAL_ADMIN_ACTOR: Final = re.compile(r"^local-admin:[a-z][a-z0-9._-]{2,63}$")
AD_ADMIN_ACTOR: Final = re.compile(r"^ad-admin:[a-z0-9][a-z0-9._-]{0,63}@[A-Z0-9][A-Z0-9.-]{2,254}$")

PERMISSION_BY_KIND = MappingProxyType(
    {
        IntentKind.NODE_DRAIN: "intent.node.plan",
        IntentKind.STORAGE_SHARE_CREATE: "intent.storage.plan",
        IntentKind.VIRTUALIZATION_WORKLOAD_CREATE: "intent.virtualization.plan",
        IntentKind.MODULE_INSTALL: "intent.module.plan",
    }
)


class IntentAuthorizationError(PermissionError):
    code = "intent_permission_denied"


def _default_policy_engine() -> PolicyEngine:
    return PolicyEngine(
        [
            PolicyRule(
                permission=permission,
                module="intent-engine",
                action=f"intent.{kind.value}.plan.v1",
                mode=AccessMode.PLAN,
            )
            for kind, permission in PERMISSION_BY_KIND.items()
        ]
    )


class IntentPlanningService:
    """Bind authenticated administrator identities to the plan-only Intent Engine."""

    def __init__(self, engine: IntentEngine | None = None) -> None:
        self._engine = engine or IntentEngine(_default_policy_engine())

    @staticmethod
    def actor_is_administrator(actor: str) -> bool:
        return bool(LOCAL_ADMIN_ACTOR.fullmatch(actor) or AD_ADMIN_ACTOR.fullmatch(actor))

    def plan(self, *, actor: str, request: IntentRequest) -> IntentPlan:
        if not self.actor_is_administrator(actor):
            raise IntentAuthorizationError("administrator actor required")
        if request.actor != actor:
            raise IntentAuthorizationError("request actor does not match authenticated actor")
        permission = PERMISSION_BY_KIND.get(request.kind)
        if permission is None:
            raise IntentAuthorizationError("intent permission unavailable")
        return self._engine.compile(request, permissions={permission})
