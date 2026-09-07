from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center import __version__  # noqa: E402

TARGET = "0.12.0"
TEST_REVISION = "8" * 40


class ReleaseSuccessor0110Tests(unittest.TestCase):
    def test_runtime_package_and_web_source_versions_are_exact(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)
        release_js = (ROOT / "product/web/static/release.js").read_text(encoding="utf-8")
        self.assertEqual(__version__, TARGET)
        self.assertEqual(project["project"]["version"], TARGET)
        self.assertIn('version: "0.12.0"', release_js)

    def test_builder_orders_release_auth_and_upgrade_renderers(self) -> None:
        builder = (ROOT / "deploy/scripts/build-artifact.sh").read_text(encoding="utf-8")
        self.assertIn('# Published 0.10 source artifact gate: [ "$VERSION" = 0.10.0 ]', builder)
        self.assertIn('[ "$VERSION" = 0.12.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED', builder)
        self.assertIn("HOME_CENTER_0100_CONFIG_SCHEMA_NOT_ADMITTED", builder)
        release_index = builder.index('render-release-policy.py"')
        auth_index = builder.index('render-auth-deployment-v2.py"')
        upgrade_index = builder.index('render-upgrade-policy-v2.py"')
        self.assertLess(release_index, auth_index)
        self.assertLess(auth_index, upgrade_index)

    def test_artifact_contains_general_upgrade_and_safe_auth_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "dist"
            environment = os.environ.copy()
            environment.update(
                HOME_CENTER_VERSION=TARGET,
                HOME_CENTER_REVISION=TEST_REVISION,
                HOME_CENTER_RELEASE_BUILD="0",
                SOURCE_DATE_EPOCH="1767225600",
            )
            result = subprocess.run(
                ["bash", str(ROOT / "deploy/scripts/build-artifact.sh"), str(out)],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=45,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            archive = out / "home-center-0.12.0-linux-amd64.tar.gz"
            with tarfile.open(archive, "r:gz") as package:
                self.assertEqual(package.extractfile("./VERSION").read(), b"0.12.0\n")
                self.assertEqual(package.extractfile("./REVISION").read(), (TEST_REVISION + "\n").encode())
                bootstrap = package.extractfile("./deploy/bootstrap-hm-dm.sh").read().decode()
                installer = package.extractfile("./deploy/install-node.sh").read().decode()
                web_css = package.extractfile("./web/app.css").read().decode()
        deployment = bootstrap + "\n" + installer
        for marker in (
            "UPGRADE_POLICY_SCHEMA=home-center.upgrade-policy.v2",
            "LOCAL_ADMIN_MIGRATION_FROM_LEGACY=PASS",
            "LEGACY_TOKEN_RETAINED_FOR_ROLLBACK_ONLY=PASS",
            "LEGACY_LOCAL_ADMIN_MIGRATION_SOURCE=/etc/home-center/secrets/admin.token",
            "/etc/home-center/secrets/local-admin.json",
            "LOCAL_ADMIN_CLUSTER_PREFLIGHT=PASS",
            "LOCAL_ADMIN_DEPLOYMENT_PREFLIGHT=PASS",
            "DC02_SOFTWARE_CANARY_30S=PASS",
            "HOME_CENTER_CLUSTER_DEPLOY=PASS",
        ):
            self.assertIn(marker, deployment)
        for forbidden in ("Authorization: Bearer", "ADMIN_TOKEN", "AUTH_CONFIG", "/api/v1/overview"):
            self.assertNotIn(forbidden, deployment)
        self.assertIn(".login-layer[hidden]", web_css)

    def test_upgrade_renderer_fails_closed_on_shape_drift(self) -> None:
        renderer = ROOT / "deploy/scripts/render-upgrade-policy-v2.py"
        with tempfile.TemporaryDirectory() as temporary:
            bootstrap = Path(temporary) / "bootstrap.sh"
            installer = Path(temporary) / "install.sh"
            bootstrap.write_text("#!/bin/sh\nchanged\n", encoding="utf-8")
            installer.write_text("#!/bin/sh\nchanged\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-I", str(renderer), str(bootstrap), str(installer)],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("upgrade_policy_", result.stderr)


if __name__ == "__main__":
    unittest.main()


