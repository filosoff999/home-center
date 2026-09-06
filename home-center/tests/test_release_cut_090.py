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


TARGET = "0.9.0"
SOURCE = "0.8.0"
SOURCE_REVISION = "bbb2b1e952b2072c8ce30ad6b3220c7c14280949"
SOURCE_ARTIFACT_SHA256 = "25fb72fdffab972703d9be2b7e457b1f4ed053bc1c013a6237558fd693e73ca8"
SOURCE_RELEASE = "/opt/home-center/releases/0.8.0-bbb2b1e952b2-25fb72fdffab"
TEST_REVISION = "8" * 40


class ReleaseCut090Tests(unittest.TestCase):
    def test_runtime_package_and_web_source_versions_are_exact(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)
        release_js = (ROOT / "product/web/static/release.js").read_text(encoding="utf-8")

        self.assertEqual(__version__, TARGET)
        self.assertEqual(project["project"]["version"], TARGET)
        self.assertIn('version: "0.9.0"', release_js)

    def test_artifact_builder_admits_only_090_and_orders_renderers(self) -> None:
        builder = (ROOT / "deploy/scripts/build-artifact.sh").read_text(encoding="utf-8")
        self.assertIn('[ "$VERSION" = 0.9.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED', builder)
        self.assertIn("HOME_CENTER_090_CONFIG_SCHEMA_NOT_ADMITTED", builder)
        release_index = builder.index('render-release-policy.py"')
        auth_index = builder.index('render-auth-deployment-v2.py"')
        self.assertLess(release_index, auth_index)

    def test_non_release_artifact_contains_exact_080_to_090_deployment(self) -> None:
        builder = ROOT / "deploy/scripts/build-artifact.sh"
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
                ["bash", str(builder), str(out)],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            archive = out / "home-center-0.9.0-linux-amd64.tar.gz"
            self.assertTrue(archive.is_file())
            with tarfile.open(archive, "r:gz") as package:
                self.assertEqual(package.extractfile("./VERSION").read().decode(), "0.9.0\n")
                self.assertEqual(package.extractfile("./REVISION").read().decode(), TEST_REVISION + "\n")
                bootstrap = package.extractfile("./deploy/bootstrap-hm-dm.sh").read().decode()
                installer = package.extractfile("./deploy/install-node.sh").read().decode()
                acceptance = package.extractfile("./release-candidate-verify.py").read().decode()
                web_css = package.extractfile("./web/app.css").read().decode()

        deployment = bootstrap + "\n" + installer
        for forbidden in (
            "/etc/home-center/secrets/admin.token",
            "Authorization: Bearer",
            "ADMIN_TOKEN",
            "AUTH_CONFIG",
            "/api/v1/overview",
        ):
            self.assertNotIn(forbidden, deployment)
        for required in (
            "/etc/home-center/secrets/local-admin.json",
            "LOCAL_ADMIN_CLUSTER_PREFLIGHT=PASS",
            "LOCAL_ADMIN_DEPLOYMENT_PREFLIGHT=PASS",
            "/var/lib/home-center/ad-auth",
            "CLUSTER_AUTH_FREE_ACCEPTANCE=PASS",
            "DC02_SOFTWARE_CANARY_30S=PASS",
            "HOME_CENTER_CLUSTER_DEPLOY=PASS",
        ):
            self.assertIn(required, deployment)
        self.assertIn('[ "$TARGET_VERSION" = 0.9.0 ]', bootstrap)
        self.assertIn("ADMITTED_SOURCE_V080_VERSION=0.8.0", bootstrap)
        self.assertIn(f"ADMITTED_SOURCE_V080_REVISION={SOURCE_REVISION}", bootstrap)
        self.assertIn(f"ADMITTED_SOURCE_V080_RELEASE={SOURCE_RELEASE}", bootstrap)
        self.assertIn('[ "$VERSION" = 0.9.0 ]', installer)
        self.assertIn("verify_release_candidate", acceptance)
        self.assertIn(".login-layer[hidden]", web_css)

    def test_release_policy_records_exact_published_080_artifact_digest(self) -> None:
        renderer = (ROOT / "deploy/scripts/render-release-policy.py").read_text(encoding="utf-8")
        self.assertIn(f'SOURCE_ARTIFACT_SHA256 = "{SOURCE_ARTIFACT_SHA256}"', renderer)

    def test_release_renderer_fails_closed_on_source_shape_drift(self) -> None:
        renderer = ROOT / "deploy/scripts/render-release-policy.py"
        with tempfile.TemporaryDirectory() as temporary:
            bootstrap = Path(temporary) / "bootstrap-hm-dm.sh"
            installer = Path(temporary) / "install-node.sh"
            bootstrap.write_text("#!/bin/sh\necho changed\n", encoding="utf-8")
            installer.write_text("#!/bin/sh\necho changed\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-I", str(renderer), str(bootstrap), str(installer)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=10,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release_policy_bootstrap_shape_rejected", result.stderr)


if __name__ == "__main__":
    unittest.main()
