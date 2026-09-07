from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))
sys.path.insert(0, str(ROOT / "tests"))

from home_center.module_permission_review import (  # noqa: E402
    ModulePermissionReviewError,
    build_module_permission_review,
    empty_permission_review,
    load_and_build_module_permission_review,
)
from test_module_admission import candidate, dependency, request  # noqa: E402


def add_action(manifest: dict, permission: str, action_id: str, risk: str) -> None:
    manifest["permissions"].append(permission)
    manifest["actions"].append(
        {
            "id": action_id,
            "permission": permission,
            "risk": risk,
            "timeout_seconds": 30,
            "idempotent": True,
        }
    )


class ModulePermissionReviewTests(unittest.TestCase):
    def test_empty_status_is_honest_and_inert(self) -> None:
        self.assertEqual(
            empty_permission_review(),
            {
                "schema": "home-center.module-permission-review-status.v1",
                "status": "no-pending-review",
                "review": None,
                "decision_persistence_enabled": False,
                "permission_grants_applied": False,
                "production_activation_enabled": False,
            },
        )

    def test_review_is_deterministic_and_binds_exact_contents(self) -> None:
        root = candidate("org.test.root")
        add_action(root, "modules.status.read", "module.status.read.v1", "read-only")
        value = request([root], [("org.test.root", "1.0.0")])
        first = build_module_permission_review(value).to_dict()
        second = build_module_permission_review(copy.deepcopy(value)).to_dict()
        self.assertEqual(first, second)
        self.assertRegex(first["review_id"], r"^sha256:[0-9a-f]{64}$")

        changed = copy.deepcopy(value)
        changed["candidates"][0]["actions"][-1]["risk"] = "destructive"
        self.assertNotEqual(
            first["review_id"],
            build_module_permission_review(changed).to_dict()["review_id"],
        )

    def test_permissions_are_attributed_and_classified_by_highest_action_risk(self) -> None:
        root = candidate("org.test.root")
        add_action(root, "modules.status.read", "module.status.read.v1", "read-only")
        add_action(root, "modules.data.manage", "module.data.erase.v1", "destructive")
        review = build_module_permission_review(
            request([root], [("org.test.root", "1.0.0")])
        ).to_dict()
        permissions = {item["id"]: item for item in review["permissions"]}
        self.assertEqual(permissions["modules.status.read"]["risk"], "read-only")
        self.assertEqual(permissions["modules.data.manage"]["risk"], "destructive")
        self.assertEqual(permissions["modules.lifecycle.install"]["risk"], "mutation")
        self.assertEqual(permissions["modules.status.read"]["modules"], ["org.test.root"])
        self.assertEqual(
            permissions["modules.status.read"]["actions"],
            [
                {
                    "module_id": "org.test.root",
                    "action_id": "module.status.read.v1",
                    "risk": "read-only",
                }
            ],
        )
        self.assertEqual(
            review["summary"],
            {"total": 5, "read_only": 1, "mutation": 3, "destructive": 1, "unclassified": 0},
        )

    def test_unreferenced_permission_is_never_silently_low_risk(self) -> None:
        root = candidate(
            "org.test.root", extra_permissions=["modules.ambient.access"]
        )
        review = build_module_permission_review(
            request([root], [("org.test.root", "1.0.0")])
        ).to_dict()
        permission = next(
            item for item in review["permissions"] if item["id"] == "modules.ambient.access"
        )
        self.assertEqual(permission["risk"], "unclassified")
        self.assertEqual(permission["actions"], [])
        self.assertEqual(review["summary"]["unclassified"], 1)

    def test_shared_permission_is_unclassified_if_one_module_has_no_typed_action(self) -> None:
        first = candidate("org.test.first", extra_permissions=["modules.shared.read"])
        second = candidate("org.test.second")
        add_action(second, "modules.shared.read", "module.shared.read.v1", "read-only")
        review = build_module_permission_review(
            request(
                [second, first],
                [("org.test.first", "1.0.0"), ("org.test.second", "1.0.0")],
            )
        ).to_dict()
        permission = next(
            item for item in review["permissions"] if item["id"] == "modules.shared.read"
        )
        self.assertEqual(permission["risk"], "unclassified")
        self.assertEqual(permission["modules"], ["org.test.first", "org.test.second"])

    def test_dependency_permissions_are_included_but_installed_dependencies_are_not(self) -> None:
        dependency_module = candidate("org.test.dependency")
        add_action(
            dependency_module,
            "modules.dependency.read",
            "module.dependency.read.v1",
            "read-only",
        )
        root = candidate(
            "org.test.root", dependencies=[dependency("org.test.dependency")]
        )
        review = build_module_permission_review(
            request([root, dependency_module], [("org.test.root", "1.0.0")])
        ).to_dict()
        self.assertIn(
            "modules.dependency.read", [item["id"] for item in review["permissions"]]
        )

        installed_review = build_module_permission_review(
            request(
                [root],
                [("org.test.root", "1.0.0")],
                installed=[("org.test.dependency", "1.0.0")],
            )
        ).to_dict()
        self.assertNotIn(
            "modules.dependency.read",
            [item["id"] for item in installed_review["permissions"]],
        )

    def test_review_never_contains_grant_or_activation_authority(self) -> None:
        root = candidate("org.test.root")
        review = build_module_permission_review(
            request([root], [("org.test.root", "1.0.0")])
        ).to_dict()
        self.assertTrue(review["acknowledgement_required"])
        self.assertIs(review["decision_persistence_enabled"], False)
        self.assertIs(review["permission_grants_applied"], False)
        self.assertIs(review["production_activation_enabled"], False)
        rendered = json.dumps(review, sort_keys=True)
        for forbidden in ("command", "executable", "service_name", "target_node"):
            self.assertNotIn(forbidden, rendered)

    def test_invalid_or_ambiguous_admission_fails_with_bounded_review_error(self) -> None:
        root = candidate(
            "org.test.root", dependencies=[dependency("org.test.missing")]
        )
        with self.assertRaises(ModulePermissionReviewError) as raised:
            build_module_permission_review(
                request([root], [("org.test.root", "1.0.0")])
            )
        self.assertEqual(raised.exception.code, "review_admission_rejected")
        self.assertNotIn("org.test.missing", str(raised.exception))

        with self.assertRaises(ModulePermissionReviewError) as duplicate:
            load_and_build_module_permission_review(
                b'{"schema":"home-center.module-admission-request.v1","schema":"other"}'
            )
        self.assertEqual(duplicate.exception.code, "review_request_rejected")

    def test_review_complexity_is_bounded(self) -> None:
        candidates = []
        requested = []
        for module_index in range(5):
            module_id = f"org.test.module-{module_index}"
            extra = [
                f"scope{module_index}.permission-{permission_index}"
                for permission_index in range(125)
            ]
            candidates.append(candidate(module_id, extra_permissions=extra))
            requested.append((module_id, "1.0.0"))
        with self.assertRaises(ModulePermissionReviewError) as raised:
            build_module_permission_review(request(candidates, requested))
        self.assertEqual(raised.exception.code, "review_complexity_rejected")


if __name__ == "__main__":
    unittest.main()
