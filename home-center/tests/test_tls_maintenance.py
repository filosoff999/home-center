from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from home_center import tls_maintenance


class TLSMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SimpleNamespace(node_id="hm-dm-dc02")
        self.base = {
            "candidate": {"certificate_present": False, "private_key_present": False, "complete": False, "partial": False},
            "renewal": {"due": False},
            "web": {
                "mode": "separate-web-identity",
                "fingerprint_sha256": "a" * 64,
                "chain_valid": True,
                "hostname_match": True,
                "profile_valid": True,
                "san_policy_valid": True,
            },
        }

    def test_healthy_certificate_does_not_call_helper(self) -> None:
        with (
            mock.patch.object(tls_maintenance, "load_config", return_value=self.config),
            mock.patch.object(tls_maintenance, "tls_status", return_value=self.base),
            mock.patch.object(tls_maintenance, "call_helper") as helper,
            mock.patch.object(tls_maintenance, "_atomic_status") as persist,
        ):
            result = tls_maintenance.maintain()
        helper.assert_not_called()
        persist.assert_called_once()
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["action"], "none")

    def test_due_without_candidate_reports_renewal_required(self) -> None:
        state = dict(self.base)
        state["renewal"] = {"due": True}
        with (
            mock.patch.object(tls_maintenance, "load_config", return_value=self.config),
            mock.patch.object(tls_maintenance, "tls_status", return_value=state),
            mock.patch.object(tls_maintenance, "call_helper") as helper,
            mock.patch.object(tls_maintenance, "_atomic_status"),
        ):
            result = tls_maintenance.maintain()
        helper.assert_not_called()
        self.assertEqual(result["status"], "renewal_required")
        self.assertEqual(result["reason"], "candidate_not_staged")

    def test_partial_candidate_attempts_reconcile_then_blocks(self) -> None:
        state = dict(self.base)
        state["renewal"] = {"due": True}
        state["candidate"] = {"certificate_present": True, "private_key_present": False, "complete": False, "partial": True}
        with (
            mock.patch.object(tls_maintenance, "load_config", return_value=self.config),
            mock.patch.object(tls_maintenance, "tls_status", return_value=state),
            mock.patch.object(tls_maintenance, "call_helper") as helper,
            mock.patch.object(tls_maintenance, "_atomic_status"),
        ):
            result = tls_maintenance.maintain()
        helper.assert_called_once_with("tls.web.reconcile.v1", request_prefix="tls-reconcile")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "partial_candidate_recovery_required")

    def test_due_complete_candidate_activates_and_rechecks_status(self) -> None:
        before = dict(self.base)
        before["renewal"] = {"due": True}
        before["candidate"] = {"certificate_present": True, "private_key_present": True, "complete": True, "partial": False}
        fingerprint = "b" * 64
        after = {
            **before,
            "web": {
                "mode": "separate-web-identity",
                "fingerprint_sha256": fingerprint,
                "chain_valid": True,
                "hostname_match": True,
                "profile_valid": True,
                "san_policy_valid": True,
            },
            "renewal": {"due": False},
            "candidate": {"certificate_present": False, "private_key_present": False, "complete": False, "partial": False},
        }
        activation = {
            "schema": "home-center.tls-activation-result.v1",
            "status": "activated",
            "certificate_sha256": fingerprint,
            "release": fingerprint[:24],
            "previous_release": "1" * 24,
        }
        helper_result = {
            "schema": "home-center.helper.result.v1",
            "status": "succeeded",
            "exit_code": 0,
            "reason": None,
            "stdout": json.dumps(activation),
        }
        with (
            mock.patch.object(tls_maintenance, "load_config", return_value=self.config),
            mock.patch.object(tls_maintenance, "tls_status", side_effect=[before, after]),
            mock.patch.object(tls_maintenance, "call_helper", return_value=helper_result) as helper,
            mock.patch.object(tls_maintenance, "_atomic_status"),
        ):
            result = tls_maintenance.maintain()
        helper.assert_called_once_with("tls.web.activate.v1", request_prefix="tls-maintenance")
        self.assertEqual(result["status"], "rotated")
        self.assertEqual(result["certificate_sha256"], fingerprint)
        self.assertEqual(result["release"], fingerprint[:24])
        self.assertEqual(result["previous_release"], "1" * 24)

    def test_force_activates_complete_candidate_even_when_not_due(self) -> None:
        before = dict(self.base)
        before["candidate"] = {"certificate_present": True, "private_key_present": True, "complete": True, "partial": False}
        fingerprint = "c" * 64
        after = {
            **before,
            "web": {
                "mode": "separate-web-identity",
                "fingerprint_sha256": fingerprint,
                "chain_valid": True,
                "hostname_match": True,
                "profile_valid": True,
                "san_policy_valid": True,
            },
            "candidate": {"certificate_present": False, "private_key_present": False, "complete": False, "partial": False},
        }
        helper_result = {
            "status": "succeeded",
            "exit_code": 0,
            "reason": None,
            "stdout": json.dumps(
                {
                    "schema": "home-center.tls-activation-result.v1",
                    "status": "activated",
                    "certificate_sha256": fingerprint,
                    "release": fingerprint[:24],
                    "previous_release": None,
                }
            ),
        }
        with (
            mock.patch.object(tls_maintenance, "load_config", return_value=self.config),
            mock.patch.object(tls_maintenance, "tls_status", side_effect=[before, after]),
            mock.patch.object(tls_maintenance, "call_helper", return_value=helper_result),
            mock.patch.object(tls_maintenance, "_atomic_status"),
        ):
            result = tls_maintenance.maintain(activate_staged=True)
        self.assertEqual(result["status"], "rotated")

    def test_post_activation_rejects_browser_incompatible_profile(self) -> None:
        before = dict(self.base)
        before["renewal"] = {"due": True}
        before["candidate"] = {"certificate_present": True, "private_key_present": True, "complete": True, "partial": False}
        fingerprint = "d" * 64
        after = {
            **before,
            "web": {
                "mode": "separate-web-identity",
                "fingerprint_sha256": fingerprint,
                "chain_valid": True,
                "hostname_match": True,
                "profile_valid": False,
                "san_policy_valid": True,
            },
        }
        helper_result = {
            "status": "succeeded",
            "exit_code": 0,
            "reason": None,
            "stdout": json.dumps(
                {
                    "schema": "home-center.tls-activation-result.v1",
                    "status": "activated",
                    "certificate_sha256": fingerprint,
                    "release": fingerprint[:24],
                    "previous_release": None,
                }
            ),
        }
        with (
            mock.patch.object(tls_maintenance, "load_config", return_value=self.config),
            mock.patch.object(tls_maintenance, "tls_status", side_effect=[before, after]),
            mock.patch.object(tls_maintenance, "call_helper", return_value=helper_result),
            mock.patch.object(tls_maintenance, "_atomic_status"),
        ):
            result = tls_maintenance.maintain()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "post_activation_validation_failed_recovery_required")

    def test_helper_failure_is_terminal_and_does_not_parse_secret_output(self) -> None:
        before = dict(self.base)
        before["renewal"] = {"due": True}
        before["candidate"] = {"certificate_present": True, "private_key_present": True, "complete": True, "partial": False}
        with (
            mock.patch.object(tls_maintenance, "load_config", return_value=self.config),
            mock.patch.object(tls_maintenance, "tls_status", return_value=before),
            mock.patch.object(
                tls_maintenance,
                "call_helper",
                return_value={"status": "failed", "exit_code": 1, "reason": "action_exit_nonzero", "stdout": "ignored"},
            ),
            mock.patch.object(tls_maintenance, "_atomic_status"),
        ):
            result = tls_maintenance.maintain()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "action_exit_nonzero")
        self.assertIsNone(result["certificate_sha256"])

    def test_unknown_helper_result_requires_recovery_and_is_not_retried(self) -> None:
        before = dict(self.base)
        before["renewal"] = {"due": True}
        before["candidate"] = {"certificate_present": True, "private_key_present": True, "complete": True, "partial": False}
        with (
            mock.patch.object(tls_maintenance, "load_config", return_value=self.config),
            mock.patch.object(tls_maintenance, "tls_status", return_value=before),
            mock.patch.object(
                tls_maintenance,
                "call_helper",
                return_value={"status": "unknown", "exit_code": None, "reason": "action_timeout_recovery_required"},
            ),
            mock.patch.object(tls_maintenance, "_atomic_status"),
        ):
            result = tls_maintenance.maintain()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "action_timeout_recovery_required")

    def test_safely_rolled_back_activation_is_distinct_from_failed_rollback(self) -> None:
        before = dict(self.base)
        before["renewal"] = {"due": True}
        before["candidate"] = {"certificate_present": True, "private_key_present": True, "complete": True, "partial": False}
        outcomes = (
            ({"status": "failed", "exit_code": 1, "reason": "activation_rolled_back"}, "rolled_back"),
            ({"status": "rejected", "exit_code": None, "reason": "mutation_recovery_required"}, "blocked"),
        )
        for helper_result, expected in outcomes:
            with self.subTest(expected=expected):
                with (
                    mock.patch.object(tls_maintenance, "load_config", return_value=self.config),
                    mock.patch.object(tls_maintenance, "tls_status", return_value=before),
                    mock.patch.object(tls_maintenance, "call_helper", return_value=helper_result),
                    mock.patch.object(tls_maintenance, "_atomic_status"),
                ):
                    result = tls_maintenance.maintain()
                self.assertEqual(result["status"], expected)


if __name__ == "__main__":
    unittest.main()
