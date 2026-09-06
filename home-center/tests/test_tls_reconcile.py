from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

from home_center import tls_activate, tls_reconcile
from home_center.tls_activate import ActivationError

ROOT = Path(__file__).resolve().parents[1]


class TLSReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fingerprint = "a" * 64
        self.spec = {
            "node_id": "hm-dm-dc02",
            "role": "standby",
            "ip": "192.168.10.253",
            "fqdn": "dc02.hm.dm",
            "legacy_tls_certificate": "/etc/home-center/pki/node.crt",
        }

    def test_direct_entrypoint_is_import_safe(self) -> None:
        result = subprocess.run(
            [sys.executable, "-I", str(ROOT / "product/control-plane/src/home_center/tls_reconcile.py")],
            check=False,
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("ImportError", result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "reason": "reconcile_validation_failed",
                "schema": "home-center.tls-reconcile-result.v1",
                "status": "failed",
            },
        )

    def test_listener_divergence_restarts_and_proves_durable_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "candidate"
            candidate.mkdir()
            release = Path(tmp) / self.fingerprint[:24]
            with (
                mock.patch.object(tls_activate, "CANDIDATE", candidate),
                mock.patch.object(tls_activate, "CANDIDATE_OWNER", candidate / ".owner.json"),
                mock.patch.object(tls_activate, "CANDIDATE_CERT", candidate / "tls.crt"),
                mock.patch.object(tls_activate, "CANDIDATE_KEY", candidate / "tls.key"),
                mock.patch.object(tls_activate, "_mutation_lock", return_value=nullcontext()),
                mock.patch.object(tls_activate, "_spec", return_value=self.spec),
                mock.patch.object(tls_activate, "_ensure_release_root"),
                mock.patch.object(tls_activate, "_recover_release_stages"),
                mock.patch.object(tls_activate, "_recover_pending_candidate_owner"),
                mock.patch.object(tls_activate, "_recover_current_pending_links"),
                mock.patch.object(tls_activate, "_current_release", return_value=release),
                mock.patch.object(
                    tls_activate,
                    "_rollback_identity",
                    return_value=(self.fingerprint, tls_activate.WEB_CA_CERT),
                ),
                mock.patch.object(tls_activate, "validate_candidate", return_value=self.fingerprint),
                mock.patch.object(tls_activate, "_restart") as restart,
                mock.patch.object(
                    tls_activate,
                    "_presented_fingerprint",
                    side_effect=[ActivationError("listener_diverged"), None],
                ) as presented,
            ):
                result = tls_reconcile.reconcile()
        restart.assert_called_once_with()
        self.assertEqual(
            presented.call_args_list,
            [
                mock.call(self.spec, self.fingerprint, tls_activate.WEB_CA_CERT, timeout_seconds=3),
                mock.call(self.spec, self.fingerprint, tls_activate.WEB_CA_CERT, timeout_seconds=20),
            ],
        )
        self.assertEqual(result["status"], "reconciled")
        self.assertEqual(result["mode"], "separate-web-identity")
        self.assertEqual(result["release"], self.fingerprint[:24])

    def test_release_name_must_bind_to_certificate_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "candidate"
            candidate.mkdir()
            release = Path(tmp) / ("b" * 24)
            with (
                mock.patch.object(tls_activate, "CANDIDATE", candidate),
                mock.patch.object(tls_activate, "CANDIDATE_OWNER", candidate / ".owner.json"),
                mock.patch.object(tls_activate, "CANDIDATE_CERT", candidate / "tls.crt"),
                mock.patch.object(tls_activate, "CANDIDATE_KEY", candidate / "tls.key"),
                mock.patch.object(tls_activate, "_mutation_lock", return_value=nullcontext()),
                mock.patch.object(tls_activate, "_spec", return_value=self.spec),
                mock.patch.object(tls_activate, "_ensure_release_root"),
                mock.patch.object(tls_activate, "_recover_release_stages"),
                mock.patch.object(tls_activate, "_recover_pending_candidate_owner"),
                mock.patch.object(tls_activate, "_recover_current_pending_links"),
                mock.patch.object(tls_activate, "_current_release", return_value=release),
                mock.patch.object(
                    tls_activate,
                    "_rollback_identity",
                    return_value=(self.fingerprint, tls_activate.WEB_CA_CERT),
                ),
                mock.patch.object(tls_activate, "validate_candidate", return_value=self.fingerprint),
            ):
                with self.assertRaisesRegex(ActivationError, "reconcile_release_identity_mismatch"):
                    tls_reconcile.reconcile()


if __name__ == "__main__":
    unittest.main()
