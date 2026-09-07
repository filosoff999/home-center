from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HCWeb001RegressionTests(unittest.TestCase):
    def test_hidden_login_overlay_has_release_blocking_css_guard(self) -> None:
        css = (ROOT / "product/web/static/hc-web-001.css").read_text(encoding="utf-8")
        self.assertRegex(
            css,
            re.compile(
                r"\.login-layer\[hidden\]\s*\{[^}]*display\s*:\s*none\s*!important\s*;[^}]*\}",
                re.DOTALL,
            ),
        )

    def test_release_artifact_appends_guard_after_web_static_copy(self) -> None:
        build = (ROOT / "deploy/scripts/build-artifact.sh").read_text(encoding="utf-8")
        copy_marker = 'cp -a "$ROOT/product/web/static/." "$STAGE/web/"'
        guard_marker = 'cat "$ROOT/product/web/static/hc-web-001.css" >>"$STAGE/web/app.css"'
        self.assertIn(copy_marker, build)
        self.assertIn(guard_marker, build)
        self.assertGreater(build.index(guard_marker), build.index(copy_marker))

    def test_login_flow_uses_hidden_attribute_consistently(self) -> None:
        javascript = (ROOT / "product/web/static/app.js").read_text(encoding="utf-8")
        self.assertIn('$("#loginLayer").hidden = false;', javascript)
        self.assertIn('$("#loginLayer").hidden = true;', javascript)
        self.assertRegex(javascript, re.compile(r"hideLogin\(\);\s*await refresh\(\);", re.DOTALL))
        self.assertRegex(javascript, re.compile(r"async function logout\(\).*?showLogin\(\);", re.DOTALL))

    def test_module_permission_review_is_read_only_and_mobile_safe(self) -> None:
        html = (ROOT / "product/web/static/index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "product/web/static/app.js").read_text(encoding="utf-8")
        css = (ROOT / "product/web/static/app.css").read_text(encoding="utf-8")
        self.assertIn('data-view="modules"', html)
        self.assertIn('data-panel="modules"', html)
        self.assertIn('id="modulePermissionReview"', html)
        self.assertIn('api("/api/v1/modules/permission-review")', javascript)
        self.assertIn("function renderModulePermissionReview()", javascript)
        self.assertIn("review.permission_grants_applied === false", javascript)
        self.assertIn("review.production_activation_enabled === false", javascript)
        self.assertNotIn("innerHTML", javascript)
        self.assertNotIn("permission-review/approve", javascript)
        self.assertNotIn("permission-review/grant", javascript)
        self.assertIn(".node-row { grid-template-columns: 42px 1fr auto; }", css)
        self.assertIn("overflow-x: auto", css)


if __name__ == "__main__":
    unittest.main()
