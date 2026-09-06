from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "product/web/static"


class HCWeb070SourceRegressionTests(unittest.TestCase):
    def test_legacy_token_wire_contract_is_unchanged(self) -> None:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        javascript = (STATIC / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="tokenInput"', html)
        self.assertIn('name="token"', html)
        self.assertNotIn('id="usernameInput"', html)
        self.assertNotIn('id="passwordInput"', html)
        self.assertIn("body: JSON.stringify({ token })", javascript)
        self.assertNotRegex(javascript, re.compile(r"JSON\.stringify\(\{\s*username"))

    def test_login_errors_are_fixed_bounded_messages(self) -> None:
        javascript = (STATIC / "app.js").read_text(encoding="utf-8")
        authenticate = javascript.split("async function authenticate(token)", 1)[1].split(
            "async function login(event)", 1
        )[0]
        login = javascript.split("async function login(event)", 1)[1].split("async function logout()", 1)[0]

        self.assertIn('invalid_credentials: "Неверный токен администратора."', javascript)
        self.assertIn('response.status === 401', authenticate)
        self.assertIn('loginFailure("invalid_credentials")', authenticate)
        self.assertIn('response.status === 429', authenticate)
        self.assertIn('loginFailure("rate_limited")', authenticate)
        self.assertNotIn("response.json", authenticate)
        self.assertNotIn("error.message", login)
        self.assertIn('await api("/api/v1/session")', login)
        self.assertIn('showLoginError(error.kind || "authentication_unavailable")', login)
        self.assertIn('$("#tokenInput").value = "";', login)

    def test_single_and_multi_element_selectors_remain_distinct(self) -> None:
        javascript = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertEqual(javascript.count("const $ = (selector) => document.querySelector(selector);"), 1)
        self.assertEqual(
            javascript.count("const $$ = (selector) => [...document.querySelectorAll(selector)];"),
            1,
        )

    def test_mobile_logout_is_visible_and_uses_fail_closed_logout(self) -> None:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        css = (STATIC / "app.css").read_text(encoding="utf-8")
        javascript = (STATIC / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="mobileLogoutButton"', html)
        self.assertRegex(css, r"\.mobile-logout-button\s*\{\s*display:\s*none;")
        mobile = re.search(r"@media\s*\(max-width:\s*720px\)\s*\{(?P<body>.*)\}\s*$", css, re.DOTALL)
        self.assertIsNotNone(mobile)
        self.assertRegex(mobile.group("body"), r"\.mobile-logout-button\s*\{\s*display:\s*inline-flex;")
        self.assertIn('$("#mobileLogoutButton").addEventListener("click", logout)', javascript)
        logout = javascript.split("async function logout()", 1)[1].split("function switchView", 1)[0]
        self.assertIn("finally { showLogin(); }", logout)

    def test_mobile_nodes_remain_one_per_row(self) -> None:
        css = (STATIC / "app.css").read_text(encoding="utf-8")
        mobile = re.search(r"@media\s*\(max-width:\s*720px\)\s*\{(?P<body>.*)\}\s*$", css, re.DOTALL)
        self.assertIsNotNone(mobile)
        self.assertRegex(
            mobile.group("body"),
            r"\.metric-grid,\s*\.node-card-grid\s*\{\s*grid-template-columns:\s*1fr;",
        )

    def test_ci_makes_browser_acceptance_release_blocking(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

        self.assertIn('- "maintenance/**"', workflow)
        self.assertIn("browser-acceptance:", workflow)
        self.assertIn("runs-on: ubuntu-24.04", workflow)
        self.assertIn("tests/browser_hc_web_070.py", workflow)
        self.assertIn("artifact_sha256: ${{ steps.browser-artifact.outputs.sha256 }}", workflow)
        artifact = workflow.split("  artifact:", 1)[1]
        self.assertRegex(artifact, r"needs:\s*\[deterministic-gates, browser-acceptance\]")
        self.assertIn("BROWSER_ACCEPTED_SHA256", artifact)
        self.assertIn('[ "$BUILT_SHA256" = "$BROWSER_ACCEPTED_SHA256" ]', artifact)

    def test_browser_gate_has_no_third_party_runtime_dependency(self) -> None:
        harness = (ROOT / "tests/browser_hc_web_070.py").read_text(encoding="utf-8").lower()
        for forbidden in ("selenium", "playwright", "requests", "pip install", "npm install"):
            self.assertNotIn(forbidden, harness)
        self.assertIn("/execute/sync", harness)
        self.assertIn("mobileemulation", harness)
        self.assertIn("ssl.sslcontext", harness)

    def test_deterministic_token_login_and_both_logout_controls(self) -> None:
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for deterministic source acceptance")
        completed = subprocess.run(
            [node, str(ROOT / "tests/hc_web_070_source_harness.js"), str(STATIC / "app.js")],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("HC_WEB_070_TOKEN_SOURCE_ACCEPTANCE=PASS", completed.stdout)


if __name__ == "__main__":
    unittest.main()
