from __future__ import annotations

import unittest

from home_center.upgrade_policy import UpgradePolicyError, is_upgrade_allowed, parse_version


class UpgradePolicyTests(unittest.TestCase):
    def test_major_bridge_accepts_every_older_semver(self) -> None:
        for source in ("0.9.9", "1.0.0", "1.7.5", "1.7.6", "1.7.7", "1.99.999"):
            with self.subTest(source=source):
                self.assertTrue(is_upgrade_allowed(source, "2.0.0"))
        for source in ("2.0.0", "2.0.1", "3.0.0"):
            with self.subTest(source=source):
                self.assertFalse(is_upgrade_allowed(source, "2.0.0"))

    def test_latest_in_major_accepts_any_older_version_in_that_major(self) -> None:
        for source in ("0.0.1", "0.6.0", "0.7.0", "0.8.0", "0.9.0", "0.9.1"):
            with self.subTest(source=source):
                self.assertTrue(is_upgrade_allowed(source, "0.9.2"))
        self.assertFalse(is_upgrade_allowed("0.9.2", "0.9.2"))
        self.assertFalse(is_upgrade_allowed("1.0.0", "0.9.2"))

    def test_non_major_bridge_does_not_cross_major_lines(self) -> None:
        self.assertTrue(is_upgrade_allowed("1.0.0", "1.7.7"))
        self.assertTrue(is_upgrade_allowed("1.7.6", "1.7.7"))
        self.assertFalse(is_upgrade_allowed("0.9.9", "1.7.7"))
        self.assertFalse(is_upgrade_allowed("2.0.0", "1.7.7"))

    def test_semver_is_strict(self) -> None:
        for value in ("", "v1.0.0", "01.0.0", "1.00.0", "1.0", "1.0.0.0", "-1.0.0"):
            with self.subTest(value=value):
                self.assertFalse(is_upgrade_allowed(value, "2.0.0"))
        with self.assertRaises(UpgradePolicyError):
            parse_version("v1.0.0")


if __name__ == "__main__":
    unittest.main()
