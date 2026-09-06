from __future__ import annotations

import ssl
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from home_center.server import (
    PEER_MINIMUM_TLS_VERSION,
    WEB_CERTIFICATE,
    WEB_MINIMUM_TLS_VERSION,
    WEB_PRIVATE_KEY,
    _peer_context,
    _web_context,
)


class _FakeContext:
    def __init__(self) -> None:
        self.minimum_version: ssl.TLSVersion | None = None
        self.verify_mode: ssl.VerifyMode = ssl.CERT_NONE
        self.loaded_ca: str | None = None

    def load_verify_locations(self, *, cafile: str) -> None:
        self.loaded_ca = cafile


class TLSPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.peer_certificate = Path("/cluster/peer.crt")
        self.peer_private_key = Path("/cluster/peer.key")
        self.runtime = SimpleNamespace(
            config=SimpleNamespace(
                cluster_ca=Path("/cluster/ca.crt"),
                tls_certificate=self.peer_certificate,
                tls_private_key=self.peer_private_key,
            )
        )

    def test_web_listener_accepts_tls12_and_newer_with_peer_fallback(self) -> None:
        fake = _FakeContext()
        with (
            patch("home_center.server._separate_web_identity_present", return_value=False),
            patch("home_center.server._server_context", return_value=fake) as server_context,
        ):
            context = _web_context(self.runtime)
        self.assertIs(context, fake)
        self.assertEqual(WEB_MINIMUM_TLS_VERSION, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        server_context.assert_called_once_with(self.peer_certificate, self.peer_private_key)

    def test_web_listener_prefers_separate_web_identity(self) -> None:
        fake = _FakeContext()
        with (
            patch("home_center.server._separate_web_identity_present", return_value=True),
            patch("home_center.server._server_context", return_value=fake) as server_context,
        ):
            context = _web_context(self.runtime)
        self.assertIs(context, fake)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        server_context.assert_called_once_with(WEB_CERTIFICATE, WEB_PRIVATE_KEY)

    def test_corrupt_web_identity_never_silently_falls_back(self) -> None:
        with (
            patch("home_center.server._separate_web_identity_present", side_effect=RuntimeError("web_identity_incomplete")),
            patch("home_center.server._server_context") as server_context,
        ):
            with self.assertRaisesRegex(RuntimeError, "web_identity_incomplete"):
                _web_context(self.runtime)
        server_context.assert_not_called()

    def test_peer_listener_keeps_tls13_mtls_and_peer_identity(self) -> None:
        fake = _FakeContext()
        with patch("home_center.server._server_context", return_value=fake) as server_context:
            context = _peer_context(self.runtime)
        self.assertIs(context, fake)
        self.assertEqual(PEER_MINIMUM_TLS_VERSION, ssl.TLSVersion.TLSv1_3)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_3)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(context.loaded_ca, "/cluster/ca.crt")
        server_context.assert_called_once_with(self.peer_certificate, self.peer_private_key)

    def test_no_listener_policy_drops_below_tls12(self) -> None:
        self.assertGreaterEqual(WEB_MINIMUM_TLS_VERSION, ssl.TLSVersion.TLSv1_2)
        self.assertGreaterEqual(PEER_MINIMUM_TLS_VERSION, ssl.TLSVersion.TLSv1_2)


if __name__ == "__main__":
    unittest.main()
