"""Bounded in-memory reservation boundary for Home Center 0.13 planning.

Reservations prevent concurrent logical plans in one control-plane process from
silently claiming the same trusted capacity.  They are intentionally
non-durable and cannot call providers or execute infrastructure mutations.
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from typing import Any, Mapping

from .core.intent_engine import IntentKind, IntentPlan, IntentPlanState, IntentRequest
from .placement_planner import PlacementPlannerError, placement_for
from .util import canonical_json


GIB = 1024**3
MIB = 1024**2
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
MIN_LEASE_SECONDS = 5
MAX_LEASE_SECONDS = 900


class ReservationSchedulerError(ValueError):
    """Stable fail-closed code for reservation/scheduler boundary failures."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _integer(value: object, *, minimum: int, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ReservationSchedulerError(code)
    return value


def _placement_from_plan(plan: IntentPlan) -> Mapping[str, Any]:
    if plan.state is not IntentPlanState.PLANNED or plan.production_execution_enabled:
        raise ReservationSchedulerError("reservation_plan_not_planned")
    target_action = {
        IntentKind.STORAGE_SHARE_CREATE: "storage.share.create.plan.v1",
        IntentKind.VIRTUALIZATION_WORKLOAD_CREATE: "virtualization.workload.create.plan.v1",
    }.get(plan.kind)
    if target_action is None:
        raise ReservationSchedulerError("reservation_intent_unsupported")
    matches = [step for step in plan.steps if step.action == target_action]
    if len(matches) != 1:
        raise ReservationSchedulerError("reservation_placement_missing")
    placement = matches[0].input.get("placement")
    if not isinstance(placement, Mapping):
        raise ReservationSchedulerError("reservation_placement_missing")
    required = {
        "schema",
        "strategy",
        "node_ids",
        "high_availability",
        "resource_snapshot_schema",
        "reservation_created",
        "production_mutation_enabled",
    }
    if set(placement) != required:
        raise ReservationSchedulerError("reservation_placement_shape_rejected")
    if (
        placement.get("schema") != "home-center.placement.v1"
        or placement.get("resource_snapshot_schema") != "home-center.resource-snapshot.v1"
        or placement.get("reservation_created") is not False
        or placement.get("production_mutation_enabled") is not False
    ):
        raise ReservationSchedulerError("reservation_placement_boundary_rejected")
    return placement


def _snapshot_nodes(snapshot: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    if (
        snapshot.get("schema") != "home-center.resource-snapshot.v1"
        or snapshot.get("planning_ready") is not True
        or snapshot.get("production_mutation_enabled") is not False
    ):
        raise ReservationSchedulerError("reservation_snapshot_rejected")
    raw_nodes = snapshot.get("nodes")
    if not isinstance(raw_nodes, list) or not 1 <= len(raw_nodes) <= 64:
        raise ReservationSchedulerError("reservation_snapshot_rejected")
    result: dict[str, Mapping[str, Any]] = {}
    for raw in raw_nodes:
        if not isinstance(raw, Mapping) or raw.get("status") not in {"ready", "unreachable"}:
            raise ReservationSchedulerError("reservation_snapshot_rejected")
        node_id = raw.get("node_id")
        if not isinstance(node_id, str) or not 3 <= len(node_id) <= 64 or node_id in result:
            raise ReservationSchedulerError("reservation_snapshot_rejected")
        result[node_id] = raw
    return result


def _claim_for(request: IntentRequest, node_ids: tuple[str, ...]) -> dict[str, dict[str, int]]:
    if request.kind is IntentKind.STORAGE_SHARE_CREATE:
        storage = _integer(request.parameters.get("capacity_gib"), minimum=1, code="reservation_capacity_rejected") * GIB
        claim = {"cpu": 0, "memory_bytes": 0, "storage_bytes": storage}
    elif request.kind is IntentKind.VIRTUALIZATION_WORKLOAD_CREATE:
        claim = {
            "cpu": _integer(request.parameters.get("vcpu"), minimum=1, code="reservation_capacity_rejected"),
            "memory_bytes": _integer(request.parameters.get("memory_mib"), minimum=1, code="reservation_capacity_rejected") * MIB,
            "storage_bytes": _integer(request.parameters.get("disk_gib"), minimum=1, code="reservation_capacity_rejected") * GIB,
        }
    else:
        raise ReservationSchedulerError("reservation_intent_unsupported")
    return {node_id: dict(claim) for node_id in node_ids}


def _node_capacity(node: Mapping[str, Any]) -> dict[str, int]:
    root = node.get("root_storage")
    if not isinstance(root, Mapping):
        raise ReservationSchedulerError("reservation_snapshot_rejected")
    return {
        "cpu": _integer(node.get("cpu_count"), minimum=1, code="reservation_snapshot_rejected"),
        "memory_bytes": _integer(node.get("memory_bytes"), minimum=1, code="reservation_snapshot_rejected"),
        "storage_bytes": _integer(root.get("free_bytes"), minimum=0, code="reservation_snapshot_rejected"),
    }


@dataclass(frozen=True, slots=True)
class ReservationDecision:
    reservation_id: str
    request_binding_sha256: str
    resource_snapshot_sha256: str
    intent_id: str
    idempotency_key: str
    node_ids: tuple[str, ...]
    claims: Mapping[str, Mapping[str, int]]
    created_at_epoch: int
    expires_at_epoch: int
    lease_seconds: int
    schema: str = "home-center.reservation.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "reservation_id": self.reservation_id,
            "request_binding_sha256": self.request_binding_sha256,
            "resource_snapshot_sha256": self.resource_snapshot_sha256,
            "intent_id": self.intent_id,
            "idempotency_key": self.idempotency_key,
            "node_ids": list(self.node_ids),
            "claims": {node: dict(values) for node, values in sorted(self.claims.items())},
            "state": "reserved",
            "created_at_epoch": self.created_at_epoch,
            "expires_at_epoch": self.expires_at_epoch,
            "lease_seconds": self.lease_seconds,
            "durable": False,
            "provider_execution_enabled": False,
            "production_mutation_enabled": False,
        }


@dataclass(slots=True)
class _Entry:
    binding: str
    decision: ReservationDecision


class ReservationLedger:
    """Single-process scheduler boundary with deterministic idempotency/conflict rules."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_intent: dict[str, _Entry] = {}

    def _purge(self, now_epoch: int) -> None:
        expired = [key for key, entry in self._by_intent.items() if entry.decision.expires_at_epoch <= now_epoch]
        for key in expired:
            del self._by_intent[key]

    def _used(self) -> dict[str, dict[str, int]]:
        used: dict[str, dict[str, int]] = {}
        for entry in self._by_intent.values():
            for node_id, claim in entry.decision.claims.items():
                totals = used.setdefault(node_id, {"cpu": 0, "memory_bytes": 0, "storage_bytes": 0})
                for dimension in totals:
                    totals[dimension] += claim[dimension]
        return used

    def reserve(
        self,
        *,
        request: IntentRequest,
        plan: IntentPlan,
        resource_snapshot: Mapping[str, Any],
        now_epoch: int,
        lease_seconds: int = 60,
    ) -> ReservationDecision:
        if not isinstance(now_epoch, int) or isinstance(now_epoch, bool) or now_epoch <= 0:
            raise ReservationSchedulerError("reservation_clock_rejected")
        if (
            not isinstance(lease_seconds, int)
            or isinstance(lease_seconds, bool)
            or not MIN_LEASE_SECONDS <= lease_seconds <= MAX_LEASE_SECONDS
        ):
            raise ReservationSchedulerError("reservation_lease_rejected")
        if plan.intent_id != request.intent_id or plan.kind is not request.kind or plan.target_id != request.target_id:
            raise ReservationSchedulerError("reservation_plan_binding_rejected")

        placement = _placement_from_plan(plan)
        try:
            expected = placement_for(request, resource_snapshot).to_dict()
        except PlacementPlannerError as exc:
            raise ReservationSchedulerError("reservation_snapshot_or_placement_rejected") from exc
        normalized_placement = {
            key: list(value) if isinstance(value, tuple) else value for key, value in placement.items()
        }
        if expected != normalized_placement:
            raise ReservationSchedulerError("reservation_snapshot_or_placement_rejected")

        raw_node_ids = placement.get("node_ids")
        if not isinstance(raw_node_ids, (list, tuple)) or not 1 <= len(raw_node_ids) <= 2:
            raise ReservationSchedulerError("reservation_placement_rejected")
        node_ids = tuple(str(item) for item in raw_node_ids)
        if len(set(node_ids)) != len(node_ids):
            raise ReservationSchedulerError("reservation_placement_rejected")
        snapshot_nodes = _snapshot_nodes(resource_snapshot)
        if any(node_id not in snapshot_nodes or snapshot_nodes[node_id].get("status") != "ready" for node_id in node_ids):
            raise ReservationSchedulerError("reservation_snapshot_or_placement_rejected")

        claims = _claim_for(request, node_ids)
        snapshot_digest = _digest(resource_snapshot)
        binding_material = {
            "request": request.to_dict(),
            "plan": plan.to_dict(),
            "resource_snapshot_sha256": snapshot_digest,
            "lease_seconds": lease_seconds,
        }
        binding = _digest(binding_material)

        with self._lock:
            self._purge(now_epoch)
            existing = self._by_intent.get(request.intent_id)
            if existing is not None:
                if existing.binding != binding:
                    raise ReservationSchedulerError("reservation_conflict")
                return existing.decision

            used = self._used()
            for node_id, claim in claims.items():
                capacity = _node_capacity(snapshot_nodes[node_id])
                already = used.get(node_id, {"cpu": 0, "memory_bytes": 0, "storage_bytes": 0})
                if any(already[dimension] + claim[dimension] > capacity[dimension] for dimension in capacity):
                    raise ReservationSchedulerError("reservation_capacity_unavailable")

            reservation_id = _digest(
                {
                    "request_binding_sha256": binding,
                    "intent_id": request.intent_id,
                    "created_at_epoch": now_epoch,
                }
            )
            decision = ReservationDecision(
                reservation_id=reservation_id,
                request_binding_sha256=binding,
                resource_snapshot_sha256=snapshot_digest,
                intent_id=request.intent_id,
                idempotency_key=request.idempotency_key,
                node_ids=node_ids,
                claims=claims,
                created_at_epoch=now_epoch,
                expires_at_epoch=now_epoch + lease_seconds,
                lease_seconds=lease_seconds,
            )
            self._by_intent[request.intent_id] = _Entry(binding=binding, decision=decision)
            return decision

    def release(self, reservation_id: str) -> dict[str, Any]:
        if not isinstance(reservation_id, str) or DIGEST.fullmatch(reservation_id) is None:
            raise ReservationSchedulerError("reservation_identity_rejected")
        with self._lock:
            for intent_id, entry in tuple(self._by_intent.items()):
                if entry.decision.reservation_id == reservation_id:
                    del self._by_intent[intent_id]
                    return {
                        "schema": "home-center.reservation-release.v1",
                        "reservation_id": reservation_id,
                        "state": "released",
                        "provider_execution_enabled": False,
                        "production_mutation_enabled": False,
                    }
        raise ReservationSchedulerError("reservation_not_found")
