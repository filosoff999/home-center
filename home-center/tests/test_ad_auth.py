from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center.ad_auth import AdAuthConfig, AdAuthError, AdAuthenticator, ID, KINIT  # noqa: E402


PASSWORD = "domain-password"


class FakeRunner:
    def __init__(self, *, kinit_code: int = 0, id_code: int = 0, groups: bytes = b"") -> None:
        self.kinit_code = kinit_code
        self.id_code = id_code
        self.groups = groups
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if args[0] == KINIT:
            return subprocess.CompletedProcess(args, self.kinit_code, stdout=None, stderr=None)
        if args[0] == ID:
            return subprocess.CompletedProcess(args, self.id_code, stdout=self.groups, stderr=None)
        raise AssertionError("unexpected executable")


class AdAuthenticatorTests(unittest.TestCase):
    def settings(self, root: Path, *, enabled: bool = True) -> AdAuthConfig:
        return AdAuthConfig(
            enabled=enabled,
            realm="HM.DM",
            kdc_hosts=("dc01.hm.dm", "dc02.hm.dm"),
            allowed_admin_groups=("domain admins@hm.dm",),
            timeout_seconds=3,
            cache_root=root / "ad-cache",
        )

    def test_disabled_provider_never_touches_backend_or_cache(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runner = FakeRunner()
            auth = AdAuthenticator(self.settings(Path(raw), enabled=False), runner=runner)
            self.assertIsNone(auth.authenticate("pavel", PASSWORD))
            self.assertEqual(runner.calls, [])
            self.assertFalse((Path(raw) / "ad-cache").exists())

    def test_success_uses_password_stdin_fixed_commands_and_group_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            runner = FakeRunner(groups=b"domain users@hm.dm\x00domain admins@hm.dm\x00")
            auth = AdAuthenticator(self.settings(root), runner=runner)
            self.assertEqual(auth.authenticate("Pavel", PASSWORD), "pavel@HM.DM")
            self.assertEqual([call[0][0] for call in runner.calls], [KINIT, ID])
            self.assertNotIn(PASSWORD, repr([call[0] for call in runner.calls]))
            self.assertEqual(runner.calls[0][1]["input"], PASSWORD.encode() + b"\n")
            self.assertNotIn(PASSWORD, repr(runner.calls[0][1]["env"]))
            self.assertNotIn("shell", runner.calls[0][1])
            self.assertEqual(runner.calls[0][1]["timeout"], 3)
            self.assertEqual(list((root / "ad-cache").iterdir()), [])
            self.assertEqual(os.stat(root / "ad-cache").st_mode & 0o777, 0o700)

    def test_invalid_password_and_denied_membership_are_generic(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            denied = FakeRunner(kinit_code=1)
            auth = AdAuthenticator(self.settings(Path(raw)), runner=denied)
            self.assertIsNone(auth.authenticate("pavel", PASSWORD))
            self.assertEqual(len(denied.calls), 1)

        with tempfile.TemporaryDirectory() as raw:
            denied = FakeRunner(groups=b"domain users@hm.dm\x00")
            auth = AdAuthenticator(self.settings(Path(raw)), runner=denied)
            self.assertIsNone(auth.authenticate("pavel@hm.dm", PASSWORD))
            self.assertEqual(len(denied.calls), 2)

    def test_wrong_realm_and_injection_shape_never_execute(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runner = FakeRunner()
            auth = AdAuthenticator(self.settings(Path(raw)), runner=runner)
            for username in ("pavel@OTHER.DM", "HM\\pavel;id", " pavel", "pavel\nadmin"):
                self.assertIsNone(auth.authenticate(username, PASSWORD))
            self.assertEqual(runner.calls, [])

    def test_backend_timeout_is_generic_and_cache_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)

            def timeout(args, **kwargs):
                raise subprocess.TimeoutExpired(args, kwargs["timeout"], output=b"secret")

            auth = AdAuthenticator(self.settings(root), runner=timeout)
            with self.assertRaisesRegex(AdAuthError, "external_authentication_unavailable"):
                auth.authenticate("pavel", PASSWORD)
            self.assertEqual(list((root / "ad-cache").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
