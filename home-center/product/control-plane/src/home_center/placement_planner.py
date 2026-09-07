"""Deterministic, provider-neutral placement planning for Home Center 0.13."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .core.intent_engine import IntentKind, IntentPlan, IntentStep, IntentRequest


GIB = 1024**3
MIB = 1024**2
MAX_NODES = 64


class PlacementPlannerError(ValueError):
    """A stable rejection code for malformed or inconsistent placement facts."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _mapping(value: object, *, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlacementPlannerError(code)
    return value


def _integer(value: object, *, minimum: int, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise PlacementPlannerError(code)
    return value


def _headroom_bps(total: int, required: int) -> int:
    if total < required or total <= 0:
        return -1
    return ((total - required) * 10_000) // total


def _ready_nodes(snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if snapshot.get("schema") != "home-center.resource-snapshot.v1":
        raise PlacementPlannerError("unsupported_resource_snapshot")
    if snapshot.get("planning_ready") is not True:
        raise PlacementPlannerError("resource_snapshot_not_ready")
    if snapshot.get("production_mutation_enabled") is not False:
        raise PlacementPlannerError("resource_snapshot_mutation_boundary_rejected")
    raw_nodes = snapshot.get("nodes")
    if not isinstance(raw_nodes, list) or not 1 <= len(raw_nodes) <= MAX_NODES:
        raise PlacementPlannerError("invalid_resource_nodes")
    ready: list[Mapping[str, Any]] = []
    for value in raw_nodes:
        node = _mapping(value, code="invalid_resource_node")
        if node.get("status") == "ready":
            ready.append(node)
        elif node.get("status") != "unreachable":
            raise PlacementPlannerError("invalid_resource_node_status")
    return ready


def _identity(node: Mapping[str, Any]) -> tuple[str, str]:
    node_id = node.get("node_id")
    name = node.get("name")
    if not isinstance(node_id, str) or not 3 <= len(node_id) <= 64:
        raise PlacementPlannerError("invalid_placement_node_id")
    if not isinstance(name, str) or not 1 <= len(name) <= 63:
        raise PlacementPlannerError("invalid_placement_node_name")
    return node_id, name


def _capacity(node: Mapping[str, Any]) -> tuple[int, int, int]:
    cpu = _integer(node.get("cpu_count"), minimum=1, code="invalid_resource_cpu")
    memory = _integer(node.get("memory_bytes"), minimum=1, code="invalid_resource_memory")
    root_storage = _mapping(node.get("root_storage"), code="invalid_resource_storage")
    free_storage = _integer(root_storage.get("free_bytes"), minimum=0, code="invalid_resource_storage_free")
    return cpu, memory, free_storage


@dataclass(frozen=True, slots=True)
class PlacementDecision:
    strategy: str
    node_ids: tuple[str, ...]
    high_availability: bool
    resource_snapshot_schema: str = "home-center.resource-snapshot.v1"
    schema: str = "home-center.placement.v1"

    def __post_init__(self) -> None:
        if self.strategy not in {"storage-headroom-v1", "balanced-headroom-v1"}:
            raise PlacementPlannerError("invalid_placement_strategy")
        if not isinstance(self.high_availability, bool):
            raise PlacementPlannerError("invalid_placement_ha_flag")
        expected = 2 if self.high_availability else 1
        if len(self.node_ids) != expected or len(set(self.node_ids)) != len(self.node_ids):
            raise PlacementPlannerError("invalid_placement_node_count")
        for node_id in self.node_ids:
            if not isinstance(node_id, str) or not 3 <= len(node_id) <= 64:
                raise PlacementPlannerError("invalid_placement_node_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "strategy": self.strategy,
            "node_ids": list(self.node_ids),
            "high_availability": self.high_availability,
            "resource_snapshot_schema": self.resource_snapshot_schema,
            "reservation_created": False,
            "production_mutation_enabled": False,
        }


def placement_for(request: IntentRequest, snapshot: Mapping[str, Any]) -> PlacementDecision:
    """Select eligible node(s) without reserving or mutating any resource."""

    if request.kind not in {IntentKind.STORAGE_SHARE_CREATE, IntentKind.VIRTUALIZATION_WORKLOAD_CREATE}:
        raise PlacementPlannerError("unsupported_placement_intent")
    ready = _ready_nodes(snapshot)
    high_availability = request.parameters["high_availability"]
    if not isinstance(high_availability, bool):
        raise PlacementPlannerError("invalid_placement_ha_flag")
    required_nodes = 2 if high_availability else 1

    ranked: list[tuple[tuple[int, ...], str, str]] = []
    if request.kind is IntentKind.STORAGE_SHARE_CREATE:
        required_storage = _integer(
            request.parameters["capacity_gib"], minimum=1, code="invalid_placement_storage_capacity"
        ) * GIB
        for node in ready:
            _, _, free_storage = _capacity(node)
            score = _headroom_bps(free_storage, required_storage)
            if score < 0:
                continue
            node_id, name = _identity(node)
            ranked.append(((-score,), name.casefold(), node_id))
        strategy = "storage-headroom-v1"
    else:
        required_cpu = _integer(request.parameters["vcpu"], minimum=1, code="invalid_placement_vcpu")
        required_memory = _integer(
            request.parameters["memory_mib"], minimum=1, code="invalid_placement_memory"
        ) * MIB
        required_storage = _integer(
            request.parameters["disk_gib"], minimum=1, code="invalid_placement_disk"
        ) * GIB
        for node in ready:
            cpu, memory, free_storage = _capacity(node)
            cpu_headroom = _headroom_bps(cpu, required_cpu)
            memory_headroom = _headroom_bps(memory, required_memory)
            storage_headroom = _headroom_bps(free_storage, required_storage)
            if min(cpu_headroom, memory_headroom, storage_headroom) < 0:
                continue
            weakest = min(cpu_headroom, memory_headroom, storage_headroom)
            total = cpu_headroom + memory_headroom + storage_headroom
            node_id, name = _identity(node)
            ranked.append(((-weakest, -total), name.casefold(), node_id))
        strategy = "balanced-headroom-v1"

    ranked.sort()
    if len(ranked) < required_nodes:
        raise PlacementPlannerError("placement_capacity_unavailable")
    selected = tuple(item[2] for item in ranked[:required_nodes])
    return PlacementDecision(strategy=strategy, node_ids=selected, high_availability=high_availability)


def attach_placement(plan: IntentPlan, decision: PlacementDecision) -> IntentPlan:
    """Return the same plan with placement metadata attached to the mutating-plan step only."""

    target_action = {
        IntentKind.STORAGE_SHARE_CREATE: "storage.share.create.plan.v1",
        IntentKind.VIRTUALIZATION_WORKLOAD_CREATE: "virtualization.workload.create.plan.v1",
    }.get(plan.kind)
    if target_action is None:
        raise PlacementPlannerError("unsupported_placement_plan")

    replacement: list[IntentStep] = []
    attached = False
    for step in plan.steps:
        if step.action != target_action:
            replacement.append(step)
            continue
        if attached:
            raise PlacementPlannerError("duplicate_placement_target_step")
        payload = dict(step.input)
        if "placement" in payload:
            raise PlacementPlannerError("placement_already_present")
        payload["placement"] = decision.to_dict()
        replacement.append(
            IntentStep(
                sequence=step.sequence,
                module=step.module,
                action=step.action,
                target_id=step.target_id,
                input=payload,
                execution_requires_approval=step.execution_requires_approval,
            )
        )
        attached = True
    if not attached:
        raise PlacementPlannerError("placement_target_step_missing")

    return IntentPlan(
        intent_id=plan.intent_id,
        kind=plan.kind,
        target_id=plan.target_id,
        state=plan.state,
        code=plan.code,
        steps=tuple(replacement),
        blockers=plan.blockers,
        production_execution_enabled=False,
    )
