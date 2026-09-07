from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from home_center.local_admin_recovery import LocalAdminRecoveryError, RecoveryEvidenceLog


class LocalAdminRecoveryEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.path = self.root / "events.jsonl"
        self.log = RecoveryEvidenceLog(
            self.path,
            expected_uid=os.geteuid(),
            expected_gid=os.getegid(),
            expected_mode=0o600,
            expected_directory_uid=os.geteuid(),
            expected_directory_gid=os.getegid(),
            expected_directory_mode=0o700,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def append(self, outcome: str, reason: str) -> str:
        return self.log.append(
            actor_uid=os.getuid(),
            target="hm-dm-dc01",
            tty="/dev/tty1",
            outcome=outcome,
            reason=reason,
        )

    def test_evidence_is_chained_root_only_and_contains_safe_metadata(self) -> None:
        first = self.append("requested", "recovery_console_verified")
        second = self.append("accepted", "credential_reset_committed")
        events = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([event["sequence"] for event in events], [1, 2])
        self.assertEqual(events[0]["event_id"], first)
        self.assertEqual(events[1]["event_id"], second)
        self.assertEqual(events[1]["previous_hash"], events[0]["entry_hash"])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        serialized = json.dumps(events)
        for forbidden in ("current_password", "new_password", "salt_b64", "verifier_b64"):
            self.assertNotIn(forbidden, serialized)

    def test_tampered_or_truncated_evidence_blocks_next_event(self) -> None:
        self.append("requested", "recovery_console_verified")
        original = self.path.read_text(encoding="utf-8")
        self.path.write_text(original.replace("hm-dm-dc01", "hm-dm-dc02"), encoding="utf-8")
        os.chmod(self.path, 0o600)
        with self.assertRaisesRegex(LocalAdminRecoveryError, "recovery_evidence_chain_rejected"):
            self.append("denied", "operator_cancelled")

        self.path.write_text(original.rstrip("\n"), encoding="utf-8")
        os.chmod(self.path, 0o600)
        with self.assertRaisesRegex(LocalAdminRecoveryError, "recovery_evidence_truncated"):
            self.append("denied", "operator_cancelled")

    def test_symlink_and_arbitrary_reason_are_rejected(self) -> None:
        target = self.root / "target"
        target.write_text("", encoding="utf-8")
        self.path.symlink_to(target)
        with self.assertRaises(LocalAdminRecoveryError):
            self.append("requested", "recovery_console_verified")
        self.path.unlink()
        with self.assertRaisesRegex(LocalAdminRecoveryError, "recovery_reason_rejected"):
            self.append("failed", "Password123")


if __name__ == "__main__":
    unittest.main()
