from __future__ import annotations

import unittest
import uuid

from home_center.core.intent_engine import IntentKind, IntentPlanState, IntentRequest
from home_center.intent_service import IntentPlanningService


GIB = 1024**3


def snapshot() -> dict[str, object]:
    return {
        "schema": "home-center.resource-snapshot.v1",
        "cluster_id": "hm-dm-production",
        "state": "ready",
        "planning_ready": True,
        "production_mutation_enabled": False,
        "totals": {},
        "nodes": [
            {
                "node_id": "hm-dm-dc01",
                "name": "dc01",
                "role": "leader",
                "status": "ready",
                "observed_at": "2026-09-07T13:00:00Z",
                "cpu_count": 8,
                "memory_bytes": 16 * GIB,
                "root_storage": {"total_bytes": 2000 * GIB, "used_bytes": 500 * GIB, "free_bytes": 700 * GIB},
                "capabilities": ["inventory.v1"],
            },
            {
                "node_id": "hm-dm-dc02",
                "name": "dc02",
                "role": "standby",
                "status": "ready",
                "observed_at": "2026-09-07T13:00:00Z",
                "cpu_count": 16,
                "memory_bytes": 32 * GIB,
                "root_storage": {"total_bytes": 2000 * GIB, "used_bytes": 400 * GIB, "free_bytes": 1200 * GIB},
                "capabilities": ["inventory.v1"],
            },
        ],
    }


def request(kind: IntentKind, target: str, parameters: dict[str, object]) -> IntentRequest:
    return IntentRequest(
        intent_id=str(uuid.uuid4()),
        idempotency_key="placement-service-0001",
        correlation_id="placement-service-0001",
        actor="local-admin:admin",
        reason="validate placement service integration",
        kind=kind,
        target_id=target,
        parameters=parameters,
    )


class PlacementService0130Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = IntentPlanningService(resource_snapshot_provider=snapshot)

    def test_storage_plan_api_shape_contains_non_reserving_ha_placement(self) -> None:
        intent = request(
            IntentKind.STORAGE_SHARE_CREATE,
            "family-share",
            {"capacity_gib": 200, "protocol": "smb", "high_availability": True, "backup_enabled": True},
        )
        plan = self.service.plan(actor="local-admin:admin", request=intent)
        self.assertEqual(plan.state, IntentPlanState.PLANNED)
        value = plan.to_dict()
        create = next(step for step in value["steps"] if step["action"] == "storage.share.create.plan.v1")
        placement = create["input"]["placement"]
        self.assertEqual(placement["schema"], "home-center.placement.v1")
        self.assertEqual(placement["strategy"], "storage-headroom-v1")
        self.assertEqual(placement["node_ids"], ["hm-dm-dc02", "hm-dm-dc01"])
        self.assertTrue(placement["high_availability"])
        self.assertFalse(placement["reservation_created"])
        self.assertFalse(placement["production_mutation_enabled"])

    def test_virtualization_plan_selects_single_best_balanced_node(self) -> None:
        intent = request(
            IntentKind.VIRTUALIZATION_WORKLOAD_CREATE,
            "minecraft-server",
            {"runtime": "lxc", "vcpu": 4, "memory_mib": 8192, "disk_gib": 100, "high_availability": False},
        )
        plan = self.service.plan(actor="local-admin:admin", request=intent)
        self.assertEqual(plan.state, IntentPlanState.PLANNED)
        value = plan.to_dict()
        create = next(step for step in value["steps"] if step["action"] == "virtualization.workload.create.plan.v1")
        placement = create["input"]["placement"]
        self.assertEqual(placement["strategy"], "balanced-headroom-v1")
        self.assertEqual(placement["node_ids"], ["hm-dm-dc02"])
        self.assertFalse(placement["reservation_created"])

    def test_non_resource_plan_has_no_placement_metadata(self) -> None:
        intent = request(
            IntentKind.MODULE_INSTALL,
            "storage-module",
            {"module_id": "storage", "version": "1.0.0", "permissions_acknowledged": True},
        )
        plan = IntentPlanningService().plan(actor="local-admin:admin", request=intent)
        self.assertEqual(plan.state, IntentPlanState.PLANNED)
        self.assertNotIn("placement", str(plan.to_dict()))


if __name__ == "__main__":
    unittest.main()
