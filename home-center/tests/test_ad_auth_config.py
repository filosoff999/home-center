from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center.config import _ad_auth  # noqa: E402


def valid() -> dict:
    return {
        "ad_auth": {
            "enabled": False,
            "realm": "HM.DM",
            "kdc_hosts": ["dc01.hm.dm", "dc02.hm.dm"],
            "allowed_admin_groups": ["domain admins@hm.dm"],
            "timeout_seconds": 5,
            "cache_root": "/var/lib/home-center/ad-auth",
        }
    }


class AdAuthConfigTests(unittest.TestCase):
    def test_disabled_default_is_explicit_and_fully_bounded(self) -> None:
        value = _ad_auth(valid())
        self.assertFalse(value.enabled)
        self.assertEqual(value.realm, "HM.DM")
        self.assertEqual(value.kdc_hosts, ("dc01.hm.dm", "dc02.hm.dm"))
        self.assertEqual(value.allowed_admin_groups, ("domain admins@hm.dm",))
        self.assertEqual(value.timeout_seconds, 5)
        self.assertTrue(value.cache_root.is_absolute())

    def test_unknown_fields_endpoints_groups_and_timeout_fail_closed(self) -> None:
        mutations = []
        extra = valid()
        extra["ad_auth"]["password"] = "forbidden"
        mutations.append(extra)
        wrong_kdc = valid()
        wrong_kdc["ad_auth"]["kdc_hosts"] = ["attacker.example"]
        mutations.append(wrong_kdc)
        no_groups = valid()
        no_groups["ad_auth"]["allowed_admin_groups"] = []
        mutations.append(no_groups)
        long_timeout = valid()
        long_timeout["ad_auth"]["timeout_seconds"] = 60
        mutations.append(long_timeout)
        for value in mutations:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    _ad_auth(value)


if __name__ == "__main__":
    unittest.main()
