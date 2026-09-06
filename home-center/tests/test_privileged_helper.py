from __future__ import annotations

import json
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import home_center.privileged_helper as privileged_helper
from home_center.privileged_helper import (
    ACTIONS,
    Action,
    HelperEngine,
    HelperError,
    MAX_OUTPUT_BYTES,
    _classify_bounded_action_failure,
    _receive_request,
    _sha256,
    _validate_request,
)


class HelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.policy = self.root / "policy.json"
        self.state = self.root / "state.json"
        self.policy.write_text(
            json.dumps(
                {
                    "schema": "home-center.helper.policy.v1",
                    "callers": {"home-center": ["helper.probe"]},
                    "enabled_actions": ["helper.probe.v1"],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def req(self, request_id: str = "req-0001") -> dict:
        return {
            "schema": "home-center.helper.request.v1",
            "request_id": request_id,
            "action": "helper.probe.v1",
            "params": {},
            "nonce": "0123456789abcdef0123456789abcdef",
        }

    def test_probe_and_exact_replay(self) -> None:
        engine = HelperEngine(self.policy, self.state)
        first = engine.execute(self.req(), 1234, "home-center")
        second = engine.execute(self.req(), 1234, "home-center")
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "succeeded")
        self.assertEqual(first["exit_code"], 0)
        self.assertEqual(first["request_sha256"], _sha256(self.req()))
        self.assertEqual(len(first["evidence_sha256"]), 64)

    def test_conflicting_request_id_is_deterministically_replayed(self) -> None:
        engine = HelperEngine(self.policy, self.state)
        engine.execute(self.req(), 1234, "home-center")
        other = self.req()
        other["nonce"] = "fedcba9876543210fedcba9876543210"
        first = engine.execute(other, 1234, "home-center")
        second = engine.execute(other, 1234, "home-center")
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "rejected")
        self.assertEqual(first["reason"], "request_id_conflict")

    def test_unknown_caller_is_denied_and_replayed_without_execution(self) -> None:
        engine = HelperEngine(self.policy, self.state)
        with mock.patch("home_center.privileged_helper.subprocess.run") as run:
            first = engine.execute(self.req("req-0002"), 9999, "intruder")
            second = engine.execute(self.req("req-0002"), 9999, "intruder")
        run.assert_not_called()
        self.assertEqual(first, second)
        self.assertEqual(first["reason"], "caller_not_allowed")
        self.assertIsNone(first["exit_code"])

    def test_disabled_action_denied_before_execution(self) -> None:
        self.policy.write_text(
            json.dumps(
                {
                    "schema": "home-center.helper.policy.v1",
                    "callers": {"home-center": ["helper.probe"]},
                    "enabled_actions": [],
                }
            ),
            encoding="utf-8",
        )
        engine = HelperEngine(self.policy, self.state)
        with mock.patch("home_center.privileged_helper.subprocess.run") as run:
            result = engine.execute(self.req("req-0003"), 1234, "home-center")
        run.assert_not_called()
        self.assertEqual(result["reason"], "action_disabled")

    def test_interrupted_execution_becomes_unknown(self) -> None:
        request = self.req("req-0004")
        self.state.write_text(
            json.dumps(
                {
                    "schema": "home-center.helper.state.v1",
                    "requests": {},
                    "conflicts": {},
                    "last_evidence_sha256": None,
                    "inflight": {
                        "request_id": request["request_id"],
                        "request_sha256": _sha256(request),
                        "action": request["action"],
                        "caller_uid": 1234,
                        "caller_name": "home-center",
                        "started_at": "2026-09-06T00:00:00Z",
                        "policy_sha256": "0" * 64,
                    },
                }
            ),
            encoding="utf-8",
        )
        engine = HelperEngine(self.policy, self.state)
        recovered = engine.state["requests"]["req-0004"]["result"]
        self.assertEqual(recovered["status"], "unknown")
        self.assertIn("operator_recovery", recovered["reason"])
        replay = engine.execute(request, 1234, "home-center")
        self.assertEqual(replay, recovered)

    def test_policy_cannot_define_arbitrary_permission_or_command(self) -> None:
        invalid = {
            "schema": "home-center.helper.policy.v1",
            "callers": {"home-center": ["shell.execute"]},
            "enabled_actions": ["helper.probe.v1"],
        }
        self.policy.write_text(json.dumps(invalid), encoding="utf-8")
        with self.assertRaises(HelperError):
            HelperEngine(self.policy, self.state)
        invalid = {
            "schema": "home-center.helper.policy.v1",
            "callers": {"home-center": ["helper.probe"]},
            "enabled_actions": ["helper.probe.v1"],
            "executable": "/bin/sh",
        }
        self.policy.write_text(json.dumps(invalid), encoding="utf-8")
        with self.assertRaises(HelperError):
            HelperEngine(self.policy, self.state)

    def test_unknown_action_and_injection_params_fail_before_execution(self) -> None:
        unknown = self.req("req-0005")
        unknown["action"] = "shell.run.v1"
        with self.assertRaises(HelperError):
            _validate_request(unknown)
        injected = self.req("req-0006")
        injected["params"] = {"argv": ["/bin/sh", "-c", "id"]}
        with self.assertRaises(HelperError):
            _validate_request(injected)

    def test_timeout_is_terminal_failed_result(self) -> None:
        engine = HelperEngine(self.policy, self.state)
        with mock.patch(
            "home_center.privileged_helper.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["/usr/bin/true"], timeout=5, output=b"partial", stderr=b"late"),
        ):
            result = engine.execute(self.req("req-0007"), 1234, "home-center")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "action_timeout")
        self.assertIsNone(engine.state["inflight"])

    def test_mutating_action_timeout_requires_recovery(self) -> None:
        recovery_action = Action(
            permission="helper.probe",
            executable="/usr/bin/true",
            argv=(),
            timeout_seconds=5,
            timeout_requires_recovery=True,
        )
        with (
            mock.patch.dict(ACTIONS, {"helper.probe.v1": recovery_action}),
            mock.patch(
                "home_center.privileged_helper.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["/usr/bin/true"], timeout=5),
            ),
        ):
            engine = HelperEngine(self.policy, self.state)
            result = engine.execute(self.req("req-timeout-recovery"), 1234, "home-center")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "action_timeout_recovery_required")
        self.assertIsNone(engine.state["inflight"])
        self.assertEqual(engine.state["recovery_required"]["request_id"], "req-timeout-recovery")
        with mock.patch.dict(ACTIONS, {"helper.probe.v1": recovery_action}):
            blocked = engine.execute(self.req("req-timeout-blocked"), 1234, "home-center")
        self.assertEqual(blocked["status"], "rejected")
        self.assertEqual(blocked["reason"], "mutation_recovery_required")

    def test_activation_failure_classification_is_fixed_and_non_secret(self) -> None:
        rolled_back = json.dumps(
            {
                "schema": "home-center.tls-activation-result.v1",
                "status": "rolled_back",
                "reason": "activation_rolled_back",
            }
        )
        unknown = json.dumps(
            {
                "schema": "home-center.tls-activation-result.v1",
                "status": "unknown",
                "reason": "rollback_failed_recovery_required",
            }
        )
        self.assertEqual(
            _classify_bounded_action_failure("tls.web.activate.v1", rolled_back),
            ("failed", "activation_rolled_back"),
        )
        self.assertEqual(
            _classify_bounded_action_failure("tls.web.activate.v1", unknown),
            ("unknown", "rollback_failed_recovery_required"),
        )
        self.assertEqual(
            _classify_bounded_action_failure("tls.web.activate.v1", "not-json secret"),
            ("unknown", "action_result_unknown_recovery_required"),
        )
        rolled_back_with_extra_field = json.dumps(
            {
                "schema": "home-center.tls-activation-result.v1",
                "status": "rolled_back",
                "reason": "activation_rolled_back",
                "untrusted": "must-not-become-a-reason",
            }
        )
        self.assertEqual(
            _classify_bounded_action_failure("tls.web.activate.v1", rolled_back_with_extra_field),
            ("unknown", "action_result_unknown_recovery_required"),
        )
        preflight = json.dumps(
            {
                "schema": "home-center.tls-activation-result.v1",
                "status": "failed",
                "reason": "activation_preflight_failed",
            }
        )
        self.assertEqual(
            _classify_bounded_action_failure("tls.web.activate.v1", preflight),
            ("failed", "activation_preflight_failed"),
        )

    def test_unstructured_mutating_failure_sets_recovery_latch(self) -> None:
        recovery_action = Action(
            permission="helper.probe",
            executable="/usr/bin/true",
            argv=(),
            timeout_seconds=5,
            timeout_requires_recovery=True,
        )
        completed = subprocess.CompletedProcess(
            args=["/usr/bin/true"],
            returncode=-9,
            stdout=b'{"truncated":',
            stderr=b"",
        )
        with (
            mock.patch.dict(ACTIONS, {"helper.probe.v1": recovery_action}),
            mock.patch("home_center.privileged_helper.subprocess.run", return_value=completed),
        ):
            engine = HelperEngine(self.policy, self.state)
            result = engine.execute(self.req("req-mutating-crash"), 1234, "home-center")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "action_result_unknown_recovery_required")
        self.assertEqual(engine.state["recovery_required"]["request_id"], "req-mutating-crash")

    def test_request_receive_timeout_bounds_slow_client(self) -> None:
        server, client = socket.socketpair()
        self.addCleanup(server.close)
        self.addCleanup(client.close)
        with self.assertRaises(TimeoutError):
            _receive_request(server, timeout_seconds=0.01)

    def test_reconcile_action_clears_latch_only_with_strict_evidence(self) -> None:
        reconcile_action = Action(
            permission="tls.web.reconcile",
            executable="/usr/bin/python3",
            argv=("/opt/home-center/current/home_center/tls_reconcile.py",),
            timeout_seconds=60,
        )
        self.policy.write_text(
            json.dumps(
                {
                    "schema": "home-center.helper.policy.v1",
                    "callers": {"home-center": ["tls.web.reconcile"]},
                    "enabled_actions": ["tls.web.reconcile.v1"],
                }
            ),
            encoding="utf-8",
        )
        request = {
            "schema": "home-center.helper.request.v1",
            "request_id": "req-reconcile-valid",
            "action": "tls.web.reconcile.v1",
            "params": {},
            "nonce": "0123456789abcdef0123456789abcdef",
        }
        evidence = {
            "schema": "home-center.tls-reconcile-result.v1",
            "status": "reconciled",
            "node_id": "hm-dm-dc02",
            "mode": "separate-web-identity",
            "release": "a" * 24,
            "certificate_sha256": "a" * 64,
        }
        completed = subprocess.CompletedProcess(
            args=["/usr/bin/python3"], returncode=0, stdout=json.dumps(evidence).encode(), stderr=b""
        )
        with (
            mock.patch.dict(ACTIONS, {"tls.web.reconcile.v1": reconcile_action}),
            mock.patch.object(privileged_helper, "PERMISSIONS", frozenset({"tls.web.reconcile"})),
            mock.patch("home_center.privileged_helper.subprocess.run", return_value=completed),
        ):
            engine = HelperEngine(self.policy, self.state)
            engine.state["recovery_required"] = {"reason": "test"}
            result = engine.execute(request, 1234, "home-center")
        self.assertEqual(result["status"], "succeeded")
        self.assertNotIn("recovery_required", engine.state)

    def test_output_is_bounded_in_bytes(self) -> None:
        engine = HelperEngine(self.policy, self.state)
        completed = subprocess.CompletedProcess(
            args=["/usr/bin/true"],
            returncode=0,
            stdout=b"x" * (MAX_OUTPUT_BYTES + 1024),
            stderr=b"y" * (MAX_OUTPUT_BYTES + 1024),
        )
        with mock.patch("home_center.privileged_helper.subprocess.run", return_value=completed):
            result = engine.execute(self.req("req-0008"), 1234, "home-center")
        self.assertLessEqual(len(result["stdout"].encode("utf-8")), MAX_OUTPUT_BYTES)
        self.assertLessEqual(len(result["stderr"].encode("utf-8")), MAX_OUTPUT_BYTES)


if __name__ == "__main__":
    unittest.main()
