from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center.inventory import collect  # noqa: E402
from schema_validator import ValidationError, validate  # noqa: E402


class ContractTests(unittest.TestCase):
    def load(self, relative: str) -> dict:
        return json.loads((ROOT / "contracts" / relative).read_text(encoding="utf-8"))

    def test_every_json_contract_is_versioned_and_closed_at_root(self) -> None:
        files = sorted((ROOT / "contracts").rglob("*.json"))
        self.assertGreaterEqual(len(files), 10)
        for path in files:
            value = json.loads(path.read_text(encoding="utf-8"))
            if path.name.endswith("openapi.json"):
                self.assertEqual(value["openapi"], "3.1.0")
                continue
            self.assertEqual(value["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertRegex(value["$id"], r"\.v[1-9][0-9]*\.schema\.json$")
            self.assertFalse(value.get("additionalProperties", True), path)

    def test_runtime_capability_matches_contract(self) -> None:
        schema = self.load("capabilities/node-capability.v1.schema.json")
        value = collect("hm-dm-test", "test-node", "standby", "192.168.10.250")
        validate(schema, value)

    def test_external_access_contracts_are_closed_and_non_secret(self) -> None:
        status = self.load("external-access/external-access-status.v1.schema.json")
        validate(
            status,
            {
                "schema": "home-center.external-access-status.v1",
                "configured_enabled": False,
                "effective_enabled": False,
                "mode": "trusted-reverse-proxy",
                "public_hostname": None,
                "trusted_proxy_count": 0,
                "gateway_configuration": "operator-managed",
                "health_path": "/external/healthz",
                "blockers": ["disabled_by_configuration"],
            },
        )
        with self.assertRaises(ValidationError):
            validate(status, {"schema": "home-center.external-access-status.v1", "router_password": "secret"})

        health = self.load("external-access/external-health.v1.schema.json")
        validate(health, {"schema": "home-center.external-health.v1", "status": "ok"})
        with self.assertRaises(ValidationError):
            validate(health, {"schema": "home-center.external-health.v1", "status": "ok", "node_id": "dc01"})

    def test_hmdm_profile_matches_contract_and_preserves_domain(self) -> None:
        schema = self.load("deployment-profiles/deployment-profile.v1.schema.json")
        profile = json.loads((ROOT / "deploy/profiles/hm-dm-two-node.v1.json").read_text(encoding="utf-8"))
        validate(schema, profile)
        self.assertEqual(profile["spec"]["domain"]["sid"], "S-1-5-21-483832520-828804035-215000592")
        self.assertTrue(profile["spec"]["domain"]["preserve_existing"])
        self.assertFalse(profile["spec"]["domain"]["provision_new_domain"])
        self.assertFalse(profile["spec"]["placement"]["automatic_failover"])
        self.assertEqual({item["name"] for item in profile["spec"]["nodes"]}, {"dc01", "dc02"})

    def test_capability_contract_rejects_identity_drift(self) -> None:
        schema = self.load("capabilities/node-capability.v1.schema.json")
        value = collect("hm-dm-test", "test-node", "standby", "192.168.10.250")
        value["node"]["machine_identity_hash"] = "not-a-hash"
        with self.assertRaises(ValidationError):
            validate(schema, value)

    def test_drain_contract_requires_destructive_wipe_separation(self) -> None:
        schema = self.load("cluster/drain.v1.schema.json")
        value = {
            "schema": "home-center.drain-plan.v1", "operation_id": "dd831779-3737-451c-8bbf-b80bb56bfb11",
            "node_id": "hm-dm-dc02", "state": "blocked",
            "preflight": {"quorum_safe": False, "mandatory_services_safe": True, "replication_healthy": True, "block_reasons": ["no-witness"]},
            "relocations": [], "membership_cleanup": {}, "destructive_wipe": False, "created_at": "2026-09-05T20:00:00Z", "updated_at": "2026-09-05T20:00:00Z"
        }
        validate(schema, value)
        value["destructive_wipe"] = True
        with self.assertRaises(ValidationError):
            validate(schema, value)


if __name__ == "__main__":
    unittest.main()
