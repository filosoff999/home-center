from __future__ import annotations

import gzip
import importlib.util
import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "deploy/scripts/build-release.py"
if not TOOL.is_file():
    TOOL = ROOT / "tools/public_release_0141.py"
SPEC = importlib.util.spec_from_file_location("home_center_public_release_0141", TOOL)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)

sys.path.insert(0, str(ROOT / "product/control-plane/src"))


class PublicRelease0141Tests(unittest.TestCase):
    def test_runtime_version_is_single_literal_assignment(self) -> None:
        self.assertEqual(release.strict_source_version(ROOT), "0.14.1")

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

        if not hasattr(product_boundary, "_HOME_CENTER_CAPABILITIES"):
            self.skipTest("positive product taxonomy is introduced by the 0.14.1 integration")
        allowed = product_boundary.evaluate_product_scope(module_id="inventory", capabilities=("inventory.v1",))
        denied = product_boundary.evaluate_product_scope(module_id="unknown-category")
        unknown_capability = product_boundary.evaluate_product_scope(
            module_id="inventory", capabilities=("inventory.future.v99",)
        )
        self.assertTrue(allowed.allowed)
        self.assertFalse(denied.allowed)
        self.assertFalse(unknown_capability.allowed)


if __name__ == "__main__":
    unittest.main()
