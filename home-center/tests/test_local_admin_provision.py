from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center.local_admin_auth import LocalAdminCredentialStore  # noqa: E402
from home_center.local_admin_provision import (  # noqa: E402
    LocalAdminProvisionError,
    credential_document,
    provision_credential_file,
)


PASSWORD = "correct horse battery staple 1"


class LocalAdminProvisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.uid = os.geteuid()
        self.gid = os.getegid()
        self.path = self.root / "local-admin.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def provision(self, path: Path | None = None) -> str:
        return provision_credential_file(
            path or self.path,
            "Admin",
            PASSWORD,
            expected_directory_uid=self.uid,
            expected_directory_gid=self.gid,
            expected_directory_mode=0o700,
            file_uid=self.uid,
            file_gid=self.gid,
            file_mode=0o640,
        )

    def test_provisioned_credential_authenticates_and_never_contains_plaintext(self) -> None:
        self.assertEqual(self.provision(), "admin")
        info = self.path.stat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(info.st_nlink, 1)
        self.assertEqual(info.st_uid, self.uid)
        self.assertEqual(info.st_gid, self.gid)
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o640)
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn(PASSWORD, raw)
        store = LocalAdminCredentialStore(
            self.path,
            expected_uid=self.uid,
            expected_gid=self.gid,
            expected_mode=0o640,
        )
        self.assertTrue(store.verify("admin", PASSWORD))
        self.assertFalse(store.verify("admin", "different strong password"))

    def test_existing_regular_file_is_never_overwritten(self) -> None:
        self.path.write_text("sentinel\n", encoding="utf-8")
        os.chmod(self.path, 0o640)
        with self.assertRaisesRegex(LocalAdminProvisionError, "credential_file_exists"):
            self.provision()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "sentinel\n")

    def test_existing_symlink_is_never_followed_or_replaced(self) -> None:
        outside = self.root / "outside"
        outside.write_text("sentinel\n", encoding="utf-8")
        self.path.symlink_to(outside)
        with self.assertRaisesRegex(LocalAdminProvisionError, "credential_file_exists"):
            self.provision()
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(outside.read_text(encoding="utf-8"), "sentinel\n")

    def test_unsafe_parent_directory_mode_is_rejected(self) -> None:
        os.chmod(self.root, 0o770)
        with self.assertRaisesRegex(LocalAdminProvisionError, "credential_directory_metadata_rejected"):
            self.provision()
        self.assertFalse(self.path.exists())

    def test_random_salt_changes_persisted_verifier(self) -> None:
        first = credential_document("admin", PASSWORD)
        second = credential_document("admin", PASSWORD)
        self.assertNotEqual(first["salt_b64"], second["salt_b64"])
        self.assertNotEqual(first["verifier_b64"], second["verifier_b64"])
        self.assertEqual(set(first), {"schema", "username", "kdf", "n", "r", "p", "dklen", "salt_b64", "verifier_b64"})
        json.dumps(first)

    def test_relative_target_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(LocalAdminProvisionError, "credential_path_rejected"):
            provision_credential_file(
                Path("local-admin.json"),
                "admin",
                PASSWORD,
                expected_directory_uid=self.uid,
                expected_directory_gid=self.gid,
                file_uid=self.uid,
                file_gid=self.gid,
            )


if __name__ == "__main__":
    unittest.main()

