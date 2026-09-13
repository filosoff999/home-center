from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts/export_public_stable_provenance.py"
SCHEMA_PATH = ROOT / "contracts/releases/public-stable-provenance.v2.schema.json"
LEGACY_MAPPING = ROOT / "APPROVED-SOURCE.json"
SPEC = importlib.util.spec_from_file_location("home_center_public_stable_provenance", TOOL)
assert SPEC is not None and SPEC.loader is not None
provenance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = provenance
SPEC.loader.exec_module(provenance)
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _fixture() -> dict[str, object]:
    files = [
        {
            "path": ".github/workflows/private-ci.yml",
            "approved_sha256": "1" * 64,
            "disposition": "excluded",
            "stable_sha256": None,
            "reason": "stable-release-boundary",
        },
        {
            "path": "README.md",
            "approved_sha256": "2" * 64,
            "disposition": "adapted",
            "stable_sha256": "3" * 64,
            "reason": "hardened-stable-integration",
        },
        {
            "path": "VERSION",
            "approved_sha256": "4" * 64,
            "disposition": "identical",
            "stable_sha256": "4" * 64,
            "reason": "approved-byte-copy",
        },
    ]
    approved_manifest = "".join(
        f'{item["approved_sha256"]}  {item["path"]}\n' for item in files
    ).encode("utf-8")
    return {
        "schema": "home-center.approved-source-provenance.v1",
        "product": "home-center",
        "version": "7.8.9",
        "approved_repository": "example.internal/home-center-source",
        "approved_revision": "a" * 40,
        "approved_manifest_sha256": hashlib.sha256(approved_manifest).hexdigest(),
        "stable_release_boundary_revision": "b" * 40,
        "summary": {"total": 3, "identical": 1, "adapted": 1, "excluded": 1},
        "files": files,
    }


class PublicStableProvenanceTests(unittest.TestCase):
    def test_export_is_deterministic_opaque_and_structurally_valid(self) -> None:
        source = _fixture()
        first = provenance.export_public_provenance(copy.deepcopy(source))
        second = provenance.export_public_provenance(copy.deepcopy(source))
        self.assertEqual(first, second)
        self.assertEqual(
            set(first),
            {
                "schema",
                "product",
                "version",
                "source_identity_sha256",
                "release_boundary_identity_sha256",
                "approved_manifest_sha256",
                "summary",
                "files",
            },
        )
        self.assertEqual(first["schema"], "home-center.public-stable-provenance.v2")
        self.assertEqual(first["product"], "home-center")
        self.assertEqual(first["version"], source["version"])
        self.assertRegex(first["source_identity_sha256"], HEX64)
        self.assertRegex(first["release_boundary_identity_sha256"], HEX64)
        self.assertEqual(first["approved_manifest_sha256"], source["approved_manifest_sha256"])
        self.assertEqual(first["summary"], source["summary"])
        self.assertEqual(first["files"], source["files"])

        payload = provenance.canonical_json(first)
        for forbidden in (
            source["approved_repository"],
            source["approved_revision"],
            source["stable_release_boundary_revision"],
            "approved_repository",
            "approved_revision",
            "stable_release_boundary_revision",
        ):
            self.assertNotIn(str(forbidden).encode("utf-8"), payload)

    def test_current_legacy_mapping_can_be_exported_without_identity_leak(self) -> None:
        if not LEGACY_MAPPING.is_file():
            self.skipTest("legacy private mapping already removed by a future promotion")
        source = json.loads(LEGACY_MAPPING.read_text(encoding="utf-8"))
        public = provenance.export_public_provenance(copy.deepcopy(source))
        payload = provenance.canonical_json(public)
        self.assertEqual(public["version"], source["version"])
        self.assertEqual(public["summary"], source["summary"])
        self.assertEqual(public["approved_manifest_sha256"], source["approved_manifest_sha256"])
        for forbidden in (
            source["approved_repository"],
            source["approved_revision"],
            source["stable_release_boundary_revision"],
        ):
            self.assertNotIn(str(forbidden).encode("utf-8"), payload)

    def test_private_identity_changes_only_the_expected_opaque_digest(self) -> None:
        source = _fixture()
        original = provenance.export_public_provenance(copy.deepcopy(source))
        changed = copy.deepcopy(source)
        changed["approved_revision"] = "c" * 40
        updated = provenance.export_public_provenance(changed)
        self.assertNotEqual(original["source_identity_sha256"], updated["source_identity_sha256"])
        self.assertEqual(
            original["release_boundary_identity_sha256"],
            updated["release_boundary_identity_sha256"],
        )

    def test_export_rejects_tampering_unknown_fields_and_traversal(self) -> None:
        source = _fixture()
        source["approved_manifest_sha256"] = "f" * 64
        with self.assertRaisesRegex(provenance.PublicStableProvenanceError, "input_manifest_binding"):
            provenance.export_public_provenance(source)

        source = _fixture()
        source["approved_repository_url"] = "https://example.invalid"
        with self.assertRaisesRegex(provenance.PublicStableProvenanceError, "input_shape"):
            provenance.export_public_provenance(source)

        source = _fixture()
        source["files"][0]["path"] = "../private"
        with self.assertRaisesRegex(provenance.PublicStableProvenanceError, "file_path"):
            provenance.export_public_provenance(source)

    def test_public_schema_is_public_safe_and_matches_export_shape(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema"]["const"], "home-center.public-stable-provenance.v2")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            set(schema["required"]),
            {
                "schema",
                "product",
                "version",
                "source_identity_sha256",
                "release_boundary_identity_sha256",
                "approved_manifest_sha256",
                "summary",
                "files",
            },
        )
        payload = SCHEMA_PATH.read_bytes()
        for forbidden in (
            b"approved_repository",
            b"approved_revision",
            b"stable_release_boundary_revision",
            b"home-center-development",
        ):
            self.assertNotIn(forbidden, payload)


if __name__ == "__main__":
    unittest.main()
