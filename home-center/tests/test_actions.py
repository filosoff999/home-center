from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))
sys.path.insert(0, str(ROOT / "tests"))

from home_center.actions import (  # noqa: E402
    ActionIdempotencyConflict,
    ActionPermissionDenied,
    ActionRegistry,
    ActionRequestError,
    ActionTargetConflict,
    SYSTEMCTL,
)
from home_center.store import StateStore  # noqa: E402
from schema_validator import validate  # noqa: E402


class ActionRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.tmp.name) / "state.sqlite3", b"a" * 32, "hm-dm-production")
        self.calls: list[list[str]] = []

        def runner(args, **kwargs):
            self.calls.append(list(args))
            self.assertEqual(args[0], SYSTEMCTL)
            self.assertNotIn("shell", kwargs)
            self.assertEqual(kwargs["timeout"], 5)
            self.assertEqual(kwargs["env"]["LC_ALL"], "C")
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="LoadState=loaded\nActiveState=active\nSubState=running\nUnitFileState=enabled\n",
                stderr="",
            )

        self.registry = ActionRegistry("hm-dm-dc01", self.store, runner=runner)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    @staticmethod
    def request(*, key: str = "request-0001", reason: str = "inspect owned service") -> dict:
        return {
            "schema": "home-center.action-request.v1",
            "idempotency_key": key,
            "target_node_id": "hm-dm-dc01",
            "reason": reason,
            "input": {"service": "home-center.service"},
        }

    def test_action_metadata_preserves_p1_jobs_table_shape(self) -> None:
        connection = sqlite3.connect(self.store.path)
        try:
            jobs_columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
            metadata_columns = {row[1] for row in connection.execute("PRAGMA table_info(action_job_metadata)")}
        finally:
            connection.close()
        self.assertNotIn("idempotency_key", jobs_columns)
        self.assertNotIn("request_hash", jobs_columns)
        self.assertNotIn("steps_json", jobs_columns)
        self.assertEqual(
            metadata_columns,
            {"job_id", "actor", "action_id", "idempotency_key", "request_hash", "steps_json"},
        )

    def test_catalog_is_machine_readable_and_immutable_to_callers(self) -> None:
        catalog = self.registry.catalog()
        schema = json.loads((ROOT / "contracts/actions/action-registry.v1.schema.json").read_text())
        validate(schema, catalog)
        self.assertEqual(catalog["actions"][0]["risk"], "read-only")
        catalog["actions"].clear()
        self.assertEqual(len(self.registry.catalog()["actions"]), 1)

    def test_action_persists_terminal_job_and_evidence(self) -> None:
        job, replay = self.registry.run(
            actor="bootstrap-admin",
            action_id="service.state.read.v1",
            request=self.request(),
            correlation_id="test-action-1",
        )
        self.assertFalse(replay)
        self.assertEqual(job["state"], "succeeded")
        self.assertEqual(job["result"]["service"], "home-center.service")
        self.assertEqual(job["result"]["active_state"], "active")
        self.assertEqual(job["steps"][0]["state"], "succeeded")
        self.assertEqual(job["recovery"]["strategy"], "none-read-only")
        self.assertEqual(job["evidence"]["target_node_id"], "hm-dm-dc01")
        persisted = self.store.job(job["job_id"])
        self.assertEqual(persisted, job)
        validate(json.loads((ROOT / "contracts/jobs/job.v1.schema.json").read_text()), job)
        validate(
            json.loads((ROOT / "contracts/actions/action-result.v1.schema.json").read_text()),
            {"schema": "home-center.action-result.v1", "idempotent_replay": False, "job": job},
        )
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.store.audit_events(1)[0]["outcome"], "succeeded")

    def test_same_request_is_replayed_without_second_execution(self) -> None:
        first, first_replay = self.registry.run(
            actor="bootstrap-admin",
            action_id="service.state.read.v1",
            request=self.request(),
            correlation_id="test-action-2a",
        )
        second, second_replay = self.registry.run(
            actor="bootstrap-admin",
            action_id="service.state.read.v1",
            request=self.request(),
            correlation_id="test-action-2b",
        )
        self.assertFalse(first_replay)
        self.assertTrue(second_replay)
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(len(self.calls), 1)

    def test_idempotency_conflict_is_rejected(self) -> None:
        self.registry.run(
            actor="bootstrap-admin",
            action_id="service.state.read.v1",
            request=self.request(),
            correlation_id="test-action-3a",
        )
        with self.assertRaises(ActionIdempotencyConflict):
            self.registry.run(
                actor="bootstrap-admin",
                action_id="service.state.read.v1",
                request=self.request(reason="different intent"),
                correlation_id="test-action-3b",
            )
        self.assertEqual(len(self.calls), 1)

    def test_injection_shaped_service_is_rejected_before_execution(self) -> None:
        request = self.request()
        request["input"]["service"] = "home-center.service;reboot"
        with self.assertRaises(ActionRequestError):
            self.registry.run(
                actor="bootstrap-admin",
                action_id="service.state.read.v1",
                request=request,
                correlation_id="test-action-4",
            )
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.jobs(), [])

    def test_permission_and_remote_target_are_fail_closed(self) -> None:
        with self.assertRaises(ActionPermissionDenied):
            self.registry.run(
                actor="observer",
                action_id="service.state.read.v1",
                request=self.request(),
                correlation_id="test-action-5a",
            )
        request = self.request()
        request["target_node_id"] = "hm-dm-dc02"
        with self.assertRaises(ActionTargetConflict):
            self.registry.run(
                actor="bootstrap-admin",
                action_id="service.state.read.v1",
                request=request,
                correlation_id="test-action-5b",
            )
        self.assertEqual(self.calls, [])

    def test_executor_failure_is_terminal_and_secret_safe(self) -> None:
        def timeout(args, **kwargs):
            raise subprocess.TimeoutExpired(args, kwargs["timeout"], output="sensitive output")

        registry = ActionRegistry("hm-dm-dc01", self.store, runner=timeout)
        job, replay = registry.run(
            actor="bootstrap-admin",
            action_id="service.state.read.v1",
            request=self.request(key="request-timeout"),
            correlation_id="test-action-6",
        )
        self.assertFalse(replay)
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["result"], {"schema": "home-center.action-error.v1", "code": "action_timeout"})
        self.assertNotIn("sensitive", json.dumps(job))


if __name__ == "__main__":
    unittest.main()
