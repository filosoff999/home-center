from __future__ import annotations

import ssl
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from home_center.server import (
    PEER_MINIMUM_TLS_VERSION,
    WEB_MINIMUM_TLS_VERSION,
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
        self.runtime = SimpleNamespace(config=SimpleNamespace(cluster_ca=Path("/cluster/ca.crt")))

    def test_web_listener_accepts_tls12_and_newer(self) -> None:
        fake = _FakeContext()
        with patch("home_center.server._server_context", return_value=fake):
            context = _web_context(self.runtime)
        self.assertIs(context, fake)
        self.assertEqual(WEB_MINIMUM_TLS_VERSION, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_peer_listener_keeps_tls13_and_mtls(self) -> None:
        fake = _FakeContext()
        with patch("home_center.server._server_context", return_value=fake):
            context = _peer_context(self.runtime)
        self.assertIs(context, fake)
        self.assertEqual(PEER_MINIMUM_TLS_VERSION, ssl.TLSVersion.TLSv1_3)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_3)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(context.loaded_ca, "/cluster/ca.crt")

    def test_no_listener_policy_drops_below_tls12(self) -> None:
        self.assertGreaterEqual(WEB_MINIMUM_TLS_VERSION, ssl.TLSVersion.TLSv1_2)
        self.assertGreaterEqual(PEER_MINIMUM_TLS_VERSION, ssl.TLSVersion.TLSv1_2)


if __name__ == "__main__":
    unittest.main()
