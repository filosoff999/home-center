"""Closed, deterministic Home Center 0.13 release-qualification boundary.

The verifier proves that one authenticated API plan is exactly reproducible from
the trusted resource snapshot and remains bound through placement, reservation,
and provider preflight.  It has no execution, persistence, network, or mutation
surface.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping

from .core.intent_engine import IntentPlanState, IntentRequest
from .intent_preflight import IntentPreflightError, resource_preflight
from .intent_service import IntentAuthorizationError, IntentPlanningService
from .placement_planner import PlacementPlannerError, placement_for
from .provider_framework import (
    ProviderAdapterDescriptor,
    ProviderFrameworkError,
    ProviderOperationPlan,
    ProviderPlanState,
    prepare_provider_operation,
)
from .reservation_scheduler import ReservationDecision, ReservationLedger, ReservationSchedulerError
from .util import canonical_json


QUALIFICATION_CHECKS = (
    "intent_core_binding",
    "api_plan_binding",
    "resource_snapshot_preflight",
    "deterministic_placement",
    "active_reservation_binding",
    "provider_boundary_binding",
    "execution_disabled",
)


class ReleaseQualificationError(ValueError):
    """Stable fail-closed code for a rejected qualification chain."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ReleaseQualificationReport:
    qualification_id: str
    intent_id: str
    kind: str
    target_id: str
    api_plan_sha256: str
    resource_snapshot_sha256: str
    placement_sha256: str
    reservation_id: str
    provider_plan_sha256: str
    provider_id: str
    provider_operation: str
    node_ids: tuple[str, ...]
    checks: tuple[str, ...] = QUALIFICATION_CHECKS
    schema: str = field(default="home-center.release-qualification.v1", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "release": "0.13.0",
            "qualification_id": self.qualification_id,
            "intent_id": self.intent_id,
            "kind": self.kind,
            "target_id": self.target_id,
            "api_plan_sha256": self.api_plan_sha256,
            "resource_snapshot_sha256": self.resource_snapshot_sha256,
            "placement_sha256": self.placement_sha256,
            "reservation_id": self.reservation_id,
            "provider_plan_sha256": self.provider_plan_sha256,
            "provider_id": self.provider_id,
            "provider_operation": self.provider_operation,
            "node_ids": list(self.node_ids),
            "checks": list(self.checks),
            "state": "qualified",
            "execution_ticket_created": False,
            "provider_execution_enabled": False,
            "production_execution_enabled": False,
            "production_mutation_enabled": False,
        }


def qualify_release_chain(
    *,
    request: IntentRequest,
    api_plan: Mapping[str, Any],
    resource_snapshot: Mapping[str, Any],
    reservation: ReservationDecision,
    provider: ProviderAdapterDescriptor,
    provider_plan: ProviderOperationPlan,
    now_epoch: int,
) -> ReleaseQualificationReport:
    """Qualify an exact plan-only chain or reject it without side effects."""

    if not isinstance(request, IntentRequest):
        raise ReleaseQualificationError("qualification_request_rejected")
    if not isinstance(api_plan, Mapping):
        raise ReleaseQualificationError("qualification_api_plan_rejected")
    if not isinstance(resource_snapshot, Mapping):
        raise ReleaseQualificationError("qualification_snapshot_rejected")
    if not isinstance(reservation, ReservationDecision):
        raise ReleaseQualificationError("qualification_reservation_rejected")
    if not isinstance(provider, ProviderAdapterDescriptor) or not isinstance(provider_plan, ProviderOperationPlan):
        raise ReleaseQualificationError("qualification_provider_rejected")
    if not isinstance(now_epoch, int) or isinstance(now_epoch, bool) or now_epoch <= 0:
        raise ReleaseQualificationError("qualification_clock_rejected")

    try:
        blockers = resource_preflight(request, resource_snapshot)
    except IntentPreflightError as exc:
        raise ReleaseQualificationError("qualification_snapshot_rejected") from exc
    if blockers:
        raise ReleaseQualificationError("qualification_preflight_blocked")

    try:
        placement = placement_for(request, resource_snapshot)
        expected_plan = IntentPlanningService(
            resource_snapshot_provider=lambda: resource_snapshot
        ).plan(actor=request.actor, request=request)
    except (IntentAuthorizationError, PlacementPlannerError) as exc:
        raise ReleaseQualificationError("qualification_intent_chain_rejected") from exc
    if expected_plan.state is not IntentPlanState.PLANNED or expected_plan.production_execution_enabled:
        raise ReleaseQualificationError("qualification_intent_chain_rejected")
    expected_api_plan = expected_plan.to_dict()
    if dict(api_plan) != expected_api_plan:
        raise ReleaseQualificationError("qualification_api_plan_rejected")

    if reservation.expires_at_epoch <= now_epoch or reservation.created_at_epoch > now_epoch:
        raise ReleaseQualificationError("qualification_reservation_expired")
    try:
        expected_reservation = ReservationLedger().reserve(
            request=request,
            plan=expected_plan,
            resource_snapshot=resource_snapshot,
            now_epoch=reservation.created_at_epoch,
            lease_seconds=reservation.lease_seconds,
        )
    except ReservationSchedulerError as exc:
        raise ReleaseQualificationError("qualification_reservation_rejected") from exc
    reservation_value = reservation.to_dict()
    if reservation_value != expected_reservation.to_dict():
        raise ReleaseQualificationError("qualification_reservation_rejected")

    try:
        expected_provider_plan = prepare_provider_operation(
            request=request,
            plan=expected_plan,
            reservation=reservation,
            resource_snapshot=resource_snapshot,
            provider=provider,
            now_epoch=now_epoch,
        )
    except ProviderFrameworkError as exc:
        raise ReleaseQualificationError("qualification_provider_rejected") from exc
    provider_value = provider_plan.to_dict()
    if provider_value != expected_provider_plan.to_dict():
        raise ReleaseQualificationError("qualification_provider_rejected")
    if expected_provider_plan.state is not ProviderPlanState.PLANNED:
        raise ReleaseQualificationError("qualification_provider_blocked")

    if (
        expected_api_plan.get("production_execution_enabled") is not False
        or resource_snapshot.get("production_mutation_enabled") is not False
        or placement.to_dict().get("production_mutation_enabled") is not False
        or reservation_value.get("durable") is not False
        or reservation_value.get("provider_execution_enabled") is not False
        or reservation_value.get("production_mutation_enabled") is not False
        or provider_value.get("execution_ticket_created") is not False
        or provider_value.get("provider_execution_enabled") is not False
        or provider_value.get("production_mutation_enabled") is not False
    ):
        raise ReleaseQualificationError("qualification_execution_boundary_rejected")

    material = {
        "release": "0.13.0",
        "request_sha256": _digest(request.to_dict()),
        "api_plan_sha256": _digest(expected_api_plan),
        "resource_snapshot_sha256": _digest(resource_snapshot),
        "placement_sha256": _digest(placement.to_dict()),
        "reservation_id": reservation.reservation_id,
        "provider_plan_sha256": _digest(provider_value),
        "checks": list(QUALIFICATION_CHECKS),
    }
    return ReleaseQualificationReport(
        qualification_id=_digest(material),
        intent_id=request.intent_id,
        kind=request.kind.value,
        target_id=request.target_id,
        api_plan_sha256=material["api_plan_sha256"],
        resource_snapshot_sha256=material["resource_snapshot_sha256"],
        placement_sha256=material["placement_sha256"],
        reservation_id=reservation.reservation_id,
        provider_plan_sha256=material["provider_plan_sha256"],
        provider_id=provider.provider_id,
        provider_operation=provider_plan.operation.value,
        node_ids=provider_plan.node_ids,
    )
