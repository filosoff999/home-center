from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "deploy/scripts/render-auth-deployment-v2.py"
BOOTSTRAP = ROOT / "deploy/scripts/bootstrap-hm-dm.sh"
INSTALLER = ROOT / "deploy/scripts/install-node.sh"


def load_renderer():
    spec = importlib.util.spec_from_file_location("home_center_auth_deployment_v2_renderer", RENDERER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load auth deployment renderer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AuthDeploymentV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = load_renderer()
        self.bootstrap_source = BOOTSTRAP.read_text(encoding="utf-8")
        self.installer_source = INSTALLER.read_text(encoding="utf-8")

    def test_installer_moves_to_local_admin_credential_without_bearer(self) -> None:
        rendered = self.renderer.render_installer(self.installer_source)
        self.assertNotIn("admin.token", rendered)
        self.assertNotIn("Authorization: Bearer", rendered)
        self.assertNotIn("ADMIN_TOKEN", rendered)
        self.assertIn("/etc/home-center/secrets/local-admin.json", rendered)
        self.assertIn("validate_local_admin_credential", rendered)
        self.assertIn("LOCAL_ADMIN_DEPLOYMENT_PREFLIGHT=PASS", rendered)
        self.assertIn("/readyz", rendered)
        self.assertIn("publish_transaction started", rendered)
        self.assertIn("verify_previous_runtime", rendered)

    def test_bootstrap_removes_authenticated_overview_probe_but_keeps_ha_gates(self) -> None:
        rendered = self.renderer.render_bootstrap(self.bootstrap_source)
        for forbidden in (
            "admin.token",
            "Authorization: Bearer",
            "ADMIN_TOKEN",
            "AUTH_CONFIG",
            "/api/v1/overview",
            "DC02_ADMIN_TOKEN_MISMATCH",
        ):
            self.assertNotIn(forbidden, rendered)
        for required in (
            "/etc/home-center/secrets/local-admin.json",
            "validate_local_admin_credential",
            "validate_remote_local_admin_credential",
            "LOCAL_ADMIN_CLUSTER_PREFLIGHT=PASS",
            "CLUSTER_AUTH_FREE_ACCEPTANCE=PASS",
            "/readyz",
            "/internal/v1/node",
            "verify_bidirectional_peer_identity",
            "fail_rollback",
            "publish_cluster_transaction",
            "DC02_SOFTWARE_CANARY_30S=PASS",
            "HOME_CENTER_CLUSTER_ROLLOUT=PASS",
        ):
            self.assertIn(required, rendered)

    def test_renderer_writes_two_independent_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bootstrap_output = root / "bootstrap.sh"
            installer_output = root / "install.sh"
            self.renderer.render(BOOTSTRAP, INSTALLER, bootstrap_output, installer_output)
            self.assertEqual(bootstrap_output.read_text(encoding="utf-8"), self.renderer.render_bootstrap(self.bootstrap_source))
            self.assertEqual(installer_output.read_text(encoding="utf-8"), self.renderer.render_installer(self.installer_source))

    def test_installer_shape_drift_fails_closed(self) -> None:
        drifted = self.installer_source.replace("BACKUP_READY=1\npublish_transaction started", "BACKUP_READY=1\n# drift\npublish_transaction started")
        with self.assertRaisesRegex(ValueError, "installer_credential_preflight_shape_rejected"):
            self.renderer.render_installer(drifted)

    def test_bootstrap_shape_drift_fails_closed(self) -> None:
        drifted = self.bootstrap_source.replace("RECOVERY_AUTH_CONFIG=$(mktemp", "RECOVERY_AUTH_CONFIG =$(mktemp", 1)
        with self.assertRaises(ValueError):
            self.renderer.render_bootstrap(drifted)


if __name__ == "__main__":
    unittest.main()
