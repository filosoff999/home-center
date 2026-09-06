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

    def test_partial_candidate_is_blocked_before_helper(self) -> None:
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
        helper.assert_not_called()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "partial_candidate")

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
            },
            "renewal": {"due": False},
            "candidate": {"certificate_present": False, "private_key_present": False, "complete": False, "partial": False},
        }
        activation = {
            "schema": "home-center.tls-activation-result.v1",
            "status": "activated",
            "certificate_sha256": fingerprint,
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


if __name__ == "__main__":
    unittest.main()
