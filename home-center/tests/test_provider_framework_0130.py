from __future__ import annotations

import json
import sys
import unittest
import uuid
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))
sys.path.insert(0, str(ROOT / "tests"))

from home_center.core.intent_engine import IntentEngineError, IntentKind, IntentRequest  # noqa: E402
from home_center.provider_framework import (  # noqa: E402
    ProviderAdapterDescriptor,
    ProviderFrameworkError,
    ProviderKind,
    ProviderPlanState,
    prepare_provider_operation,
)
from home_center.reservation_scheduler import ReservationLedger  # noqa: E402
from schema_validator import validate  # noqa: E402
from test_placement_planner_0130 import node, snapshot  # noqa: E402
from test_reservation_scheduler_0130 import placed, storage_request  # noqa: E402


def virtualization_request(runtime: str = "lxc") -> IntentRequest:
    return IntentRequest(
        intent_id=str(uuid.uuid4()),
        idempotency_key=f"provider-{runtime}-0001",
        correlation_id=f"provider-{runtime}-0001",
        actor="local-admin:admin",
        reason="prepare provider-bound operation without execution",
        kind=IntentKind.VIRTUALIZATION_WORKLOAD_CREATE,
        target_id=f"test-{runtime}-workload",
        parameters={
            "runtime": runtime,
            "vcpu": 2,
            "memory_mib": 2048,
            "disk_gib": 20,
            "high_availability": False,
        },
    )


def reserve(request: IntentRequest, facts: dict, *, now: int = 1_000, lease: int = 60):
    plan = placed(request, facts)
    decision = ReservationLedger().reserve(
        request=request,
        plan=plan,
        resource_snapshot=facts,
        now_epoch=now,
        lease_seconds=lease,
    )
    return plan, decision


class ProviderFramework0130Tests(unittest.TestCase):
    def test_storage_reservation_prepares_filesystem_operation_without_execution(self) -> None:
        facts = snapshot([node("hm-dm-dc01", "dc01", free_gib=500)])
        request = storage_request()
        plan, reservation = reserve(request, facts)
        provider = ProviderAdapterDescriptor.create(
            provider_id="filesystem-primary",
            kind=ProviderKind.FILESYSTEM,
            healthy=True,
            managed_node_ids=("hm-dm-dc01",),
            capabilities=("storage.share.plan.v1",),
        )
        result = prepare_provider_operation(
            request=request,
            plan=plan,
            reservation=reservation,
            resource_snapshot=facts,
            provider=provider,
            now_epoch=1_010,
        ).to_dict()
        schema = json.loads((ROOT / "contracts/intents/provider-operation-plan.v1.schema.json").read_text(encoding="utf-8"))
        validate(schema, result)
        self.assertEqual(result["state"], "planned")
        self.assertEqual(result["operation"], "storage.share.create.v1")
        self.assertFalse(result["execution_ticket_created"])
        self.assertFalse(result["provider_execution_enabled"])
        self.assertFalse(result["production_mutation_enabled"])

    def test_lxc_and_vm_route_only_to_typed_proxmox_capabilities(self) -> None:
        facts = snapshot([node("hm-dm-dc01", "dc01", cpu=8, memory_gib=16, free_gib=500)])
        for runtime, operation, capability in (
            ("lxc", "virtualization.lxc.create.v1", "compute.lxc.plan.v1"),
            ("vm", "virtualization.vm.create.v1", "compute.vm.plan.v1"),
        ):
            request = virtualization_request(runtime)
            plan, reservation = reserve(request, facts)
            provider = ProviderAdapterDescriptor.create(
                provider_id="proxmox-primary",
                kind=ProviderKind.PROXMOX,
                healthy=True,
                managed_node_ids=("hm-dm-dc01",),
                capabilities=(capability,),
            )
            with self.subTest(runtime=runtime):
                result = prepare_provider_operation(
                    request=request,
                    plan=plan,
                    reservation=reservation,
                    resource_snapshot=facts,
                    provider=provider,
                    now_epoch=1_010,
                )
                self.assertEqual(result.state, ProviderPlanState.PLANNED)
                self.assertEqual(result.operation.value, operation)
                self.assertEqual(result.capability, capability)

    def test_expired_or_tampered_reservation_fails_before_provider_preflight(self) -> None:
        facts = snapshot([node("hm-dm-dc01", "dc01", free_gib=500)])
        request = storage_request()
        plan, reservation = reserve(request, facts, now=2_000, lease=5)
        provider = ProviderAdapterDescriptor.create(
            provider_id="filesystem-primary",
            kind=ProviderKind.FILESYSTEM,
            healthy=True,
            managed_node_ids=("hm-dm-dc01",),
            capabilities=("storage.share.plan.v1",),
        )
        with self.assertRaisesRegex(ProviderFrameworkError, "provider_reservation_expired"):
            prepare_provider_operation(
                request=request,
                plan=plan,
                reservation=reservation,
                resource_snapshot=facts,
                provider=provider,
                now_epoch=2_005,
            )
        tampered = replace(reservation, request_binding_sha256="sha256:" + "0" * 64)
        with self.assertRaisesRegex(ProviderFrameworkError, "provider_reservation_binding_rejected"):
            prepare_provider_operation(
                request=request,
                plan=plan,
                reservation=tampered,
                resource_snapshot=facts,
                provider=provider,
                now_epoch=2_001,
            )

    def test_provider_mismatch_health_capability_and_node_scope_are_blocked(self) -> None:
        facts = snapshot([node("hm-dm-dc01", "dc01", free_gib=500)])
        request = storage_request()
        plan, reservation = reserve(request, facts, now=3_000)
        provider = ProviderAdapterDescriptor.create(
            provider_id="wrong-provider",
            kind=ProviderKind.PROXMOX,
            healthy=False,
            managed_node_ids=("hm-dm-dc02",),
            capabilities=("compute.vm.plan.v1",),
        )
        result = prepare_provider_operation(
            request=request,
            plan=plan,
            reservation=reservation,
            resource_snapshot=facts,
            provider=provider,
            now_epoch=3_001,
        )
        self.assertEqual(result.state, ProviderPlanState.BLOCKED)
        self.assertEqual(
            result.blockers,
            (
                "provider_kind_mismatch",
                "provider_not_healthy",
                "provider_capability_unsupported",
                "provider_node_scope_mismatch",
            ),
        )
        self.assertFalse(result.provider_execution_enabled)
        self.assertFalse(result.production_mutation_enabled)

    def test_snapshot_drift_and_unknown_runtime_fail_closed(self) -> None:
        facts = snapshot([node("hm-dm-dc01", "dc01", cpu=8, memory_gib=16, free_gib=500)])
        request = virtualization_request("lxc")
        plan, reservation = reserve(request, facts, now=4_000)
        provider = ProviderAdapterDescriptor.create(
            provider_id="proxmox-primary",
            kind=ProviderKind.PROXMOX,
            healthy=True,
            managed_node_ids=("hm-dm-dc01",),
            capabilities=("compute.lxc.plan.v1",),
        )
        drifted = snapshot([node("hm-dm-dc01", "dc01", cpu=8, memory_gib=16, free_gib=450)])
        with self.assertRaises(ProviderFrameworkError):
            prepare_provider_operation(
                request=request,
                plan=plan,
                reservation=reservation,
                resource_snapshot=drifted,
                provider=provider,
                now_epoch=4_001,
            )

        unknown = virtualization_request("container")
        with self.assertRaisesRegex(IntentEngineError, "invalid_virtualization_runtime"):
            placed(unknown, facts)

    def test_result_has_no_generic_execution_material(self) -> None:
        facts = snapshot([node("hm-dm-dc01", "dc01", free_gib=500)])
        request = storage_request()
        plan, reservation = reserve(request, facts, now=5_000)
        provider = ProviderAdapterDescriptor.create(
            provider_id="filesystem-primary",
            kind=ProviderKind.FILESYSTEM,
            healthy=True,
            managed_node_ids=("hm-dm-dc01",),
            capabilities=("storage.share.plan.v1",),
        )
        serialized = json.dumps(
            prepare_provider_operation(
                request=request,
                plan=plan,
                reservation=reservation,
                resource_snapshot=facts,
                provider=provider,
                now_epoch=5_001,
            ).to_dict(),
            sort_keys=True,
        ).casefold()
        for forbidden in ("command", "argv", "shell", "password", "secret", "private_key", "provider_url"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
