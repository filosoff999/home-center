from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))
sys.path.insert(0, str(ROOT / "tests"))

from home_center.local_admin_acceptance import verify_local_admin_recovery_acceptance  # noqa: E402
from home_center.local_admin_release_qualification import (  # noqa: E402
    EXPECTED_WORKSTREAMS,
    MAX_QUALIFICATION_BYTES,
    LocalAdminReleaseQualificationError,
    load_local_admin_release_qualification,
    verify_local_admin_release_qualification,
)
from schema_validator import validate  # noqa: E402
from test_local_admin_acceptance import evidence as recovery_evidence  # noqa: E402

NOW = 1_788_800_000


def evidence() -> dict:
    recovery = verify_local_admin_recovery_acceptance(recovery_evidence())
    return {
        "schema": "home-center.local-admin-release-qualification.v1",
        "release_version": "0.12.0",
        "observed_at_epoch": NOW - 60,
        "workstreams": [
            {"id": item, "evidence_sha256": "sha256:" + f"{index + 20:064x}"}
            for index, item in enumerate(EXPECTED_WORKSTREAMS, start=1)
        ],
        "recovery_acceptance": recovery,
        "safety": {
            "credential_mutation_enabled": False,
            "remote_recovery_enabled": False,
            "automatic_retry_after_ambiguous": False,
            "local_console_recovery_required": True,
            "secret_scan": "passed",
        },
    }


class LocalAdminReleaseQualificationTests(unittest.TestCase):
    def test_complete_acceptance_chain_qualifies_without_mutation(self) -> None:
        value = evidence()
        schema = json.loads((ROOT / "contracts/auth/local-admin-release-qualification.v1.schema.json").read_text(encoding="utf-8"))
        validate(schema, value)
        result = verify_local_admin_release_qualification(value, now_epoch=NOW)
        result_schema = json.loads((ROOT / "contracts/auth/local-admin-release-qualification-result.v1.schema.json").read_text(encoding="utf-8"))
        validate(result_schema, result)
        self.assertEqual(result["status"], "qualified")
        self.assertFalse(result["credential_mutation_enabled"])
        self.assertFalse(result["remote_recovery_enabled"])
        self.assertFalse(result["production_mutation_enabled"])
        self.assertEqual(result["transaction_id"], value["recovery_acceptance"]["transaction_id"])

    def test_result_is_deterministic(self) -> None:
        self.assertEqual(
            verify_local_admin_release_qualification(evidence(), now_epoch=NOW),
            verify_local_admin_release_qualification(copy.deepcopy(evidence()), now_epoch=NOW),
        )

    def test_recovery_acceptance_is_mandatory_and_exact(self) -> None:
        cases = (
            ("rollback", "rollback_verified", False),
            ("ambiguous", "ambiguous_recovery_verified", False),
            ("protected", "protected_recovery_verified", False),
            ("mutation", "production_mutation_enabled", True),
        )
        for name, key, value in cases:
            candidate = evidence()
            candidate["recovery_acceptance"][key] = value
            with self.subTest(name=name), self.assertRaisesRegex(
                LocalAdminReleaseQualificationError, "qualification_recovery_acceptance_rejected"
            ):
                verify_local_admin_release_qualification(candidate, now_epoch=NOW)

    def test_safety_drift_fails_closed(self) -> None:
        cases = (
            ("credential", "credential_mutation_enabled", True),
            ("remote", "remote_recovery_enabled", True),
            ("retry", "automatic_retry_after_ambiguous", True),
            ("console", "local_console_recovery_required", False),
        )
        for name, key, value in cases:
            candidate = evidence()
            candidate["safety"][key] = value
            with self.subTest(name=name), self.assertRaisesRegex(
                LocalAdminReleaseQualificationError, "qualification_safety_boundary_rejected"
            ):
                verify_local_admin_release_qualification(candidate, now_epoch=NOW)

    def test_workstream_set_freshness_and_secret_fields_fail_closed(self) -> None:
        missing = evidence()
        missing["workstreams"].pop()
        with self.assertRaisesRegex(LocalAdminReleaseQualificationError, "qualification_workstream_set_rejected"):
            verify_local_admin_release_qualification(missing, now_epoch=NOW)
        stale = evidence()
        stale["observed_at_epoch"] = NOW - 86_401
        with self.assertRaisesRegex(LocalAdminReleaseQualificationError, "qualification_evidence_stale"):
            verify_local_admin_release_qualification(stale, now_epoch=NOW)
        secret = evidence()
        secret["safety"]["password_hash"] = "forbidden"
        with self.assertRaisesRegex(
            LocalAdminReleaseQualificationError,
            "qualification_secret_field_rejected|qualification_safety_shape_rejected",
        ):
            verify_local_admin_release_qualification(secret, now_epoch=NOW)

    def test_loader_rejects_ambiguous_json(self) -> None:
        payloads = (
            b'{"schema":"one","schema":"two"}',
            b"\xef\xbb\xbf{}",
            b'{"value":1.5}',
            b'{"value":NaN}',
            b"{" + b" " * MAX_QUALIFICATION_BYTES + b"}",
        )
        for payload in payloads:
            with self.subTest(payload=payload[:24]), self.assertRaises(LocalAdminReleaseQualificationError):
                load_local_admin_release_qualification(payload)


if __name__ == "__main__":
    unittest.main()
