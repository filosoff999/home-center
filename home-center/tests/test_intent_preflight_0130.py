from __future__ import annotations

import unittest
import uuid

from home_center.core.intent_engine import IntentKind, IntentRequest
from home_center.intent_preflight import IntentPreflightError, resource_preflight


GIB = 1024**3
MIB = 1024**2


def request(kind: IntentKind, parameters: dict[str, object]) -> IntentRequest:
    return IntentRequest(
        intent_id=str(uuid.uuid4()),
        idempotency_key="preflight-test-0001",
        correlation_id="preflight-test-0001",
        actor="local-admin:admin",
        reason="validate trusted resource preflight",
        kind=kind,
        target_id="target-a",
        parameters=parameters,
    )


def node(*, cpu: int = 8, memory_gib: int = 16, free_gib: int = 500, status: str = "ready") -> dict[str, object]:
    return {
        "node_id": "hm-dm-node",
        "name": "node",
        "role": "leader",
        "status": status,
        "observed_at": "2026-09-07T13:00:00Z",
        "cpu_count": cpu,
        "memory_bytes": memory_gib * GIB,
        "root_storage": {"total_bytes": 1000 * GIB, "used_bytes": 100 * GIB, "free_bytes": free_gib * GIB},
        "capabilities": ["inventory.v1"],
    }


def snapshot(nodes: list[dict[str, object]], *, planning_ready: bool = True) -> dict[str, object]:
    return {
        "schema": "home-center.resource-snapshot.v1",
        "cluster_id": "hm-dm-production",
        "state": "ready" if planning_ready else "degraded",
        "planning_ready": planning_ready,
        "production_mutation_enabled": False,
        "totals": {
            "observed_nodes": len(nodes),
            "expected_nodes": 2,
            "ready_nodes": sum(item["status"] == "ready" for item in nodes),
            "cpu_count": sum(int(item["cpu_count"]) for item in nodes),
            "memory_bytes": sum(int(item["memory_bytes"]) for item in nodes),
            "root_storage_total_bytes": sum(int(item["root_storage"]["total_bytes"]) for item in nodes),  # type: ignore[index]
            "root_storage_used_bytes": sum(int(item["root_storage"]["used_bytes"]) for item in nodes),  # type: ignore[index]
            "root_storage_free_bytes": sum(int(item["root_storage"]["free_bytes"]) for item in nodes),  # type: ignore[index]
        },
        "nodes": nodes,
    }


class IntentPreflight0130Tests(unittest.TestCase):
    def test_degraded_cluster_blocks_resource_intents_before_capacity_math(self) -> None:
        value = request(
            IntentKind.STORAGE_SHARE_CREATE,
            {"capacity_gib": 100, "protocol": "smb", "high_availability": False, "backup_enabled": False},
        )
        blockers = resource_preflight(value, snapshot([node()], planning_ready=False))
        self.assertEqual(blockers, ("cluster_resources_not_ready",))

    def test_storage_ha_requires_capacity_on_two_ready_nodes(self) -> None:
        value = request(
            IntentKind.STORAGE_SHARE_CREATE,
            {"capacity_gib": 200, "protocol": "smb", "high_availability": True, "backup_enabled": True},
        )
        blockers = resource_preflight(value, snapshot([node(free_gib=500), node(free_gib=100)]))
        self.assertEqual(blockers, ("insufficient_storage_capacity",))
        self.assertEqual(resource_preflight(value, snapshot([node(free_gib=500), node(free_gib=500)])), ())

    def test_virtualization_reports_deterministic_dimension_blockers(self) -> None:
        value = request(
            IntentKind.VIRTUALIZATION_WORKLOAD_CREATE,
            {"runtime": "vm", "vcpu": 16, "memory_mib": 32768, "disk_gib": 600, "high_availability": False},
        )
        blockers = resource_preflight(value, snapshot([node(cpu=8, memory_gib=16, free_gib=500), node(cpu=4, memory_gib=8, free_gib=100)]))
        self.assertEqual(
            blockers,
            ("insufficient_cpu_capacity", "insufficient_memory_capacity", "insufficient_storage_capacity"),
        )

    def test_virtualization_requires_one_node_to_satisfy_all_dimensions(self) -> None:
        value = request(
            IntentKind.VIRTUALIZATION_WORKLOAD_CREATE,
            {"runtime": "lxc", "vcpu": 8, "memory_mib": 16384, "disk_gib": 100, "high_availability": False},
        )
        cpu_node = node(cpu=8, memory_gib=8, free_gib=500)
        memory_node = node(cpu=4, memory_gib=16, free_gib=500)
        self.assertEqual(
            resource_preflight(value, snapshot([cpu_node, memory_node])),
            ("insufficient_combined_capacity",),
        )

    def test_snapshot_mutation_boundary_drift_is_rejected(self) -> None:
        value = request(
            IntentKind.STORAGE_SHARE_CREATE,
            {"capacity_gib": 10, "protocol": "smb", "high_availability": False, "backup_enabled": False},
        )
        facts = snapshot([node()])
        facts["production_mutation_enabled"] = True
        with self.assertRaisesRegex(IntentPreflightError, "resource_snapshot_mutation_boundary_rejected"):
            resource_preflight(value, facts)


if __name__ == "__main__":
    unittest.main()
