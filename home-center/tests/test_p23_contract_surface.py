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
        self.assertIn('__version__ = "0.4.1"', runtime)
        self.assertIn('version = "0.4.1"', project)
        self.assertIn("HOME_CENTER_VERSION:-$SOURCE_VERSION", builder)
        self.assertIn('[ "$VERSION" = "$SOURCE_VERSION" ]', builder)
        self.assertIn('[ "$VERSION" = 0.4.1 ]', builder)
        self.assertIn('[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]]', builder)

    def test_deploy_scripts_reject_unadmitted_release_versions(self) -> None:
        for name in ("install-node.sh", "bootstrap-hm-dm.sh"):
            script = (ROOT / "deploy/scripts" / name).read_text(encoding="utf-8")
            self.assertIn('[ "$VERSION" = 0.4.1 ]' if name == "install-node.sh" else '[ "$TARGET_VERSION" = 0.4.1 ]', script)
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
        self.assertEqual(value["info"]["version"], "0.4.1")
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


if __name__ == "__main__":
    unittest.main()
