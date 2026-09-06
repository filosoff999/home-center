from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from home_center import tls_status

ROOT = Path(__file__).resolve().parents[1]


class TLSStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.web_cert = root / "web.crt"
        self.web_key = root / "web.key"
        self.peer_cert = root / "peer.crt"
        self.ca_cert = root / "ca.crt"
        self.web_ca_cert = root / "web-ca.crt"
        for path in (self.web_cert, self.web_key, self.peer_cert, self.ca_cert, self.web_ca_cert):
            path.write_text("fixture", encoding="ascii")
        self.config = SimpleNamespace(
            node_id="hm-dm-dc02",
            node_name="dc02",
            management_address="192.168.10.253",
            web_port=8443,
            tls_certificate=self.peer_cert,
            cluster_ca=self.ca_cert,
            web_ca=self.web_ca_cert,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _certificate(self, *, mode: str, days: int = 120, chain: bool = True, host: bool = True, profile: bool = True) -> dict[str, object]:
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
            "required_san_dns": ["dc02.hm.dm", "home-center.hm.dm"],
            "required_san_ip": ["192.168.10.253"],
            "san_policy_valid": host,
            "profile": "ecdsa-p256-sha256" if profile else "unsupported-for-browser-web",
            "profile_valid": profile,
            "public_key_algorithm": "ECDSA" if profile else "Ed25519",
            "public_key_curve": "P-256" if profile else None,
            "signature_algorithm": "ecdsa-with-SHA256" if profile else "ED25519",
        }

    def _status(self, web: dict[str, object], *, separate: bool = True) -> dict[str, object]:
        if not separate:
            self.web_cert.unlink(missing_ok=True)
            self.web_key.unlink(missing_ok=True)
        ca = self._certificate(mode="trust-anchor", days=2000)
        managed_ca = self._certificate(mode="managed-web-ca", days=2000)
        peer = self._certificate(mode="peer-mtls-identity")
        with (
            mock.patch.object(tls_status, "WEB_CERTIFICATE", self.web_cert),
            mock.patch.object(tls_status, "WEB_PRIVATE_KEY", self.web_key),
            mock.patch.object(tls_status, "CANDIDATE_CERTIFICATE", Path(self.tmp.name) / "candidate.crt"),
            mock.patch.object(tls_status, "CANDIDATE_PRIVATE_KEY", Path(self.tmp.name) / "candidate.key"),
            mock.patch.object(tls_status, "_separate_web_identity_present", return_value=separate),
            mock.patch.object(tls_status, "_certificate", side_effect=[ca, managed_ca, web, peer]),
            mock.patch.object(tls_status, "_maintenance_status", return_value=None),
        ):
            return tls_status.status(self.config)

    def test_healthy_separate_identity_is_not_due(self) -> None:
        result = self._status(self._certificate(mode="separate-web-identity", days=120))
        self.assertFalse(result["renewal"]["due"])
        self.assertEqual(result["renewal"]["threshold_days"], 30)

    def test_runtime_shape_matches_closed_openapi_tls_schema(self) -> None:
        result = self._status(self._certificate(mode="separate-web-identity", days=120))
        contract = json.loads(
            (ROOT / "contracts/openapi/home-center.v1.openapi.json").read_text(encoding="utf-8")
        )["components"]["schemas"]
        tls_schema = contract["TlsStatus"]
        certificate_schema = contract["CertificateStatus"]
        self.assertEqual(set(result), set(tls_schema["properties"]))
        self.assertEqual(set(result), set(tls_schema["required"]))
        for field in ("web", "peer"):
            self.assertEqual(set(result[field]), set(certificate_schema["properties"]))
            self.assertEqual(set(result[field]), set(certificate_schema["required"]))
        for field in ("web_ca", "trust_anchor", "candidate", "operational", "renewal"):
            schema = tls_schema["properties"][field]
            self.assertEqual(set(result[field]), set(schema["properties"]))
            self.assertEqual(set(result[field]), set(schema["required"]))

    def test_trust_anchor_tracks_the_actually_served_web_identity(self) -> None:
        with (
            mock.patch.object(tls_status, "_separate_web_identity_present", return_value=True),
        ):
            self.assertEqual(tls_status.web_trust_anchor(self.config), self.web_ca_cert)
        with mock.patch.object(tls_status, "_separate_web_identity_present", return_value=False):
            self.assertEqual(tls_status.web_trust_anchor(self.config), self.ca_cert)

    def test_dangling_or_incomplete_current_identity_is_fail_closed(self) -> None:
        root = Path(self.tmp.name) / "identity"
        releases = root / "releases"
        release = releases / ("a" * 24)
        release.mkdir(parents=True)
        os.chmod(release, 0o750)
        (release / "tls.crt").write_text("certificate", encoding="ascii")
        os.chmod(release / "tls.crt", 0o644)
        current = root / "current"
        current.symlink_to(release)
        with (
            mock.patch.object(tls_status, "WEB_ROOT", root),
            mock.patch.object(tls_status, "WEB_RELEASES", releases),
            mock.patch.object(tls_status, "WEB_CURRENT", current),
            mock.patch.object(tls_status, "WEB_CERTIFICATE", current / "tls.crt"),
            mock.patch.object(tls_status, "WEB_PRIVATE_KEY", current / "tls.key"),
            mock.patch.object(tls_status, "ROOT_UID", os.getuid()),
            mock.patch.object(tls_status, "_fingerprint", return_value="a" * 64),
        ):
            with self.assertRaisesRegex(RuntimeError, "web_identity_incomplete"):
                tls_status._separate_web_identity_present()
            (release / "tls.key").write_text("private", encoding="ascii")
            os.chmod(release / "tls.key", 0o640)
            self.assertTrue(tls_status._separate_web_identity_present())
            current.unlink()
            current.symlink_to(releases / ("b" * 24))
            with self.assertRaisesRegex(RuntimeError, "web_current_dangling"):
                tls_status._separate_web_identity_present()

    def test_expiry_threshold_is_due(self) -> None:
        result = self._status(self._certificate(mode="separate-web-identity", days=30))
        self.assertTrue(result["renewal"]["due"])

    def test_invalid_chain_is_due_even_with_long_validity(self) -> None:
        result = self._status(self._certificate(mode="separate-web-identity", days=365, chain=False))
        self.assertTrue(result["renewal"]["due"])

    def test_hostname_mismatch_is_due_even_with_long_validity(self) -> None:
        result = self._status(self._certificate(mode="separate-web-identity", days=365, host=False))
        self.assertTrue(result["renewal"]["due"])

    def test_browser_incompatible_profile_is_due_even_with_valid_chain(self) -> None:
        result = self._status(self._certificate(mode="separate-web-identity", days=365, profile=False))
        self.assertTrue(result["renewal"]["due"])
        self.assertFalse(result["web"]["profile_valid"])

    def test_expiring_web_ca_is_due_before_a_full_leaf_lifetime(self) -> None:
        web = self._certificate(mode="separate-web-identity", days=365)
        served_ca = self._certificate(mode="trust-anchor", days=426)
        managed_ca = self._certificate(mode="managed-web-ca", days=426)
        peer = self._certificate(mode="peer-mtls-identity")
        with (
            mock.patch.object(tls_status, "_separate_web_identity_present", return_value=True),
            mock.patch.object(tls_status, "_certificate", side_effect=[served_ca, managed_ca, web, peer]),
            mock.patch.object(tls_status, "CANDIDATE_CERTIFICATE", Path(self.tmp.name) / "candidate.crt"),
            mock.patch.object(tls_status, "CANDIDATE_PRIVATE_KEY", Path(self.tmp.name) / "candidate.key"),
            mock.patch.object(tls_status, "_maintenance_status", return_value=None),
        ):
            result = tls_status.status(self.config)
        self.assertTrue(result["renewal"]["due"])
        self.assertEqual(result["renewal"]["web_ca_threshold_days"], 427)

    def test_legacy_peer_fallback_is_always_due(self) -> None:
        result = self._status(self._certificate(mode="legacy-peer-fallback", days=365), separate=False)
        self.assertTrue(result["renewal"]["due"])
        self.assertEqual(result["web"]["mode"], "legacy-peer-fallback")

    def test_partial_candidate_is_visible_without_private_key_contents(self) -> None:
        candidate_cert = Path(self.tmp.name) / "candidate.crt"
        candidate_cert.write_text("certificate-only", encoding="ascii")
        ca = self._certificate(mode="trust-anchor", days=2000)
        managed_ca = self._certificate(mode="managed-web-ca", days=2000)
        web = self._certificate(mode="separate-web-identity")
        peer = self._certificate(mode="peer-mtls-identity")
        with (
            mock.patch.object(tls_status, "WEB_CERTIFICATE", self.web_cert),
            mock.patch.object(tls_status, "WEB_PRIVATE_KEY", self.web_key),
            mock.patch.object(tls_status, "CANDIDATE_CERTIFICATE", candidate_cert),
            mock.patch.object(tls_status, "CANDIDATE_PRIVATE_KEY", Path(self.tmp.name) / "candidate.key"),
            mock.patch.object(tls_status, "ROOT_UID", os.getuid()),
            mock.patch.object(tls_status, "_separate_web_identity_present", return_value=True),
            mock.patch.object(tls_status, "_certificate", side_effect=[ca, managed_ca, web, peer]),
            mock.patch.object(tls_status, "_maintenance_status", return_value=None),
        ):
            result = tls_status.status(self.config)
        self.assertEqual(
            result["candidate"],
            {
                "certificate_present": True,
                "private_key_present": False,
                "owner_marker_present": False,
                "complete": False,
                "partial": True,
                "invalid": False,
            },
        )
        self.assertEqual(result["operational"]["state"], "recovery_required")
        self.assertTrue(result["operational"]["recovery_required"])
        self.assertNotIn("certificate-only", str(result))


if __name__ == "__main__":
    unittest.main()
