from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/runtime/provision-local-admin.py"


def load_cli():
    spec = importlib.util.spec_from_file_location("home_center_provision_local_admin_cli", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load provision CLI")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocalAdminProvisionCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = load_cli()

    @staticmethod
    def args():
        return argparse.Namespace(username="Admin")

    @staticmethod
    def tty(value: bool):
        return types.SimpleNamespace(isatty=lambda: value)

    def test_non_root_is_rejected_before_password_prompt(self) -> None:
        stderr = io.StringIO()
        with (
            patch.object(self.cli, "parse_args", return_value=self.args()),
            patch.object(self.cli.os, "geteuid", return_value=1000),
            patch.object(self.cli.getpass, "getpass") as prompt,
            contextlib.redirect_stderr(stderr),
        ):
            rc = self.cli.main()
        self.assertEqual(rc, 77)
        prompt.assert_not_called()
        self.assertIn("ROOT_REQUIRED", stderr.getvalue())

    def test_non_interactive_input_is_rejected_before_password_prompt(self) -> None:
        stderr = io.StringIO()
        with (
            patch.object(self.cli, "parse_args", return_value=self.args()),
            patch.object(self.cli.os, "geteuid", return_value=0),
            patch.object(self.cli.sys, "stdin", self.tty(False)),
            patch.object(self.cli.getpass, "getpass") as prompt,
            contextlib.redirect_stderr(stderr),
        ):
            rc = self.cli.main()
        self.assertEqual(rc, 64)
        prompt.assert_not_called()
        self.assertIn("INTERACTIVE_TTY_REQUIRED", stderr.getvalue())

    def test_confirmation_mismatch_never_calls_writer(self) -> None:
        stderr = io.StringIO()
        group = types.SimpleNamespace(gr_gid=1234)
        with (
            patch.object(self.cli, "parse_args", return_value=self.args()),
            patch.object(self.cli.os, "geteuid", return_value=0),
            patch.object(self.cli.sys, "stdin", self.tty(True)),
            patch.object(self.cli.grp, "getgrnam", return_value=group),
            patch.object(self.cli.getpass, "getpass", side_effect=["strong password one", "strong password two"]),
            patch.object(self.cli, "provision_credential_file") as writer,
            contextlib.redirect_stderr(stderr),
        ):
            rc = self.cli.main()
        self.assertEqual(rc, 65)
        writer.assert_not_called()
        self.assertIn("PASSWORD_CONFIRMATION_MISMATCH", stderr.getvalue())
        self.assertNotIn("strong password", stderr.getvalue())

    def test_success_output_contains_identity_only(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        group = types.SimpleNamespace(gr_gid=1234)
        password = "correct horse battery staple"
        with (
            patch.object(self.cli, "parse_args", return_value=self.args()),
            patch.object(self.cli.os, "geteuid", return_value=0),
            patch.object(self.cli.sys, "stdin", self.tty(True)),
            patch.object(self.cli.grp, "getgrnam", return_value=group),
            patch.object(self.cli.getpass, "getpass", side_effect=[password, password]),
            patch.object(self.cli, "provision_credential_file", return_value="admin") as writer,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc = self.cli.main()
        self.assertEqual(rc, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("LOCAL_ADMIN_PROVISIONED username=admin", stdout.getvalue())
        self.assertNotIn(password, stdout.getvalue())
        writer.assert_called_once()
        call = writer.call_args
        self.assertEqual(call.args[1], "Admin")
        self.assertEqual(call.args[2], password)
        self.assertEqual(call.kwargs["file_mode"], 0o640)


if __name__ == "__main__":
    unittest.main()
