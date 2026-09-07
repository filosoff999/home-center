from __future__ import annotations

import unittest
import uuid

from home_center.core.intent_engine import (
    IntentEngine,
    IntentEngineError,
    IntentKind,
    IntentPlanState,
    IntentRequest,
)
from home_center.core.policy_engine import AccessMode, PolicyEngine, PolicyRule


class IntentEngine0130Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = PolicyEngine(
            [
                PolicyRule("intent.node.plan", "intent-engine", "intent.node.drain.plan.v1", AccessMode.PLAN),
                PolicyRule(
                    "intent.storage.plan",
                    "intent-engine",
                    "intent.storage.share.create.plan.v1",
                    AccessMode.PLAN,
                ),
                PolicyRule(
                    "intent.virtualization.plan",
                    "intent-engine",
                    "intent.virtualization.workload.create.plan.v1",
                    AccessMode.PLAN,
                ),
                PolicyRule("intent.module.plan", "intent-engine", "intent.module.install.plan.v1", AccessMode.PLAN),
            ]
        )
        self.engine = IntentEngine(self.policy)

    def request(self, *, kind: IntentKind, target_id: str, parameters: dict[str, object]) -> IntentRequest:
        return IntentRequest(
            intent_id=str(uuid.uuid4()),
            idempotency_key="intent-test-0001",
            correlation_id="intent-test-0001",
            actor="local-admin",
            reason="validate 0.13 intent planner",
            kind=kind,
            target_id=target_id,
            parameters=parameters,
        )

    def test_node_drain_is_plan_only_and_deterministic(self) -> None:
        request = self.request(
            kind=IntentKind.NODE_DRAIN,
            target_id="node-a",
            parameters={"quorum_safe": True, "mandatory_services_safe": True},
        )
        plan = self.engine.compile(request, permissions={"intent.node.plan"})
        self.assertEqual(plan.state, IntentPlanState.PLANNED)
        self.assertEqual([step.action for step in plan.steps], ["node.drain.plan.v1"])
        self.assertTrue(plan.steps[0].execution_requires_approval)
        self.assertFalse(plan.production_execution_enabled)

    def test_unsafe_node_drain_fails_closed_before_step_generation(self) -> None:
        request = self.request(
            kind=IntentKind.NODE_DRAIN,
            target_id="node-a",
            parameters={"quorum_safe": False, "mandatory_services_safe": True},
        )
        plan = self.engine.compile(request, permissions={"intent.node.plan"})
        self.assertEqual(plan.state, IntentPlanState.BLOCKED)
        self.assertEqual(plan.blockers, ("quorum_not_safe",))
        self.assertEqual(plan.steps, ())

    def test_default_deny_blocks_intent(self) -> None:
        request = self.request(
            kind=IntentKind.STORAGE_SHARE_CREATE,
            target_id="family-share",
            parameters={
                "capacity_gib": 5120,
                "protocol": "smb",
                "high_availability": True,
                "backup_enabled": True,
            },
        )
        plan = self.engine.compile(request, permissions=set())
        self.assertEqual(plan.state, IntentPlanState.BLOCKED)
        self.assertEqual(plan.blockers, ("default_deny",))

    def test_storage_plan_adds_backup_step_only_when_requested(self) -> None:
        request = self.request(
            kind=IntentKind.STORAGE_SHARE_CREATE,
            target_id="family-share",
            parameters={
                "capacity_gib": 5120,
                "protocol": "SMB",
                "high_availability": True,
                "backup_enabled": True,
            },
        )
        plan = self.engine.compile(request, permissions={"intent.storage.plan"})
        self.assertEqual(
            [step.action for step in plan.steps],
            [
                "storage.capacity.check.plan.v1",
                "storage.share.create.plan.v1",
                "backup.policy.attach.plan.v1",
            ],
        )
        self.assertEqual([step.sequence for step in plan.steps], [1, 2, 3])

    def test_virtualization_plan_is_provider_neutral(self) -> None:
        request = self.request(
            kind=IntentKind.VIRTUALIZATION_WORKLOAD_CREATE,
            target_id="minecraft-server",
            parameters={
                "runtime": "lxc",
                "vcpu": 4,
                "memory_mib": 8192,
                "disk_gib": 100,
                "high_availability": False,
            },
        )
        plan = self.engine.compile(request, permissions={"intent.virtualization.plan"})
        self.assertEqual(
            [step.module.value for step in plan.steps],
            ["virtualization-manager", "virtualization-manager"],
        )
        self.assertEqual(plan.steps[1].input["runtime"], "lxc")

    def test_module_install_requires_permission_acknowledgement(self) -> None:
        request = self.request(
            kind=IntentKind.MODULE_INSTALL,
            target_id="storage-module",
            parameters={
                "module_id": "storage",
                "version": "1.0.0",
                "permissions_acknowledged": False,
            },
        )
        plan = self.engine.compile(request, permissions={"intent.module.plan"})
        self.assertEqual(plan.state, IntentPlanState.BLOCKED)
        self.assertEqual(plan.blockers, ("module_permissions_not_acknowledged",))

    def test_intent_envelope_rejects_execution_unknown_fields_and_secrets(self) -> None:
        mapping = {
            "schema": "home-center.intent-request.v1",
            "intent_id": str(uuid.uuid4()),
            "idempotency_key": "intent-test-0002",
            "correlation_id": "intent-test-0002",
            "actor": "local-admin",
            "reason": "validate closed envelope",
            "kind": "node.drain",
            "target_id": "node-a",
            "parameters": {"quorum_safe": True, "mandatory_services_safe": True},
            "mode": "execute",
        }
        with self.assertRaisesRegex(IntentEngineError, "execution_not_certified"):
            IntentRequest.from_mapping(mapping)
        mapping["mode"] = "plan"
        mapping["secret"] = "forbidden"
        with self.assertRaisesRegex(IntentEngineError, "invalid_intent_envelope"):
            IntentRequest.from_mapping(mapping)
        with self.assertRaisesRegex(IntentEngineError, "secret_parameter_rejected"):
            self.request(
                kind=IntentKind.MODULE_INSTALL,
                target_id="storage-module",
                parameters={
                    "module_id": "storage",
                    "version": "1.0.0",
                    "permissions_acknowledged": True,
                    "token": "forbidden",
                },
            )


if __name__ == "__main__":
    unittest.main()
