from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class P23ContractSurfaceTests(unittest.TestCase):
    def test_release_version_is_consistent(self) -> None:
        runtime = (ROOT / "product/control-plane/src/home_center/__init__.py").read_text(encoding="utf-8")
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        builder = (ROOT / "deploy/scripts/build-artifact.sh").read_text(encoding="utf-8")
        self.assertIn('__version__ = "0.5.0"', runtime)
        self.assertIn('version = "0.5.0"', project)
        self.assertIn("HOME_CENTER_VERSION:-$SOURCE_VERSION", builder)
        self.assertIn('[ "$VERSION" = "$SOURCE_VERSION" ]', builder)
        self.assertIn('[ "$VERSION" = 0.5.0 ]', builder)
        self.assertIn('[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]]', builder)

    def test_deploy_scripts_reject_unadmitted_release_versions(self) -> None:
        for name in ("install-node.sh", "bootstrap-hm-dm.sh"):
            script = (ROOT / "deploy/scripts" / name).read_text(encoding="utf-8")
            self.assertIn('[ "$VERSION" = 0.5.0 ]' if name == "install-node.sh" else '[ "$TARGET_VERSION" = 0.5.0 ]', script)
            self.assertIn("RELEASE_VERSION_NOT_ADMITTED", script)
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ["bash", str(ROOT / "deploy/scripts/build-artifact.sh"), tmp],
                check=False,
                capture_output=True,
                text=True,
                cwd=ROOT,
                env={**os.environ, "HOME_CENTER_VERSION": "0.4.0"},
            )
        self.assertEqual(result.returncode, 66)
        self.assertIn("VERSION_OVERRIDE_MISMATCH", result.stderr)
        installer = (ROOT / "deploy/scripts/install-node.sh").read_text(encoding="utf-8")
        self.assertIn('[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]]', installer)
        self.assertNotIn("^working-tree$", installer)

    def test_bootstrap_admits_only_exact_accepted_source_identity(self) -> None:
        bootstrap = (ROOT / "deploy/scripts/bootstrap-hm-dm.sh").read_text(encoding="utf-8")
        for required in (
            "ADMITTED_SOURCE_V043_VERSION=0.4.3",
            "ADMITTED_SOURCE_V043_REVISION=64f798ceae0b669cbac01b452c3cf4fd96070136",
            "ADMITTED_SOURCE_V043_RELEASE=/opt/home-center/releases/0.4.3-64f798ceae0b-b2dde6a51ec9",
            'source_identity_admitted "$LOCAL_SOURCE_VERSION" "$LOCAL_SOURCE_REVISION" "$LOCAL_SOURCE_RELEASE"',
            '[ "$LOCAL_SOURCE_VERSION" = "$REMOTE_SOURCE_VERSION" ]',
            '[ "$LOCAL_SOURCE_REVISION" = "$REMOTE_SOURCE_REVISION" ]',
            '"$local_ready" "$remote_ready" "$LOCAL_SOURCE_VERSION"',
        ):
            self.assertIn(required, bootstrap)
        self.assertNotIn("ADMITTED_SOURCE_VERSION=", bootstrap)
        self.assertNotIn("ADMITTED_SOURCE_REVISION=", bootstrap)

    def test_software_rollout_preserves_existing_web_identity_transactionally(self) -> None:
        bootstrap = (ROOT / "deploy/scripts/bootstrap-hm-dm.sh").read_text(encoding="utf-8")
        for required in (
            "web_public_state_local()",
            "web_public_state_remote()",
            "LOCAL_WEB_STATE_BEFORE=$(web_public_state_local)",
            "REMOTE_WEB_STATE_BEFORE=$(web_public_state_remote)",
            '[ "$(web_public_state_local)" = "$LOCAL_WEB_STATE_BEFORE" ]',
            '[ "$(web_public_state_remote)" = "$REMOTE_WEB_STATE_BEFORE" ]',
            "DC01_FINAL_WEB_IDENTITY_CHANGED",
            "DC02_FINAL_WEB_IDENTITY_CHANGED",
            "FINAL_EXACT_RELEASE_AND_WEB_PEER_INVARIANTS=PASS",
        ):
            self.assertIn(required, bootstrap)
        rollback_proof = bootstrap.split("verify_cluster_source_restored()", 1)[1].split("cleanup()", 1)[0]
        self.assertIn('web_public_state_local)" = "$LOCAL_WEB_STATE_BEFORE"', rollback_proof)
        self.assertIn('web_public_state_remote)" = "$REMOTE_WEB_STATE_BEFORE"', rollback_proof)
        self.assertIn("--cacert /etc/home-center/pki/web-ca/ca.crt", rollback_proof)
        self.assertNotIn("--cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.254:8443", rollback_proof)

    def test_prior_cluster_recovery_ignores_valid_terminal_old_target_only(self) -> None:
        bootstrap = (ROOT / "deploy/scripts/bootstrap-hm-dm.sh").read_text(encoding="utf-8")
        recovery_block = bootstrap.split("PRIOR_CLUSTER_RECOVERY_JSON=", 1)[1]
        validator = recovery_block.split("<<'PY'\n", 1)[1].split("\nPY\n)", 1)[0]
        validator = validator.replace("info.st_uid != 0", f"info.st_uid != {os.getuid()}")
        validator = validator.replace("entry.st_uid != 0", f"entry.st_uid != {os.getuid()}")

        expected_artifact = "a" * 64
        expected_revision = "b" * 40

        def transaction(transaction_id: str, *, status: str, version: str, revision: str, artifact: str) -> dict:
            return {
                "artifact_sha256": artifact,
                "peer_identity": {
                    node: {
                        "ca_sha256": "c" * 64,
                        "certificate_sha256": "d" * 64,
                        "public_key_sha256": "e" * 64,
                    }
                    for node in ("dc01", "dc02")
                },
                "rollback_points": {
                    node: f"/var/backups/home-center-deploy/{transaction_id}-{node}"
                    for node in ("dc01", "dc02")
                },
                "schema": "home-center.cluster-deploy-transaction.v1",
                "source": {
                    node: {
                        "release": "/opt/home-center/releases/0.4.2-9f376e3d39eb-e259a050e9c7",
                        "revision": "9f376e3d39eb29b2c8e402d085cba8b9fee4258d",
                        "version": "0.4.2",
                    }
                    for node in ("dc01", "dc02")
                },
                "status": status,
                "target": {"revision": revision, "version": version},
                "transaction_id": transaction_id,
            }

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "transactions"
            directory.mkdir(mode=0o700)
            terminal_id = "20260906T153747Z-c66e25e1dbd2"
            unresolved_id = "20260906T160000Z-111111111111"
            terminal = transaction(
                terminal_id,
                status="succeeded",
                version="0.4.2",
                revision="9f376e3d39eb29b2c8e402d085cba8b9fee4258d",
                artifact="f" * 64,
            )
            terminal_path = directory / f"{terminal_id}.json"
            terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
            terminal_path.chmod(0o600)

            args = [sys.executable, "-I", "-", str(directory), expected_artifact, "0.5.0", expected_revision]
            completed = subprocess.run(args, input=validator, text=True, capture_output=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "")

            unresolved_path = directory / f"{unresolved_id}.json"
            unresolved = transaction(
                unresolved_id,
                status="recovery_required",
                version="0.4.2",
                revision="9f376e3d39eb29b2c8e402d085cba8b9fee4258d",
                artifact=expected_artifact,
            )
            unresolved_path.write_text(json.dumps(unresolved), encoding="utf-8")
            unresolved_path.chmod(0o600)
            completed = subprocess.run(args, input=validator, text=True, capture_output=True, check=False)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("prior_cluster_target_mismatch_recovery_required", completed.stderr)

            unresolved["target"] = {"revision": expected_revision, "version": "0.5.0"}
            unresolved_path.write_text(json.dumps(unresolved), encoding="utf-8")
            unresolved_path.chmod(0o600)
            completed = subprocess.run(args, input=validator, text=True, capture_output=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout), unresolved)

            unresolved["artifact_sha256"] = "f" * 64
            unresolved_path.write_text(json.dumps(unresolved), encoding="utf-8")
            unresolved_path.chmod(0o600)
            completed = subprocess.run(args, input=validator, text=True, capture_output=True, check=False)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("prior_cluster_artifact_mismatch_recovery_required", completed.stderr)

    def test_activation_timeout_envelope_reserves_rollback_and_postflight(self) -> None:
        from home_center import tls_activate
        from home_center.helper_client import TLS_HELPER_TIMEOUT_SECONDS

        self.assertEqual(tls_activate.ACTIVATION_HELPER_TIMEOUT_SECONDS, 360)
        self.assertGreaterEqual(tls_activate.ACTIVATION_ROLLBACK_RESERVE_SECONDS, 130)
        self.assertGreater(TLS_HELPER_TIMEOUT_SECONDS, tls_activate.ACTIVATION_HELPER_TIMEOUT_SECONDS)
        unit = (ROOT / "deploy/systemd/home-center-tls-maintenance.service").read_text(encoding="utf-8")
        self.assertIn("TimeoutStartSec=600s", unit)

    def test_tls_maintenance_release_entrypoint_is_directly_runnable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp)
            shutil.copy2(ROOT / "deploy/runtime/tls-maintenance-run.py", release)
            shutil.copytree(ROOT / "product/control-plane/src/home_center", release / "home_center")
            result = subprocess.run(
                [sys.executable, "-I", str(release / "tls-maintenance-run.py"), "--help"],
                check=False,
                capture_output=True,
                text=True,
                cwd=release,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--activate-staged", result.stdout)

    def test_openapi_publishes_tls_status_and_public_trust_anchor(self) -> None:
        value = json.loads((ROOT / "contracts/openapi/home-center.v1.openapi.json").read_text(encoding="utf-8"))
        self.assertEqual(value["openapi"], "3.1.0")
        self.assertEqual(value["info"]["version"], "0.5.0")
        self.assertEqual(value["servers"], [{"url": "https://dc01.hm.dm:8443", "description": "Current canonical Home Center production endpoint"}])
        paths = value["paths"]
        self.assertIn("/api/v1/tls", paths)
        self.assertIn("/api/v1/tls/ca.crt", paths)
        self.assertNotIn("security", paths["/api/v1/tls"]["get"])
        self.assertEqual(paths["/api/v1/tls/ca.crt"]["get"]["security"], [])
        self.assertEqual(
            paths["/api/v1/tls"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/TlsStatus",
        )

    def test_tls_status_schema_is_closed_and_exposes_presence_only_for_private_key(self) -> None:
        value = json.loads((ROOT / "contracts/openapi/home-center.v1.openapi.json").read_text(encoding="utf-8"))
        schemas = value["components"]["schemas"]
        schema = schemas["TlsStatus"]
        self.assertFalse(schema["additionalProperties"])
        top_properties = schema["properties"]
        self.assertNotIn("private_key", top_properties)
        self.assertNotIn("private_key_pem", top_properties)
        candidate_properties = top_properties["candidate"]["properties"]
        self.assertEqual(candidate_properties["private_key_present"], {"type": "boolean"})
        self.assertNotIn("private_key", candidate_properties)
        self.assertNotIn("private_key_pem", candidate_properties)
        certificate_properties = schemas["CertificateStatus"]["properties"]
        self.assertIn("fingerprint_sha256", certificate_properties)
        self.assertIn("chain_valid", certificate_properties)
        self.assertIn("hostname_match", certificate_properties)
        self.assertEqual(certificate_properties["profile_valid"], {"type": "boolean"})
        for field in ("profile", "public_key_algorithm", "public_key_curve", "signature_algorithm"):
            self.assertIn(field, certificate_properties)
        serialized = json.dumps(schema, sort_keys=True)
        self.assertNotIn("tls.key", serialized)
        self.assertIn("candidate", serialized)
        self.assertIn("renewal", serialized)

    def test_rollout_and_rotation_recovery_evidence_is_durable_and_fail_closed(self) -> None:
        bootstrap = (ROOT / "deploy/scripts/bootstrap-hm-dm.sh").read_text(encoding="utf-8")
        rotate = (ROOT / "deploy/scripts/rotate-web-tls.sh").read_text(encoding="utf-8")
        installer = (ROOT / "deploy/scripts/install-node.sh").read_text(encoding="utf-8")
        rollback = (ROOT / "deploy/scripts/rollback-node.sh").read_text(encoding="utf-8")
        for required in (
            '"peer_identity"',
            "PRIOR_CLUSTER_RECOVERY_PEER_STATE_CHANGED",
            "prior_cluster_recovery_overview_rejected",
            "cluster_source_overview_rejected",
            "publish_cluster_transaction rolled_back",
        ):
            self.assertIn(required, bootstrap)
        for required in (
            'WEB_CANDIDATE_OWNER=$WEB_CANDIDATE/.owner.json',
            "home-center.web-candidate-owner.v1",
            "candidate_cas_local cleanup-owned",
            "candidate_cas_remote cleanup-owned",
            "DC02_WEB_TLS_CANARY_30S=PASS",
            "verify_peer_invariants_checked",
        ):
            self.assertIn(required, rotate)
        for script in (installer, rollback):
            self.assertIn("sync -f /opt/home-center", script)

    def test_empty_regular_flock_files_are_admitted_without_stat_type_labels(self) -> None:
        scripts = {
            name: (ROOT / "deploy/scripts" / name).read_text(encoding="utf-8")
            for name in ("bootstrap-hm-dm.sh", "install-node.sh", "rollback-node.sh", "rotate-web-tls.sh")
        }
        for name, script in scripts.items():
            with self.subTest(script=name):
                self.assertIn('[ -f "$LOCK_FILE" ]', script)
                self.assertIn("stat -c '%u:%g:%a' \"$LOCK_FILE\"", script)
                self.assertNotIn("stat -c '%F:%u:%g:%a' \"$LOCK_FILE\"", script)
        rotate = scripts["rotate-web-tls.sh"]
        self.assertIn('[ -f "$lock_file" ]', rotate)
        self.assertIn('[ -f "$LOCK_DIR/node-mutation.lock" ]', rotate)
        self.assertIn("sudo -n test -f '$LOCK_DIR/node-mutation.lock'", rotate)

        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "empty.lock"
            lock.touch(mode=0o600)
            lock.chmod(0o600)
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    '[ ! -L "$1" ] && [ -f "$1" ] && '
                    '[ "$(stat -c \'%u:%g:%a\' "$1")" = "$(id -u):$(id -g):600" ]',
                    "bash",
                    str(lock),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
