from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center.auth import LoginRateLimiter, SessionManager  # noqa: E402


class AuthTests(unittest.TestCase):
    def test_token_and_signed_session(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            token = root / "admin.token"; key = root / "session.key"
            token.write_text("x" * 64, encoding="utf-8"); key.write_bytes(b"k" * 32)
            os.chmod(token, 0o600); os.chmod(key, 0o600)
            sessions = SessionManager(token, key, lifetime_seconds=60)
            self.assertTrue(sessions.verify_admin_token("x" * 64))
            self.assertFalse(sessions.verify_admin_token("y" * 64))
            signed, _ = sessions.new_session()
            self.assertEqual(sessions.actor_from_headers(None, f"hc_session={signed}"), "bootstrap-admin")
            broken = signed[:-1] + ("a" if signed[-1] != "a" else "b")
            self.assertIsNone(sessions.actor_from_headers(None, f"hc_session={broken}"))

    def test_rate_limiter_fails_closed(self) -> None:
        limiter = LoginRateLimiter(attempts=2, window_seconds=60)
        self.assertTrue(limiter.allow("192.0.2.1"))
        self.assertTrue(limiter.allow("192.0.2.1"))
        self.assertFalse(limiter.allow("192.0.2.1"))
        limiter.clear("192.0.2.1")
        self.assertTrue(limiter.allow("192.0.2.1"))


if __name__ == "__main__":
    unittest.main()
