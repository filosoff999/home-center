from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "deploy/scripts/build-release.py"
SPEC = importlib.util.spec_from_file_location("home_center_public_release", TOOL)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)

sys.path.insert(0, str(ROOT / "product/control-plane/src"))


def _write_identity(root: Path, *, version: str, runtime_version: str, project_version: str) -> None:
    package = root / "product/control-plane/src/home_center"
    package.mkdir(parents=True)
    (root / "VERSION").write_text(version + "\n", encoding="ascii")
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "home-center"\nversion = "{project_version}"\n',
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        f'__version__ = "{runtime_version}"\n',
        encoding="utf-8",
    )


def _write_runtime_artifact(root: Path, *, version: str, runtime_version: str) -> None:
    package = root / "home_center"
    package.mkdir(parents=True)
    (root / "VERSION").write_text(version + "\n", encoding="ascii")
    (root / "REVISION").write_text("a" * 40 + "\n", encoding="ascii")
    (package / "__init__.py").write_text(
        f'__version__ = "{runtime_version}"\n',
        encoding="utf-8",
    )
    rows = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root).as_posix()
        rows.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {relative}")
    (root / "MANIFEST.sha256").write_text("\n".join(rows) + "\n", encoding="ascii")


class PublicReleaseTests(unittest.TestCase):
    def test_version_identity_and_release_names_come_from_version_file(self) -> None:
        expected = (ROOT / "VERSION").read_text(encoding="ascii").removesuffix("\n")
        self.assertEqual(release.strict_release_version(ROOT), expected)
        self.assertEqual(release.strict_source_version(ROOT), expected)
        self.assertEqual(release.VERSION, expected)
        self.assertEqual(release.TAG, f"v{expected}")
        self.assertEqual(release.RUNTIME_ARCHIVE, f"home-center-{expected}-linux-amd64.tar.gz")
        self.assertEqual(release.SOURCE_ARCHIVE, f"home-center-{expected}-source.tar.gz")
        self.assertEqual(release.SBOM_NAME, f"home-center-{expected}.spdx.json")
        self.assertEqual(release.ACCEPTANCE_NAME, f"home-center-{expected}.acceptance.json")
        self.assertEqual(
            release.RELEASE_MANIFEST_NAME,
            f"home-center-{expected}.release-manifest.json",
        )
        tool_source = TOOL.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r'(?<![0-9])(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?![0-9])', tool_source))

    def test_different_version_derives_different_release_identity(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            tool = root / "deploy/scripts/build-release.py"
            tool.parent.mkdir(parents=True)
            shutil.copyfile(TOOL, tool)
            (root / "VERSION").write_text("7.8.9\n", encoding="ascii")
            spec = importlib.util.spec_from_file_location("home_center_public_release_789", tool)
            assert spec is not None and spec.loader is not None
            derived = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = derived
            spec.loader.exec_module(derived)
            self.assertEqual(derived.VERSION, "7.8.9")
            self.assertEqual(derived.TAG, "v7.8.9")
            self.assertEqual(derived.RUNTIME_ARCHIVE, "home-center-7.8.9-linux-amd64.tar.gz")
            self.assertEqual(derived.SOURCE_ARCHIVE, "home-center-7.8.9-source.tar.gz")

    def test_runtime_artifact_verifier_accepts_matching_generic_version(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            _write_runtime_artifact(root, version="7.8.9", runtime_version="7.8.9")
            result = subprocess.run(
                ["bash", str(ROOT / "deploy/scripts/verify-artifact.sh"), str(root)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("HOME_CENTER_ARTIFACT=PASS version=7.8.9", result.stdout)

    def test_runtime_artifact_verifier_rejects_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            _write_runtime_artifact(root, version="7.8.9", runtime_version="7.8.8")
            result = subprocess.run(
                ["bash", str(ROOT / "deploy/scripts/verify-artifact.sh"), str(root)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ARTIFACT_RUNTIME_VERSION_MISMATCH", result.stderr)

    def test_release_identity_rejects_runtime_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            _write_identity(root, version="7.8.9", runtime_version="7.8.8", project_version="7.8.9")
            with self.assertRaisesRegex(release.PublicReleaseError, "runtime_version_mismatch"):
                release.strict_release_version(root)

    def test_release_identity_rejects_project_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            _write_identity(root, version="7.8.9", runtime_version="7.8.9", project_version="7.8.8")
            with self.assertRaisesRegex(release.PublicReleaseError, "project_version_mismatch"):
                release.strict_release_version(root)

    def test_release_identity_rejects_noncanonical_semver(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            _write_identity(root, version="07.8.9", runtime_version="07.8.9", project_version="07.8.9")
            with self.assertRaisesRegex(release.PublicReleaseError, "release_version_invalid"):
                release.strict_release_version(root)

    def test_release_contracts_are_version_neutral(self) -> None:
        expected = {
            "approved-source-provenance.v1.schema.json": "home-center.approved-source-provenance.v1",
            "public-release-acceptance.v1.schema.json": "home-center.public-release-acceptance.v1",
            "public-release-manifest.v1.schema.json": "home-center.public-release-manifest.v1",
        }
        release_contracts = ROOT / "contracts/releases"
        self.assertEqual({path.name for path in release_contracts.iterdir()}, set(expected))
        current = release.VERSION
        for name, schema_id in expected.items():
            path = release_contracts / name
            payload = path.read_text(encoding="utf-8")
            schema = json.loads(payload)
            self.assertNotIn(current, payload)
            self.assertEqual(schema["properties"]["schema"]["const"], schema_id)
            self.assertIn("pattern", schema["properties"]["version"])

    def test_runtime_contains_source_provenance_and_portable_profile(self) -> None:
        self.assertIn(
            ("APPROVED-SOURCE.json", "APPROVED-SOURCE.json"),
            release.RUNTIME_FILE_MAPPINGS,
        )
        self.assertIn(("deploy/examples", "deploy/examples"), release.RUNTIME_TREE_MAPPINGS)

    def test_nested_bytecode_cache_is_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            (root / "kept").mkdir()
            (root / "kept/value.txt").write_text("kept\n", encoding="utf-8")
            (root / "kept/__pycache__").mkdir()
            (root / "kept/__pycache__/value.pyc").write_bytes(b"bytecode")
            self.assertEqual([name for name, _ in release.iter_source_files(root)], ["kept/value.txt"])

    def test_archive_traversal_is_rejected_without_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            archive_path = root / "traversal.tar.gz"
            with archive_path.open("wb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                    with tarfile.open(fileobj=compressed, mode="w") as archive:
                        payload = b"blocked"
                        member = tarfile.TarInfo("../escape")
                        member.size = len(payload)
                        member.mode = 0o644
                        member.uid = member.gid = 0
                        member.mtime = 1_800_000_000
                        archive.addfile(member, io.BytesIO(payload))
            with self.assertRaisesRegex(release.PublicReleaseError, "archive_member_path_rejected"):
                release._archive_files(archive_path)
            self.assertFalse((root / "escape").exists())

    def test_archive_cumulative_expansion_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            archive_path = Path(value) / "large.tar.gz"
            with archive_path.open("wb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                    with tarfile.open(fileobj=compressed, mode="w") as archive:
                        for name in ("a", "b"):
                            member = tarfile.TarInfo(name)
                            member.size = 2
                            member.mode = 0o644
                            member.uid = member.gid = 0
                            member.mtime = 1_800_000_000
                            archive.addfile(member, io.BytesIO(b"ok"))
            with mock.patch.object(release, "MAX_ARCHIVE_EXPANDED_BYTES", 3):
                with self.assertRaisesRegex(release.PublicReleaseError, "archive_expanded_size_rejected"):
                    release._archive_files(archive_path)

    def test_generic_action_target_is_bounded(self) -> None:
        from home_center.actions import ActionRegistry, ActionRequestError

        definition = {"input": {"properties": {"service": {"enum": ["home-center.service"]}}}}
        request = {
            "schema": "home-center.action-request.v1",
            "idempotency_key": "request-node-a",
            "target_node_id": "node-a",
            "reason": "inspect local service",
            "input": {"service": "home-center.service"},
        }
        self.assertEqual(ActionRegistry._validate_request(definition, request)["target_node_id"], "node-a")
        request["target_node_id"] = "../node-a"
        with self.assertRaises(ActionRequestError):
            ActionRegistry._validate_request(definition, request)

    def test_product_scope_is_default_deny(self) -> None:
        from home_center import product_boundary

        allowed = product_boundary.evaluate_product_scope(module_id="inventory", capabilities=("inventory.v1",))
        denied = product_boundary.evaluate_product_scope(module_id="unknown-category")
        unknown_capability = product_boundary.evaluate_product_scope(
            module_id="inventory",
            capabilities=("inventory.future.v99",),
        )
        self.assertTrue(allowed.allowed)
        self.assertFalse(denied.allowed)
        self.assertFalse(unknown_capability.allowed)


if __name__ == "__main__":
    unittest.main()
