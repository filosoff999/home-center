from __future__ import annotations

import sqlite3
import ssl
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center.inventory import collect  # noqa: E402
from home_center.reconcile import Reconciler  # noqa: E402
from home_center.store import StateStore  # noqa: E402


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.sqlite3"
        self.store = StateStore(self.path, b"a" * 32, "test-cluster")

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_node_upsert_is_idempotent(self) -> None:
        capability = collect("node-1", "node1", "leader", "192.168.10.10")
        self.store.upsert_node(capability, "ready")
        self.store.upsert_node(capability, "ready")
        self.assertEqual(len(self.store.nodes()), 1)

    def test_audit_chain_detects_tampering(self) -> None:
        self.store.audit(actor="test", action="read", target="node-1", outcome="accepted", correlation_id="case-1")
        self.store.audit(actor="test", action="read", target="node-2", outcome="accepted", correlation_id="case-2")
        self.assertRegex(self.store.verify_audit_chain(), r"^[0-9a-f]{64}$")
        connection = sqlite3.connect(self.path)
        connection.execute("UPDATE audit SET target='tampered' WHERE seq=1")
        connection.commit()
        connection.close()
        with self.assertRaises(RuntimeError):
            self.store.verify_audit_chain()

    def test_database_backup_is_independently_readable(self) -> None:
        self.store.audit(actor="test", action="backup", target="db", outcome="accepted", correlation_id="backup-1")
        destination = Path(self.tmp.name) / "copy.sqlite3"
        self.store.backup_to(destination)
        connection = sqlite3.connect(f"file:{destination}?mode=ro", uri=True)
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT count(*) FROM audit").fetchone()[0], 1)
        finally:
            connection.close()

    def test_peer_failure_class_is_specific_without_error_text(self) -> None:
        reason = ssl.SSLCertVerificationError(1, "sensitive transport detail")
        reason.verify_code = 92
        error = __import__("urllib.error").error.URLError(reason)
        self.assertEqual(Reconciler._failure_class(error), "tls_verify_92")


if __name__ == "__main__":
    unittest.main()
