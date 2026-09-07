from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import hc_dev_control as control  # noqa: E402


class DevelopmentControlTests(unittest.TestCase):
    def test_repository_registry_is_valid(self) -> None:
        registry = control.load_registry(ROOT / ".hc-dev" / "releases.json")
        self.assertEqual(registry["schema_version"], 1)
        self.assertEqual([item["version"] for item in registry["releases"]], ["0.11.0", "0.12.0", "0.13.0", "0.14.0"])

    def test_render_bar_is_bounded(self) -> None:
        self.assertEqual(control.render_bar(0), "[░░░░░░░░░░]")
        self.assertEqual(control.render_bar(50), "[█████░░░░░]")
        self.assertEqual(control.render_bar(100), "[██████████]")
        self.assertEqual(control.render_bar(150), "[██████████]")

    def test_pr_gate_accepts_matching_release_and_issue(self) -> None:
        registry = control.load_registry(ROOT / ".hc-dev" / "releases.json")
        event = {
            "pull_request": {
                "base": {"ref": "release/0.14.0"},
                "body": "Target release: `0.14.0`\n\nTracking issue: #120\n",
            }
        }
        control.validate_pr_event(registry, event)

    def test_pr_gate_rejects_release_mismatch(self) -> None:
        registry = control.load_registry(ROOT / ".hc-dev" / "releases.json")
        event = {
            "pull_request": {
                "base": {"ref": "release/0.14.0"},
                "body": "Target release: `0.13.0`\n\nCloses #120\n",
            }
        }
        with self.assertRaises(control.ControlError):
            control.validate_pr_event(registry, event)

    def test_pr_gate_rejects_missing_issue_reference(self) -> None:
        registry = control.load_registry(ROOT / ".hc-dev" / "releases.json")
        event = {
            "pull_request": {
                "base": {"ref": "release/0.12.0"},
                "body": "Target release: `0.12.0`\n",
            }
        }
        with self.assertRaises(control.ControlError):
            control.validate_pr_event(registry, event)

    def test_product_doc_boundary_terms_are_detected(self) -> None:
        text = "Home Center is developed through ChatGPT and an AI Development Orchestrator."
        found = control.forbidden_product_doc_terms(text)
        self.assertIn("ChatGPT", found)
        self.assertIn("AI Development Orchestrator", found)

    def test_registry_rejects_wrong_release_branch(self) -> None:
        registry = control.load_registry(ROOT / ".hc-dev" / "releases.json")
        modified = json.loads(json.dumps(registry))
        modified["releases"][0]["branch"] = "main"
        with self.assertRaises(control.ControlError):
            control.validate_registry(modified)

    def test_read_event_rejects_non_object(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "event.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(control.ControlError):
                control.read_event(str(path))


if __name__ == "__main__":
    unittest.main()
