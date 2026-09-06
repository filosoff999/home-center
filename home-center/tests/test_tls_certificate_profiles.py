from __future__ import annotations

import subprocess
import socket
import tempfile
import time
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

    def _make_key(self, path: Path, profile: str) -> None:
        if profile == "p256":
            self._run(
                "openssl", "genpkey", "-algorithm", "EC",
                "-pkeyopt", "ec_paramgen_curve:prime256v1", "-out", str(path),
            )
        elif profile == "p384":
            self._run(
                "openssl", "genpkey", "-algorithm", "EC",
                "-pkeyopt", "ec_paramgen_curve:secp384r1", "-out", str(path),
            )
        elif profile == "ed25519":
            self._run("openssl", "genpkey", "-algorithm", "ED25519", "-out", str(path))
        else:
            raise AssertionError(f"unsupported test key profile: {profile}")

    def _make_ca(self, name: str, *, key_profile: str = "p256") -> tuple[Path, Path]:
        key = self.root / f"{name}.key"
        cert = self.root / f"{name}.crt"
        self._make_key(key, key_profile)
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
        key_profile: str = "p256",
    ) -> tuple[Path, Path]:
        ca_cert, ca_key = ca or self.ca1
        key = self.root / f"{name}.key"
        csr = self.root / f"{name}.csr"
        cert = self.root / f"{name}.crt"
        ext = self.root / f"{name}.cnf"
        self._make_key(key, key_profile)
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
            mock.patch.object(tls_activate, "WEB_CA_CERT", self.ca1[0]),
            mock.patch.object(tls_activate, "_regular_secure"),
        ):
            return tls_activate.validate_candidate(self.spec, cert, key)

    def _restricted_sigalg_handshake(self, cert: Path, key: Path, protocol: str) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        server = subprocess.Popen(
            (
                "openssl", "s_server", "-accept", f"127.0.0.1:{port}",
                "-cert", str(cert), "-key", str(key), "-www", "-naccept", "1", protocol,
            ),
            cwd=self.root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.15)
            client = subprocess.run(
                (
                    "openssl", "s_client", "-connect", f"127.0.0.1:{port}",
                    "-servername", "dc02.hm.dm", "-CAfile", str(self.ca1[0]),
                    "-verify_return_error", "-sigalgs", "ecdsa_secp256r1_sha256", protocol,
                ),
                input=b"GET / HTTP/1.0\r\n\r\n",
                check=False,
                capture_output=True,
                timeout=5,
                cwd=self.root,
            )
        finally:
            if server.poll() is None:
                server.terminate()
            try:
                server.wait(timeout=2)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=2)
        self.assertEqual(client.returncode, 0, (client.stdout + client.stderr).decode(errors="replace"))
        self.assertIn(b"Verify return code: 0", client.stdout + client.stderr)

    def test_valid_server_only_profile_is_accepted(self) -> None:
        cert, key = self._make_leaf("valid")
        fingerprint = self._validate(cert, key)
        self.assertEqual(len(fingerprint), 64)

    def test_p256_web_chain_negotiates_without_ed25519_sigalgs(self) -> None:
        cert, key = self._make_leaf("restricted-sigalgs")
        for protocol in ("-tls1_2", "-tls1_3"):
            with self.subTest(protocol=protocol):
                self._restricted_sigalg_handshake(cert, key, protocol)

    def test_ed25519_web_leaf_is_rejected_for_browser_compatibility(self) -> None:
        cert, key = self._make_leaf("ed25519-leaf", key_profile="ed25519")
        with self.assertRaisesRegex(ActivationError, "web_algorithm_rejected"):
            self._validate(cert, key)

    def test_p384_web_leaf_is_rejected_by_exact_p256_profile(self) -> None:
        cert, key = self._make_leaf("p384-leaf", key_profile="p384")
        with self.assertRaisesRegex(ActivationError, "web_algorithm_rejected"):
            self._validate(cert, key)

    def test_ed25519_web_ca_is_rejected_even_with_p256_leaf(self) -> None:
        ed_ca = self._make_ca("ed25519-ca", key_profile="ed25519")
        cert, key = self._make_leaf("ed25519-chain", ca=ed_ca)
        with (
            mock.patch.object(tls_activate, "WEB_CA_CERT", ed_ca[0]),
            mock.patch.object(tls_activate, "_regular_secure"),
        ):
            with self.assertRaisesRegex(ActivationError, "web_ca_algorithm_rejected"):
                tls_activate.validate_candidate(self.spec, cert, key)

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

    def test_extra_dns_identity_is_rejected(self) -> None:
        cert, key = self._make_leaf("extra-dns", dns=("dc02.hm.dm", "home-center.hm.dm", "unexpected.hm.dm"))
        with self.assertRaisesRegex(ActivationError, "unexpected_dns_san"):
            self._validate(cert, key)

    def test_extra_ip_identity_is_rejected(self) -> None:
        cert, key = self._make_leaf("extra-ip-base")
        # Exercise the exact-set gate directly so the fixture remains concise.
        with mock.patch.object(
            tls_activate,
            "_san_identities",
            return_value=(
                frozenset({"dc02.hm.dm", "home-center.hm.dm"}),
                frozenset({"192.168.10.253", "192.168.10.252"}),
            ),
        ):
            with self.assertRaisesRegex(ActivationError, "unexpected_ip_san"):
                tls_activate._require_expected_identities(self.spec, cert)

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
