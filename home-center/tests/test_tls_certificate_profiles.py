from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from home_center import tls_activate
from home_center.tls_activate import ActivationError


class TLSCertificateProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ca1 = self._make_ca("ca1")
        self.ca2 = self._make_ca("ca2")
        self.spec = {
            "node_id": "hm-dm-dc02",
            "role": "standby",
            "ip": "192.168.10.253",
            "fqdn": "dc02.hm.dm",
            "legacy_tls_certificate": "/unused/node.crt",
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, *argv: str) -> None:
        subprocess.run(argv, check=True, capture_output=True, cwd=self.root)

    def _make_ca(self, name: str) -> tuple[Path, Path]:
        key = self.root / f"{name}.key"
        cert = self.root / f"{name}.crt"
        self._run("openssl", "genpkey", "-algorithm", "ED25519", "-out", str(key))
        self._run(
            "openssl", "req", "-x509", "-new", "-key", str(key), "-days", "3650",
            "-subj", f"/CN=Home Center Test {name}",
            "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign",
            "-addext", "subjectKeyIdentifier=hash",
            "-out", str(cert),
        )
        return cert, key

    def _make_leaf(
        self,
        name: str,
        *,
        ca: tuple[Path, Path] | None = None,
        dns: tuple[str, ...] = ("dc02.hm.dm", "home-center.hm.dm"),
        ip: str = "192.168.10.253",
        days: int = 397,
        eku: str = "serverAuth",
    ) -> tuple[Path, Path]:
        ca_cert, ca_key = ca or self.ca1
        key = self.root / f"{name}.key"
        csr = self.root / f"{name}.csr"
        cert = self.root / f"{name}.crt"
        ext = self.root / f"{name}.cnf"
        self._run("openssl", "genpkey", "-algorithm", "ED25519", "-out", str(key))
        self._run("openssl", "req", "-new", "-key", str(key), "-subj", f"/CN={dns[0]}/O=Home Center", "-out", str(csr))
        san = ",".join([*(f"DNS:{value}" for value in dns), f"IP:{ip}"])
        ext.write_text(
            "\n".join(
                (
                    "basicConstraints=critical,CA:FALSE",
                    "keyUsage=critical,digitalSignature",
                    f"extendedKeyUsage={eku}",
                    f"subjectAltName={san}",
                    "subjectKeyIdentifier=hash",
                    "authorityKeyIdentifier=keyid,issuer",
                )
            ) + "\n",
            encoding="ascii",
        )
        self._run(
            "openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca_cert), "-CAkey", str(ca_key),
            "-CAcreateserial", "-days", str(days), "-extfile", str(ext), "-out", str(cert),
        )
        return cert, key

    def _validate(self, cert: Path, key: Path) -> str:
        with (
            mock.patch.object(tls_activate, "CA_CERT", self.ca1[0]),
            mock.patch.object(tls_activate, "_regular_secure"),
        ):
            return tls_activate.validate_candidate(self.spec, cert, key)

    def test_valid_server_only_profile_is_accepted(self) -> None:
        cert, key = self._make_leaf("valid")
        fingerprint = self._validate(cert, key)
        self.assertEqual(len(fingerprint), 64)

    def test_wrong_chain_is_rejected(self) -> None:
        cert, key = self._make_leaf("wrong-chain", ca=self.ca2)
        with self.assertRaises(ActivationError):
            self._validate(cert, key)

    def test_short_lived_certificate_is_rejected_before_activation(self) -> None:
        cert, key = self._make_leaf("short", days=1)
        with self.assertRaises(ActivationError):
            self._validate(cert, key)

    def test_wrong_node_hostname_is_rejected(self) -> None:
        cert, key = self._make_leaf("wrong-host", dns=("wrong.hm.dm", "home-center.hm.dm"))
        with self.assertRaises(ActivationError):
            self._validate(cert, key)

    def test_missing_future_vip_identity_is_rejected(self) -> None:
        cert, key = self._make_leaf("missing-vip", dns=("dc02.hm.dm",))
        with self.assertRaises(ActivationError):
            self._validate(cert, key)

    def test_wrong_node_ip_is_rejected(self) -> None:
        cert, key = self._make_leaf("wrong-ip", ip="192.168.10.252")
        with self.assertRaises(ActivationError):
            self._validate(cert, key)

    def test_client_auth_eku_is_rejected_for_web_identity(self) -> None:
        cert, key = self._make_leaf("client-auth", eku="serverAuth,clientAuth")
        with self.assertRaisesRegex(ActivationError, "client_identity_rejected"):
            self._validate(cert, key)

    def test_wrong_private_key_is_rejected(self) -> None:
        cert, _ = self._make_leaf("right-key")
        _, wrong_key = self._make_leaf("other-key")
        with self.assertRaisesRegex(ActivationError, "key_mismatch"):
            self._validate(cert, wrong_key)


if __name__ == "__main__":
    unittest.main()
