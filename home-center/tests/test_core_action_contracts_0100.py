from __future__ import annotations

import json
import sys
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from home_center.core.action_contracts import (  # noqa: E402
    ConfigurationChangePlanInput,
    NodeDrainPlanInput,
    PolicyAuthorizePlanInput,
    ServiceStartPlanInput,
    UpgradeReleasePlanInput,
    parse_action_input,
)
from home_center.core.configuration_engine import ConfigurationEngine  # noqa: E402
from home_center.core.contracts import CoreCommand, CoreContractError  # noqa: E402
from home_center.core.node_manager import NodeDescriptor, NodeManager, NodeState  # noqa: E402
from home_center.core.policy_engine import AccessMode, PolicyEngine, PolicyRule  # noqa: E402
from home_center.core.service_manager import ServiceDefinition, ServiceManager  # noqa: E402
from home_center.core.upgrade_engine import ReleaseIdentity, UpgradeEngine  # noqa: E402
from schema_validator import ValidationError, validate  # noqa: E402


class CoreActionContractTests0100(unittest.TestCase):
    def schema(self, kind: str, name: str) -> dict:
        return json.loads(
            (ROOT / "contracts/core" / kind / f"{name}.v1.schema.json").read_text(encoding="utf-8")
        )

    def command(self, module: str, action: str, target_id: str, input_value: dict) -> CoreCommand:
        return CoreCommand.from_mapping(
            {
                "schema": "home-center.core-command.v1",
                "operation_id": str(uuid.uuid4()),
                "idempotency_key": "module-contract-0100",
                "correlation_id": "module-contract-0100",
                "actor": "local-admin",
                "reason": "validate typed module contract",
                "module": module,
                "action": action,
                "target_id": target_id,
                "input": input_value,
                "mode": "plan",
            }
        )

    def test_all_admitted_action_inputs_are_typed_and_closed(self) -> None:
        current = {"version": "0.9.2", "revision": "1" * 40, "artifact_sha256": "2" * 64}
        target = {"version": "0.10.0", "revision": "3" * 40, "artifact_sha256": "4" * 64}
        cases = (
            (
                "node-manager",
                "node.drain.plan.v1",
                "test-node",
                "node-drain-plan-input",
                {
                    "schema": "home-center.node-drain-plan-input.v1",
                    "quorum_safe": True,
                    "mandatory_services_safe": True,
                },
                NodeDrainPlanInput,
            ),
            (
                "upgrade-engine",
                "upgrade.release.plan.v1",
                "cluster",
                "upgrade-release-plan-input",
                {
                    "schema": "home-center.upgrade-release-plan-input.v1",
                    "current": current,
                    "target": target,
                },
                UpgradeReleasePlanInput,
            ),
            (
                "configuration-engine",
                "configuration.change.plan.v1",
                "cluster",
                "configuration-change-plan-input",
                {
                    "schema": "home-center.configuration-change-plan-input.v1",
                    "key": "network.hostname",
                },
                ConfigurationChangePlanInput,
            ),
            (
                "service-manager",
                "service.start.plan.v1",
                "test-node",
                "service-start-plan-input",
                {
                    "schema": "home-center.service-start-plan-input.v1",
                    "service_id": "home-center.service",
                },
                ServiceStartPlanInput,
            ),
            (
                "policy-engine",
                "policy.authorize.plan.v1",
                "local-admin",
                "policy-authorize-plan-input",
                {
                    "schema": "home-center.policy-authorize-plan-input.v1",
                    "requested_module": "node-manager",
                    "requested_action": "node.drain.plan.v1",
                    "requested_mode": "plan",
                    "permissions": ["node.plan"],
                },
                PolicyAuthorizePlanInput,
            ),
        )
        for module, action, target_id, schema_name, input_value, expected_type in cases:
            with self.subTest(action=action):
                validate(self.schema("actions", schema_name), input_value)
                command = self.command(module, action, target_id, input_value)
                self.assertIsInstance(parse_action_input(command), expected_type)
                self.assertEqual(command.to_dict()["input"], input_value)
                invalid = dict(input_value)
                invalid["ambient_authority"] = True
                with self.assertRaises(ValidationError):
                    validate(self.schema("actions", schema_name), invalid)
                with self.assertRaises(CoreContractError):
                    parse_action_input(self.command(module, action, target_id, invalid))

    def test_unknown_action_is_rejected(self) -> None:
        command = self.command(
            "node-manager",
            "node.unknown.plan.v1",
            "test-node",
            {"schema": "home-center.node-drain-plan-input.v1"},
        )
        with self.assertRaisesRegex(CoreContractError, "unsupported_action"):
            parse_action_input(command)

    def test_all_plan_outputs_match_closed_schemas(self) -> None:
        node = NodeDescriptor.create(
            node_id="test-node",
            hostname="test-node.local",
            state=NodeState.ACTIVE,
            capabilities={"core.api"},
        )
        validate(
            self.schema("plans", "node-lifecycle-plan"),
            NodeManager([node]).plan_drain(
                "test-node", quorum_safe=False, mandatory_services_safe=True
            ).to_dict(),
        )
        current = ReleaseIdentity("0.9.2", "1" * 40, "2" * 64)
        target = ReleaseIdentity("0.10.0", "3" * 40, "4" * 64)
        validate(
            self.schema("plans", "upgrade-plan"),
            UpgradeEngine().plan(current=current, target=target).to_dict(),
        )
        validate(
            self.schema("plans", "configuration-plan"),
            ConfigurationEngine().plan_change("network.hostname").to_dict(),
        )
        validate(
            self.schema("plans", "service-plan"),
            ServiceManager(
                [ServiceDefinition("database"), ServiceDefinition("api", ("database",))]
            ).plan_start("api").to_dict(),
        )
        validate(
            self.schema("plans", "policy-decision"),
            PolicyEngine(
                [PolicyRule("node.plan", "node-manager", "node.drain.plan.v1", AccessMode.PLAN)]
            ).authorize(
                module="node-manager",
                action="node.drain.plan.v1",
                mode=AccessMode.PLAN,
                permissions={"node.plan"},
            ).to_dict(),
        )


if __name__ == "__main__":
    unittest.main()
