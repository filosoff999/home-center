from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from home_center import tls_activate
from home_center.tls_activate import ActivationError


class TLSActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = {
            "node_id": "hm-dm-dc02",
            "role": "standby",
            "ip": "192.168.10.253",
            "fqdn": "dc02.hm.dm",
            "legacy_tls_certificate": "/etc/home-center/pki/node.crt",
        }
        self.fingerprint = "a" * 64
        self.previous_fingerprint = "b" * 64
        self.previous = Path("/etc/home-center/pki/web-releases/previous")
        self.release = Path("/etc/home-center/pki/web-releases/current")

    def test_success_points_to_new_release_and_verifies_presented_certificate(self) -> None:
        with (
            mock.patch.object(tls_activate, "_spec", return_value=self.spec),
            mock.patch.object(tls_activate, "validate_candidate", return_value=self.fingerprint),
            mock.patch.object(tls_activate, "_current_release", return_value=self.previous),
            mock.patch.object(tls_activate, "_prepare_release", return_value=self.release),
            mock.patch.object(tls_activate, "_clear_candidate") as clear_candidate,
            mock.patch.object(tls_activate, "_point_current") as point_current,
            mock.patch.object(tls_activate, "_restart") as restart,
            mock.patch.object(tls_activate, "_presented_fingerprint") as presented,
        ):
            result = tls_activate.activate()
        clear_candidate.assert_called_once_with()
        point_current.assert_called_once_with(self.release, "new")
        restart.assert_called_once_with()
        presented.assert_called_once_with(self.spec, self.fingerprint)
        self.assertEqual(result["status"], "activated")
        self.assertEqual(result["node_id"], "hm-dm-dc02")
        self.assertEqual(result["certificate_sha256"], self.fingerprint)
        self.assertEqual(result["previous_release"], "previous")

    def test_failed_postflight_rolls_back_and_verifies_previous_certificate(self) -> None:
        with (
            mock.patch.object(tls_activate, "_spec", return_value=self.spec),
            mock.patch.object(tls_activate, "validate_candidate", return_value=self.fingerprint),
            mock.patch.object(tls_activate, "_current_release", return_value=self.previous),
            mock.patch.object(tls_activate, "_prepare_release", return_value=self.release),
            mock.patch.object(tls_activate, "_clear_candidate"),
            mock.patch.object(tls_activate, "_point_current") as point_current,
            mock.patch.object(tls_activate, "_restart") as restart,
            mock.patch.object(
                tls_activate,
                "_presented_fingerprint",
                side_effect=[ActivationError("new_certificate_not_presented"), None],
            ) as presented,
            mock.patch.object(tls_activate, "_rollback_fingerprint", return_value=self.previous_fingerprint),
        ):
            with self.assertRaisesRegex(ActivationError, "activation_rolled_back"):
                tls_activate.activate()
        self.assertEqual(
            point_current.call_args_list,
            [mock.call(self.release, "new"), mock.call(self.previous, "rollback")],
        )
        self.assertEqual(restart.call_count, 2)
        self.assertEqual(
            presented.call_args_list,
            [mock.call(self.spec, self.fingerprint), mock.call(self.spec, self.previous_fingerprint)],
        )

    def test_failed_rollback_is_explicit(self) -> None:
        with (
            mock.patch.object(tls_activate, "_spec", return_value=self.spec),
            mock.patch.object(tls_activate, "validate_candidate", return_value=self.fingerprint),
            mock.patch.object(tls_activate, "_current_release", return_value=self.previous),
            mock.patch.object(tls_activate, "_prepare_release", return_value=self.release),
            mock.patch.object(tls_activate, "_clear_candidate"),
            mock.patch.object(tls_activate, "_point_current"),
            mock.patch.object(tls_activate, "_restart"),
            mock.patch.object(
                tls_activate,
                "_presented_fingerprint",
                side_effect=[ActivationError("activation_failure"), ActivationError("rollback_health_failure")],
            ),
            mock.patch.object(tls_activate, "_rollback_fingerprint", return_value=self.previous_fingerprint),
        ):
            with self.assertRaisesRegex(ActivationError, "rollback_failed"):
                tls_activate.activate()

    def test_existing_release_must_match_candidate_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            releases = root / "web-releases"
            releases.mkdir()
            release = releases / self.fingerprint[:24]
            release.mkdir()
            with (
                mock.patch.object(tls_activate, "WEB_RELEASES", releases),
                mock.patch.object(tls_activate, "_group"),
                mock.patch.object(tls_activate, "validate_candidate", return_value=self.previous_fingerprint),
            ):
                with self.assertRaisesRegex(ActivationError, "release_fingerprint_mismatch"):
                    tls_activate._prepare_release(self.fingerprint, self.spec)

    def test_spec_rejects_node_identity_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "schema": "home-center.config.v1",
                        "node_name": "dc02",
                        "node_id": "hm-dm-dc02",
                        "role": "leader",
                        "management_address": "192.168.10.253",
                        "tls_certificate": "/etc/home-center/pki/node.crt",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(tls_activate, "CONFIG", config):
                with self.assertRaisesRegex(ActivationError, "node_identity_mismatch"):
                    tls_activate._spec()

    def test_candidate_owner_gate_rejects_non_root_owned_file(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("non-root ownership gate requires an unprivileged test runner")
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(b"x" * 128)
            handle.flush()
            with self.assertRaisesRegex(ActivationError, "candidate_owner_rejected"):
                tls_activate._regular_secure(Path(handle.name), private=False)

    def test_san_gate_requires_node_vip_and_ip_exactly(self) -> None:
        with mock.patch.object(
            tls_activate,
            "_san_identities",
            return_value=(frozenset({"dc02.hm.dm", "home-center.hm.dm"}), frozenset({"192.168.10.253"})),
        ):
            tls_activate._require_expected_identities(self.spec, Path("candidate.crt"))

        cases = (
            ((frozenset({"home-center.hm.dm"}), frozenset({"192.168.10.253"})), "node_hostname_mismatch"),
            ((frozenset({"dc02.hm.dm"}), frozenset({"192.168.10.253"})), "vip_hostname_mismatch"),
            ((frozenset({"dc02.hm.dm", "home-center.hm.dm"}), frozenset({"192.168.10.252"})), "node_ip_mismatch"),
        )
        for identities, error in cases:
            with self.subTest(error=error):
                with mock.patch.object(tls_activate, "_san_identities", return_value=identities):
                    with self.assertRaisesRegex(ActivationError, error):
                        tls_activate._require_expected_identities(self.spec, Path("candidate.crt"))

    def test_validation_checks_chain_expiry_identity_profile_and_key_match(self) -> None:
        good_text = b"X509v3 Basic Constraints: critical\n    CA:FALSE\nTLS Web Server Authentication\n"
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=good_text, stderr=b"")
        with (
            mock.patch.object(tls_activate, "_regular_secure"),
            mock.patch.object(tls_activate, "_run", return_value=completed) as run,
            mock.patch.object(tls_activate, "_require_expected_identities") as identities,
            mock.patch.object(tls_activate, "_key_public", return_value=b"same"),
            mock.patch.object(tls_activate, "_cert_public", return_value=b"same"),
            mock.patch.object(tls_activate, "_fingerprint", return_value=self.fingerprint),
        ):
            result = tls_activate.validate_candidate(self.spec, Path("candidate.crt"), Path("candidate.key"))
        self.assertEqual(result, self.fingerprint)
        identities.assert_called_once_with(self.spec, Path("candidate.crt"))
        argv = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any("-x509_strict" in command for command in argv))
        self.assertTrue(any("-checkend" in command for command in argv))

    def test_validation_rejects_wrong_key(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"CA:FALSE\nTLS Web Server Authentication\n",
            stderr=b"",
        )
        with (
            mock.patch.object(tls_activate, "_regular_secure"),
            mock.patch.object(tls_activate, "_run", return_value=completed),
            mock.patch.object(tls_activate, "_require_expected_identities"),
            mock.patch.object(tls_activate, "_key_public", return_value=b"key-a"),
            mock.patch.object(tls_activate, "_cert_public", return_value=b"key-b"),
        ):
            with self.assertRaisesRegex(ActivationError, "key_mismatch"):
                tls_activate.validate_candidate(self.spec, Path("candidate.crt"), Path("candidate.key"))


if __name__ == "__main__":
    unittest.main()
