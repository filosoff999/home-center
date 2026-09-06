from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center import __version__  # noqa: E402


TARGET = "0.7.0"
SOURCE = "0.6.0"
SOURCE_REVISION = "2235670d77bccc1c777223eb4f50a546313b4d2d"
SOURCE_ARTIFACT_SHA256 = "bf68e870339f18351f4401895f7bff18c093fa9809eb4eefda2633391fe9a811"
SOURCE_RELEASE = "/opt/home-center/releases/0.6.0-2235670d77bc-bf68e870339f"


class ReleaseCut070Tests(unittest.TestCase):
    def test_runtime_package_and_web_source_versions_are_exact(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)
        release_js = (ROOT / "product/web/static/release.js").read_text(encoding="utf-8")

        self.assertEqual(__version__, TARGET)
        self.assertEqual(project["project"]["version"], TARGET)
        self.assertIn('version: "0.7.0"', release_js)

    def test_artifact_builder_admits_only_070_and_uses_unified_renderer(self) -> None:
        builder = (ROOT / "deploy/scripts/build-artifact.sh").read_text(encoding="utf-8")
        self.assertIn('[ "$VERSION" = 0.7.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED', builder)
        self.assertIn('render-release-policy.py"', builder)
        self.assertIn('"$STAGE/deploy/bootstrap-hm-dm.sh"', builder)
        self.assertIn('"$STAGE/deploy/install-node.sh"', builder)

    def test_rendered_scripts_accept_exact_060_predecessor_and_070_target(self) -> None:
        renderer = ROOT / "deploy/scripts/render-release-policy.py"
        bootstrap_source = ROOT / "deploy/scripts/bootstrap-hm-dm.sh"
        installer_source = ROOT / "deploy/scripts/install-node.sh"
        with tempfile.TemporaryDirectory() as temporary:
            bootstrap = Path(temporary) / "bootstrap-hm-dm.sh"
            installer = Path(temporary) / "install-node.sh"
            shutil.copyfile(bootstrap_source, bootstrap)
            shutil.copyfile(installer_source, installer)
            result = subprocess.run(
                [sys.executable, "-I", str(renderer), str(bootstrap), str(installer)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rendered_bootstrap = bootstrap.read_text(encoding="utf-8")
            rendered_installer = installer.read_text(encoding="utf-8")

        self.assertIn('[ "$TARGET_VERSION" = 0.7.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED', rendered_bootstrap)
        self.assertIn("ADMITTED_SOURCE_V060_VERSION=0.6.0", rendered_bootstrap)
        self.assertIn(f"ADMITTED_SOURCE_V060_REVISION={SOURCE_REVISION}", rendered_bootstrap)
        self.assertIn(f"ADMITTED_SOURCE_V060_RELEASE={SOURCE_RELEASE}", rendered_bootstrap)
        self.assertNotIn("ADMITTED_SOURCE_V043_", rendered_bootstrap)
        self.assertNotIn('[ "$TARGET_VERSION" = 0.5.0 ]', rendered_bootstrap)
        self.assertIn('[ "$VERSION" = 0.7.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED', rendered_installer)
        self.assertNotIn('[ "$VERSION" = 0.6.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED', rendered_installer)

        renderer_source = renderer.read_text(encoding="utf-8")
        self.assertIn(f'SOURCE_ARTIFACT_SHA256 = "{SOURCE_ARTIFACT_SHA256}"', renderer_source)

    def test_renderer_fails_closed_if_bootstrap_shape_changes(self) -> None:
        renderer = ROOT / "deploy/scripts/render-release-policy.py"
        installer_source = ROOT / "deploy/scripts/install-node.sh"
        with tempfile.TemporaryDirectory() as temporary:
            bootstrap = Path(temporary) / "bootstrap-hm-dm.sh"
            installer = Path(temporary) / "install-node.sh"
            bootstrap.write_text("#!/bin/sh\necho changed\n", encoding="utf-8")
            shutil.copyfile(installer_source, installer)
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

    def test_renderer_fails_closed_if_installer_shape_changes(self) -> None:
        renderer = ROOT / "deploy/scripts/render-release-policy.py"
        bootstrap_source = ROOT / "deploy/scripts/bootstrap-hm-dm.sh"
        with tempfile.TemporaryDirectory() as temporary:
            bootstrap = Path(temporary) / "bootstrap-hm-dm.sh"
            installer = Path(temporary) / "install-node.sh"
            shutil.copyfile(bootstrap_source, bootstrap)
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
        self.assertIn("release_policy_installer_shape_rejected", result.stderr)


if __name__ == "__main__":
    unittest.main()
