from __future__ import annotations

import hashlib
import json
import re
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = ROOT / "APPROVED-SOURCE.json"
APPROVED_REVISION = "68326c9c213ab0ffede11decfff27e663e95e761"
STABLE_RELEASE_BOUNDARY_REVISION = "3e95b8b9a088e175224b218c1e4a0be86415ab84"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ApprovedSourceTests(unittest.TestCase):
    def test_mapping_is_complete_self_consistent_and_matches_stable_files(self) -> None:
        value = json.loads(PROVENANCE.read_text(encoding="utf-8"))
        version = (ROOT / "VERSION").read_text(encoding="ascii").removesuffix("\n")
        self.assertEqual(value["schema"], "home-center.approved-source-provenance.v1")
        self.assertEqual(value["product"], "home-center")
        self.assertEqual(value["version"], version)
        self.assertEqual(value["approved_repository"], "ControlCenterSoft/home-center-development")
        self.assertEqual(value["approved_revision"], APPROVED_REVISION)
        self.assertEqual(
            value["stable_release_boundary_revision"],
            STABLE_RELEASE_BOUNDARY_REVISION,
        )

        files = value["files"]
        paths = [item["path"] for item in files]
        self.assertEqual(paths, sorted(set(paths)))
        approved_manifest = "".join(
            f'{item["approved_sha256"]}  {item["path"]}\n' for item in files
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(approved_manifest).hexdigest(),
            value["approved_manifest_sha256"],
        )

        counts = Counter(item["disposition"] for item in files)
        self.assertEqual(
            value["summary"],
            {
                "total": len(files),
                "identical": counts["identical"],
                "adapted": counts["adapted"],
                "excluded": counts["excluded"],
            },
        )
        self.assertEqual(
            value["summary"]["identical"]
            + value["summary"]["adapted"]
            + value["summary"]["excluded"],
            len(files),
        )

        for item in files:
            with self.subTest(path=item["path"]):
                self.assertRegex(item["approved_sha256"], HEX64)
                path = ROOT / item["path"]
                if item["disposition"] == "excluded":
                    self.assertIsNone(item["stable_sha256"])
                    self.assertFalse(path.exists())
                    self.assertIn(
                        item["reason"],
                        {
                            "stable-public-surface",
                            "stable-release-boundary",
                            "stable-release-documentation",
                        },
                    )
                    continue
                self.assertTrue(path.is_file())
                self.assertFalse(path.is_symlink())
                self.assertEqual(item["stable_sha256"], _sha256(path))
                if item["disposition"] == "identical":
                    self.assertEqual(item["approved_sha256"], item["stable_sha256"])
                    self.assertEqual(item["reason"], "approved-byte-copy")
                else:
                    self.assertNotEqual(item["approved_sha256"], item["stable_sha256"])
                    self.assertEqual(item["reason"], "hardened-stable-integration")


if __name__ == "__main__":
    unittest.main()
