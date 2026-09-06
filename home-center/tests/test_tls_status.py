from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from home_center import tls_status


class TLSStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.web_cert = root / "web.crt"
        self.web_key = root / "web.key"
        self.peer_cert = root / "peer.crt"
        self.ca_cert = root / "ca.crt"
        for path in (self.web_cert, self.web_key, self.peer_cert, self.ca_cert):
            path.write_text("fixture", encoding="ascii")
        self.config = SimpleNamespace(
            node_id="hm-dm-dc02",
            node_name="dc02",
            web_port=8443,
            tls_certificate=self.peer_cert,
            cluster_ca=self.ca_cert,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _certificate(self, *, mode: str, days: int = 120, chain: bool = True, host: bool = True) -> dict[str, object]:
        return {
            "mode": mode,
            "fingerprint_sha256": "a" * 64,
            "subject": "commonName=fixture",
            "issuer": "commonName=fixture-ca",
            "serial_number": "01",
            "not_before": "2026-01-01T00:00:00Z",
            "not_after": "2027-01-01T00:00:00Z",
            "days_remaining": days,
            "san_dns": ["dc02.hm.dm", "home-center.hm.dm"],
            "san_ip": ["192.168.10.253"],
            "expected_hostname": "dc02.hm.dm",
            "hostname_match": host,
            "chain_valid": chain,
        }

    def _status(self, web: dict[str, object], *, separate: bool = True) -> dict[str, object]:
        if not separate:
            self.web_cert.unlink(missing_ok=True)
            self.web_key.unlink(missing_ok=True)
        ca = self._certificate(mode="trust-anchor")
        peer = self._certificate(mode="peer-mtls-identity")
        with (
            mock.patch.object(tls_status, "WEB_CERTIFICATE", self.web_cert),
            mock.patch.object(tls_status, "WEB_PRIVATE_KEY", self.web_key),
            mock.patch.object(tls_status, "CANDIDATE_CERTIFICATE", Path(self.tmp.name) / "candidate.crt"),
            mock.patch.object(tls_status, "CANDIDATE_PRIVATE_KEY", Path(self.tmp.name) / "candidate.key"),
            mock.patch.object(tls_status, "_certificate", side_effect=[ca, web, peer]),
            mock.patch.object(tls_status, "_maintenance_status", return_value=None),
        ):
            return tls_status.status(self.config)

    def test_healthy_separate_identity_is_not_due(self) -> None:
        result = self._status(self._certificate(mode="separate-web-identity", days=120))
        self.assertFalse(result["renewal"]["due"])
        self.assertEqual(result["renewal"]["threshold_days"], 30)

    def test_expiry_threshold_is_due(self) -> None:
        result = self._status(self._certificate(mode="separate-web-identity", days=30))
        self.assertTrue(result["renewal"]["due"])

    def test_invalid_chain_is_due_even_with_long_validity(self) -> None:
        result = self._status(self._certificate(mode="separate-web-identity", days=365, chain=False))
        self.assertTrue(result["renewal"]["due"])

    def test_hostname_mismatch_is_due_even_with_long_validity(self) -> None:
        result = self._status(self._certificate(mode="separate-web-identity", days=365, host=False))
        self.assertTrue(result["renewal"]["due"])

    def test_legacy_peer_fallback_is_always_due(self) -> None:
        result = self._status(self._certificate(mode="legacy-peer-fallback", days=365), separate=False)
        self.assertTrue(result["renewal"]["due"])
        self.assertEqual(result["web"]["mode"], "legacy-peer-fallback")

    def test_partial_candidate_is_visible_without_private_key_contents(self) -> None:
        candidate_cert = Path(self.tmp.name) / "candidate.crt"
        candidate_cert.write_text("certificate-only", encoding="ascii")
        ca = self._certificate(mode="trust-anchor")
        web = self._certificate(mode="separate-web-identity")
        peer = self._certificate(mode="peer-mtls-identity")
        with (
            mock.patch.object(tls_status, "WEB_CERTIFICATE", self.web_cert),
            mock.patch.object(tls_status, "WEB_PRIVATE_KEY", self.web_key),
            mock.patch.object(tls_status, "CANDIDATE_CERTIFICATE", candidate_cert),
            mock.patch.object(tls_status, "CANDIDATE_PRIVATE_KEY", Path(self.tmp.name) / "candidate.key"),
            mock.patch.object(tls_status, "_certificate", side_effect=[ca, web, peer]),
            mock.patch.object(tls_status, "_maintenance_status", return_value=None),
        ):
            result = tls_status.status(self.config)
        self.assertEqual(
            result["candidate"],
            {"certificate_present": True, "private_key_present": False, "complete": False, "partial": True},
        )
        self.assertNotIn("certificate-only", str(result))


if __name__ == "__main__":
    unittest.main()
