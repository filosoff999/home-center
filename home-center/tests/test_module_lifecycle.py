from __future__ import annotations

import copy
import json
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))
sys.path.insert(0, str(ROOT / "tests"))

from home_center.module_lifecycle import (  # noqa: E402
    BLOCKERS,
    ModuleLifecycleError,
    empty_module_lifecycle_status,
    load_module_install_lifecycle_request,
    plan_module_install_lifecycle,
)
from home_center.module_permission_acknowledgement import (  # noqa: E402
    ACKNOWLEDGEMENT_TTL_SECONDS,
    load_and_prepare_module_permission_acknowledgement,
)
from home_center.module_permission_review import build_module_permission_review  # noqa: E402
from test_module_admission import candidate, dependency, request  # noqa: E402
from schema_validator import validate  # noqa: E402


NOW = datetime(2026, 9, 7, 12, 30, tzinfo=UTC)
ACKNOWLEDGEMENT_ID = "12345678-1234-4234-9234-123456789abc"


def acknowledgement_record(
    admission: dict,
    *,
    actor: str = "local-admin:admin",
    state: str = "recorded",
) -> dict:
    review_id = build_module_permission_review(admission).review_id
    payload = json.dumps(
        {
            "schema": "home-center.module-permission-acknowledgement-request.v1",
            "review_id": review_id,
            "acknowledgement": "permissions-reviewed",
            "idempotency_key": "lifecycle-ack-0001",
            "admission_request": admission,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    draft = load_and_prepare_module_permission_acknowledgement(
        payload, actor=actor, now=NOW
    )
    return {**draft.__dict__, "acknowledgement_id": ACKNOWLEDGEMENT_ID, "state": state}


def lifecycle_payload(admission: dict, *, extra: dict | None = None) -> bytes:
    value = {
        "schema": "home-center.module-install-lifecycle-request.v1",
        "operation": "install",
        "acknowledgement_id": ACKNOWLEDGEMENT_ID,
        "admission_request": admission,
    }
    value.update(extra or {})
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class ModuleLifecycleTests(unittest.TestCase):
    def dependency_plan(self) -> dict:
        core = candidate("org.test.core")
        root = candidate(
            "org.test.root", dependencies=[dependency("org.test.core")]
        )
        return request([root, core], [("org.test.root", "1.0.0")])

    def plan(self, admission: dict, *, record: dict | None = None, actor: str = "local-admin:admin", now=NOW):
        parsed = load_module_install_lifecycle_request(lifecycle_payload(admission))
        return plan_module_install_lifecycle(
            parsed,
            acknowledgement_record=record or acknowledgement_record(admission),
            actor=actor,
            now=now,
        )

    def test_exact_acknowledgement_builds_only_a_blocked_plan(self) -> None:
        plan = self.plan(self.dependency_plan())
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["operation"], "install")
        self.assertEqual(plan["blockers"], list(BLOCKERS))
        self.assertEqual(plan["acknowledgement"]["status"], "evidence-validated")
        self.assertFalse(plan["acknowledgement"]["consumable"])
        for field in (
            "acknowledgement_consumption_enabled",
            "authorization_decisions_enabled",
            "artifact_mutation_enabled",
            "lifecycle_persistence_enabled",
            "lifecycle_execution_enabled",
            "production_activation_enabled",
        ):
            self.assertFalse(plan[field])
        self.assertRegex(plan["plan_id"], r"^sha256:[0-9a-f]{64}$")

    def test_install_is_dependency_first_and_recovery_is_exact_reverse(self) -> None:
        plan = self.plan(self.dependency_plan())
        self.assertEqual(
            [step["module"]["id"] for step in plan["steps"]],
            ["org.test.core", "org.test.root"],
        )
        self.assertEqual(
            [step["module"]["id"] for step in plan["recovery"]["steps"]],
            ["org.test.root", "org.test.core"],
        )
        for step in plan["steps"]:
            self.assertEqual(step["state"], "blocked")
            self.assertEqual(step["action"]["risk"], "mutation")
            self.assertTrue(step["action"]["idempotent"])
            self.assertGreaterEqual(len(step["postconditions"]), 1)
        for step in plan["recovery"]["steps"]:
            self.assertEqual(step["state"], "planned")
            self.assertEqual(step["data_policy"], "preserve")

    def test_plan_is_deterministic_for_the_exact_acknowledged_request(self) -> None:
        first = candidate("org.test.first")
        second = candidate("org.test.second")
        admission = request(
            [first, second],
            [("org.test.first", "1.0.0"), ("org.test.second", "1.0.0")],
        )
        record = acknowledgement_record(admission)
        first_plan = self.plan(admission, record=record)
        second_plan = self.plan(copy.deepcopy(admission), record=record)
        self.assertEqual(first_plan, second_plan)

    def test_foreign_expired_and_superseded_acknowledgements_fail_closed(self) -> None:
        admission = self.dependency_plan()
        foreign = acknowledgement_record(admission, actor="ad:other@HM.DM")
        with self.assertRaises(ModuleLifecycleError) as actor_error:
            self.plan(admission, record=foreign)
        self.assertEqual(actor_error.exception.code, "lifecycle_acknowledgement_rejected")

        superseded = acknowledgement_record(admission, state="superseded")
        with self.assertRaises(ModuleLifecycleError) as state_error:
            self.plan(admission, record=superseded)
        self.assertEqual(state_error.exception.code, "lifecycle_acknowledgement_rejected")

        expired = acknowledgement_record(admission)
        with self.assertRaises(ModuleLifecycleError) as expiry_error:
            self.plan(
                admission,
                record=expired,
                now=NOW + timedelta(seconds=ACKNOWLEDGEMENT_TTL_SECONDS),
            )
        self.assertEqual(expiry_error.exception.code, "lifecycle_acknowledgement_rejected")

    def test_review_or_scope_mismatch_fails_without_echo(self) -> None:
        admission = self.dependency_plan()
        record = acknowledgement_record(admission)
        changed = copy.deepcopy(admission)
        changed["candidates"][0]["artifact"]["sha256"] = "d" * 64
        with self.assertRaises(ModuleLifecycleError) as mismatch:
            self.plan(changed, record=record)
        self.assertEqual(mismatch.exception.code, "lifecycle_admission_binding_rejected")
        self.assertNotIn("org.test.root", str(mismatch.exception))

        reordered = copy.deepcopy(admission)
        reordered["candidates"].reverse()
        with self.assertRaises(ModuleLifecycleError) as exact_request:
            self.plan(reordered, record=record)
        self.assertEqual(
            exact_request.exception.code, "lifecycle_admission_binding_rejected"
        )

        health_drift = copy.deepcopy(admission)
        original_review = build_module_permission_review(admission).review_id
        health_drift["candidates"][0]["health"][0]["interval_seconds"] = 45
        self.assertEqual(
            build_module_permission_review(health_drift).review_id, original_review
        )
        with self.assertRaises(ModuleLifecycleError) as full_binding:
            self.plan(health_drift, record=record)
        self.assertEqual(
            full_binding.exception.code, "lifecycle_admission_binding_rejected"
        )

    def test_request_rejects_unknown_fields_operations_and_ambiguous_json(self) -> None:
        admission = self.dependency_plan()
        with self.assertRaises(ModuleLifecycleError) as unknown:
            load_module_install_lifecycle_request(
                lifecycle_payload(admission, extra={"execute": True})
            )
        self.assertEqual(unknown.exception.code, "lifecycle_request_fields_rejected")
        value = json.loads(lifecycle_payload(admission))
        value["operation"] = "remove"
        with self.assertRaises(ModuleLifecycleError) as operation:
            load_module_install_lifecycle_request(json.dumps(value).encode())
        self.assertEqual(operation.exception.code, "lifecycle_operation_rejected")
        for payload in (
            b'{"schema":"one","schema":"two"}',
            b"\xef\xbb\xbf{}",
            b'{"value":1.5}',
            b'{"value":NaN}',
        ):
            with self.subTest(payload=payload), self.assertRaises(ModuleLifecycleError):
                load_module_install_lifecycle_request(payload)

    def test_public_plan_contains_no_execution_surface(self) -> None:
        plan = self.plan(self.dependency_plan())
        schema = json.loads(
            (ROOT / "contracts/modules/module-install-lifecycle-plan.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        validate(schema, plan)
        serialized = json.dumps(plan, sort_keys=True)
        for forbidden in (
            '"command"',
            '"argv"',
            '"path"',
            '"execute"',
            '"granted_permissions"',
        ):
            self.assertNotIn(forbidden, serialized)

    def test_empty_status_is_honest_and_fully_inert(self) -> None:
        status = empty_module_lifecycle_status()
        schema = json.loads(
            (ROOT / "contracts/modules/module-lifecycle-status.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        validate(schema, status)
        self.assertEqual(
            status,
            {
                "schema": "home-center.module-lifecycle-status.v1",
                "status": "no-pending-lifecycle",
                "plan": None,
                "acknowledgement_consumption_enabled": False,
                "authorization_decisions_enabled": False,
                "artifact_mutation_enabled": False,
                "lifecycle_persistence_enabled": False,
                "lifecycle_execution_enabled": False,
                "production_activation_enabled": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
