from __future__ import annotations

import unittest
from email.message import Message

from home_center.external_access import (
    ExternalAccessPolicy,
    ExternalAccessRejected,
    ExternalRequestRateLimiter,
    normalize_public_hostname,
    normalize_trusted_proxy_addresses,
)


def headers(**values: str) -> Message:
    result = Message()
    for name, value in values.items():
        result[name.replace("_", "-")] = value
    return result


class ExternalAccessPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = ExternalAccessPolicy(True, "home.example.net", ("192.168.10.1",), True)

    def forwarded(self) -> Message:
        return headers(
            X_Forwarded_For="203.0.113.18",
            X_Forwarded_Proto="https",
            X_Forwarded_Host="home.example.net",
        )

    def test_direct_lan_request_remains_internal(self) -> None:
        context = self.policy.classify("192.168.10.42", headers())
        self.assertFalse(context.external)
        self.assertEqual(context.client_address, "192.168.10.42")

    def test_valid_trusted_https_proxy_context(self) -> None:
        context = self.policy.classify("192.168.10.1", self.forwarded())
        self.assertTrue(context.external)
        self.assertEqual(context.client_address, "203.0.113.18")
        self.assertEqual(context.proxy_address, "192.168.10.1")
        self.assertEqual(context.public_hostname, "home.example.net")
        self.assertEqual(context.limiter_key, "192.168.10.1/203.0.113.18")

    def test_untrusted_forwarding_and_direct_global_peers_are_rejected(self) -> None:
        with self.assertRaisesRegex(ExternalAccessRejected, "untrusted_forwarded_headers"):
            self.policy.classify("192.168.10.42", self.forwarded())
        with self.assertRaisesRegex(ExternalAccessRejected, "direct_external_peer_rejected"):
            self.policy.classify("8.8.8.8", headers())

    def test_trusted_proxy_never_falls_through_without_exact_headers(self) -> None:
        with self.assertRaisesRegex(ExternalAccessRejected, "forwarded_headers_missing"):
            self.policy.classify("192.168.10.1", headers())
        incomplete = headers(X_Forwarded_For="203.0.113.18", X_Forwarded_Proto="https")
        with self.assertRaisesRegex(ExternalAccessRejected, "forwarded_header_cardinality_rejected"):
            self.policy.classify("192.168.10.1", incomplete)

    def test_duplicate_chained_standard_and_wrong_forwarding_fail_closed(self) -> None:
        duplicate = self.forwarded()
        duplicate["X-Forwarded-For"] = "198.51.100.7"
        with self.assertRaisesRegex(ExternalAccessRejected, "forwarded_header_cardinality_rejected"):
            self.policy.classify("192.168.10.1", duplicate)
        cases = (
            ("203.0.113.18, 192.168.10.1", "https", "home.example.net", "forwarded_header_value_rejected"),
            ("203.0.113.18", "http", "home.example.net", "forwarded_proto_rejected"),
            ("203.0.113.18", "https", "other.example.net", "forwarded_host_rejected"),
            ("0.0.0.0", "https", "home.example.net", "forwarded_client_rejected"),
        )
        for address, proto, host, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(ExternalAccessRejected, code):
                self.policy.classify(
                    "192.168.10.1",
                    headers(X_Forwarded_For=address, X_Forwarded_Proto=proto, X_Forwarded_Host=host),
                )
        standard = Message()
        standard["Forwarded"] = 'for=203.0.113.18;proto=https;host="home.example.net"'
        with self.assertRaisesRegex(ExternalAccessRejected, "forwarded_header_unsupported"):
            self.policy.classify("192.168.10.1", standard)

    def test_disabled_or_incomplete_policy_is_explicitly_inert(self) -> None:
        disabled = ExternalAccessPolicy(False, None, (), True)
        self.assertFalse(disabled.effective_enabled)
        self.assertEqual(
            disabled.blockers,
            ("disabled_by_configuration", "public_hostname_missing", "trusted_proxy_missing"),
        )
        configured_off = ExternalAccessPolicy(False, "home.example.net", ("192.168.10.1",), True)
        with self.assertRaisesRegex(ExternalAccessRejected, "external_access_disabled"):
            configured_off.classify("192.168.10.1", self.forwarded())

    def test_configuration_normalizers_reject_ambiguous_values(self) -> None:
        self.assertEqual(normalize_public_hostname("Home.Example.NET"), "home.example.net")
        for value in (
            "localhost",
            "a..example.net",
            "-a.example.net",
            "a-.example.net",
            "192.168.10.1",
            "home.example.net:443",
            "https://home.example.net",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_public_hostname(value)
        self.assertEqual(
            normalize_trusted_proxy_addresses(["192.168.10.1", "fd00::1"]),
            ("192.168.10.1", "fd00::1"),
        )
        for value in (["8.8.8.8"], ["0.0.0.0"], ["192.168.10.1", "192.168.10.1"], ["fe80::1%eth0"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_trusted_proxy_addresses(value)

    def test_two_level_rate_limit_bounds_client_and_proxy(self) -> None:
        context = self.policy.classify("192.168.10.1", self.forwarded())
        limiter = ExternalRequestRateLimiter(client_requests=2, proxy_requests=3, window_seconds=60)
        self.assertTrue(limiter.allow(context))
        self.assertTrue(limiter.allow(context))
        self.assertFalse(limiter.allow(context))
        other = self.policy.classify(
            "192.168.10.1",
            headers(
                X_Forwarded_For="198.51.100.9",
                X_Forwarded_Proto="https",
                X_Forwarded_Host="home.example.net",
            ),
        )
        self.assertTrue(limiter.allow(other))
        self.assertFalse(limiter.allow(other))


if __name__ == "__main__":
    unittest.main()
