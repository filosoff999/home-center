from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))
sys.path.insert(0, str(ROOT / "tests"))

from home_center.module_admission import (  # noqa: E402
    MAX_ADMISSION_REQUEST_BYTES,
    ModuleAdmissionError,
    load_and_plan_module_admission,
    plan_module_admission,
)
from test_module_manifest import valid_manifest  # noqa: E402


def dependency(
    module_id: str,
    minimum: str = "1.0.0",
    maximum_exclusive: str = "2.0.0",
    *,
    optional: bool = False,
) -> dict:
    return {
        "id": module_id,
        "minimum_version": minimum,
        "maximum_version_exclusive": maximum_exclusive,
        "optional": optional,
    }


def candidate(
    module_id: str,
    version: str = "1.0.0",
    *,
    dependencies: list[dict] | None = None,
    capabilities: list[str] | None = None,
    conflicts: list[str] | None = None,
    extra_permissions: list[str] | None = None,
) -> dict:
    value = valid_manifest()
    value["module"]["id"] = module_id
    value["module"]["version"] = version
    value["module"]["name"] = module_id
    value["dependencies"] = copy.deepcopy(dependencies or [])
    value["capabilities"] = list(capabilities or ["systemd.v1"])
    value["conflicts"] = list(conflicts or [])
    value["permissions"].extend(extra_permissions or [])
    value["artifact"]["sha256"] = hashlib.sha256(f"{module_id}:{version}".encode()).hexdigest()
    return value


def request(
    candidates: list[dict],
    requested: list[tuple[str, str]],
    *,
    installed: list[tuple[str, str]] | None = None,
    capabilities: list[str] | None = None,
) -> dict:
    return {
        "schema": "home-center.module-admission-request.v1",
        "environment": {
            "home_center_version": "0.11.0",
            "architecture": "amd64",
            "operating_system": "linux",
            "capabilities": list(capabilities or ["systemd.v1", "storage.local.v1"]),
        },
        "installed_modules": [
            {"id": module_id, "version": version, "conflicts": []}
            for module_id, version in (installed or [])
        ],
        "candidates": candidates,
        "requested_modules": [
            {"id": module_id, "version": version} for module_id, version in requested
        ],
    }


class ModuleAdmissionTests(unittest.TestCase):
    def assert_rejected(self, value: dict, code: str) -> None:
        with self.assertRaises(ModuleAdmissionError) as raised:
            plan_module_admission(value)
        self.assertEqual(raised.exception.code, code)

    def test_dependency_dag_is_deterministic_and_dependency_first(self) -> None:
        core = candidate("org.test.core")
        helper = candidate("org.test.helper", dependencies=[dependency("org.test.core")])
        root = candidate(
            "org.test.root",
            dependencies=[dependency("org.test.helper"), dependency("org.test.core")],
            capabilities=["storage.local.v1", "systemd.v1"],
            extra_permissions=["modules.status.read"],
        )
        value = request([root, helper, core], [("org.test.root", "1.0.0")])

        first = plan_module_admission(value).to_dict()
        second = plan_module_admission(copy.deepcopy(value)).to_dict()

        self.assertEqual(first, second)
        self.assertEqual(
            [item["id"] for item in first["install_order"]],
            ["org.test.core", "org.test.helper", "org.test.root"],
        )
        self.assertEqual(first["required_capabilities"], ["storage.local.v1", "systemd.v1"])
        self.assertIn("modules.status.read", first["requested_permissions"])
        self.assertIs(first["production_activation_enabled"], False)

    def test_installed_dependency_is_reused_but_never_changed(self) -> None:
        root = candidate("org.test.root", dependencies=[dependency("org.test.core")])
        plan = plan_module_admission(
            request(
                [root],
                [("org.test.root", "1.0.0")],
                installed=[("org.test.core", "1.4.0")],
            )
        ).to_dict()
        self.assertEqual([item["id"] for item in plan["install_order"]], ["org.test.root"])
        self.assertEqual(
            plan["satisfied_by_installed"], [{"id": "org.test.core", "version": "1.4.0"}]
        )

    def test_missing_optional_dependency_is_omitted(self) -> None:
        root = candidate(
            "org.test.root", dependencies=[dependency("org.test.optional", optional=True)]
        )
        plan = plan_module_admission(
            request([root], [("org.test.root", "1.0.0")])
        ).to_dict()
        self.assertEqual([item["id"] for item in plan["install_order"]], ["org.test.root"])

    def test_optional_candidate_is_not_installed_implicitly(self) -> None:
        root = candidate(
            "org.test.root",
            dependencies=[dependency("org.test.optional", "2.0.0", "3.0.0", optional=True)],
        )
        optional = candidate("org.test.optional", "1.0.0")
        plan = plan_module_admission(
            request([root, optional], [("org.test.root", "1.0.0")])
        ).to_dict()
        self.assertEqual([item["id"] for item in plan["install_order"]], ["org.test.root"])

    def test_selected_optional_dependency_must_be_compatible(self) -> None:
        root = candidate(
            "org.test.root",
            dependencies=[dependency("org.test.optional", "2.0.0", "3.0.0", optional=True)],
        )
        optional = candidate("org.test.optional", "1.0.0")
        self.assert_rejected(
            request(
                [root, optional],
                [("org.test.root", "1.0.0"), ("org.test.optional", "1.0.0")],
            ),
            "planner_dependency_version_incompatible",
        )

    def test_selected_optional_dependency_is_ordered_first(self) -> None:
        root = candidate(
            "org.test.alpha",
            dependencies=[dependency("org.test.zeta", optional=True)],
        )
        optional = candidate("org.test.zeta")
        plan = plan_module_admission(
            request(
                [root, optional],
                [("org.test.alpha", "1.0.0"), ("org.test.zeta", "1.0.0")],
            )
        ).to_dict()
        self.assertEqual(
            [item["id"] for item in plan["install_order"]],
            ["org.test.zeta", "org.test.alpha"],
        )

    def test_missing_required_dependency_is_rejected(self) -> None:
        root = candidate("org.test.root", dependencies=[dependency("org.test.missing")])
        self.assert_rejected(
            request([root], [("org.test.root", "1.0.0")]), "planner_dependency_missing"
        )

    def test_incompatible_dependency_version_is_rejected(self) -> None:
        root = candidate(
            "org.test.root", dependencies=[dependency("org.test.core", "2.0.0", "3.0.0")]
        )
        core = candidate("org.test.core", "1.9.9")
        self.assert_rejected(
            request([root, core], [("org.test.root", "1.0.0")]),
            "planner_dependency_version_incompatible",
        )

    def test_multiple_matching_dependency_versions_are_rejected_as_ambiguous(self) -> None:
        root = candidate("org.test.root", dependencies=[dependency("org.test.core", "1.0.0", "3.0.0")])
        core_v1 = candidate("org.test.core", "1.0.0")
        core_v2 = candidate("org.test.core", "2.0.0")
        self.assert_rejected(
            request([root, core_v2, core_v1], [("org.test.root", "1.0.0")]),
            "planner_candidate_ambiguous",
        )

    def test_dependency_cycle_is_rejected(self) -> None:
        first = candidate("org.test.first", dependencies=[dependency("org.test.second")])
        second = candidate("org.test.second", dependencies=[dependency("org.test.first")])
        self.assert_rejected(
            request([first, second], [("org.test.first", "1.0.0")]),
            "planner_dependency_cycle",
        )

    def test_incompatible_shared_dependency_constraints_are_rejected(self) -> None:
        first = candidate(
            "org.test.first", dependencies=[dependency("org.test.core", "1.0.0", "2.0.0")]
        )
        second = candidate(
            "org.test.second", dependencies=[dependency("org.test.core", "2.0.0", "3.0.0")]
        )
        core_v1 = candidate("org.test.core", "1.0.0")
        core_v2 = candidate("org.test.core", "2.0.0")
        self.assert_rejected(
            request(
                [second, core_v2, first, core_v1],
                [("org.test.first", "1.0.0"), ("org.test.second", "1.0.0")],
            ),
            "planner_dependency_version_incompatible",
        )

    def test_environment_compatibility_is_fail_closed(self) -> None:
        cases = (
            ("home_center_version", "0.12.0", "planner_home_center_incompatible"),
            ("architecture", "riscv64", "planner_architecture_incompatible"),
            ("operating_system", "freebsd", "planner_operating_system_incompatible"),
        )
        for field, invalid, code in cases:
            with self.subTest(field=field):
                root = candidate("org.test.root")
                value = request([root], [("org.test.root", "1.0.0")])
                value["environment"][field] = invalid
                self.assert_rejected(value, code)

    def test_missing_capability_is_rejected(self) -> None:
        root = candidate("org.test.root", capabilities=["hardware.tpm.v2"])
        self.assert_rejected(
            request([root], [("org.test.root", "1.0.0")]), "planner_capability_missing"
        )

    def test_conflicts_with_selected_or_installed_module_are_rejected(self) -> None:
        root = candidate("org.test.root", dependencies=[dependency("org.test.plugin")])
        plugin = candidate("org.test.plugin", conflicts=["org.test.root"])
        self.assert_rejected(
            request([root, plugin], [("org.test.root", "1.0.0")]), "planner_module_conflict"
        )

        root = candidate("org.test.root", conflicts=["org.test.legacy"])
        self.assert_rejected(
            request(
                [root],
                [("org.test.root", "1.0.0")],
                installed=[("org.test.legacy", "1.0.0")],
            ),
            "planner_module_conflict",
        )

        root = candidate("org.test.root")
        value = request(
            [root],
            [("org.test.root", "1.0.0")],
            installed=[("org.test.legacy", "1.0.0")],
        )
        value["installed_modules"][0]["conflicts"] = ["org.test.root"]
        self.assert_rejected(value, "planner_module_conflict")

    def test_requested_installed_module_is_not_promoted_to_update(self) -> None:
        root = candidate("org.test.root", "2.0.0")
        self.assert_rejected(
            request(
                [root],
                [("org.test.root", "2.0.0")],
                installed=[("org.test.root", "1.0.0")],
            ),
            "planner_requested_module_already_installed",
        )

    def test_duplicate_candidates_and_requests_are_rejected(self) -> None:
        root = candidate("org.test.root")
        self.assert_rejected(
            request([root, copy.deepcopy(root)], [("org.test.root", "1.0.0")]),
            "planner_candidate_duplicate",
        )
        self.assert_rejected(
            request(
                [root],
                [("org.test.root", "1.0.0"), ("org.test.root", "1.0.0")],
            ),
            "planner_requested_module_duplicate",
        )

    def test_missing_exact_requested_candidate_is_rejected(self) -> None:
        root = candidate("org.test.root", "1.0.0")
        self.assert_rejected(
            request([root], [("org.test.root", "2.0.0")]),
            "planner_requested_candidate_missing",
        )

    def test_loader_rejects_ambiguity_and_bounds_input(self) -> None:
        with self.assertRaises(ModuleAdmissionError) as duplicate:
            load_and_plan_module_admission(b'{"schema":"one","schema":"two"}')
        self.assertEqual(duplicate.exception.code, "planner_duplicate_key")
        with self.assertRaises(ModuleAdmissionError) as floated:
            load_and_plan_module_admission(b'{"value":1.0}')
        self.assertEqual(floated.exception.code, "planner_float_rejected")
        with self.assertRaises(ModuleAdmissionError) as oversized:
            load_and_plan_module_admission(b" " * (MAX_ADMISSION_REQUEST_BYTES + 1))
        self.assertEqual(oversized.exception.code, "planner_request_size_rejected")

    def test_unknown_request_fields_and_invalid_manifest_are_rejected(self) -> None:
        root = candidate("org.test.root")
        value = request([root], [("org.test.root", "1.0.0")])
        value["execute"] = True
        self.assert_rejected(value, "planner_request_fields_rejected")

        invalid = candidate("org.test.root")
        invalid["install_command"] = "anything"
        self.assert_rejected(
            request([invalid], [("org.test.root", "1.0.0")]), "planner_manifest_rejected"
        )

    def test_result_contains_no_execution_or_grant_authority(self) -> None:
        root = candidate("org.test.root")
        payload = json.dumps(
            request([root], [("org.test.root", "1.0.0")]),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        result = load_and_plan_module_admission(payload).to_dict()
        self.assertEqual(
            set(result),
            {
                "schema",
                "status",
                "requested_modules",
                "install_order",
                "satisfied_by_installed",
                "required_capabilities",
                "requested_permissions",
                "production_activation_enabled",
            },
        )
        rendered = json.dumps(result, sort_keys=True)
        for forbidden in ("command", "grant", "node", "placement", "rollout", "service"):
            self.assertNotIn(forbidden, rendered)

    def test_aggregated_permissions_match_result_contract_bound(self) -> None:
        first = candidate(
            "org.test.first",
            extra_permissions=[f"first.scope-{index}" for index in range(70)],
        )
        second = candidate(
            "org.test.second",
            extra_permissions=[f"second.scope-{index}" for index in range(70)],
        )
        self.assert_rejected(
            request(
                [first, second],
                [("org.test.first", "1.0.0"), ("org.test.second", "1.0.0")],
            ),
            "planner_permissions_rejected",
        )


if __name__ == "__main__":
    unittest.main()
