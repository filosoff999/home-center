from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from home_center.local_admin_auth import (
    CREDENTIAL_SCHEMA,
    KDF_DKLEN,
    KDF_MAXMEM,
    KDF_N,
    KDF_P,
    KDF_R,
    LocalAdminAuthError,
    LocalAdminCredentialStore,
    SALT_BYTES,
)


PASSWORD = "correct horse battery staple"


def credential_document(*, username: str = "admin", password: str = PASSWORD, n: int = KDF_N) -> dict[str, object]:
    salt = b"s" * SALT_BYTES
    verifier = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=KDF_N,
        r=KDF_R,
        p=KDF_P,
        maxmem=KDF_MAXMEM,
        dklen=KDF_DKLEN,
    )
    return {
        "schema": CREDENTIAL_SCHEMA,
        "username": username,
        "kdf": "scrypt",
        "n": n,
        "r": KDF_R,
        "p": KDF_P,
        "dklen": KDF_DKLEN,
        "salt_b64": base64.b64encode(salt).decode("ascii"),
        "verifier_b64": base64.b64encode(verifier).decode("ascii"),
    }


class LocalAdminCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "local-admin.json"
        self.uid = os.geteuid()
        self.gid = os.getegid()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, value: dict[str, object] | None = None, *, mode: int = 0o600) -> None:
        self.path.write_text(json.dumps(value or credential_document(), sort_keys=True), encoding="utf-8")
        os.chmod(self.path, mode)

    def store(self, *, mode: int = 0o600) -> LocalAdminCredentialStore:
        return LocalAdminCredentialStore(
            self.path,
            expected_uid=self.uid,
            expected_gid=self.gid,
            expected_mode=mode,
        )

    def test_correct_password_and_canonical_username(self) -> None:
        self.write()
        store = self.store()
        self.assertEqual(store.authenticate("Admin", PASSWORD), "admin")
        self.assertTrue(store.verify("admin", PASSWORD))

    def test_wrong_username_and_password_fail_closed(self) -> None:
        self.write()
        store = self.store()
        self.assertIsNone(store.authenticate("other", PASSWORD))
        self.assertIsNone(store.authenticate("admin", "wrong-password-value"))
        self.assertFalse(store.verify(" admin ", PASSWORD))

    def test_invalid_candidate_still_executes_scrypt(self) -> None:
        self.write()
        store = self.store()
        with patch("home_center.local_admin_auth.hashlib.scrypt", wraps=hashlib.scrypt) as derive:
            self.assertIsNone(store.authenticate("!", "short"))
        self.assertEqual(derive.call_count, 1)

    def test_kdf_parameter_downgrade_is_rejected(self) -> None:
        self.write(credential_document(n=1 << 14))
        with self.assertRaisesRegex(LocalAdminAuthError, "credential_kdf_parameters_rejected"):
            self.store()

    def test_symlink_is_rejected(self) -> None:
        real = self.root / "real.json"
        real.write_text(json.dumps(credential_document()), encoding="utf-8")
        os.chmod(real, 0o600)
        self.path.symlink_to(real)
        with self.assertRaises(LocalAdminAuthError):
            self.store()

    def test_owner_group_and_mode_are_exact(self) -> None:
        self.write(mode=0o640)
        self.assertTrue(self.store(mode=0o640).verify("admin", PASSWORD))
        with self.assertRaisesRegex(LocalAdminAuthError, "credential_file_metadata_rejected"):
            LocalAdminCredentialStore(
                self.path,
                expected_uid=self.uid + 1,
                expected_gid=self.gid,
                expected_mode=0o640,
            )
        with self.assertRaisesRegex(LocalAdminAuthError, "credential_file_metadata_rejected"):
            LocalAdminCredentialStore(
                self.path,
                expected_uid=self.uid,
                expected_gid=self.gid,
                expected_mode=0o600,
            )

    def test_duplicate_json_key_is_rejected(self) -> None:
        document = credential_document()
        text = json.dumps(document)
        text = text[:-1] + ',"schema":"home-center.local-admin-credential.v1"}'
        self.path.write_text(text, encoding="utf-8")
        os.chmod(self.path, 0o600)
        with self.assertRaisesRegex(LocalAdminAuthError, "credential_duplicate_json_key"):
            self.store()


if __name__ == "__main__":
    unittest.main()
