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


TARGET = "0.9.1"
SOURCE = "0.9.0"
SOURCE_REVISION = "29b2f61071067028c14febbbaf0103c5600380e9"
SOURCE_ARTIFACT_SHA256 = "66531867f806c6665f41d2bb82dccfb5670403acd0c9988271714da09172f668"
SOURCE_RELEASE = "/opt/home-center/releases/0.9.0-29b2f6107106-66531867f806"
TEST_REVISION = "8" * 40


class ReleaseCut091Tests(unittest.TestCase):
    def test_runtime_package_and_web_source_versions_are_exact(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)
        release_js = (ROOT / "product/web/static/release.js").read_text(encoding="utf-8")

        self.assertEqual(__version__, TARGET)
        self.assertEqual(project["project"]["version"], TARGET)
        self.assertIn('version: "0.9.1"', release_js)

    def test_artifact_builder_admits_only_091_and_orders_renderers(self) -> None:
        builder = (ROOT / "deploy/scripts/build-artifact.sh").read_text(encoding="utf-8")
        self.assertIn('[ "$VERSION" = 0.9.1 ] || { echo RELEASE_VERSION_NOT_ADMITTED', builder)
        self.assertIn("HOME_CENTER_091_CONFIG_SCHEMA_NOT_ADMITTED", builder)
        release_index = builder.index('render-release-policy.py"')
        auth_index = builder.index('render-auth-deployment-v2.py"')
        self.assertLess(release_index, auth_index)

    def test_non_release_artifact_contains_exact_090_to_091_deployment(self) -> None:
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
            archive = out / "home-center-0.9.1-linux-amd64.tar.gz"
            self.assertTrue(archive.is_file())
            with tarfile.open(archive, "r:gz") as package:
                self.assertEqual(package.extractfile("./VERSION").read().decode(), "0.9.1\n")
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
        self.assertIn('[ "$TARGET_VERSION" = 0.9.1 ]', bootstrap)
        self.assertIn("ADMITTED_SOURCE_V090_VERSION=0.9.0", bootstrap)
        self.assertIn(f"ADMITTED_SOURCE_V090_REVISION={SOURCE_REVISION}", bootstrap)
        self.assertIn(f"ADMITTED_SOURCE_V090_RELEASE={SOURCE_RELEASE}", bootstrap)
        self.assertIn('[ "$VERSION" = 0.9.1 ]', installer)
        self.assertIn("verify_release_candidate", acceptance)
        self.assertIn(".login-layer[hidden]", web_css)

    def test_release_policy_records_exact_published_090_artifact_digest(self) -> None:
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
