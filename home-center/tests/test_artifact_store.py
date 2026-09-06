from __future__ import annotations

import hashlib
import inspect
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
from home_center.artifact_store import (  # noqa: E402
    AdmittedArtifact,
    ArtifactStoreError,
    ArtifactStoreStatus,
    ContentAddressedArtifactStore,
)
from home_center.release_channel import (  # noqa: E402
    REQUIRED_ACCEPTANCE_CHECKS,
    ReleaseChannelError,
    VerifiedLedger,
    VerifiedRelease,
    canonical_json,
    release_record_sha256,
)


NOW = datetime(2026, 9, 6, 19, 50, 0, tzinfo=timezone.utc)
REVISION = "7" * 40


class ArtifactStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._build_temp = tempfile.TemporaryDirectory()
        output = Path(cls._build_temp.name) / "build"
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
        cls.artifact_sha256 = hashlib.sha256(cls.archive.read_bytes()).hexdigest()
        with tarfile.open(cls.archive, "r:gz") as bundle:
            manifest = bundle.extractfile("./MANIFEST.sha256")
            if manifest is None:
                raise AssertionError("manifest missing from test artifact")
            cls.manifest_sha256 = hashlib.sha256(manifest.read()).hexdigest()
        cls.verified = cls._verified_ledger()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._build_temp.cleanup()

    @classmethod
    def _verified_ledger(cls) -> VerifiedLedger:
        filename = cls.archive.name
        object_key = f"sha256/{cls.artifact_sha256[:2]}/{cls.artifact_sha256}/{filename}"
        record = {
            "schema": "home-center.release-record.v1",
            "product": "home-center",
            "version": __version__,
            "revision": REVISION,
            "platform": {"os": "linux", "architecture": "amd64"},
            "artifact": {
                "filename": filename,
                "sha256": cls.artifact_sha256,
                "bytes": cls.artifact_bytes,
                "manifest_sha256": cls.manifest_sha256,
                "media_type": "application/gzip",
                "object_key": object_key,
            },
            "provenance": {
                "repository": "ControlCenterSoft/home-center",
                "source_ref": "refs/heads/main",
                "workflow": ".github/workflows/ci.yml",
                "workflow_sha256": "d" * 64,
                "run_id": 34055837243,
                "run_attempt": 1,
                "ci_artifact_id": 9995925367,
                "ci_outer_zip_sha256": "e" * 64,
                "source_date_epoch": 1767225600,
            },
            "acceptance": {
                "profile": "hm-dm-two-node.v1",
                "status": "passed",
                "transaction_id": "fixture-artifact-store",
                "accepted_at": "2026-09-06T19:45:00Z",
                "evidence_bundle_sha256": "f" * 64,
                "evidence_refs": [
                    {
                        "kind": "github-issue-comment",
                        "locator": "ControlCenterSoft/home-center#25@fixture",
                        "content_sha256": "1" * 64,
                    }
                ],
                "required_checks": list(REQUIRED_ACCEPTANCE_CHECKS),
            },
        }
        record_bytes = canonical_json(record)
        release = VerifiedRelease(
            version=__version__,
            revision=REVISION,
            filename=filename,
            artifact_sha256=cls.artifact_sha256,
            artifact_bytes=cls.artifact_bytes,
            manifest_sha256=cls.manifest_sha256,
            object_key=object_key,
            record_sha256=release_record_sha256(record),
            record_bytes=record_bytes,
        )
        return VerifiedLedger(
            generation=1,
            ledger_sequence=1,
            payload_sha256="2" * 64,
            events_sha256="3" * 64,
            last_event_sha256="4" * 64,
            stable_artifact_sha256=cls.artifact_sha256,
            stable_release=release,
            verified_at=NOW,
            signing_key_ids=("sha256:" + "5" * 64,),
        )

    def _roots(self, temporary: str) -> tuple[Path, Path, ContentAddressedArtifactStore]:
        base = Path(temporary)
        objects = base / "objects"
        inbox = base / "inbox"
        objects.mkdir(mode=0o700)
        inbox.mkdir(mode=0o700)
        objects.chmod(0o700)
        inbox.chmod(0o700)
        store = ContentAddressedArtifactStore(
            object_root=objects,
            inbox_root=inbox,
            expected_uid=os.getuid(),
            expected_gid=os.getgid(),
        )
        return objects, inbox, store

    def _put_inbox(self, inbox: Path, *, data: bytes | None = None, mode: int = 0o600) -> Path:
        release = self.verified.stable_release
        assert release is not None
        target = inbox / release.filename
        if data is None:
            shutil.copyfile(self.archive, target)
        else:
            target.write_bytes(data)
        target.chmod(mode)
        return target

    def _object_path(self, objects: Path) -> Path:
        release = self.verified.stable_release
        assert release is not None
        return objects / release.object_key

    def test_admit_publishes_exact_verified_object_and_consumes_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            objects, inbox, store = self._roots(temporary)
            source = self._put_inbox(inbox)
            self.assertEqual(store.status(self.verified), ArtifactStoreStatus.MISSING)
            admitted = store.admit(self.verified)
            self.assertIsInstance(admitted, AdmittedArtifact)
            self.assertFalse(admitted.reused)
            self.assertEqual(admitted.artifact_sha256, self.artifact_sha256)
            self.assertFalse(source.exists())
            target = self._object_path(objects)
            self.assertTrue(target.is_file())
            self.assertEqual(stat_mode(target), 0o600)
            self.assertEqual(target.stat().st_nlink, 1)
            self.assertEqual(store.status(self.verified), ArtifactStoreStatus.VERIFIED)

    def test_existing_verified_object_is_reused_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            objects, inbox, store = self._roots(temporary)
            self._put_inbox(inbox)
            first = store.admit(self.verified)
            self.assertFalse(first.reused)
            original = self._object_path(objects).read_bytes()
            self._put_inbox(inbox)
            second = store.admit(self.verified)
            self.assertTrue(second.reused)
            self.assertEqual(self._object_path(objects).read_bytes(), original)

    def test_corrupted_inbox_digest_fails_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            objects, inbox, store = self._roots(temporary)
            content = bytearray(self.archive.read_bytes())
            content[-1] ^= 1
            self._put_inbox(inbox, data=bytes(content))
            with self.assertRaisesRegex(ArtifactStoreError, "artifact_store_source_digest_mismatch"):
                store.admit(self.verified)
            self.assertFalse(self._object_path(objects).exists())

    def test_truncated_inbox_is_rejected_by_exact_size_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            objects, inbox, store = self._roots(temporary)
            self._put_inbox(inbox, data=self.archive.read_bytes()[:-1])
            with self.assertRaisesRegex(ArtifactStoreError, "artifact_store_inbox_metadata_rejected"):
                store.admit(self.verified)
            self.assertFalse(self._object_path(objects).exists())

    def test_inbox_symlink_and_wrong_mode_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, inbox, store = self._roots(temporary)
            release = self.verified.stable_release
            assert release is not None
            real = Path(temporary) / "real.tar.gz"
            shutil.copyfile(self.archive, real)
            real.chmod(0o600)
            (inbox / release.filename).symlink_to(real)
            with self.assertRaisesRegex(ArtifactStoreError, "artifact_store_inbox_rejected"):
                store.admit(self.verified)

        with tempfile.TemporaryDirectory() as temporary:
            _, inbox, store = self._roots(temporary)
            self._put_inbox(inbox, mode=0o644)
            with self.assertRaisesRegex(ArtifactStoreError, "artifact_store_inbox_metadata_rejected"):
                store.admit(self.verified)

    def test_object_directory_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            objects, inbox, store = self._roots(temporary)
            outside = Path(temporary) / "outside"
            outside.mkdir(mode=0o700)
            (objects / "sha256").symlink_to(outside, target_is_directory=True)
            self._put_inbox(inbox)
            with self.assertRaisesRegex(ArtifactStoreError, "artifact_store_object_directory_rejected"):
                store.admit(self.verified)

    def test_poisoned_existing_object_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            objects, inbox, store = self._roots(temporary)
            target = self._object_path(objects)
            target.parent.mkdir(parents=True, mode=0o700)
            for parent in [objects / "sha256", objects / "sha256" / self.artifact_sha256[:2], target.parent]:
                parent.chmod(0o700)
            poisoned = bytearray(self.archive.read_bytes())
            poisoned[-1] ^= 1
            target.write_bytes(poisoned)
            target.chmod(0o600)
            before = target.read_bytes()
            self._put_inbox(inbox)
            with self.assertRaises(ReleaseChannelError):
                store.admit(self.verified)
            self.assertEqual(target.read_bytes(), before)
            self.assertTrue((inbox / self.archive.name).exists())

    def test_public_admit_api_has_no_source_or_destination_path(self) -> None:
        parameters = list(inspect.signature(ContentAddressedArtifactStore.admit).parameters)
        self.assertEqual(parameters, ["self", "verified"])
        self.assertNotIn("socket", (ROOT / "product/control-plane/src/home_center/artifact_store.py").read_text(encoding="utf-8"))
        self.assertNotIn("urllib", (ROOT / "product/control-plane/src/home_center/artifact_store.py").read_text(encoding="utf-8"))


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
