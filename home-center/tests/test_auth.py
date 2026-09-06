from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center.auth import LoginRateLimiter, SessionManager  # noqa: E402


class AuthTests(unittest.TestCase):
    def test_signed_local_admin_session(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            key = root / "session.key"
            key.write_bytes(b"k" * 32)
            os.chmod(key, 0o600)
            sessions = SessionManager(key, lifetime_seconds=60)
            signed, _ = sessions.new_session("local-admin:admin")
            self.assertEqual(sessions.actor_from_headers(f"hc_session={signed}"), "local-admin:admin")
            broken = signed[:-1] + ("a" if signed[-1] != "a" else "b")
            self.assertIsNone(sessions.actor_from_headers(f"hc_session={broken}"))

    def test_bootstrap_actor_cannot_create_session(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            key = Path(raw) / "session.key"
            key.write_bytes(b"k" * 32)
            os.chmod(key, 0o600)
            sessions = SessionManager(key, lifetime_seconds=60)
            with self.assertRaisesRegex(ValueError, "unsupported session actor"):
                sessions.new_session("bootstrap-admin")

    def test_rate_limiter_fails_closed(self) -> None:
        limiter = LoginRateLimiter(attempts=2, window_seconds=60)
        self.assertTrue(limiter.allow("192.0.2.1"))
        self.assertTrue(limiter.allow("192.0.2.1"))
        self.assertFalse(limiter.allow("192.0.2.1"))
        limiter.clear("192.0.2.1")
        self.assertTrue(limiter.allow("192.0.2.1"))


if __name__ == "__main__":
    unittest.main()
