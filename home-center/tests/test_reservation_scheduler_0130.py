from __future__ import annotations

import json
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))
sys.path.insert(0, str(ROOT / "tests"))

from home_center.core.intent_engine import (  # noqa: E402
    IntentKind,
    IntentPlan,
    IntentPlanState,
    IntentRequest,
    IntentStep,
)
from home_center.intent_service import IntentPlanningService  # noqa: E402
from home_center.reservation_scheduler import (  # noqa: E402
    ReservationLedger,
    ReservationSchedulerError,
)
from schema_validator import validate  # noqa: E402
from test_placement_planner_0130 import GIB, node, snapshot  # noqa: E402


def storage_request(intent_id: str | None = None, *, capacity_gib: int = 100) -> IntentRequest:
    return IntentRequest(
        intent_id=intent_id or str(uuid.uuid4()),
        idempotency_key="reservation-storage-0001",
        correlation_id="reservation-storage-0001",
        actor="local-admin:admin",
        reason="reserve logical storage planning capacity",
        kind=IntentKind.STORAGE_SHARE_CREATE,
        target_id="media-share",
        parameters={
            "capacity_gib": capacity_gib,
            "protocol": "smb",
            "high_availability": False,
            "backup_enabled": False,
        },
    )


def placed(request: IntentRequest, resource_snapshot: dict) -> IntentPlan:
    return IntentPlanningService(resource_snapshot_provider=lambda: resource_snapshot).plan(
        actor=request.actor,
        request=request,
    )


class ReservationScheduler0130Tests(unittest.TestCase):
    def test_reservation_requires_placed_plan_and_returns_inert_closed_metadata(self) -> None:
        facts = snapshot([node("hm-dm-dc01", "dc01", free_gib=500)])
        request = storage_request()
        plan = placed(request, facts)
        decision = ReservationLedger().reserve(
            request=request,
            plan=plan,
            resource_snapshot=facts,
            now_epoch=1_788_800_000,
            lease_seconds=60,
        )
        value = decision.to_dict()
        schema = json.loads((ROOT / "contracts/intents/reservation.v1.schema.json").read_text(encoding="utf-8"))
        validate(schema, value)
        self.assertEqual(value["node_ids"], ["hm-dm-dc01"])
        self.assertEqual(value["claims"]["hm-dm-dc01"]["storage_bytes"], 100 * GIB)
        self.assertFalse(value["durable"])
        self.assertFalse(value["provider_execution_enabled"])
        self.assertFalse(value["production_mutation_enabled"])

        stripped_steps: list[IntentStep] = []
        for step in plan.steps:
            payload = dict(step.input)
            payload.pop("placement", None)
            stripped_steps.append(
                IntentStep(
                    sequence=step.sequence,
                    module=step.module,
                    action=step.action,
                    target_id=step.target_id,
                    input=payload,
                    execution_requires_approval=step.execution_requires_approval,
                )
            )
        unplaced = IntentPlan(
            intent_id=plan.intent_id,
            kind=plan.kind,
            target_id=plan.target_id,
            state=IntentPlanState.PLANNED,
            code=plan.code,
            steps=tuple(stripped_steps),
        )
        with self.assertRaisesRegex(ReservationSchedulerError, "reservation_placement_missing"):
            ReservationLedger().reserve(
                request=request,
                plan=unplaced,
                resource_snapshot=facts,
                now_epoch=1_788_800_000,
            )

    def test_same_intent_is_idempotent_and_snapshot_drift_conflicts(self) -> None:
        facts = snapshot([node("hm-dm-dc01", "dc01", free_gib=500)])
        request = storage_request()
        plan = placed(request, facts)
        ledger = ReservationLedger()
        first = ledger.reserve(request=request, plan=plan, resource_snapshot=facts, now_epoch=100, lease_seconds=60)
        replay = ledger.reserve(request=request, plan=plan, resource_snapshot=facts, now_epoch=120, lease_seconds=60)
        self.assertEqual(first, replay)

        drifted = snapshot([node("hm-dm-dc01", "dc01", free_gib=450)])
        drifted_plan = placed(request, drifted)
        with self.assertRaisesRegex(ReservationSchedulerError, "reservation_conflict"):
            ledger.reserve(
                request=request,
                plan=drifted_plan,
                resource_snapshot=drifted,
                now_epoch=121,
                lease_seconds=60,
            )

    def test_concurrent_logical_claims_cannot_overcommit_capacity(self) -> None:
        facts = snapshot([node("hm-dm-dc01", "dc01", free_gib=150)])
        first_request = storage_request(capacity_gib=100)
        second_request = storage_request(capacity_gib=100)
        ledger = ReservationLedger()
        ledger.reserve(
            request=first_request,
            plan=placed(first_request, facts),
            resource_snapshot=facts,
            now_epoch=200,
            lease_seconds=60,
        )
        with self.assertRaisesRegex(ReservationSchedulerError, "reservation_capacity_unavailable"):
            ledger.reserve(
                request=second_request,
                plan=placed(second_request, facts),
                resource_snapshot=facts,
                now_epoch=201,
                lease_seconds=60,
            )

    def test_expiry_and_explicit_release_restore_planning_capacity(self) -> None:
        facts = snapshot([node("hm-dm-dc01", "dc01", free_gib=150)])
        first_request = storage_request(capacity_gib=100)
        ledger = ReservationLedger()
        first = ledger.reserve(
            request=first_request,
            plan=placed(first_request, facts),
            resource_snapshot=facts,
            now_epoch=300,
            lease_seconds=5,
        )
        after_expiry = storage_request(capacity_gib=100)
        second = ledger.reserve(
            request=after_expiry,
            plan=placed(after_expiry, facts),
            resource_snapshot=facts,
            now_epoch=305,
            lease_seconds=5,
        )
        self.assertNotEqual(first.reservation_id, second.reservation_id)

        release = ledger.release(second.reservation_id)
        release_schema = json.loads(
            (ROOT / "contracts/intents/reservation-release.v1.schema.json").read_text(encoding="utf-8")
        )
        validate(release_schema, release)
        third_request = storage_request(capacity_gib=100)
        ledger.reserve(
            request=third_request,
            plan=placed(third_request, facts),
            resource_snapshot=facts,
            now_epoch=306,
            lease_seconds=5,
        )

    def test_changed_placement_or_untrusted_snapshot_is_rejected(self) -> None:
        facts = snapshot(
            [
                node("hm-dm-dc01", "dc01", free_gib=500),
                node("hm-dm-dc02", "dc02", free_gib=900),
            ]
        )
        request = storage_request()
        plan = placed(request, facts)
        target = next(step for step in plan.steps if step.action == "storage.share.create.plan.v1")
        payload = dict(target.input)
        placement = dict(payload["placement"])
        placement["node_ids"] = ["hm-dm-dc01"]
        payload["placement"] = placement
        replacement = []
        for step in plan.steps:
            if step is target:
                replacement.append(
                    IntentStep(
                        sequence=step.sequence,
                        module=step.module,
                        action=step.action,
                        target_id=step.target_id,
                        input=payload,
                        execution_requires_approval=step.execution_requires_approval,
                    )
                )
            else:
                replacement.append(step)
        drifted_plan = IntentPlan(
            intent_id=plan.intent_id,
            kind=plan.kind,
            target_id=plan.target_id,
            state=plan.state,
            code=plan.code,
            steps=tuple(replacement),
        )
        with self.assertRaisesRegex(ReservationSchedulerError, "reservation_snapshot_or_placement_rejected"):
            ReservationLedger().reserve(
                request=request,
                plan=drifted_plan,
                resource_snapshot=facts,
                now_epoch=400,
            )

        degraded = dict(facts)
        degraded["planning_ready"] = False
        with self.assertRaises(ReservationSchedulerError):
            ReservationLedger().reserve(
                request=request,
                plan=plan,
                resource_snapshot=degraded,
                now_epoch=400,
            )

    def test_virtualization_claims_all_dimensions_and_no_secret_execution_surface(self) -> None:
        facts = snapshot([node("hm-dm-dc01", "dc01", cpu=8, memory_gib=16, free_gib=500)])
        request = IntentRequest(
            intent_id=str(uuid.uuid4()),
            idempotency_key="reservation-vm-0001",
            correlation_id="reservation-vm-0001",
            actor="local-admin:admin",
            reason="reserve virtualization planning capacity",
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
        value = ReservationLedger().reserve(
            request=request,
            plan=placed(request, facts),
            resource_snapshot=facts,
            now_epoch=500,
        ).to_dict()
        claim = value["claims"]["hm-dm-dc01"]
        self.assertEqual(claim, {"cpu": 4, "memory_bytes": 8192 * 1024**2, "storage_bytes": 100 * GIB})
        serialized = json.dumps(value, sort_keys=True).casefold()
        for forbidden in ("password", "secret", "token", "command", "argv", "provider_call"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
