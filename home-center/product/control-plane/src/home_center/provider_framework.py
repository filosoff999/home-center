"""Closed provider-adapter preflight boundary for Home Center 0.13.

The framework binds an exact active reservation to one typed provider operation.
It deliberately exposes no provider execution method and performs no network,
filesystem, child-process, hypervisor, or storage mutation.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Mapping

from .core.intent_engine import IntentKind, IntentPlan, IntentPlanState, IntentRequest
from .placement_planner import PlacementPlannerError, placement_for
from .reservation_scheduler import ReservationDecision
from .util import canonical_json


IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.:-]{1,127}$")
CAPABILITY = re.compile(r"^[a-z][a-z0-9.-]{1,126}\.v[1-9][0-9]*$")


class ProviderFrameworkError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProviderKind(StrEnum):
    PROXMOX = "proxmox"
    FILESYSTEM = "filesystem"


class ProviderOperation(StrEnum):
    STORAGE_SHARE_CREATE = "storage.share.create.v1"
    VIRTUAL_MACHINE_CREATE = "virtualization.vm.create.v1"
    LXC_CREATE = "virtualization.lxc.create.v1"


class ProviderPlanState(StrEnum):
    PLANNED = "planned"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ProviderAdapterDescriptor:
    provider_id: str
    kind: ProviderKind
    healthy: bool
    managed_node_ids: tuple[str, ...]
    capabilities: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        provider_id: str,
        kind: ProviderKind,
        healthy: bool,
        managed_node_ids: Iterable[str],
        capabilities: Iterable[str],
    ) -> "ProviderAdapterDescriptor":
        if IDENTIFIER.fullmatch(provider_id) is None or not isinstance(kind, ProviderKind) or not isinstance(healthy, bool):
            raise ProviderFrameworkError("provider_descriptor_rejected")
        nodes = tuple(sorted(set(managed_node_ids)))
        if not nodes or len(nodes) > 64 or any(IDENTIFIER.fullmatch(item) is None for item in nodes):
            raise ProviderFrameworkError("provider_nodes_rejected")
        normalized_capabilities = tuple(sorted(set(capabilities)))
        if not normalized_capabilities or any(CAPABILITY.fullmatch(item) is None for item in normalized_capabilities):
            raise ProviderFrameworkError("provider_capabilities_rejected")
        return cls(
            provider_id=provider_id,
            kind=kind,
            healthy=healthy,
            managed_node_ids=nodes,
            capabilities=normalized_capabilities,
        )


@dataclass(frozen=True, slots=True)
class ProviderOperationPlan:
    provider_id: str
    provider_kind: ProviderKind
    operation: ProviderOperation
    capability: str
    intent_id: str
    target_id: str
    reservation_id: str
    request_binding_sha256: str
    resource_snapshot_sha256: str
    node_ids: tuple[str, ...]
    state: ProviderPlanState
    blockers: tuple[str, ...]
    provider_execution_enabled: bool = False
    production_mutation_enabled: bool = False
    schema: str = field(default="home-center.provider-operation-plan.v1", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "provider_id": self.provider_id,
            "provider_kind": self.provider_kind.value,
            "operation": self.operation.value,
            "capability": self.capability,
            "intent_id": self.intent_id,
            "target_id": self.target_id,
            "reservation_id": self.reservation_id,
            "request_binding_sha256": self.request_binding_sha256,
            "resource_snapshot_sha256": self.resource_snapshot_sha256,
            "node_ids": list(self.node_ids),
            "state": self.state.value,
            "blockers": list(self.blockers),
            "execution_ticket_created": False,
            "provider_execution_enabled": self.provider_execution_enabled,
            "production_mutation_enabled": self.production_mutation_enabled,
        }


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _placement(plan: IntentPlan) -> Mapping[str, Any]:
    if plan.state is not IntentPlanState.PLANNED or plan.production_execution_enabled:
        raise ProviderFrameworkError("provider_plan_not_planned")
    target_action = {
        IntentKind.STORAGE_SHARE_CREATE: "storage.share.create.plan.v1",
        IntentKind.VIRTUALIZATION_WORKLOAD_CREATE: "virtualization.workload.create.plan.v1",
    }.get(plan.kind)
    if target_action is None:
        raise ProviderFrameworkError("provider_intent_unsupported")
    matches = [step for step in plan.steps if step.action == target_action]
    if len(matches) != 1:
        raise ProviderFrameworkError("provider_placement_missing")
    value = matches[0].input.get("placement")
    if not isinstance(value, Mapping):
        raise ProviderFrameworkError("provider_placement_missing")
    return value


def _operation_for(request: IntentRequest) -> tuple[ProviderKind, ProviderOperation, str]:
    if request.kind is IntentKind.STORAGE_SHARE_CREATE:
        return ProviderKind.FILESYSTEM, ProviderOperation.STORAGE_SHARE_CREATE, "storage.share.plan.v1"
    if request.kind is IntentKind.VIRTUALIZATION_WORKLOAD_CREATE:
        runtime = request.parameters.get("runtime")
        if runtime == "vm":
            return ProviderKind.PROXMOX, ProviderOperation.VIRTUAL_MACHINE_CREATE, "compute.vm.plan.v1"
        if runtime == "lxc":
            return ProviderKind.PROXMOX, ProviderOperation.LXC_CREATE, "compute.lxc.plan.v1"
        raise ProviderFrameworkError("provider_runtime_rejected")
    raise ProviderFrameworkError("provider_intent_unsupported")


def prepare_provider_operation(
    *,
    request: IntentRequest,
    plan: IntentPlan,
    reservation: ReservationDecision,
    resource_snapshot: Mapping[str, Any],
    provider: ProviderAdapterDescriptor,
    now_epoch: int,
) -> ProviderOperationPlan:
    """Validate the full preflight→placement→reservation chain and return an inert provider plan."""

    if not isinstance(now_epoch, int) or isinstance(now_epoch, bool) or now_epoch <= 0:
        raise ProviderFrameworkError("provider_clock_rejected")
    if not isinstance(reservation, ReservationDecision):
        raise ProviderFrameworkError("provider_reservation_rejected")
    if reservation.expires_at_epoch <= now_epoch:
        raise ProviderFrameworkError("provider_reservation_expired")
    if (
        reservation.intent_id != request.intent_id
        or reservation.idempotency_key != request.idempotency_key
        or plan.intent_id != request.intent_id
        or plan.kind is not request.kind
        or plan.target_id != request.target_id
    ):
        raise ProviderFrameworkError("provider_request_binding_rejected")

    placement = _placement(plan)
    try:
        expected_placement = placement_for(request, resource_snapshot).to_dict()
    except PlacementPlannerError as exc:
        raise ProviderFrameworkError("provider_snapshot_or_placement_rejected") from exc
    normalized_placement = {
        key: list(value) if isinstance(value, tuple) else value for key, value in placement.items()
    }
    if normalized_placement != expected_placement:
        raise ProviderFrameworkError("provider_snapshot_or_placement_rejected")

    snapshot_digest = _digest(resource_snapshot)
    binding = _digest(
        {
            "request": request.to_dict(),
            "plan": plan.to_dict(),
            "resource_snapshot_sha256": snapshot_digest,
            "lease_seconds": reservation.lease_seconds,
        }
    )
    raw_node_ids = placement.get("node_ids")
    if not isinstance(raw_node_ids, (list, tuple)):
        raise ProviderFrameworkError("provider_placement_rejected")
    node_ids = tuple(str(item) for item in raw_node_ids)
    if (
        reservation.request_binding_sha256 != binding
        or reservation.resource_snapshot_sha256 != snapshot_digest
        or reservation.node_ids != node_ids
    ):
        raise ProviderFrameworkError("provider_reservation_binding_rejected")

    expected_kind, operation, required_capability = _operation_for(request)
    blockers: list[str] = []
    if provider.kind is not expected_kind:
        blockers.append("provider_kind_mismatch")
    if not provider.healthy:
        blockers.append("provider_not_healthy")
    if required_capability not in provider.capabilities:
        blockers.append("provider_capability_unsupported")
    if any(node_id not in provider.managed_node_ids for node_id in node_ids):
        blockers.append("provider_node_scope_mismatch")

    return ProviderOperationPlan(
        provider_id=provider.provider_id,
        provider_kind=provider.kind,
        operation=operation,
        capability=required_capability,
        intent_id=request.intent_id,
        target_id=request.target_id,
        reservation_id=reservation.reservation_id,
        request_binding_sha256=binding,
        resource_snapshot_sha256=snapshot_digest,
        node_ids=node_ids,
        state=ProviderPlanState.BLOCKED if blockers else ProviderPlanState.PLANNED,
        blockers=tuple(blockers),
    )
