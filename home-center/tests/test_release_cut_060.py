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


TARGET = "0.6.0"
SOURCE = "0.5.0"
SOURCE_REVISION = "1d1ff0be759667c40361bbd04b9da273a778b9c8"
SOURCE_RELEASE = "/opt/home-center/releases/0.5.0-1d1ff0be7596-3898daba8711"


class ReleaseCut060Tests(unittest.TestCase):
    def test_runtime_package_and_web_source_versions_are_exact(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)
        release_js = (ROOT / "product/web/static/release.js").read_text(encoding="utf-8")

        self.assertEqual(__version__, TARGET)
        self.assertEqual(project["project"]["version"], TARGET)
        self.assertIn('version: "0.6.0"', release_js)

    def test_artifact_builder_admits_only_060_and_renders_bootstrap(self) -> None:
        builder = (ROOT / "deploy/scripts/build-artifact.sh").read_text(encoding="utf-8")
        self.assertIn('[ "$VERSION" = 0.6.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED', builder)
        self.assertIn('render-bootstrap-policy.py" "$STAGE/deploy/bootstrap-hm-dm.sh"', builder)
        self.assertNotIn('[ "$VERSION" = 0.4.3 ] || { echo RELEASE_VERSION_NOT_ADMITTED', builder)

    def test_node_installer_admits_only_060_target(self) -> None:
        installer = (ROOT / "deploy/scripts/install-node.sh").read_text(encoding="utf-8")
        self.assertIn('[ "$VERSION" = 0.6.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED', installer)
        self.assertNotIn('[ "$VERSION" = 0.5.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED', installer)

    def test_rendered_cluster_bootstrap_accepts_exact_050_predecessor_only(self) -> None:
        renderer = ROOT / "deploy/scripts/render-bootstrap-policy.py"
        source = ROOT / "deploy/scripts/bootstrap-hm-dm.sh"
        with tempfile.TemporaryDirectory() as temporary:
            rendered_path = Path(temporary) / "bootstrap-hm-dm.sh"
            shutil.copyfile(source, rendered_path)
            result = subprocess.run(
                [sys.executable, "-I", str(renderer), str(rendered_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rendered = rendered_path.read_text(encoding="utf-8")

        self.assertIn('[ "$TARGET_VERSION" = 0.6.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED', rendered)
        self.assertIn("ADMITTED_SOURCE_V050_VERSION=0.5.0", rendered)
        self.assertIn(f"ADMITTED_SOURCE_V050_REVISION={SOURCE_REVISION}", rendered)
        self.assertIn(f"ADMITTED_SOURCE_V050_RELEASE={SOURCE_RELEASE}", rendered)
        self.assertIn('source_identity_admitted "$LOCAL_SOURCE_VERSION" "$LOCAL_SOURCE_REVISION" "$LOCAL_SOURCE_RELEASE"', rendered)
        self.assertNotIn("ADMITTED_SOURCE_V043_", rendered)
        self.assertNotIn('[ "$TARGET_VERSION" = 0.5.0 ]', rendered)

    def test_renderer_fails_closed_if_reviewed_bootstrap_shape_changes(self) -> None:
        renderer = ROOT / "deploy/scripts/render-bootstrap-policy.py"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bootstrap-hm-dm.sh"
            path.write_text("#!/bin/sh\necho changed\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-I", str(renderer), str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=10,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bootstrap_policy_source_shape_rejected", result.stderr)


if __name__ == "__main__":
    unittest.main()
