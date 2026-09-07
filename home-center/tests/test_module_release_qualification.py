from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))
sys.path.insert(0, str(ROOT / "tests"))

from home_center.module_release_qualification import (  # noqa: E402
    EXPECTED_WORKSTREAMS,
    MAX_QUALIFICATION_BYTES,
    ModuleReleaseQualificationError,
    load_module_release_qualification,
    verify_module_release_qualification,
)
from schema_validator import validate  # noqa: E402

NOW = 1_788_800_000


def evidence() -> dict:
    return {
        "schema": "home-center.module-release-qualification.v1",
        "release_version": "0.11.0",
        "observed_at_epoch": NOW - 60,
        "workstreams": [
            {"id": item, "evidence_sha256": "sha256:" + f"{index:064x}"}
            for index, item in enumerate(EXPECTED_WORKSTREAMS, start=1)
        ],
        "safety": {
            "artifact_binding": "exact",
            "authorization_binding": "exact",
            "acknowledgement_consumed": False,
            "remaining_blockers": ["lifecycle_executor_unavailable", "placement_unresolved"],
            "lifecycle_persistence_enabled": False,
            "lifecycle_execution_enabled": False,
            "production_activation_enabled": False,
            "secret_scan": "passed",
        },
    }


class ModuleReleaseQualificationTests(unittest.TestCase):
    def test_complete_evidence_qualifies_without_removing_execution_blockers(self) -> None:
        value = evidence()
        schema = json.loads((ROOT / "contracts/modules/module-release-qualification.v1.schema.json").read_text(encoding="utf-8"))
        validate(schema, value)
        result = verify_module_release_qualification(value, now_epoch=NOW)
        result_schema = json.loads((ROOT / "contracts/modules/module-release-qualification-result.v1.schema.json").read_text(encoding="utf-8"))
        validate(result_schema, result)
        self.assertEqual(result["status"], "qualified")
        self.assertEqual(result["remaining_blockers"], ["lifecycle_executor_unavailable", "placement_unresolved"])
        self.assertFalse(result["acknowledgement_consumed"])
        self.assertFalse(result["lifecycle_execution_enabled"])
        self.assertFalse(result["production_activation_enabled"])

    def test_result_is_deterministic(self) -> None:
        self.assertEqual(
            verify_module_release_qualification(evidence(), now_epoch=NOW),
            verify_module_release_qualification(copy.deepcopy(evidence()), now_epoch=NOW),
        )

    def test_missing_reordered_or_equivocated_workstream_evidence_fails_closed(self) -> None:
        missing = evidence()
        missing["workstreams"].pop()
        with self.assertRaisesRegex(ModuleReleaseQualificationError, "qualification_workstream_set_rejected"):
            verify_module_release_qualification(missing, now_epoch=NOW)

        reordered = evidence()
        reordered["workstreams"][0], reordered["workstreams"][1] = reordered["workstreams"][1], reordered["workstreams"][0]
        with self.assertRaisesRegex(ModuleReleaseQualificationError, "qualification_workstream_set_rejected"):
            verify_module_release_qualification(reordered, now_epoch=NOW)

        duplicate_digest = evidence()
        duplicate_digest["workstreams"][1]["evidence_sha256"] = duplicate_digest["workstreams"][0]["evidence_sha256"]
        with self.assertRaisesRegex(ModuleReleaseQualificationError, "qualification_evidence_equivocation"):
            verify_module_release_qualification(duplicate_digest, now_epoch=NOW)

    def test_stale_future_or_wrong_release_evidence_fails_closed(self) -> None:
        stale = evidence()
        stale["observed_at_epoch"] = NOW - 86_401
        with self.assertRaisesRegex(ModuleReleaseQualificationError, "qualification_evidence_stale"):
            verify_module_release_qualification(stale, now_epoch=NOW)
        future = evidence()
        future["observed_at_epoch"] = NOW + 1
        with self.assertRaisesRegex(ModuleReleaseQualificationError, "qualification_evidence_stale"):
            verify_module_release_qualification(future, now_epoch=NOW)
        wrong = evidence()
        wrong["release_version"] = "0.12.0"
        with self.assertRaisesRegex(ModuleReleaseQualificationError, "qualification_release_rejected"):
            verify_module_release_qualification(wrong, now_epoch=NOW)

    def test_execution_or_acknowledgement_boundary_drift_fails_closed(self) -> None:
        cases = (
            ("ack", "acknowledgement_consumed", True, "qualification_acknowledgement_consumption_rejected"),
            ("persist", "lifecycle_persistence_enabled", True, "qualification_mutation_boundary_rejected"),
            ("execute", "lifecycle_execution_enabled", True, "qualification_mutation_boundary_rejected"),
            ("activate", "production_activation_enabled", True, "qualification_mutation_boundary_rejected"),
        )
        for name, key, value, code in cases:
            candidate = evidence()
            candidate["safety"][key] = value
            with self.subTest(name=name), self.assertRaisesRegex(ModuleReleaseQualificationError, code):
                verify_module_release_qualification(candidate, now_epoch=NOW)
        blockers = evidence()
        blockers["safety"]["remaining_blockers"] = []
        with self.assertRaisesRegex(ModuleReleaseQualificationError, "qualification_execution_blockers_rejected"):
            verify_module_release_qualification(blockers, now_epoch=NOW)

    def test_loader_rejects_ambiguous_json(self) -> None:
        payloads = (
            b'{"schema":"one","schema":"two"}',
            b"\xef\xbb\xbf{}",
            b'{"value":1.5}',
            b'{"value":NaN}',
            b"{" + b" " * MAX_QUALIFICATION_BYTES + b"}",
        )
        for payload in payloads:
            with self.subTest(payload=payload[:24]), self.assertRaises(ModuleReleaseQualificationError):
                load_module_release_qualification(payload)


if __name__ == "__main__":
    unittest.main()
