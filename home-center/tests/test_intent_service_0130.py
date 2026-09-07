from __future__ import annotations

import unittest
import uuid

from home_center.core.intent_engine import IntentKind, IntentPlanState, IntentRequest
from home_center.intent_service import IntentAuthorizationError, IntentPlanningService


class IntentPlanningService0130Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = IntentPlanningService()

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


if __name__ == "__main__":
    unittest.main()
