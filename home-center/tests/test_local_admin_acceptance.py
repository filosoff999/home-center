from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))
sys.path.insert(0, str(ROOT / "tests"))

from home_center.local_admin_acceptance import (  # noqa: E402
    MAX_ACCEPTANCE_BYTES,
    LocalAdminAcceptanceError,
    load_local_admin_acceptance,
    verify_local_admin_recovery_acceptance,
)
from schema_validator import validate  # noqa: E402


def evidence() -> dict:
    return {
        "schema": "home-center.local-admin-recovery-acceptance.v1",
        "cluster_id": "hm-dm-production",
        "success": {
            "transaction_id": "la-20260907T120000Z-0123456789abcdef",
            "commit_order": ["hm-dm-dc02", "hm-dm-dc01"],
            "nodes": [
                {
                    "node_id": "hm-dm-dc02",
                    "role": "standby",
                    "commit_sequence": 1,
                    "new_credential_canary": "passed",
                    "credential_record_sha256": "sha256:" + "1" * 64,
                },
                {
                    "node_id": "hm-dm-dc01",
                    "role": "leader",
                    "commit_sequence": 2,
                    "new_credential_canary": "passed",
                    "credential_record_sha256": "sha256:" + "2" * 64,
                },
            ],
            "terminal_phase": "completed",
            "outcome": "accepted",
        },
        "rollback": {
            "terminal_phase": "rolled_back",
            "outcome": "rolled_back",
            "old_credential_canary": "passed",
            "new_credential_canary": "rejected",
            "automatic_retry": False,
        },
        "ambiguous": {
            "terminal_phase": "recovery_required",
            "outcome": "unknown",
            "recovery_required": True,
            "automatic_retry": False,
            "remote_recovery_enabled": False,
        },
        "recovery_boundary": {
            "local_console_only": True,
            "root_required": True,
            "ssh_pty_rejected": True,
            "evidence_chain": "passed",
        },
        "audit": {"audit_chain": "passed", "secret_scan": "passed"},
    }


class LocalAdminAcceptanceTests(unittest.TestCase):
    def test_complete_secret_free_evidence_is_accepted(self) -> None:
        value = evidence()
        evidence_schema = json.loads(
            (ROOT / "contracts/auth/local-admin-recovery-acceptance.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        validate(evidence_schema, value)
        result = verify_local_admin_recovery_acceptance(value)
        result_schema = json.loads(
            (ROOT / "contracts/auth/local-admin-recovery-acceptance-result.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        validate(result_schema, result)
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["rollback_verified"])
        self.assertTrue(result["ambiguous_recovery_verified"])
        self.assertTrue(result["protected_recovery_verified"])
        self.assertFalse(result["production_mutation_enabled"])
        self.assertRegex(result["evidence_sha256"], r"^sha256:[0-9a-f]{64}$")

    def test_acceptance_is_deterministic(self) -> None:
        first = verify_local_admin_recovery_acceptance(evidence())
        second = verify_local_admin_recovery_acceptance(copy.deepcopy(evidence()))
        self.assertEqual(first, second)

    def test_commit_order_and_independent_credential_state_are_mandatory(self) -> None:
        wrong_order = evidence()
        wrong_order["success"]["commit_order"].reverse()
        with self.assertRaisesRegex(LocalAdminAcceptanceError, "acceptance_commit_order_rejected"):
            verify_local_admin_recovery_acceptance(wrong_order)

        same_state = evidence()
        same_state["success"]["nodes"][1]["credential_record_sha256"] = same_state["success"]["nodes"][0]["credential_record_sha256"]
        with self.assertRaisesRegex(LocalAdminAcceptanceError, "acceptance_independent_credential_state_rejected"):
            verify_local_admin_recovery_acceptance(same_state)

    def test_canary_rollback_and_ambiguous_outcome_fail_closed(self) -> None:
        failed_canary = evidence()
        failed_canary["success"]["nodes"][0]["new_credential_canary"] = "failed"
        with self.assertRaisesRegex(LocalAdminAcceptanceError, "acceptance_new_credential_canary_rejected"):
            verify_local_admin_recovery_acceptance(failed_canary)

        retry = evidence()
        retry["rollback"]["automatic_retry"] = True
        with self.assertRaisesRegex(LocalAdminAcceptanceError, "acceptance_rollback_rejected"):
            verify_local_admin_recovery_acceptance(retry)

        ambiguous = evidence()
        ambiguous["ambiguous"]["automatic_retry"] = True
        with self.assertRaisesRegex(LocalAdminAcceptanceError, "acceptance_ambiguous_recovery_rejected"):
            verify_local_admin_recovery_acceptance(ambiguous)

    def test_protected_recovery_boundary_is_mandatory(self) -> None:
        remote = evidence()
        remote["recovery_boundary"]["local_console_only"] = False
        with self.assertRaisesRegex(LocalAdminAcceptanceError, "acceptance_recovery_boundary_rejected"):
            verify_local_admin_recovery_acceptance(remote)

        ssh = evidence()
        ssh["recovery_boundary"]["ssh_pty_rejected"] = False
        with self.assertRaisesRegex(LocalAdminAcceptanceError, "acceptance_recovery_boundary_rejected"):
            verify_local_admin_recovery_acceptance(ssh)

    def test_secret_named_fields_unknown_fields_and_bad_json_are_rejected(self) -> None:
        secret = evidence()
        secret["audit"]["password_hash"] = "not-allowed"
        with self.assertRaisesRegex(LocalAdminAcceptanceError, "acceptance_secret_field_rejected|acceptance_audit_shape_rejected"):
            verify_local_admin_recovery_acceptance(secret)

        unknown = evidence()
        unknown["comment"] = "free form"
        with self.assertRaisesRegex(LocalAdminAcceptanceError, "acceptance_document_shape_rejected"):
            verify_local_admin_recovery_acceptance(unknown)

        for payload in (
            b'{"schema":"one","schema":"two"}',
            b"\xef\xbb\xbf{}",
            b'{"value":1.5}',
            b'{"value":NaN}',
            b"{" + b" " * MAX_ACCEPTANCE_BYTES + b"}",
        ):
            with self.subTest(payload=payload[:32]), self.assertRaises(LocalAdminAcceptanceError):
                load_local_admin_acceptance(payload)

    def test_result_never_contains_secret_material(self) -> None:
        serialized = json.dumps(verify_local_admin_recovery_acceptance(evidence()), sort_keys=True).casefold()
        for forbidden in ("password", "salt", "verifier", "secret", "token", "private_key"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
