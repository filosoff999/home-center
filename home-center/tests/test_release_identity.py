from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center import __version__  # noqa: E402
from home_center.release_identity import ReleaseIdentityError, current_release_identity  # noqa: E402


class ReleaseIdentityTests(unittest.TestCase):
    def test_source_tree_identity_is_explicit_not_invented(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            value = current_release_identity(Path(temporary))
        self.assertEqual(value["schema"], "home-center.release-identity.v1")
        self.assertEqual(value["version"], __version__)
        self.assertIsNone(value["revision"])
        self.assertEqual(value["build"], "source")
        self.assertEqual(value["source"], "source-tree")

    def test_immutable_artifact_identity_uses_exact_revision(self) -> None:
        revision = "1" * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "VERSION").write_text(__version__ + "\n", encoding="ascii")
            (root / "REVISION").write_text(revision + "\n", encoding="ascii")
            os.chmod(root / "VERSION", 0o644)
            os.chmod(root / "REVISION", 0o644)
            value = current_release_identity(root)
        self.assertEqual(value["version"], __version__)
        self.assertEqual(value["revision"], revision)
        self.assertEqual(value["build"], revision[:12])
        self.assertEqual(value["source"], "immutable-artifact")

    def test_incomplete_identity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "VERSION").write_text(__version__ + "\n", encoding="ascii")
            with self.assertRaisesRegex(ReleaseIdentityError, "release_identity_incomplete"):
                current_release_identity(root)

    def test_runtime_version_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "VERSION").write_text("9.9.9\n", encoding="ascii")
            (root / "REVISION").write_text("2" * 40 + "\n", encoding="ascii")
            with self.assertRaisesRegex(ReleaseIdentityError, "release_version_runtime_mismatch"):
                current_release_identity(root)

    def test_identity_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "real-version"
            real.write_text(__version__ + "\n", encoding="ascii")
            (root / "VERSION").symlink_to(real)
            (root / "REVISION").write_text("3" * 40 + "\n", encoding="ascii")
            with self.assertRaisesRegex(ReleaseIdentityError, "release_version_rejected"):
                current_release_identity(root)

    def test_web_ui_loads_release_identity_and_mobile_nodes_stay_vertical(self) -> None:
        index = (ROOT / "product/web/static/index.html").read_text(encoding="utf-8")
        release_js = (ROOT / "product/web/static/release.js").read_text(encoding="utf-8")
        css = (ROOT / "product/web/static/app.css").read_text(encoding="utf-8")
        builder = (ROOT / "deploy/scripts/build-artifact.sh").read_text(encoding="utf-8")

        self.assertIn('<script src="/static/release.js" defer></script>', index)
        self.assertIn("homeCenterVersion", release_js)
        self.assertIn('revision: "$REVISION"', builder)
        self.assertIn("window.HOME_CENTER_RELEASE.version", builder)
        self.assertIn(".metric-grid, .node-card-grid { grid-template-columns: 1fr; }", css)


if __name__ == "__main__":
    unittest.main()
