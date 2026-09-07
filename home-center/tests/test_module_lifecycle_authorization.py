from __future__ import annotations

import copy
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))
sys.path.insert(0, str(ROOT / "tests"))

from home_center.module_lifecycle import (  # noqa: E402
    load_module_install_lifecycle_request,
    plan_module_install_lifecycle,
)
from home_center.module_lifecycle_authorization import (  # noqa: E402
    ModuleLifecycleAuthorizationError,
    authorize_module_install_lifecycle,
)
from home_center.module_permission_acknowledgement import ACKNOWLEDGEMENT_TTL_SECONDS  # noqa: E402
from schema_validator import validate  # noqa: E402
from test_module_admission import candidate, dependency, request  # noqa: E402
from test_module_lifecycle import (  # noqa: E402
    NOW,
    acknowledgement_record,
    lifecycle_payload,
    publication_record,
)


class ModuleLifecycleAuthorizationTests(unittest.TestCase):
    def admission(self) -> dict:
        core = candidate("org.test.core")
        root = candidate("org.test.root", dependencies=[dependency("org.test.core")])
        return request([root, core], [("org.test.root", "1.0.0")])

    def plan(self, admission: dict, *, publications: bool = True):
        record = acknowledgement_record(admission)
        parsed = load_module_install_lifecycle_request(lifecycle_payload(admission))
        publication_records = (
            [publication_record(item) for item in admission["candidates"]] if publications else []
        )
        plan = plan_module_install_lifecycle(
            parsed,
            acknowledgement_record=record,
            actor="local-admin:admin",
            publication_records=publication_records,
            now=NOW,
        )
        return plan, record

    def test_exact_plan_authorizes_only_the_authorization_precondition(self) -> None:
        plan, record = self.plan(self.admission())
        result = authorize_module_install_lifecycle(
            plan,
            acknowledgement_record=record,
            actor="local-admin:admin",
            now=NOW,
        )
        self.assertEqual(result["decision"], "authorized")
        self.assertEqual(
            result["remaining_blockers"],
            ["lifecycle_executor_unavailable", "placement_unresolved"],
        )
        self.assertEqual(result["plan_id"], plan["plan_id"])
        self.assertFalse(result["acknowledgement_consumed"])
        self.assertFalse(result["lifecycle_persistence_enabled"])
        self.assertFalse(result["lifecycle_execution_enabled"])
        self.assertFalse(result["production_activation_enabled"])
        self.assertRegex(result["authorization_id"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(result["actor_binding_sha256"], r"^sha256:[0-9a-f]{64}$")
        schema = json.loads(
            (ROOT / "contracts/modules/module-lifecycle-authorization.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        validate(schema, result)

    def test_authorization_is_deterministic_and_does_not_echo_actor(self) -> None:
        plan, record = self.plan(self.admission())
        first = authorize_module_install_lifecycle(
            plan, acknowledgement_record=record, actor="local-admin:admin", now=NOW
        )
        second = authorize_module_install_lifecycle(
            copy.deepcopy(plan),
            acknowledgement_record=copy.deepcopy(record),
            actor="local-admin:admin",
            now=NOW,
        )
        self.assertEqual(first, second)
        serialized = json.dumps(first, sort_keys=True)
        self.assertNotIn("local-admin:admin", serialized)
        for forbidden in ('"command"', '"argv"', '"path"', '"password"', '"secret"'):
            self.assertNotIn(forbidden, serialized)

    def test_missing_publication_cannot_be_authorized(self) -> None:
        plan, record = self.plan(self.admission(), publications=False)
        with self.assertRaises(ModuleLifecycleAuthorizationError) as error:
            authorize_module_install_lifecycle(
                plan, acknowledgement_record=record, actor="local-admin:admin", now=NOW
            )
        self.assertEqual(error.exception.code, "authorization_preconditions_incomplete")

    def test_foreign_or_expired_acknowledgement_fails_closed(self) -> None:
        admission = self.admission()
        plan, _ = self.plan(admission)
        foreign = acknowledgement_record(admission, actor="local-admin:other")
        with self.assertRaises(ModuleLifecycleAuthorizationError) as actor_error:
            authorize_module_install_lifecycle(
                plan,
                acknowledgement_record=foreign,
                actor="local-admin:admin",
                now=NOW,
            )
        self.assertEqual(actor_error.exception.code, "authorization_acknowledgement_rejected")

        record = acknowledgement_record(admission)
        with self.assertRaises(ModuleLifecycleAuthorizationError) as expiry_error:
            authorize_module_install_lifecycle(
                plan,
                acknowledgement_record=record,
                actor="local-admin:admin",
                now=NOW + timedelta(seconds=ACKNOWLEDGEMENT_TTL_SECONDS),
            )
        self.assertEqual(expiry_error.exception.code, "authorization_acknowledgement_rejected")

    def test_plan_identity_or_blocker_drift_fails_closed(self) -> None:
        plan, record = self.plan(self.admission())
        tampered = copy.deepcopy(plan)
        tampered["steps"][0]["action"]["id"] = "changed.action"
        with self.assertRaises(ModuleLifecycleAuthorizationError) as identity_error:
            authorize_module_install_lifecycle(
                tampered,
                acknowledgement_record=record,
                actor="local-admin:admin",
                now=NOW,
            )
        self.assertEqual(identity_error.exception.code, "authorization_plan_identity_rejected")

        blockers = copy.deepcopy(plan)
        blockers["blockers"] = ["lifecycle_executor_unavailable", "placement_unresolved"]
        material = copy.deepcopy(blockers)
        material.pop("plan_id")
        from home_center.util import canonical_json
        import hashlib

        blockers["plan_id"] = "sha256:" + hashlib.sha256(
            canonical_json(material).encode("utf-8")
        ).hexdigest()
        with self.assertRaises(ModuleLifecycleAuthorizationError) as blocker_error:
            authorize_module_install_lifecycle(
                blockers,
                acknowledgement_record=record,
                actor="local-admin:admin",
                now=NOW,
            )
        self.assertEqual(blocker_error.exception.code, "authorization_preconditions_incomplete")


if __name__ == "__main__":
    unittest.main()
