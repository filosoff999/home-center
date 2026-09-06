from __future__ import annotations

import unittest

from home_center.config import ExternalAccessConfig, _external_access


class ExternalAccessConfigTests(unittest.TestCase):
    def test_disabled_configuration_is_explicit(self) -> None:
        self.assertEqual(
            _external_access(
                {
                    "external_access": {
                        "enabled": False,
                        "mode": "trusted-reverse-proxy",
                        "public_hostname": None,
                        "trusted_proxy_addresses": [],
                    }
                }
            ),
            ExternalAccessConfig(),
        )

    def test_enabled_configuration_is_canonical_and_exact(self) -> None:
        config = _external_access(
            {
                "external_access": {
                    "enabled": True,
                    "mode": "trusted-reverse-proxy",
                    "public_hostname": "Home.Example.NET",
                    "trusted_proxy_addresses": ["192.168.10.1", "fd00::1"],
                }
            }
        )
        self.assertTrue(config.enabled)
        self.assertEqual(config.public_hostname, "home.example.net")
        self.assertEqual(config.trusted_proxy_addresses, ("192.168.10.1", "fd00::1"))

    def test_incomplete_wrong_type_mode_shape_and_address_fail_closed(self) -> None:
        base = {
            "enabled": False,
            "mode": "trusted-reverse-proxy",
            "public_hostname": None,
            "trusted_proxy_addresses": [],
        }
        cases = (
            {**base, "enabled": "false"},
            {**base, "mode": "automatic"},
            {**base, "public_hostname": "a..example.net"},
            {**base, "trusted_proxy_addresses": ["8.8.8.8"]},
            {**base, "unexpected": True},
            {**base, "enabled": True},
        )
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                _external_access({"external_access": value})


if __name__ == "__main__":
    unittest.main()
