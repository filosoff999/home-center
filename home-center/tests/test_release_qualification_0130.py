from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))
sys.path.insert(0, str(ROOT / "tests"))

from home_center.provider_framework import (  # noqa: E402
    ProviderAdapterDescriptor,
    ProviderKind,
    prepare_provider_operation,
)
from home_center.release_qualification import ReleaseQualificationError, qualify_release_chain  # noqa: E402
from home_center.reservation_scheduler import ReservationLedger  # noqa: E402
from schema_validator import validate  # noqa: E402
from test_placement_planner_0130 import node, snapshot  # noqa: E402
from test_provider_framework_0130 import virtualization_request  # noqa: E402
from test_reservation_scheduler_0130 import placed, storage_request  # noqa: E402


def provider_for(request, *, healthy: bool = True, node_ids=("hm-dm-dc01",)):
    if request.kind.value == "storage.share.create":
        return ProviderAdapterDescriptor.create(
            provider_id="filesystem-primary",
            kind=ProviderKind.FILESYSTEM,
            healthy=healthy,
            managed_node_ids=node_ids,
            capabilities=("storage.share.plan.v1",),
        )
    runtime = request.parameters["runtime"]
    return ProviderAdapterDescriptor.create(
        provider_id="proxmox-primary",
        kind=ProviderKind.PROXMOX,
        healthy=healthy,
        managed_node_ids=node_ids,
        capabilities=(f"compute.{runtime}.plan.v1",),
    )


def chain(request, facts, *, now: int = 10_000, provider=None):
    plan = placed(request, facts)
    reservation = ReservationLedger().reserve(
        request=request,
        plan=plan,
        resource_snapshot=facts,
        now_epoch=now,
        lease_seconds=60,
    )
    descriptor = provider or provider_for(request)
    provider_plan = prepare_provider_operation(
        request=request,
        plan=plan,
        reservation=reservation,
        resource_snapshot=facts,
        provider=descriptor,
        now_epoch=now + 1,
    )
    return plan, reservation, descriptor, provider_plan


class ReleaseQualification0130Tests(unittest.TestCase):
    def qualify(self, request, facts, *, now: int = 10_000, provider=None):
        plan, reservation, descriptor, provider_plan = chain(request, facts, now=now, provider=provider)
        report = qualify_release_chain(
            request=request,
            api_plan=plan.to_dict(),
            resource_snapshot=facts,
            reservation=reservation,
            provider=descriptor,
            provider_plan=provider_plan,
            now_epoch=now + 1,
        )
        return report, plan, reservation, descriptor, provider_plan

    def test_storage_chain_is_qualified_and_matches_closed_contract(self) -> None:
        facts = snapshot([node("hm-dm-dc01", "dc01", free_gib=500)])
        report, _, _, _, _ = self.qualify(storage_request(), facts)
        value = report.to_dict()
        schema = json.loads(
            (ROOT / "contracts/release/release-qualification.v1.schema.json").read_text(encoding="utf-8")
        )
        validate(schema, value)
        self.assertEqual(value["state"], "qualified")
        self.assertEqual(value["provider_operation"], "storage.share.create.v1")
        self.assertFalse(value["execution_ticket_created"])
        self.assertFalse(value["provider_execution_enabled"])
        self.assertFalse(value["production_execution_enabled"])
        self.assertFalse(value["production_mutation_enabled"])

    def test_vm_and_lxc_chains_are_qualified_deterministically(self) -> None:
        facts = snapshot([node("hm-dm-dc01", "dc01", cpu=16, memory_gib=32, free_gib=500)])
        for runtime in ("vm", "lxc"):
            request = virtualization_request(runtime)
            first, _, _, _, _ = self.qualify(request, facts, now=20_000)
            second, _, _, _, _ = self.qualify(request, facts, now=20_000)
            with self.subTest(runtime=runtime):
                self.assertEqual(first.to_dict(), second.to_dict())
                self.assertEqual(first.provider_operation, f"virtualization.{runtime}.create.v1")

    def test_api_plan_or_snapshot_drift_fails_closed(self) -> None:
        facts = snapshot([node("hm-dm-dc01", "dc01", free_gib=500)])
        request = storage_request()
        _, plan, reservation, descriptor, provider_plan = self.qualify(request, facts, now=30_000)
        api_value = plan.to_dict()
        api_value["target_id"] = "tampered-share"
        with self.assertRaisesRegex(ReleaseQualificationError, "qualification_api_plan_rejected"):
            qualify_release_chain(
                request=request,
                api_plan=api_value,
                resource_snapshot=facts,
                reservation=reservation,
                provider=descriptor,
                provider_plan=provider_plan,
                now_epoch=30_001,
            )

        drifted = snapshot([node("hm-dm-dc01", "dc01", free_gib=450)])
        with self.assertRaises(ReleaseQualificationError):
            qualify_release_chain(
                request=request,
                api_plan=plan.to_dict(),
                resource_snapshot=drifted,
                reservation=reservation,
                provider=descriptor,
                provider_plan=provider_plan,
                now_epoch=30_001,
            )

    def test_tampered_or_expired_reservation_fails_closed(self) -> None:
        facts = snapshot([node("hm-dm-dc01", "dc01", free_gib=500)])
        request = storage_request()
        _, plan, reservation, descriptor, provider_plan = self.qualify(request, facts, now=40_000)
        tampered = replace(reservation, request_binding_sha256="sha256:" + "0" * 64)
        with self.assertRaisesRegex(ReleaseQualificationError, "qualification_reservation_rejected"):
            qualify_release_chain(
                request=request,
                api_plan=plan.to_dict(),
                resource_snapshot=facts,
                reservation=tampered,
                provider=descriptor,
                provider_plan=provider_plan,
                now_epoch=40_001,
            )
        with self.assertRaisesRegex(ReleaseQualificationError, "qualification_reservation_expired"):
            qualify_release_chain(
                request=request,
                api_plan=plan.to_dict(),
                resource_snapshot=facts,
                reservation=reservation,
                provider=descriptor,
                provider_plan=provider_plan,
                now_epoch=reservation.expires_at_epoch,
            )

    def test_blocked_or_tampered_provider_boundary_fails_closed(self) -> None:
        facts = snapshot([node("hm-dm-dc01", "dc01", free_gib=500)])
        request = storage_request()
        blocked_provider = provider_for(request, healthy=False)
        plan, reservation, _, blocked_plan = chain(request, facts, now=50_000, provider=blocked_provider)
        with self.assertRaisesRegex(ReleaseQualificationError, "qualification_provider_blocked"):
            qualify_release_chain(
                request=request,
                api_plan=plan.to_dict(),
                resource_snapshot=facts,
                reservation=reservation,
                provider=blocked_provider,
                provider_plan=blocked_plan,
                now_epoch=50_001,
            )
        healthy = provider_for(request)
        healthy_plan = prepare_provider_operation(
            request=request,
            plan=plan,
            reservation=reservation,
            resource_snapshot=facts,
            provider=healthy,
            now_epoch=50_001,
        )
        tampered = replace(healthy_plan, provider_execution_enabled=True)
        with self.assertRaisesRegex(ReleaseQualificationError, "qualification_provider_rejected"):
            qualify_release_chain(
                request=request,
                api_plan=plan.to_dict(),
                resource_snapshot=facts,
                reservation=reservation,
                provider=healthy,
                provider_plan=tampered,
                now_epoch=50_001,
            )

    def test_report_contains_digests_not_request_or_provider_secrets(self) -> None:
        facts = snapshot([node("hm-dm-dc01", "dc01", free_gib=500)])
        report, _, _, _, _ = self.qualify(storage_request(), facts, now=60_000)
        encoded = json.dumps(report.to_dict(), sort_keys=True).casefold()
        for forbidden in ("password", "secret", "private_key", "token", "provider_url", "command", "argv", "shell"):
            self.assertNotIn(forbidden, encoded)


if __name__ == "__main__":
    unittest.main()
