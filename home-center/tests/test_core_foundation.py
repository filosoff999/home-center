from __future__ import annotations

import unittest
import uuid

from home_center.core.configuration_engine import (
    ConfigurationEngine,
    ConfigurationEngineError,
    ConfigurationLock,
)
from home_center.core.contracts import (
    CoreCommand,
    CoreContractError,
    CoreError,
    CoreModule,
    CoreResult,
    ResultState,
)
from home_center.core.node_manager import NodeDescriptor, NodeManager, NodeState
from home_center.core.policy_engine import AccessMode, PolicyEngine, PolicyRule
from home_center.core.service_manager import ServiceDefinition, ServiceManager, ServiceManagerError
from home_center.core.upgrade_engine import ReleaseIdentity, UpgradeEngine, UpgradeEngineError


class CoreFoundationTests(unittest.TestCase):
    def command(self, **overrides: object) -> dict[str, object]:
        value: dict[str, object] = {
            "schema": "home-center.core-command.v1",
            "operation_id": str(uuid.uuid4()),
            "idempotency_key": "core-test-0001",
            "correlation_id": "core-test-0001",
            "actor": "local-admin",
            "reason": "validate planner boundary",
            "module": "node-manager",
            "action": "node.drain.plan.v1",
            "target_id": "test-node",
            "input": {
                "schema": "home-center.node-drain-plan-input.v1",
                "quorum_safe": True,
                "mandatory_services_safe": True,
            },
            "mode": "plan",
        }
        value.update(overrides)
        return value

    def test_command_is_closed_and_plan_only(self) -> None:
        parsed = CoreCommand.from_mapping(self.command())
        self.assertEqual(parsed.mode, "plan")
        with self.assertRaises(CoreContractError):
            CoreCommand.from_mapping(self.command(mode="execute"))
        with self.assertRaises(CoreContractError):
            CoreCommand.from_mapping(self.command(secret="forbidden"))
        with self.assertRaises(CoreContractError):
            CoreCommand(
                operation_id=str(uuid.uuid4()),
                idempotency_key="core-test-0001",
                correlation_id="core-test-0001",
                actor="local-admin",
                reason="direct construction remains validated",
                module=CoreModule.NODE_MANAGER,
                action="node.drain.plan.v1",
                target_id="test-node",
                input={},
                mode="execute",
            )

    def test_result_and_error_runtime_envelopes_are_validated(self) -> None:
        operation_id = str(uuid.uuid4())
        result = CoreResult(
            operation_id=operation_id,
            module=CoreModule.NODE_MANAGER,
            target_id="test-node",
            state=ResultState.PLANNED,
            code="drain_planned",
        )
        self.assertEqual(result.operation_id, operation_id)
        CoreError("default_deny", "Operation denied", "core-test-0001")
        with self.assertRaises(CoreContractError):
            CoreResult(
                operation_id="not-a-uuid",
                module=CoreModule.NODE_MANAGER,
                target_id="test-node",
                state=ResultState.PLANNED,
                code="drain_planned",
            )
        with self.assertRaises(CoreContractError):
            CoreError("INVALID CODE", "Operation denied", "core-test-0001")

    def test_policy_is_exact_and_production_mutation_is_disabled(self) -> None:
        engine = PolicyEngine(
            [PolicyRule("node.plan", "node-manager", "node.drain.plan.v1", AccessMode.PLAN)]
        )
        self.assertTrue(
            engine.authorize(
                module="node-manager",
                action="node.drain.plan.v1",
                mode=AccessMode.PLAN,
                permissions={"node.plan"},
            ).allowed
        )
        self.assertFalse(
            engine.authorize(
                module="node-manager",
                action="node.drain.plan.v1",
                mode=AccessMode.MUTATE,
                permissions={"node.plan"},
            ).allowed
        )

    def test_node_drain_fails_closed_on_quorum(self) -> None:
        node = NodeDescriptor.create(
            node_id="test-node", hostname="test-node.local", state=NodeState.ACTIVE, capabilities={"core.api"}
        )
        plan = NodeManager([node]).plan_drain(
            "test-node", quorum_safe=False, mandatory_services_safe=True
        )
        self.assertEqual(plan.state, "blocked")
        self.assertIn("quorum_not_safe", plan.blockers)
        self.assertFalse(plan.production_activation_enabled)

    def test_configuration_lock_explains_blocker(self) -> None:
        engine = ConfigurationEngine(
            [ConfigurationLock("network.hostname", "domain-services", "identity is protected")]
        )
        plan = engine.plan_change("network.hostname")
        self.assertEqual(plan.state, "blocked")
        self.assertEqual(plan.blockers, ("locked_by:domain-services",))
        with self.assertRaises(ConfigurationEngineError):
            ConfigurationEngine(
                [ConfigurationLock("network.hostname", "invalid owner", "identity is protected")]
            )

    def test_service_plan_is_deterministic_and_rejects_cycles(self) -> None:
        engine = ServiceManager(
            [ServiceDefinition("database"), ServiceDefinition("api", ("database",))]
        )
        self.assertEqual(engine.plan_start("api").ordered_services, ("database", "api"))
        cyclic = ServiceManager([ServiceDefinition("a-service", ("b-service",)), ServiceDefinition("b-service", ("a-service",))])
        with self.assertRaises(ServiceManagerError):
            cyclic.plan_start("a-service")

    def test_upgrade_plan_requires_exact_newer_identity(self) -> None:
        current = ReleaseIdentity("0.9.2", "1" * 40, "2" * 64)
        target = ReleaseIdentity("0.10.0", "3" * 40, "4" * 64)
        plan = UpgradeEngine().plan(current=current, target=target)
        self.assertEqual(plan.steps[3:6], ("canary-secondary", "soak", "primary"))
        self.assertFalse(plan.production_activation_enabled)
        with self.assertRaises(UpgradeEngineError):
            UpgradeEngine().plan(current=target, target=current)


if __name__ == "__main__":
    unittest.main()
