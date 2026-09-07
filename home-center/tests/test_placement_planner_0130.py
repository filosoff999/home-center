from __future__ import annotations

import json
import sys
import unittest
import uuid
from pathlib import Path

from home_center.core.intent_engine import (
    IntentKind,
    IntentModule,
    IntentPlan,
    IntentPlanState,
    IntentRequest,
    IntentStep,
)
from home_center.placement_planner import PlacementDecision, PlacementPlannerError, attach_placement, placement_for


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema_validator import validate  # noqa: E402

GIB = 1024**3


def request(kind: IntentKind, parameters: dict[str, object]) -> IntentRequest:
    return IntentRequest(
        intent_id=str(uuid.uuid4()),
        idempotency_key="placement-test-0001",
        correlation_id="placement-test-0001",
        actor="local-admin:admin",
        reason="validate deterministic placement planning",
        kind=kind,
        target_id="target-a",
        parameters=parameters,
    )


def node(
    node_id: str,
    name: str,
    *,
    cpu: int = 8,
    memory_gib: int = 16,
    free_gib: int = 500,
    status: str = "ready",
) -> dict[str, object]:
    return {
        "node_id": node_id,
        "name": name,
        "role": "leader" if name == "dc01" else "standby",
        "status": status,
        "observed_at": "2026-09-07T13:00:00Z",
        "cpu_count": cpu,
        "memory_bytes": memory_gib * GIB,
        "root_storage": {
            "total_bytes": 2000 * GIB,
            "used_bytes": 500 * GIB,
            "free_bytes": free_gib * GIB,
        },
        "capabilities": ["inventory.v1"],
    }


def snapshot(nodes: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": "home-center.resource-snapshot.v1",
        "cluster_id": "hm-dm-production",
        "state": "ready",
        "planning_ready": True,
        "production_mutation_enabled": False,
        "totals": {},
        "nodes": nodes,
    }


class PlacementPlanner0130Tests(unittest.TestCase):
    def test_storage_selects_greatest_relative_free_space_headroom(self) -> None:
        intent = request(
            IntentKind.STORAGE_SHARE_CREATE,
            {"capacity_gib": 100, "protocol": "smb", "high_availability": False, "backup_enabled": False},
        )
        decision = placement_for(
            intent,
            snapshot(
                [
                    node("hm-dm-dc01", "dc01", free_gib=300),
                    node("hm-dm-dc02", "dc02", free_gib=900),
                ]
            ),
        )
        self.assertEqual(decision.strategy, "storage-headroom-v1")
        self.assertEqual(decision.node_ids, ("hm-dm-dc02",))

    def test_ha_storage_selects_two_distinct_eligible_nodes_in_rank_order(self) -> None:
        intent = request(
            IntentKind.STORAGE_SHARE_CREATE,
            {"capacity_gib": 200, "protocol": "smb", "high_availability": True, "backup_enabled": True},
        )
        decision = placement_for(
            intent,
            snapshot(
                [
                    node("node-c", "dc03", free_gib=250),
                    node("node-a", "dc01", free_gib=1000),
                    node("node-b", "dc02", free_gib=700),
                ]
            ),
        )
        self.assertEqual(decision.node_ids, ("node-a", "node-b"))
        self.assertTrue(decision.high_availability)

    def test_virtualization_balances_weakest_relative_headroom(self) -> None:
        intent = request(
            IntentKind.VIRTUALIZATION_WORKLOAD_CREATE,
            {"runtime": "vm", "vcpu": 4, "memory_mib": 8192, "disk_gib": 100, "high_availability": False},
        )
        decision = placement_for(
            intent,
            snapshot(
                [
                    node("hm-dm-dc01", "dc01", cpu=16, memory_gib=10, free_gib=1000),
                    node("hm-dm-dc02", "dc02", cpu=8, memory_gib=32, free_gib=500),
                ]
            ),
        )
        self.assertEqual(decision.strategy, "balanced-headroom-v1")
        self.assertEqual(decision.node_ids, ("hm-dm-dc02",))

    def test_equal_scores_use_stable_name_then_id_tie_breaker(self) -> None:
        intent = request(
            IntentKind.VIRTUALIZATION_WORKLOAD_CREATE,
            {"runtime": "lxc", "vcpu": 2, "memory_mib": 4096, "disk_gib": 50, "high_availability": False},
        )
        decision = placement_for(
            intent,
            snapshot(
                [
                    node("node-z", "node-b", cpu=8, memory_gib=16, free_gib=500),
                    node("node-a", "node-a", cpu=8, memory_gib=16, free_gib=500),
                ]
            ),
        )
        self.assertEqual(decision.node_ids, ("node-a",))

    def test_placement_decision_is_closed_non_reserving_metadata(self) -> None:
        decision = PlacementDecision(
            strategy="balanced-headroom-v1",
            node_ids=("hm-dm-dc01",),
            high_availability=False,
        )
        value = decision.to_dict()
        self.assertFalse(value["reservation_created"])
        self.assertFalse(value["production_mutation_enabled"])
        schema = json.loads((ROOT / "contracts/intents/placement.v1.schema.json").read_text(encoding="utf-8"))
        validate(schema, value)

    def test_attach_placement_changes_only_target_plan_step(self) -> None:
        intent_id = str(uuid.uuid4())
        check = IntentStep(
            sequence=1,
            module=IntentModule.VIRTUALIZATION_MANAGER,
            action="virtualization.capacity.check.plan.v1",
            target_id="minecraft-server",
            input={"runtime": "lxc", "vcpu": 4, "memory_mib": 8192, "disk_gib": 100, "high_availability": False},
            execution_requires_approval=False,
        )
        create = IntentStep(
            sequence=2,
            module=IntentModule.VIRTUALIZATION_MANAGER,
            action="virtualization.workload.create.plan.v1",
            target_id="minecraft-server",
            input={"runtime": "lxc", "vcpu": 4, "memory_mib": 8192, "disk_gib": 100, "high_availability": False},
            execution_requires_approval=True,
        )
        plan = IntentPlan(
            intent_id=intent_id,
            kind=IntentKind.VIRTUALIZATION_WORKLOAD_CREATE,
            target_id="minecraft-server",
            state=IntentPlanState.PLANNED,
            code="intent_planned",
            steps=(check, create),
        )
        placed = attach_placement(
            plan,
            PlacementDecision(
                strategy="balanced-headroom-v1",
                node_ids=("hm-dm-dc02",),
                high_availability=False,
            ),
        )
        self.assertNotIn("placement", placed.steps[0].input)
        self.assertEqual(placed.steps[1].input["placement"]["node_ids"], ("hm-dm-dc02",))
        self.assertFalse(placed.steps[1].input["placement"]["reservation_created"])
        self.assertFalse(placed.production_execution_enabled)

    def test_insufficient_eligible_nodes_fails_closed(self) -> None:
        intent = request(
            IntentKind.STORAGE_SHARE_CREATE,
            {"capacity_gib": 900, "protocol": "smb", "high_availability": True, "backup_enabled": False},
        )
        with self.assertRaisesRegex(PlacementPlannerError, "placement_capacity_unavailable"):
            placement_for(
                intent,
                snapshot(
                    [
                        node("hm-dm-dc01", "dc01", free_gib=1000),
                        node("hm-dm-dc02", "dc02", free_gib=500),
                    ]
                ),
            )


if __name__ == "__main__":
    unittest.main()
