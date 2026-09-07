from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/runtime/recover-local-admin.py"


def load_cli():
    spec = importlib.util.spec_from_file_location("home_center_recover_local_admin_cli", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load recovery CLI")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Stream:
    def __init__(self, descriptor: int, tty: bool = True) -> None:
        self.descriptor = descriptor
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty

    def fileno(self) -> int:
        return self.descriptor


class LocalAdminRecoveryCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = load_cli()

    @staticmethod
    def args() -> argparse.Namespace:
        return argparse.Namespace()

    def test_non_root_is_rejected_before_any_prompt(self) -> None:
        stderr = io.StringIO()
        with (
            patch.object(self.cli, "parse_args", return_value=self.args()),
            patch.object(self.cli.os, "geteuid", return_value=1000),
            patch.object(self.cli, "_local_console_tty") as console,
            patch.object(self.cli.getpass, "getpass") as password_prompt,
            contextlib.redirect_stderr(stderr),
        ):
            rc = self.cli.main()
        self.assertEqual(rc, 77)
        console.assert_not_called()
        password_prompt.assert_not_called()
        self.assertIn("ROOT_REQUIRED", stderr.getvalue())

    def test_ssh_pty_and_mixed_descriptors_are_not_a_local_console(self) -> None:
        for names in (
            {0: "/dev/pts/1", 1: "/dev/pts/1", 2: "/dev/pts/1"},
            {0: "/dev/tty1", 1: "/dev/tty1", 2: "/dev/tty2"},
        ):
            with (
                self.subTest(names=names),
                patch.object(self.cli.sys, "stdin", Stream(0)),
                patch.object(self.cli.sys, "stdout", Stream(1)),
                patch.object(self.cli.sys, "stderr", Stream(2)),
                patch.object(self.cli.os, "ttyname", side_effect=lambda descriptor: names[descriptor]),
                self.assertRaisesRegex(self.cli.LocalAdminRecoveryError, "physical_console_required"),
            ):
                self.cli._local_console_tty()

    def test_confirmation_failure_never_prompts_for_password(self) -> None:
        stderr = io.StringIO()
        evidence = Mock()
        evidence.append.return_value = "requested-event"
        config = types.SimpleNamespace(
            local_admin_credentials_file=self.cli.CREDENTIAL_PATH,
            node_id="hm-dm-dc01",
        )
        group = types.SimpleNamespace(gr_gid=1234)
        with (
            patch.object(self.cli, "parse_args", return_value=self.args()),
            patch.object(self.cli.os, "geteuid", return_value=0),
            patch.object(self.cli.os, "getuid", return_value=1000),
            patch.object(self.cli, "_local_console_tty", return_value="/dev/tty1"),
            patch.object(self.cli, "_root_controlled_config"),
            patch.object(self.cli, "load_config", return_value=config),
            patch.object(self.cli.grp, "getgrnam", return_value=group),
            patch.object(self.cli, "RecoveryEvidenceLog", return_value=evidence),
            patch.object(self.cli.secrets, "token_hex", return_value="a1b2c3"),
            patch("builtins.input", return_value="wrong phrase"),
            patch.object(self.cli.getpass, "getpass") as password_prompt,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(stderr),
        ):
            rc = self.cli.main()
        self.assertEqual(rc, 65)
        password_prompt.assert_not_called()
        self.assertIn("OPERATOR_CONFIRMATION_FAILED", stderr.getvalue())
        self.assertEqual(evidence.append.call_args_list[-1].kwargs["reason"], "operator_confirmation_failed")

    def test_success_uses_hidden_input_and_emits_only_safe_identity(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        evidence = Mock()
        evidence.append.side_effect = ["requested-event", "accepted-event"]
        config = types.SimpleNamespace(
            local_admin_credentials_file=self.cli.CREDENTIAL_PATH,
            node_id="hm-dm-dc01",
        )
        group = types.SimpleNamespace(gr_gid=1234)
        rotator = Mock()

        def reset(password: str, *, commit_hook):
            commit_hook()
            return types.SimpleNamespace(username="admin")

        rotator.reset.side_effect = reset
        password = "new recovery password 42"
        with (
            patch.object(self.cli, "parse_args", return_value=self.args()),
            patch.object(self.cli.os, "geteuid", return_value=0),
            patch.object(self.cli.os, "getuid", return_value=1000),
            patch.object(self.cli, "_local_console_tty", return_value="/dev/tty1"),
            patch.object(self.cli, "_root_controlled_config"),
            patch.object(self.cli, "load_config", return_value=config),
            patch.object(self.cli.grp, "getgrnam", return_value=group),
            patch.object(self.cli, "RecoveryEvidenceLog", return_value=evidence),
            patch.object(self.cli, "LocalAdminCredentialRotator", return_value=rotator),
            patch.object(self.cli.secrets, "token_hex", return_value="a1b2c3"),
            patch("builtins.input", return_value="RESET admin a1b2c3"),
            patch.object(self.cli.getpass, "getpass", side_effect=[password, password]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc = self.cli.main()
        self.assertEqual(rc, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("LOCAL_ADMIN_RECOVERY=PASS event_id=accepted-event", stdout.getvalue())
        self.assertNotIn(password, stdout.getvalue())
        rotator.reset.assert_called_once()
        self.assertEqual(rotator.reset.call_args.args, (password,))
        self.assertEqual(evidence.append.call_args_list[-1].kwargs["reason"], "credential_reset_committed")


if __name__ == "__main__":
    unittest.main()
