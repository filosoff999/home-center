from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/promote-qualified-stable-v10.yml"


class StablePromotionV10ContractTests(unittest.TestCase):
    def test_promotion_is_exact_main_bound_and_never_cancels_existing_work(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("PROMOTION_NOT_CREATED_FROM_MAIN", text)
        self.assertIn("git ls-remote --exit-code origin refs/heads/main", text)
        self.assertIn("startsWith(github.ref_name, 'promote10/')", text)

    def test_source_release_and_runtime_assets_are_fail_closed(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("SOURCE_RELEASE_DRAFT", text)
        self.assertIn("SOURCE_RELEASE_PRERELEASE", text)
        self.assertIn('runtime_name="home-center-${version}-linux-amd64.tar.gz"', text)
        self.assertIn('sidecar_name="${runtime_name}.sha256"', text)
        self.assertIn("SOURCE_RUNTIME_ASSET_MISSING", text)
        self.assertIn("SOURCE_RUNTIME_SIDECAR_MISSING", text)

    def test_approved_source_ancestry_and_exact_version_are_required(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("SOURCE_VERSION_MISMATCH", text)
        self.assertIn("SOURCE_NOT_DESCENDANT_OF_APPROVED_BASE", text)
        self.assertIn('refs/tags/v$version:refs/tags/source-v$version', text)

    def test_generated_candidate_drops_promotion_tooling_and_requalifies_public_surface(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("'promote-qualified-stable-v10.yml'", text)
        self.assertIn("python3 deploy/scripts/build-release.py verify-source --source-root .", text)
        self.assertIn("python3 tests/security_gate_public_release.py", text)
        self.assertIn("python3 -m unittest discover -v -s tests -p 'test_*.py'", text)
        self.assertIn("HOME_CENTER_PUBLIC_EXPORT=PASS", text)


if __name__ == "__main__":
    unittest.main()
