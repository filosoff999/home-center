from __future__ import annotations

import json
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
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
            "legacy_tls_private_key": "/etc/home-center/pki/node.key",
        }
        self.fingerprint = "a" * 64
        self.previous_fingerprint = "b" * 64
        self.previous = Path("/etc/home-center/pki/web-releases/previous")
        self.release = Path("/etc/home-center/pki/web-releases/current")

    def test_success_points_to_new_release_and_verifies_presented_certificate(self) -> None:
        with (
            mock.patch.object(tls_activate, "_mutation_lock", return_value=nullcontext()),
            mock.patch.object(tls_activate, "_spec", return_value=self.spec),
            mock.patch.object(tls_activate, "_candidate_owner", return_value={"operation_id": "1" * 32}),
            mock.patch.object(tls_activate, "validate_candidate", return_value=self.fingerprint),
            mock.patch.object(tls_activate, "_current_release", return_value=self.previous),
            mock.patch.object(tls_activate, "_rollback_identity", return_value=(self.previous_fingerprint, tls_activate.WEB_CA_CERT)),
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
        presented.assert_called_once_with(self.spec, self.fingerprint, tls_activate.WEB_CA_CERT)
        self.assertEqual(result["status"], "activated")
        self.assertEqual(result["node_id"], "hm-dm-dc02")
        self.assertEqual(result["certificate_sha256"], self.fingerprint)
        self.assertEqual(result["previous_release"], "previous")

    def test_failed_postflight_rolls_back_and_verifies_previous_certificate(self) -> None:
        with (
            mock.patch.object(tls_activate, "_mutation_lock", return_value=nullcontext()),
            mock.patch.object(tls_activate, "_spec", return_value=self.spec),
            mock.patch.object(tls_activate, "_candidate_owner", return_value={"operation_id": "1" * 32}),
            mock.patch.object(tls_activate, "validate_candidate", return_value=self.fingerprint),
            mock.patch.object(tls_activate, "_current_release", return_value=self.previous),
            mock.patch.object(tls_activate, "_rollback_identity", return_value=(self.previous_fingerprint, tls_activate.WEB_CA_CERT)),
            mock.patch.object(tls_activate, "_prepare_release", return_value=self.release),
            mock.patch.object(tls_activate, "_clear_candidate"),
            mock.patch.object(tls_activate, "_point_current") as point_current,
            mock.patch.object(tls_activate, "_restart") as restart,
            mock.patch.object(
                tls_activate,
                "_presented_fingerprint",
                side_effect=[ActivationError("new_certificate_not_presented"), None],
            ) as presented,
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
            [
                mock.call(self.spec, self.fingerprint, tls_activate.WEB_CA_CERT),
                mock.call(self.spec, self.previous_fingerprint, tls_activate.WEB_CA_CERT),
            ],
        )

    def test_first_rotation_rollback_verifies_legacy_identity_with_peer_ca(self) -> None:
        with (
            mock.patch.object(tls_activate, "_regular_secure"),
            mock.patch.object(tls_activate, "_cert_public", return_value=b"public"),
            mock.patch.object(tls_activate, "_key_public", return_value=b"public"),
            mock.patch.object(tls_activate, "_fingerprint", return_value=self.previous_fingerprint),
            mock.patch.object(tls_activate, "_presented_fingerprint") as presented,
            mock.patch.object(tls_activate, "_run") as run,
        ):
            result = tls_activate._rollback_identity(self.spec, None)
        self.assertEqual(result, (self.previous_fingerprint, tls_activate.PEER_CA_CERT))
        self.assertEqual(run.call_args.args[0][-2:], [str(tls_activate.PEER_CA_CERT), self.spec["legacy_tls_certificate"]])
        presented.assert_called_once_with(self.spec, self.previous_fingerprint, tls_activate.PEER_CA_CERT)

    def test_previous_ed25519_web_release_uses_peer_ca_for_rollback(self) -> None:
        with (
            mock.patch.object(tls_activate, "_regular_secure"),
            mock.patch.object(tls_activate, "_cert_public", return_value=b"public"),
            mock.patch.object(tls_activate, "_key_public", return_value=b"public"),
            mock.patch.object(tls_activate, "_fingerprint", return_value=self.previous_fingerprint),
            mock.patch.object(tls_activate, "_run", side_effect=[ActivationError("wrong_chain"), mock.DEFAULT]) as run,
        ):
            result = tls_activate._rollback_identity(self.spec, self.previous)
        self.assertEqual(result, (self.previous_fingerprint, tls_activate.PEER_CA_CERT))
        self.assertEqual(run.call_count, 2)

    def test_unknown_previous_chain_blocks_before_switch(self) -> None:
        with (
            mock.patch.object(tls_activate, "_regular_secure"),
            mock.patch.object(tls_activate, "_cert_public", return_value=b"public"),
            mock.patch.object(tls_activate, "_key_public", return_value=b"public"),
            mock.patch.object(tls_activate, "_run", side_effect=ActivationError("wrong_chain")),
        ):
            with self.assertRaisesRegex(ActivationError, "previous_chain_rejected"):
                tls_activate._rollback_identity(self.spec, self.previous)

    def test_failed_rollback_is_explicit(self) -> None:
        with (
            mock.patch.object(tls_activate, "_mutation_lock", return_value=nullcontext()),
            mock.patch.object(tls_activate, "_spec", return_value=self.spec),
            mock.patch.object(tls_activate, "_candidate_owner", return_value={"operation_id": "1" * 32}),
            mock.patch.object(tls_activate, "validate_candidate", return_value=self.fingerprint),
            mock.patch.object(tls_activate, "_current_release", return_value=self.previous),
            mock.patch.object(tls_activate, "_rollback_identity", return_value=(self.previous_fingerprint, tls_activate.WEB_CA_CERT)),
            mock.patch.object(tls_activate, "_prepare_release", return_value=self.release),
            mock.patch.object(tls_activate, "_clear_candidate"),
            mock.patch.object(tls_activate, "_point_current"),
            mock.patch.object(tls_activate, "_restart"),
            mock.patch.object(
                tls_activate,
                "_presented_fingerprint",
                side_effect=[ActivationError("activation_failure"), ActivationError("rollback_health_failure")],
            ),
        ):
            with self.assertRaisesRegex(ActivationError, "rollback_failed"):
                tls_activate.activate()

    def test_budget_exhaustion_blocks_before_candidate_clear_or_switch(self) -> None:
        with (
            mock.patch.object(tls_activate, "_spec", return_value=self.spec),
            mock.patch.object(tls_activate, "_candidate_owner", return_value={"operation_id": "1" * 32}),
            mock.patch.object(tls_activate, "validate_candidate", return_value=self.fingerprint),
            mock.patch.object(tls_activate, "_current_release", return_value=self.previous),
            mock.patch.object(tls_activate, "_rollback_identity", return_value=(self.previous_fingerprint, tls_activate.WEB_CA_CERT)),
            mock.patch.object(tls_activate, "_prepare_release", return_value=self.release),
            mock.patch.object(tls_activate, "_clear_candidate") as clear_candidate,
            mock.patch.object(tls_activate, "_point_current") as point_current,
            mock.patch.object(tls_activate.time, "monotonic", return_value=100.0),
        ):
            with self.assertRaisesRegex(ActivationError, "activation_budget_exhausted_before_switch"):
                tls_activate._activate_locked(200.0)
        clear_candidate.assert_not_called()
        point_current.assert_not_called()

    def test_main_emits_allowlisted_rollback_outcomes(self) -> None:
        cases = (
            ("activation_rolled_back", "rolled_back", "activation_rolled_back"),
            ("rollback_failed", "unknown", "rollback_failed_recovery_required"),
            (
                "activation_switch_outcome_unknown",
                "unknown",
                "activation_switch_outcome_unknown_recovery_required",
            ),
            ("sensitive internal detail", "failed", "activation_preflight_failed"),
        )
        for error, expected_status, expected_reason in cases:
            output = io.StringIO()
            with self.subTest(error=error), mock.patch.object(
                tls_activate, "activate", side_effect=ActivationError(error)
            ), redirect_stdout(output):
                code = tls_activate.main()
            value = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(value["status"], expected_status)
            self.assertEqual(value["reason"], expected_reason)
            self.assertNotIn("sensitive internal detail", output.getvalue())

    def test_node_mutation_lock_is_non_reentrant_and_root_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_dir = Path(tmp) / "locks"
            lock_file = lock_dir / "node-mutation.lock"
            with (
                mock.patch.object(tls_activate, "MUTATION_LOCK_DIR", lock_dir),
                mock.patch.object(tls_activate, "MUTATION_LOCK", lock_file),
                mock.patch.object(tls_activate, "ROOT_UID", os.getuid()),
                tls_activate._mutation_lock(),
            ):
                with self.assertRaisesRegex(ActivationError, "mutation_in_progress"):
                    with tls_activate._mutation_lock():
                        pass
            self.assertEqual(lock_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual(lock_file.stat().st_mode & 0o777, 0o600)

    def test_existing_release_must_match_candidate_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            releases = root / "web-releases"
            releases.mkdir()
            release = releases / self.fingerprint[:24]
            release.mkdir()
            with (
                mock.patch.object(tls_activate, "WEB_RELEASES", releases),
                mock.patch.object(tls_activate, "_ensure_release_root"),
                mock.patch.object(tls_activate, "_recover_release_stages"),
                mock.patch.object(tls_activate, "_candidate_owner", return_value={"operation_id": "1" * 32}),
                mock.patch.object(
                    tls_activate,
                    "_validate_release",
                    side_effect=ActivationError("release_fingerprint_mismatch"),
                ) as validate_release,
            ):
                with self.assertRaisesRegex(ActivationError, "release_fingerprint_mismatch"):
                    tls_activate._prepare_release(self.fingerprint, self.spec)
            validate_release.assert_called_once_with(release, self.fingerprint, self.spec)

    def test_existing_release_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            releases = root / "releases"
            releases.mkdir()
            target = root / "outside"
            target.mkdir()
            release = releases / self.fingerprint[:24]
            release.symlink_to(target, target_is_directory=True)
            with (
                mock.patch.object(tls_activate, "WEB_RELEASES", releases),
                mock.patch.object(tls_activate, "_ensure_release_root"),
                mock.patch.object(tls_activate, "_recover_release_stages"),
                mock.patch.object(tls_activate, "_candidate_owner", return_value={"operation_id": "1" * 32}),
                mock.patch.object(
                    tls_activate.pwd,
                    "getpwnam",
                    return_value=SimpleNamespace(pw_gid=os.getgid()),
                ),
            ):
                with self.assertRaisesRegex(ActivationError, "release_stage_rejected"):
                    tls_activate._prepare_release(self.fingerprint, self.spec)

    def test_point_current_fsyncs_parent_after_replace_and_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release = root / "releases" / self.fingerprint[:24]
            release.mkdir(parents=True)
            current = root / "current"
            with (
                mock.patch.object(tls_activate, "WEB_ROOT", root),
                mock.patch.object(tls_activate, "WEB_CURRENT", current),
                mock.patch.object(tls_activate, "_fsync_directory") as fsync_directory,
            ):
                tls_activate._point_current(release, "new")
                self.assertEqual(current.resolve(), release.resolve())
                fsync_directory.assert_called_once_with(root)
                fsync_directory.reset_mock()
                tls_activate._point_current(None, "rollback")
                self.assertFalse(current.exists())
                fsync_directory.assert_called_once_with(root)

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

    def test_spec_binds_distinct_fixed_peer_and_web_trust_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "schema": "home-center.config.v1",
                        "node_name": "dc02",
                        "node_id": "hm-dm-dc02",
                        "role": "standby",
                        "management_address": "192.168.10.253",
                        "tls_certificate": "/etc/home-center/pki/node.crt",
                        "cluster_ca": str(tls_activate.PEER_CA_CERT),
                        "web_ca": str(tls_activate.PEER_CA_CERT),
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(tls_activate, "CONFIG", config):
                with self.assertRaisesRegex(ActivationError, "trust_anchor_path_rejected"):
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
        good_text = (
            b"Signature Algorithm: ecdsa-with-SHA256\n"
            b"Public Key Algorithm: id-ecPublicKey\n"
            b"ASN1 OID: prime256v1\n"
            b"X509v3 Basic Constraints: critical\n    CA:FALSE\n"
            b"TLS Web Server Authentication\n"
        )
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=good_text, stderr=b"")
        with (
            mock.patch.object(tls_activate, "_regular_secure"),
            mock.patch.object(tls_activate, "_run", return_value=completed) as run,
            mock.patch.object(tls_activate, "_require_browser_compatible_certificate"),
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
            stdout=(
                b"Signature Algorithm: ecdsa-with-SHA256\n"
                b"Public Key Algorithm: id-ecPublicKey\n"
                b"ASN1 OID: prime256v1\n"
                b"CA:FALSE\nTLS Web Server Authentication\n"
            ),
            stderr=b"",
        )
        with (
            mock.patch.object(tls_activate, "_regular_secure"),
            mock.patch.object(tls_activate, "_run", return_value=completed),
            mock.patch.object(tls_activate, "_require_browser_compatible_certificate"),
            mock.patch.object(tls_activate, "_require_expected_identities"),
            mock.patch.object(tls_activate, "_key_public", return_value=b"key-a"),
            mock.patch.object(tls_activate, "_cert_public", return_value=b"key-b"),
        ):
            with self.assertRaisesRegex(ActivationError, "key_mismatch"):
                tls_activate.validate_candidate(self.spec, Path("candidate.crt"), Path("candidate.key"))


if __name__ == "__main__":
    unittest.main()
