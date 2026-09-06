from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center import __version__  # noqa: E402
from home_center.artifact_store import ContentAddressedArtifactStore  # noqa: E402
from home_center.release_channel import (  # noqa: E402
    PAYLOAD_TYPE,
    REQUIRED_ACCEPTANCE_CHECKS,
    ReleaseChannelError,
    canonical_json,
    release_record_sha256,
)
from home_center.release_manager import (  # noqa: E402
    CHECKPOINT_NAME,
    ENVELOPE_DIRECTORY,
    ENVELOPE_NAME,
    PRODUCTION_RELEASE_MANAGER_ENABLED,
    TRUST_POLICY_NAME,
    FixedSignedSnapshotSource,
    OfflineReleaseManager,
    ReleaseChannelCheckpointStore,
    ReleaseManagerError,
    ReleaseManagerStatus,
)


NOW = datetime(2026, 9, 6, 20, 0, 0, tzinfo=timezone.utc)
REVISION = "8" * 40


class ReleaseManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._fixture = tempfile.TemporaryDirectory()
        fixture = Path(cls._fixture.name)
        cls.private_key = fixture / "fixture-private.pem"
        cls.public_key = fixture / "fixture-public.pem"
        subprocess.run(
            ["/usr/bin/openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(cls.private_key)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["/usr/bin/openssl", "pkey", "-in", str(cls.private_key), "-pubout", "-out", str(cls.public_key)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        der = subprocess.run(
            ["/usr/bin/openssl", "pkey", "-pubin", "-in", str(cls.public_key), "-outform", "DER"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        cls.keyid = "sha256:" + hashlib.sha256(der).hexdigest()
        cls.policy = {
            "schema": "home-center.release-trust-policy.v1",
            "product": "home-center",
            "channel": "stable",
            "payload_type": PAYLOAD_TYPE,
            "threshold": 1,
            "keys": [
                {
                    "keyid": cls.keyid,
                    "algorithm": "ecdsa-p256-sha256",
                    "state": "active",
                    "public_key_pem": cls.public_key.read_text(encoding="ascii"),
                }
            ],
        }

        output = fixture / "build"
        subprocess.run(
            ["bash", "deploy/scripts/build-artifact.sh", str(output)],
            cwd=ROOT,
            env={**os.environ, "HOME_CENTER_VERSION": __version__, "HOME_CENTER_REVISION": REVISION},
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        cls.archive = output / f"home-center-{__version__}-linux-amd64.tar.gz"
        cls.artifact_bytes = cls.archive.stat().st_size
        cls.artifact_sha = hashlib.sha256(cls.archive.read_bytes()).hexdigest()
        with tarfile.open(cls.archive, "r:gz") as bundle:
            manifest = bundle.extractfile("./MANIFEST.sha256")
            if manifest is None:
                raise AssertionError("manifest missing")
            cls.manifest_sha = hashlib.sha256(manifest.read()).hexdigest()
        cls.record = cls._record()
        cls.event = cls._event(cls.record)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._fixture.cleanup()

    @classmethod
    def _record(cls) -> dict:
        filename = cls.archive.name
        return {
            "schema": "home-center.release-record.v1",
            "product": "home-center",
            "version": __version__,
            "revision": REVISION,
            "platform": {"os": "linux", "architecture": "amd64"},
            "artifact": {
                "filename": filename,
                "sha256": cls.artifact_sha,
                "bytes": cls.artifact_bytes,
                "manifest_sha256": cls.manifest_sha,
                "media_type": "application/gzip",
                "object_key": f"sha256/{cls.artifact_sha[:2]}/{cls.artifact_sha}/{filename}",
            },
            "provenance": {
                "repository": "ControlCenterSoft/home-center",
                "source_ref": "refs/heads/main",
                "workflow": ".github/workflows/ci.yml",
                "workflow_sha256": "a" * 64,
                "run_id": 34055837243,
                "run_attempt": 1,
                "ci_artifact_id": 9995925367,
                "ci_outer_zip_sha256": "b" * 64,
                "source_date_epoch": 1767225600,
            },
            "acceptance": {
                "profile": "hm-dm-two-node.v1",
                "status": "passed",
                "transaction_id": "fixture-release-manager",
                "accepted_at": "2026-09-06T18:30:00Z",
                "evidence_bundle_sha256": "c" * 64,
                "evidence_refs": [
                    {
                        "kind": "github-issue-comment",
                        "locator": "ControlCenterSoft/home-center#25@fixture",
                        "content_sha256": "d" * 64,
                    }
                ],
                "required_checks": list(REQUIRED_ACCEPTANCE_CHECKS),
            },
        }

    @staticmethod
    def _event(record: dict) -> dict:
        record_id = release_record_sha256(record)
        return {
            "sequence": 1,
            "previous_event_sha256": None,
            "recorded_at": "2026-09-06T19:00:00Z",
            "changes": [
                {
                    "release_record_sha256": record_id,
                    "record": record,
                    "from": "unlisted",
                    "to": "stable",
                    "reason_code": "production_acceptance_passed",
                    "evidence_sha256": "e" * 64,
                }
            ],
            "resulting_stable_artifact_sha256": record["artifact"]["sha256"],
        }

    @classmethod
    def _ledger(cls, *, generation: int = 2) -> dict:
        return {
            "schema": "home-center.release-ledger.v1",
            "product": "home-center",
            "channel": "stable",
            "generation": generation,
            "ledger_sequence": 1,
            "issued_at": "2026-09-06T19:30:00Z",
            "expires_at": "2026-09-07T19:30:00Z",
            "events": [cls.event],
            "stable_artifact_sha256": cls.artifact_sha,
        }

    @classmethod
    def _envelope(cls, *, generation: int = 2) -> bytes:
        payload = canonical_json(cls._ledger(generation=generation))
        payload_type = PAYLOAD_TYPE.encode("ascii")
        pae = (
            b"DSSEv1 "
            + str(len(payload_type)).encode()
            + b" "
            + payload_type
            + b" "
            + str(len(payload)).encode()
            + b" "
            + payload
        )
        signature = subprocess.run(
            ["/usr/bin/openssl", "dgst", "-sha256", "-sign", str(cls.private_key)],
            input=pae,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        return canonical_json(
            {
                "payloadType": PAYLOAD_TYPE,
                "payload": base64.b64encode(payload).decode("ascii"),
                "signatures": [{"keyid": cls.keyid, "sig": base64.b64encode(signature).decode("ascii")}],
            }
        )

    def _manager(self, temporary: str, *, with_artifact: bool) -> tuple[OfflineReleaseManager, Path, Path, Path]:
        base = Path(temporary)
        trust = base / "trust"
        channel = base / "channel"
        inbox = channel / ENVELOPE_DIRECTORY
        objects = base / "objects"
        artifact_inbox = base / "artifact-inbox"
        for directory in (trust, channel, inbox, objects, artifact_inbox):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)

        policy_path = trust / TRUST_POLICY_NAME
        policy_path.write_bytes(canonical_json(self.policy))
        policy_path.chmod(0o644)
        envelope_path = inbox / ENVELOPE_NAME
        envelope_path.write_bytes(self._envelope())
        envelope_path.chmod(0o600)
        if with_artifact:
            shutil.copyfile(self.archive, artifact_inbox / self.archive.name)
            (artifact_inbox / self.archive.name).chmod(0o600)

        uid = os.getuid()
        gid = os.getgid()
        snapshots = FixedSignedSnapshotSource(
            trust_root=trust,
            channel_root=channel,
            expected_uid=uid,
            expected_gid=gid,
        )
        checkpoints = ReleaseChannelCheckpointStore(
            channel_root=channel,
            expected_uid=uid,
            expected_gid=gid,
        )
        artifacts = ContentAddressedArtifactStore(
            object_root=objects,
            inbox_root=artifact_inbox,
            expected_uid=uid,
            expected_gid=gid,
        )
        return (
            OfflineReleaseManager(snapshots=snapshots, checkpoints=checkpoints, artifacts=artifacts),
            trust,
            channel,
            objects,
        )

    def test_production_release_manager_activation_is_inert(self) -> None:
        self.assertIs(PRODUCTION_RELEASE_MANAGER_ENABLED, False)

    def test_evaluate_is_read_only_and_reports_missing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager, _, channel, _ = self._manager(temporary, with_artifact=False)
            decision = manager.evaluate(now=NOW)
            self.assertEqual(decision.status, ReleaseManagerStatus.ARTIFACT_MISSING)
            self.assertEqual(decision.version, __version__)
            self.assertEqual(decision.revision, REVISION)
            self.assertFalse((channel / CHECKPOINT_NAME).exists())

    def test_admit_local_verifies_artifact_then_advances_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager, _, channel, objects = self._manager(temporary, with_artifact=True)
            decision = manager.admit_local(now=NOW)
            self.assertEqual(decision.status, ReleaseManagerStatus.ADMITTED)
            self.assertIsNotNone(decision.checkpoint_sha256)
            checkpoint = channel / CHECKPOINT_NAME
            self.assertTrue(checkpoint.is_file())
            self.assertEqual(checkpoint.stat().st_mode & 0o777, 0o600)
            object_path = objects / self.record["artifact"]["object_key"]
            self.assertTrue(object_path.is_file())
            self.assertEqual(hashlib.sha256(object_path.read_bytes()).hexdigest(), self.artifact_sha)

            repeated = manager.evaluate(now=NOW)
            self.assertEqual(repeated.status, ReleaseManagerStatus.ARTIFACT_VERIFIED)
            self.assertEqual(repeated.checkpoint_sha256, decision.checkpoint_sha256)

    def test_saved_checkpoint_rejects_generation_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager, _, channel, _ = self._manager(temporary, with_artifact=True)
            manager.admit_local(now=NOW)
            envelope = channel / ENVELOPE_DIRECTORY / ENVELOPE_NAME
            envelope.write_bytes(self._envelope(generation=1))
            envelope.chmod(0o600)
            with self.assertRaisesRegex(ReleaseChannelError, "ledger_generation_rollback"):
                manager.evaluate(now=NOW)

    def test_trust_policy_and_envelope_metadata_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager, trust, _, _ = self._manager(temporary, with_artifact=False)
            (trust / TRUST_POLICY_NAME).chmod(0o666)
            with self.assertRaisesRegex(ReleaseManagerError, "release_trust_policy_file_rejected"):
                manager.evaluate(now=NOW)

        with tempfile.TemporaryDirectory() as temporary:
            manager, _, channel, _ = self._manager(temporary, with_artifact=False)
            envelope = channel / ENVELOPE_DIRECTORY / ENVELOPE_NAME
            envelope.chmod(0o644)
            with self.assertRaisesRegex(ReleaseManagerError, "release_envelope_file_rejected"):
                manager.evaluate(now=NOW)

    def test_trust_policy_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager, trust, _, _ = self._manager(temporary, with_artifact=False)
            policy = trust / TRUST_POLICY_NAME
            copy = Path(temporary) / "policy-copy.json"
            shutil.copyfile(policy, copy)
            policy.unlink()
            policy.symlink_to(copy)
            with self.assertRaisesRegex(ReleaseManagerError, "release_trust_policy_file_rejected"):
                manager.evaluate(now=NOW)

    def test_checkpoint_symlink_is_not_treated_as_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager, _, channel, _ = self._manager(temporary, with_artifact=False)
            target = Path(temporary) / "fake-checkpoint.json"
            target.write_text("{}", encoding="utf-8")
            target.chmod(0o600)
            (channel / CHECKPOINT_NAME).symlink_to(target)
            with self.assertRaisesRegex(ReleaseManagerError, "release_checkpoint_file_rejected"):
                manager.evaluate(now=NOW)

    def test_release_manager_has_no_network_or_node_mutation_surface(self) -> None:
        source = (ROOT / "product/control-plane/src/home_center/release_manager.py").read_text(encoding="utf-8")
        for forbidden in ("socket", "urllib", "requests", "systemctl", "subprocess", "ssh ", "paramiko"):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("def deploy", source)
        self.assertNotIn("def install", source)


if __name__ == "__main__":
    unittest.main()
