from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from home_center.local_admin_auth import LocalAdminCredentialStore
from home_center.local_admin_cluster import (
    COMMAND_SCHEMA,
    RESULT_SCHEMA,
    LocalAdminClusterCoordinator,
    LocalAdminClusterError,
    LocalAdminTransactionParticipant,
    decode_cluster_command,
)
from home_center.local_admin_provision import provision_credential_file
from home_center.local_admin_rotation import LocalAdminCredentialRotator, LocalAdminRotationError
from home_center.store import StateStore


CURRENT_PASSWORD = "current cluster password 17"
NEW_PASSWORD = "new cluster password 42"
TRANSACTION_ID = "la-20260907T120000Z-0123456789abcdef"
INTENT_TOKEN = "1" * 32


class PeerAdapter:
    def __init__(self, participant: LocalAdminTransactionParticipant) -> None:
        self.participant = participant
        self.actions: list[str] = []
        self.fail_canary = False
        self.ambiguous_commit = False
        self.block_rollback = False
        self.ambiguous_finalize = False

    def execute(self, command: dict[str, Any]) -> dict[str, Any]:
        self.actions.append(command["action"])
        if command["action"] == "canary" and self.fail_canary:
            return {
                "schema": RESULT_SCHEMA,
                "transaction_id": command["transaction_id"],
                "node_id": "hm-dm-dc02",
                "status": "failed",
                "phase": "committed",
                "reason": "local_canary_failed",
                "observed_at": "2026-09-07T12:00:00Z",
            }
        if command["action"] == "rollback" and self.block_rollback:
            raise LocalAdminClusterError("peer_transport_unavailable")
        result = self.participant.handle(command, coordinator_node_id="hm-dm-dc01")
        if command["action"] == "commit" and self.ambiguous_commit:
            raise LocalAdminClusterError("peer_transport_unavailable")
        if command["action"] == "finalize" and self.ambiguous_finalize:
            raise LocalAdminClusterError("peer_transport_unavailable")
        return result


class LocalAdminClusterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.uid = os.geteuid()
        self.gid = os.getegid()
        self.events: list[str] = []
        self.local_path = self._credential("dc01")
        self.remote_path = self._credential("dc02")
        self.local_store = StateStore(self.root / "state-dc01/state.sqlite3", b"a" * 32, "cluster-test")
        self.remote_store = StateStore(self.root / "state-dc02/state.sqlite3", b"b" * 32, "cluster-test")
        self.local_participant = self._participant("hm-dm-dc01", self.local_path, self.local_store)
        self.remote_participant = self._participant("hm-dm-dc02", self.remote_path, self.remote_store)
        self.peer = PeerAdapter(self.remote_participant)
        self.config = SimpleNamespace(
            cluster_id="cluster-test",
            node_id="hm-dm-dc01",
            role="leader",
            peer=SimpleNamespace(node_id="hm-dm-dc02"),
        )
        self.sleeps: list[float] = []
        self.coordinator = LocalAdminClusterCoordinator(
            config=self.config,
            store=self.local_store,
            participant=self.local_participant,
            peer_client=self.peer,
            sleep=self.sleeps.append,
            canary_attempts=3,
            canary_interval_seconds=0.25,
        )

    def tearDown(self) -> None:
        self.local_store.close()
        self.remote_store.close()
        self.tmp.cleanup()

    def _credential(self, name: str) -> Path:
        directory = self.root / f"secret-{name}"
        directory.mkdir()
        os.chmod(directory, 0o700)
        path = directory / "local-admin.json"
        provision_credential_file(
            path,
            "admin",
            CURRENT_PASSWORD,
            expected_directory_uid=self.uid,
            expected_directory_gid=self.gid,
            expected_directory_mode=0o700,
            file_uid=self.uid,
            file_gid=self.gid,
            file_mode=0o640,
        )
        return path

    def _rotator(self, path: Path) -> LocalAdminCredentialRotator:
        return LocalAdminCredentialRotator(
            path,
            expected_uid=self.uid,
            expected_gid=self.gid,
            expected_mode=0o640,
            expected_directory_uid=self.uid,
            expected_directory_gid=self.gid,
            expected_directory_mode=0o700,
        )

    def _authenticator(self, path: Path):
        def authenticate(username: str, password: str) -> str | None:
            store = LocalAdminCredentialStore(
                path,
                expected_uid=self.uid,
                expected_gid=self.gid,
                expected_mode=0o640,
            )
            return store.authenticate(username, password)

        return authenticate

    def _participant(self, node_id: str, path: Path, store: StateStore) -> LocalAdminTransactionParticipant:
        rotator = self._rotator(path)

        def result(operation: str, username: str, current: str, new: str) -> dict[str, Any]:
            try:
                if operation == "validate":
                    rotator.validate(username, current, new)
                else:
                    self.events.append(f"commit:{node_id}")
                    rotator.rotate(username, current, new)
            except LocalAdminRotationError as exc:
                return {"status": "rejected", "reason": exc.code}
            return {"status": "succeeded", "reason": None}

        return LocalAdminTransactionParticipant(
            cluster_id="cluster-test",
            node_id=node_id,
            store=store,
            validator=lambda username, current, new: result("validate", username, current, new),
            rotator=lambda username, current, new: result("rotate", username, current, new),
            authenticator=self._authenticator(path),
        )

    def _verify(self, path: Path, password: str) -> bool:
        return self._authenticator(path)("admin", password) == "admin"

    def test_standby_first_soak_then_leader_with_independent_salts(self) -> None:
        result = self.coordinator.change("admin", CURRENT_PASSWORD, NEW_PASSWORD)
        self.assertEqual(result["schema"], "home-center.local-admin-password-change-result.v2")
        self.assertEqual(result["commit_order"], ["hm-dm-dc02", "hm-dm-dc01"])
        self.assertEqual(self.events[:2], ["commit:hm-dm-dc02", "commit:hm-dm-dc01"])
        self.assertEqual(self.peer.actions, ["prepare", "commit", "canary", "canary", "canary", "finalize"])
        self.assertEqual(self.sleeps, [0.25, 0.25])
        self.assertTrue(self._verify(self.local_path, NEW_PASSWORD))
        self.assertTrue(self._verify(self.remote_path, NEW_PASSWORD))
        self.assertFalse(self._verify(self.local_path, CURRENT_PASSWORD))
        self.assertFalse(self._verify(self.remote_path, CURRENT_PASSWORD))
        local = json.loads(self.local_path.read_text(encoding="utf-8"))
        remote = json.loads(self.remote_path.read_text(encoding="utf-8"))
        self.assertNotEqual(local["salt_b64"], remote["salt_b64"])
        self.assertNotEqual(local["verifier_b64"], remote["verifier_b64"])
        evidence = json.dumps(self.local_store.audit_events(100)) + json.dumps(self.remote_store.audit_events(100))
        self.assertNotIn(CURRENT_PASSWORD, evidence)
        self.assertNotIn(NEW_PASSWORD, evidence)
        self.assertNotIn("salt_b64", evidence)
        self.assertNotIn("verifier_b64", evidence)

    def test_failed_standby_canary_compensates_both_nodes(self) -> None:
        self.peer.fail_canary = True
        with self.assertRaisesRegex(LocalAdminClusterError, "cluster_rotation_rolled_back"):
            self.coordinator.change("admin", CURRENT_PASSWORD, NEW_PASSWORD)
        self.assertTrue(self._verify(self.local_path, CURRENT_PASSWORD))
        self.assertTrue(self._verify(self.remote_path, CURRENT_PASSWORD))
        self.assertFalse(self._verify(self.remote_path, NEW_PASSWORD))
        self.assertEqual(self.events, ["commit:hm-dm-dc02", "commit:hm-dm-dc02"])

    def test_local_commit_failure_rolls_standby_back(self) -> None:
        original = self.local_participant.rotator
        self.local_participant.rotator = lambda *_args: {"status": "failed", "reason": "credential_operation_failed"}
        try:
            with self.assertRaisesRegex(LocalAdminClusterError, "cluster_rotation_rolled_back"):
                self.coordinator.change("admin", CURRENT_PASSWORD, NEW_PASSWORD)
        finally:
            self.local_participant.rotator = original
        self.assertTrue(self._verify(self.local_path, CURRENT_PASSWORD))
        self.assertTrue(self._verify(self.remote_path, CURRENT_PASSWORD))

    def test_ambiguous_peer_commit_and_unavailable_rollback_requires_recovery(self) -> None:
        self.peer.ambiguous_commit = True
        self.peer.block_rollback = True
        with self.assertRaisesRegex(LocalAdminClusterError, "cluster_rotation_recovery_required"):
            self.coordinator.change("admin", CURRENT_PASSWORD, NEW_PASSWORD)
        self.assertTrue(self._verify(self.local_path, CURRENT_PASSWORD))
        self.assertTrue(self._verify(self.remote_path, NEW_PASSWORD))
        evidence = json.dumps(self.local_store.audit_events(100))
        self.assertIn("recovery_required", evidence)

    def test_finalize_failure_preserves_verified_credentials(self) -> None:
        self.peer.ambiguous_finalize = True
        with self.assertRaisesRegex(LocalAdminClusterError, "cluster_rotation_recovery_required"):
            self.coordinator.change("admin", CURRENT_PASSWORD, NEW_PASSWORD)
        self.assertTrue(self._verify(self.local_path, NEW_PASSWORD))
        self.assertTrue(self._verify(self.remote_path, NEW_PASSWORD))
        self.assertFalse(self._verify(self.local_path, CURRENT_PASSWORD))
        self.assertFalse(self._verify(self.remote_path, CURRENT_PASSWORD))
        self.assertNotIn("rollback", self.peer.actions)
        evidence = json.dumps(self.local_store.audit_events(100))
        self.assertIn("recovery_required", evidence)

    def test_prepare_replay_is_idempotent_and_changed_intent_is_rejected(self) -> None:
        command = {
            "schema": COMMAND_SCHEMA,
            "cluster_id": "cluster-test",
            "transaction_id": TRANSACTION_ID,
            "intent_token": INTENT_TOKEN,
            "action": "prepare",
            "username": "admin",
            "current_password": CURRENT_PASSWORD,
            "new_password": NEW_PASSWORD,
        }
        first = self.remote_participant.handle(command, coordinator_node_id="hm-dm-dc01")
        second = self.remote_participant.handle(command, coordinator_node_id="hm-dm-dc01")
        self.assertEqual(first["phase"], "prepared")
        self.assertEqual(second["phase"], "prepared")
        conflicting = dict(command, intent_token="2" * 32)
        rejected = self.remote_participant.handle(conflicting, coordinator_node_id="hm-dm-dc01")
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["reason"], "cluster_transaction_conflict")

    def test_restart_marks_memory_only_transaction_recovery_required(self) -> None:
        self.local_store.begin_local_admin_transaction(
            transaction_id=TRANSACTION_ID,
            intent_token=INTENT_TOKEN,
            coordinator_node_id="hm-dm-dc01",
            phase="commit_started",
        )
        path = self.local_store.path
        self.local_store.close()
        self.local_store = StateStore(path, b"a" * 32, "cluster-test")
        transaction = self.local_store.local_admin_transaction(TRANSACTION_ID)
        self.assertIsNotNone(transaction)
        self.assertEqual(transaction["phase"], "recovery_required")
        self.assertEqual(transaction["reason"], "process_restart")

    def test_command_rejects_duplicate_keys_and_generic_path(self) -> None:
        with self.assertRaisesRegex(LocalAdminClusterError, "invalid_cluster_command"):
            decode_cluster_command(b'{"schema":"x","schema":"y"}')
        command = {
            "schema": COMMAND_SCHEMA,
            "cluster_id": "cluster-test",
            "transaction_id": TRANSACTION_ID,
            "intent_token": INTENT_TOKEN,
            "action": "status",
            "path": "/tmp/credential",
        }
        with self.assertRaisesRegex(LocalAdminClusterError, "invalid_cluster_command"):
            decode_cluster_command(json.dumps(command).encode())

    def test_standby_cannot_coordinate(self) -> None:
        self.config.role = "standby"
        with self.assertRaisesRegex(LocalAdminClusterError, "cluster_leader_required"):
            self.coordinator.change("admin", CURRENT_PASSWORD, NEW_PASSWORD)


if __name__ == "__main__":
    unittest.main()
