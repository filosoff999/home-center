from __future__ import annotations

import unittest
import uuid

from home_center.core.intent_engine import IntentKind, IntentPlanState, IntentRequest
from home_center.intent_service import IntentAuthorizationError, IntentPlanningService


GIB = 1024**3


def trusted_snapshot(*, planning_ready: bool = True, free_gib: int = 1000) -> dict[str, object]:
    nodes = [
        {
            "node_id": "hm-dm-dc01",
            "name": "dc01",
            "role": "leader",
            "status": "ready",
            "observed_at": "2026-09-07T13:00:00Z",
            "cpu_count": 16,
            "memory_bytes": 64 * GIB,
            "root_storage": {"total_bytes": 2000 * GIB, "used_bytes": 500 * GIB, "free_bytes": free_gib * GIB},
            "capabilities": ["inventory.v1"],
        },
        {
            "node_id": "hm-dm-dc02",
            "name": "dc02",
            "role": "standby",
            "status": "ready",
            "observed_at": "2026-09-07T13:00:00Z",
            "cpu_count": 16,
            "memory_bytes": 64 * GIB,
            "root_storage": {"total_bytes": 2000 * GIB, "used_bytes": 500 * GIB, "free_bytes": free_gib * GIB},
            "capabilities": ["inventory.v1"],
        },
    ]
    return {
        "schema": "home-center.resource-snapshot.v1",
        "cluster_id": "hm-dm-production",
        "state": "ready" if planning_ready else "degraded",
        "planning_ready": planning_ready,
        "production_mutation_enabled": False,
        "totals": {
            "observed_nodes": 2,
            "expected_nodes": 2,
            "ready_nodes": 2,
            "cpu_count": 32,
            "memory_bytes": 128 * GIB,
            "root_storage_total_bytes": 4000 * GIB,
            "root_storage_used_bytes": 1000 * GIB,
            "root_storage_free_bytes": 2 * free_gib * GIB,
        },
        "nodes": nodes,
    }


class IntentPlanningService0130Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = IntentPlanningService(resource_snapshot_provider=trusted_snapshot)

    @staticmethod
    def request(*, actor: str, kind: IntentKind, target: str, parameters: dict[str, object]) -> IntentRequest:
        return IntentRequest(
            intent_id=str(uuid.uuid4()),
            idempotency_key="intent-service-0001",
            correlation_id="intent-service-0001",
            actor=actor,
            reason="validate authenticated intent service",
            kind=kind,
            target_id=target,
            parameters=parameters,
        )

    def test_local_and_ad_administrators_receive_only_kind_specific_plan_permission(self) -> None:
        local = self.request(
            actor="local-admin:admin",
            kind=IntentKind.STORAGE_SHARE_CREATE,
            target="family-share",
            parameters={
                "capacity_gib": 100,
                "protocol": "smb",
                "high_availability": False,
                "backup_enabled": False,
            },
        )
        local_plan = self.service.plan(actor="local-admin:admin", request=local)
        self.assertEqual(local_plan.state, IntentPlanState.PLANNED)

        ad_actor = "ad-admin:administrator@HM.DM"
        ad = self.request(
            actor=ad_actor,
            kind=IntentKind.MODULE_INSTALL,
            target="storage-module",
            parameters={"module_id": "storage", "version": "1.0.0", "permissions_acknowledged": True},
        )
        ad_plan = self.service.plan(actor=ad_actor, request=ad)
        self.assertEqual(ad_plan.state, IntentPlanState.PLANNED)

    def test_non_admin_and_actor_spoof_are_denied_before_engine_use(self) -> None:
        request = self.request(
            actor="local-admin:admin",
            kind=IntentKind.NODE_DRAIN,
            target="node-a",
            parameters={"quorum_safe": True, "mandatory_services_safe": True},
        )
        with self.assertRaises(IntentAuthorizationError):
            self.service.plan(actor="system:runtime", request=request)
        with self.assertRaises(IntentAuthorizationError):
            self.service.plan(actor="local-admin:other", request=request)

    def test_resource_intent_without_trusted_provider_fails_closed(self) -> None:
        service = IntentPlanningService()
        request = self.request(
            actor="local-admin:admin",
            kind=IntentKind.STORAGE_SHARE_CREATE,
            target="family-share",
            parameters={
                "capacity_gib": 10,
                "protocol": "smb",
                "high_availability": False,
                "backup_enabled": False,
            },
        )
        plan = service.plan(actor="local-admin:admin", request=request)
        self.assertEqual(plan.state, IntentPlanState.BLOCKED)
        self.assertEqual(plan.blockers, ("resource_snapshot_unavailable",))
        self.assertEqual(plan.steps, ())

    def test_resource_preflight_can_only_turn_valid_plan_into_blocked_plan(self) -> None:
        service = IntentPlanningService(resource_snapshot_provider=lambda: trusted_snapshot(free_gib=50))
        request = self.request(
            actor="local-admin:admin",
            kind=IntentKind.VIRTUALIZATION_WORKLOAD_CREATE,
            target="large-vm",
            parameters={
                "runtime": "vm",
                "vcpu": 32,
                "memory_mib": 131072,
                "disk_gib": 100,
                "high_availability": False,
            },
        )
        plan = service.plan(actor="local-admin:admin", request=request)
        self.assertEqual(plan.state, IntentPlanState.BLOCKED)
        self.assertEqual(
            plan.blockers,
            ("insufficient_cpu_capacity", "insufficient_memory_capacity", "insufficient_storage_capacity"),
        )
        self.assertEqual(plan.steps, ())

    def test_non_resource_intent_does_not_depend_on_resource_provider(self) -> None:
        service = IntentPlanningService()
        request = self.request(
            actor="local-admin:admin",
            kind=IntentKind.MODULE_INSTALL,
            target="storage-module",
            parameters={"module_id": "storage", "version": "1.0.0", "permissions_acknowledged": True},
        )
        plan = service.plan(actor="local-admin:admin", request=request)
        self.assertEqual(plan.state, IntentPlanState.PLANNED)


if __name__ == "__main__":
    unittest.main()
