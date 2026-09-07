from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from home_center.resource_snapshot import ResourceSnapshotError, build_resource_snapshot


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema_validator import validate  # noqa: E402


def node(
    *,
    node_id: str,
    name: str,
    role: str,
    address: str,
    status: str = "ready",
    cpu_count: int = 4,
    memory_bytes: int = 8 * 1024**3,
    total_bytes: int = 1000,
    used_bytes: int = 400,
    free_bytes: int = 500,
) -> dict[str, object]:
    return {
        "node_id": node_id,
        "name": name,
        "role": role,
        "address": address,
        "status": status,
        "capabilities": {
            "schema": "home-center.node-capability.v1",
            "observed_at": "2026-09-07T13:00:00Z",
            "node": {
                "id": node_id,
                "name": name,
                "role": role,
                "address": address,
                "machine_identity_hash": "a" * 24,
            },
            "operating_system": {"id": "ubuntu", "version": "26.04", "kernel": "test", "architecture": "x86_64"},
            "hardware": {"cpu_count": cpu_count, "memory_bytes": memory_bytes},
            "storage": {
                "root": {
                    "total_bytes": total_bytes,
                    "used_bytes": used_bytes,
                    "free_bytes": free_bytes,
                }
            },
            "services": {},
            "capabilities": ["health.v1", "inventory.v1"],
        },
        "last_seen": "2026-09-07T13:00:00Z",
        "updated_at": "2026-09-07T13:00:00Z",
    }


class ResourceSnapshot0130Tests(unittest.TestCase):
    def test_two_ready_nodes_produce_deterministic_closed_capacity_snapshot(self) -> None:
        dc02 = node(
            node_id="hm-dm-dc02",
            name="dc02",
            role="standby",
            address="192.168.10.253",
            cpu_count=8,
            memory_bytes=16 * 1024**3,
            total_bytes=2000,
            used_bytes=500,
            free_bytes=1400,
        )
        dc01 = node(
            node_id="hm-dm-dc01",
            name="dc01",
            role="leader",
            address="192.168.10.254",
            cpu_count=4,
            memory_bytes=8 * 1024**3,
            total_bytes=1000,
            used_bytes=400,
            free_bytes=500,
        )
        snapshot = build_resource_snapshot(cluster_id="hm-dm-production", expected_nodes=2, nodes=[dc02, dc01])
        self.assertEqual(snapshot["state"], "ready")
        self.assertTrue(snapshot["planning_ready"])
        self.assertFalse(snapshot["production_mutation_enabled"])
        self.assertEqual([item["name"] for item in snapshot["nodes"]], ["dc01", "dc02"])
        self.assertEqual(snapshot["totals"]["cpu_count"], 12)
        self.assertEqual(snapshot["totals"]["memory_bytes"], 24 * 1024**3)
        self.assertEqual(snapshot["totals"]["root_storage_total_bytes"], 3000)
        self.assertEqual(snapshot["totals"]["root_storage_free_bytes"], 1900)

        schema = json.loads((ROOT / "contracts/resources/resource-snapshot.v1.schema.json").read_text(encoding="utf-8"))
        validate(schema, snapshot)

    def test_missing_or_unreachable_node_is_degraded_and_not_planning_ready(self) -> None:
        only = node(
            node_id="hm-dm-dc01",
            name="dc01",
            role="leader",
            address="192.168.10.254",
            status="unreachable",
        )
        snapshot = build_resource_snapshot(cluster_id="hm-dm-production", expected_nodes=2, nodes=[only])
        self.assertEqual(snapshot["state"], "degraded")
        self.assertFalse(snapshot["planning_ready"])
        self.assertEqual(snapshot["totals"]["observed_nodes"], 1)
        self.assertEqual(snapshot["totals"]["ready_nodes"], 0)

    def test_persisted_identity_drift_fails_closed(self) -> None:
        value = node(
            node_id="hm-dm-dc01",
            name="dc01",
            role="leader",
            address="192.168.10.254",
        )
        value["capabilities"]["node"]["id"] = "hm-dm-dc02"  # type: ignore[index]
        with self.assertRaisesRegex(ResourceSnapshotError, "node_identity_mismatch"):
            build_resource_snapshot(cluster_id="hm-dm-production", expected_nodes=2, nodes=[value])

    def test_inconsistent_capacity_and_unknown_status_fail_closed(self) -> None:
        inconsistent = node(
            node_id="hm-dm-dc01",
            name="dc01",
            role="leader",
            address="192.168.10.254",
            total_bytes=100,
            used_bytes=80,
            free_bytes=40,
        )
        with self.assertRaisesRegex(ResourceSnapshotError, "inconsistent_storage_capacity"):
            build_resource_snapshot(cluster_id="hm-dm-production", expected_nodes=2, nodes=[inconsistent])

        unknown = node(
            node_id="hm-dm-dc01",
            name="dc01",
            role="leader",
            address="192.168.10.254",
            status="maybe",
        )
        with self.assertRaisesRegex(ResourceSnapshotError, "invalid_node_status"):
            build_resource_snapshot(cluster_id="hm-dm-production", expected_nodes=2, nodes=[unknown])

    def test_snapshot_does_not_publish_addresses_machine_identity_or_services(self) -> None:
        source = node(
            node_id="hm-dm-dc01",
            name="dc01",
            role="leader",
            address="192.168.10.254",
        )
        source["capabilities"]["services"] = {"ssh.service": "active"}  # type: ignore[index]
        snapshot = build_resource_snapshot(cluster_id="hm-dm-production", expected_nodes=1, nodes=[source])
        encoded = json.dumps(snapshot, sort_keys=True)
        self.assertNotIn("192.168.10.254", encoded)
        self.assertNotIn("machine_identity_hash", encoded)
        self.assertNotIn("ssh.service", encoded)


if __name__ == "__main__":
    unittest.main()
