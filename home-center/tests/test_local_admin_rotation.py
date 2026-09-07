from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from home_center.local_admin_auth import LocalAdminCredentialStore
from home_center.local_admin_provision import provision_credential_file
from home_center.local_admin_rotation import (
    NEXT_NAME,
    ROLLBACK_NAME,
    LocalAdminCredentialRotator,
    LocalAdminRotationError,
)

CURRENT_PASSWORD = "current password 17"
NEW_PASSWORD = "new password 42"


class LocalAdminRotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.uid = os.geteuid()
        self.gid = os.getegid()
        self.path = self.root / "local-admin.json"
        provision_credential_file(
            self.path,
            "admin",
            CURRENT_PASSWORD,
            expected_directory_uid=self.uid,
            expected_directory_gid=self.gid,
            expected_directory_mode=0o700,
            file_uid=self.uid,
            file_gid=self.gid,
            file_mode=0o640,
        )
        self.rotator = LocalAdminCredentialRotator(
            self.path,
            expected_uid=self.uid,
            expected_gid=self.gid,
            expected_mode=0o640,
            expected_directory_uid=self.uid,
            expected_directory_gid=self.gid,
            expected_directory_mode=0o700,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def store(self) -> LocalAdminCredentialStore:
        return LocalAdminCredentialStore(
            self.path,
            expected_uid=self.uid,
            expected_gid=self.gid,
            expected_mode=0o640,
        )

    def test_success_uses_fresh_salt_and_replaces_authentication_state(self) -> None:
        before = json.loads(self.path.read_text(encoding="utf-8"))
        committed = self.rotator.rotate("admin", CURRENT_PASSWORD, NEW_PASSWORD)
        after = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotEqual(before["salt_b64"], after["salt_b64"])
        self.assertNotEqual(before["verifier_b64"], after["verifier_b64"])
        self.assertTrue(committed.verify("admin", NEW_PASSWORD))
        self.assertFalse(committed.verify("admin", CURRENT_PASSWORD))
        self.assertNotIn(CURRENT_PASSWORD, self.path.read_text(encoding="utf-8"))
        self.assertNotIn(NEW_PASSWORD, self.path.read_text(encoding="utf-8"))
        self.assertFalse((self.root / NEXT_NAME).exists())
        self.assertFalse((self.root / ROLLBACK_NAME).exists())

    def test_wrong_current_password_and_policy_rejections_keep_old_credential(self) -> None:
        cases = (
            ("wrong current 1", NEW_PASSWORD, "current_password_invalid"),
            (CURRENT_PASSWORD, "short1", "password_too_short"),
            (CURRENT_PASSWORD, "12345678", "password_letter_required"),
            (CURRENT_PASSWORD, "onlyletters", "password_digit_required"),
        )
        for current, new, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(LocalAdminRotationError, code):
                self.rotator.rotate("admin", current, new)
            self.assertTrue(self.store().verify("admin", CURRENT_PASSWORD))

    def test_cluster_prepare_validates_without_writing_credential(self) -> None:
        before = self.path.read_bytes()
        self.rotator.validate("admin", CURRENT_PASSWORD, NEW_PASSWORD)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.root / NEXT_NAME).exists())
        self.assertFalse((self.root / ROLLBACK_NAME).exists())

    def test_cluster_prepare_rejects_unchanged_password(self) -> None:
        with self.assertRaisesRegex(LocalAdminRotationError, "password_unchanged"):
            self.rotator.validate("admin", CURRENT_PASSWORD, CURRENT_PASSWORD)
        self.assertTrue(self.store().verify("admin", CURRENT_PASSWORD))

    def test_replace_failure_rolls_back_and_removes_transaction_files(self) -> None:
        with patch("home_center.local_admin_rotation.os.replace", side_effect=OSError("fixture")):
            with self.assertRaisesRegex(LocalAdminRotationError, "credential_rotation_failed"):
                self.rotator.rotate("admin", CURRENT_PASSWORD, NEW_PASSWORD)
        self.assertTrue(self.store().verify("admin", CURRENT_PASSWORD))
        self.assertFalse((self.root / NEXT_NAME).exists())
        self.assertFalse((self.root / ROLLBACK_NAME).exists())

    def test_recovery_restores_validated_rollback_when_target_is_invalid(self) -> None:
        old_data = self.path.read_bytes()
        rollback = self.root / ROLLBACK_NAME
        rollback.write_bytes(old_data)
        os.chmod(rollback, 0o640)
        self.path.write_text("{invalid", encoding="utf-8")
        os.chmod(self.path, 0o640)
        recovered = self.rotator.recover()
        self.assertTrue(recovered.verify("admin", CURRENT_PASSWORD))
        self.assertFalse(rollback.exists())

    def test_authorized_reset_uses_fresh_salt_without_current_password(self) -> None:
        before = json.loads(self.path.read_text(encoding="utf-8"))
        committed = self.rotator.reset(NEW_PASSWORD)
        after = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotEqual(before["salt_b64"], after["salt_b64"])
        self.assertTrue(committed.verify("admin", NEW_PASSWORD))
        self.assertFalse(committed.verify("admin", CURRENT_PASSWORD))

    def test_reset_commit_evidence_failure_rolls_back_old_credential(self) -> None:
        def fail_evidence() -> None:
            raise OSError("fixture")

        with self.assertRaisesRegex(LocalAdminRotationError, "credential_commit_hook_failed"):
            self.rotator.reset(NEW_PASSWORD, commit_hook=fail_evidence)
        self.assertTrue(self.store().verify("admin", CURRENT_PASSWORD))
        self.assertFalse(self.store().verify("admin", NEW_PASSWORD))

    def test_postcommit_cleanup_failure_keeps_new_authoritative_credential(self) -> None:
        evidence = Mock()
        original = self.rotator._unlink_regular

        def fail_rollback_cleanup(directory_fd: int, name: str) -> None:
            if name == ROLLBACK_NAME:
                raise OSError("fixture")
            original(directory_fd, name)

        with patch.object(self.rotator, "_unlink_regular", side_effect=fail_rollback_cleanup):
            committed = self.rotator.reset(NEW_PASSWORD, commit_hook=evidence)
        evidence.assert_called_once_with()
        self.assertTrue(committed.verify("admin", NEW_PASSWORD))
        self.assertTrue(self.store().verify("admin", NEW_PASSWORD))


if __name__ == "__main__":
    unittest.main()
