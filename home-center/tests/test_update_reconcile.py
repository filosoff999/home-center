from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center.release_channel import VerifiedRelease  # noqa: E402
from home_center.update_reconcile import (  # noqa: E402
    PRODUCTION_ACTIVATION_ENABLED,
    CheckpointStore,
    ClusterUpdateStatus,
    NodeReleaseIdentity,
    ReconcilePhase,
    SingleWriterLock,
    UpdateCheckpoint,
    UpdateReconcileError,
    advance,
    classify_cluster,
    ensure_monotonic_stable,
    identities_preserved,
    new_checkpoint,
    quarantine,
    require_recovery,
)


NOW = datetime(2026, 9, 6, 18, 0, 0, tzinfo=timezone.utc)


def release(
    *,
    version: str = "0.6.0",
    revision: str = "1" * 40,
    artifact: str = "a" * 64,
    record: str = "c" * 64,
) -> VerifiedRelease:
    return VerifiedRelease(
        version=version,
        revision=revision,
        filename=f"home-center-{version}-linux-amd64.tar.gz",
        artifact_sha256=artifact,
        artifact_bytes=1024,
        manifest_sha256="b" * 64,
        object_key=f"sha256/{artifact[:2]}/{artifact}/home-center-{version}-linux-amd64.tar.gz",
        record_sha256=record,
        record_bytes=b"{}",
    )


def node(
    node_id: str,
    *,
    target: VerifiedRelease | None = None,
    ready: bool = True,
    revision: str | None = None,
    artifact: str | None = None,
    web_leaf: str = "e" * 64,
) -> NodeReleaseIdentity:
    target = target or release()
    return NodeReleaseIdentity(
        node_id=node_id,
        version=target.version,
        revision=revision or target.revision,
        artifact_sha256=artifact or target.artifact_sha256,
        ready=ready,
        web_ca_sha256="d" * 64,
        web_leaf_sha256=web_leaf,
        web_public_key_sha256="f" * 64,
        peer_ca_sha256="1" * 64,
        peer_certificate_sha256="2" * 64,
        peer_public_key_sha256="3" * 64,
    )


class UpdateReconcileTests(unittest.TestCase):
    def test_production_activation_is_inert(self) -> None:
        self.assertIs(PRODUCTION_ACTIVATION_ENABLED, False)

    def test_exact_dc02_first_state_machine(self) -> None:
        target = release()
        checkpoint = new_checkpoint("tx-1", target, ledger_sequence=10, now=NOW)
        expected = [
            ReconcilePhase.ACQUIRE,
            ReconcilePhase.VERIFY,
            ReconcilePhase.ADMIT,
            ReconcilePhase.BACKUP_DC02,
            ReconcilePhase.UPDATE_DC02,
            ReconcilePhase.CANARY_SOAK,
            ReconcilePhase.BACKUP_DC01,
            ReconcilePhase.UPDATE_DC01,
            ReconcilePhase.CLUSTER_ACCEPT,
            ReconcilePhase.CHECKPOINT,
            ReconcilePhase.SUCCEEDED,
        ]
        for index, phase in enumerate(expected, start=1):
            checkpoint = advance(checkpoint, now=NOW + timedelta(seconds=index))
            self.assertEqual(checkpoint.phase, phase)
            if phase in {
                ReconcilePhase.ACQUIRE,
                ReconcilePhase.VERIFY,
                ReconcilePhase.ADMIT,
                ReconcilePhase.BACKUP_DC02,
                ReconcilePhase.UPDATE_DC02,
            }:
                self.assertFalse(checkpoint.dc02_complete)
                self.assertFalse(checkpoint.dc01_complete)
            if phase in {
                ReconcilePhase.CANARY_SOAK,
                ReconcilePhase.BACKUP_DC01,
                ReconcilePhase.UPDATE_DC01,
            }:
                self.assertTrue(checkpoint.dc02_complete)
                self.assertFalse(checkpoint.dc01_complete)
            if phase in {
                ReconcilePhase.CLUSTER_ACCEPT,
                ReconcilePhase.CHECKPOINT,
                ReconcilePhase.SUCCEEDED,
            }:
                self.assertTrue(checkpoint.dc02_complete)
                self.assertTrue(checkpoint.dc01_complete)

        with self.assertRaisesRegex(UpdateReconcileError, "checkpoint_terminal"):
            advance(checkpoint, now=NOW + timedelta(seconds=20))

    def test_clock_rollback_fails_closed(self) -> None:
        checkpoint = new_checkpoint("tx-clock", release(), ledger_sequence=10, now=NOW)
        checkpoint = advance(checkpoint, now=NOW + timedelta(seconds=5))
        with self.assertRaisesRegex(UpdateReconcileError, "clock_rollback_rejected"):
            advance(checkpoint, now=NOW + timedelta(seconds=4))

    def test_recovery_is_terminal_and_requires_reason(self) -> None:
        checkpoint = new_checkpoint("tx-recovery", release(), ledger_sequence=10, now=NOW)
        checkpoint = require_recovery(checkpoint, "ambiguous_postcondition", now=NOW + timedelta(seconds=1))
        self.assertEqual(checkpoint.phase, ReconcilePhase.RECOVERY_REQUIRED)
        self.assertEqual(checkpoint.recovery_reason, "ambiguous_postcondition")
        with self.assertRaisesRegex(UpdateReconcileError, "checkpoint_terminal"):
            advance(checkpoint, now=NOW + timedelta(seconds=2))

    def test_quarantined_target_is_visible(self) -> None:
        target = release()
        checkpoint = new_checkpoint("tx-quarantine", target, ledger_sequence=10, now=NOW)
        checkpoint = quarantine(checkpoint, target.artifact_sha256, now=NOW + timedelta(seconds=1))
        status = classify_cluster(target, node("dc01", target=target), node("dc02", target=target), checkpoint)
        self.assertEqual(status, ClusterUpdateStatus.RECOVERY_REQUIRED)
        self.assertEqual(checkpoint.quarantined_artifact_sha256, target.artifact_sha256)

    def test_cluster_status_current_drifted_and_blocked(self) -> None:
        target = release()
        dc01 = node("dc01", target=target)
        dc02 = node("dc02", target=target)
        self.assertEqual(classify_cluster(target, dc01, dc02, None), ClusterUpdateStatus.CURRENT)

        drift = node("dc02", target=target, revision="2" * 40)
        self.assertEqual(classify_cluster(target, dc01, drift, None), ClusterUpdateStatus.DRIFTED)

        blocked = node("dc02", target=target, ready=False)
        self.assertEqual(classify_cluster(target, dc01, blocked, None), ClusterUpdateStatus.BLOCKED)

    def test_unresolved_transaction_bound_to_other_release_requires_recovery(self) -> None:
        old = release(version="0.6.0", revision="1" * 40, artifact="a" * 64, record="c" * 64)
        new = release(version="0.6.1", revision="2" * 40, artifact="d" * 64, record="e" * 64)
        checkpoint = new_checkpoint("tx-old", old, ledger_sequence=10, now=NOW)
        status = classify_cluster(new, node("dc01", target=old), node("dc02", target=old), checkpoint)
        self.assertEqual(status, ClusterUpdateStatus.RECOVERY_REQUIRED)

    def test_sequence_rollback_and_equivocation_are_rejected(self) -> None:
        stable = release()
        checkpoint = new_checkpoint("tx-ledger", stable, ledger_sequence=10, now=NOW)
        with self.assertRaisesRegex(UpdateReconcileError, "stable_sequence_rollback"):
            ensure_monotonic_stable(stable, ledger_sequence=9, previous=checkpoint)

        other = release(version="0.6.1", revision="2" * 40, artifact="d" * 64, record="e" * 64)
        with self.assertRaisesRegex(UpdateReconcileError, "stable_sequence_equivocation"):
            ensure_monotonic_stable(other, ledger_sequence=10, previous=checkpoint)

        ensure_monotonic_stable(stable, ledger_sequence=10, previous=checkpoint)
        ensure_monotonic_stable(other, ledger_sequence=11, previous=checkpoint)

    def test_web_and_peer_identity_must_be_preserved(self) -> None:
        before = node("dc02")
        self.assertTrue(identities_preserved(before, before))
        after = replace(before, web_leaf_sha256="4" * 64)
        self.assertFalse(identities_preserved(before, after))
        with self.assertRaisesRegex(UpdateReconcileError, "identity_node_mismatch"):
            identities_preserved(before, replace(before, node_id="dc01"))

    def test_checkpoint_document_rejects_dc01_before_dc02(self) -> None:
        checkpoint = new_checkpoint("tx-order", release(), ledger_sequence=10, now=NOW)
        invalid = replace(checkpoint, dc01_complete=True)
        with self.assertRaisesRegex(UpdateReconcileError, "checkpoint_dc01_before_dc02_rejected"):
            invalid.validate()

    def test_atomic_checkpoint_round_trip_and_phase_rollback_rejection(self) -> None:
        target = release()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            store = CheckpointStore(
                directory / "update.json",
                expected_uid=os.getuid(),
                expected_gid=os.getgid(),
            )
            first = new_checkpoint("tx-store", target, ledger_sequence=10, now=NOW)
            store.save(first)
            self.assertEqual(store.load(), first)

            second = advance(first, now=NOW + timedelta(seconds=1))
            store.save(second)
            self.assertEqual(store.load(), second)

            with self.assertRaisesRegex(UpdateReconcileError, "checkpoint_phase_rollback"):
                store.save(first)

    def test_checkpoint_store_rejects_duplicate_json_key(self) -> None:
        target = release()
        checkpoint = new_checkpoint("tx-json", target, ledger_sequence=10, now=NOW)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "update.json"
            document = checkpoint.to_document()
            payload = json.dumps(document, separators=(",", ":"))
            payload = payload[:-1] + ',"schema":"home-center.update-reconcile-checkpoint.v1"}'
            path.write_text(payload, encoding="utf-8")
            path.chmod(0o600)
            store = CheckpointStore(path, expected_uid=os.getuid(), expected_gid=os.getgid())
            with self.assertRaisesRegex(UpdateReconcileError, "checkpoint_duplicate_json_key"):
                store.load()

    def test_checkpoint_store_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            target = directory / "real.json"
            target.write_text("{}", encoding="utf-8")
            target.chmod(0o600)
            link = directory / "update.json"
            link.symlink_to(target)
            store = CheckpointStore(link, expected_uid=os.getuid(), expected_gid=os.getgid())
            with self.assertRaisesRegex(UpdateReconcileError, "checkpoint_open_rejected"):
                store.load()

    def test_single_writer_lock_rejects_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "update.lock"
            kwargs = {"expected_uid": os.getuid(), "expected_gid": os.getgid()}
            with SingleWriterLock(path, **kwargs):
                with self.assertRaisesRegex(UpdateReconcileError, "reconcile_lock_busy"):
                    with SingleWriterLock(path, **kwargs):
                        self.fail("second writer unexpectedly acquired lock")


if __name__ == "__main__":
    unittest.main()
