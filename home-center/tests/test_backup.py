from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center.backup import create_backup, verify_backup  # noqa: E402
from home_center.store import StateStore  # noqa: E402


class BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        web = root / "web"
        secrets = root / "secrets"
        web.mkdir()
        secrets.mkdir()
        (web / "index.html").write_text("<!doctype html>", encoding="utf-8")

        self.audit_key = b"a" * 32
        for name, value in (
            ("session.key", b"s" * 32),
            ("audit.key", self.audit_key),
            ("node.key", b"test-private-key"),
        ):
            path = secrets / name
            path.write_bytes(value)
            os.chmod(path, 0o600)
        local_admin = secrets / "local-admin.json"
        local_admin.write_text("{}", encoding="utf-8")
        os.chmod(local_admin, 0o640)
        for name in ("node.crt", "ca.crt", "web-ca.crt"):
            (secrets / name).write_text("test-public-certificate", encoding="utf-8")

        profile = root / "profile.json"
        profile.write_text(
            (ROOT / "deploy/profiles/hm-dm-two-node.v1.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.state_db = root / "state" / "state.sqlite3"
        self.backup_dir = root / "backups"
        config = {
            "schema": "home-center.config.v3",
            "cluster_id": "hm-dm-production",
            "node_id": "hm-dm-dc01",
            "node_name": "dc01",
            "role": "leader",
            "management_address": "192.168.10.254",
            "web_port": 8443,
            "peer_port": 9443,
            "state_db": str(self.state_db),
            "backup_dir": str(self.backup_dir),
            "web_root": str(web),
            "local_admin_credentials_file": str(local_admin),
            "ad_auth": {
                "enabled": False,
                "realm": "HM.DM",
                "kdc_hosts": ["dc01.hm.dm", "dc02.hm.dm"],
                "allowed_admin_groups": ["domain admins@hm.dm"],
                "timeout_seconds": 5,
                "cache_root": str(root / "ad-auth"),
            },
            "session_key_file": str(secrets / "session.key"),
            "audit_key_file": str(secrets / "audit.key"),
            "tls_certificate": str(secrets / "node.crt"),
            "tls_private_key": str(secrets / "node.key"),
            "cluster_ca": str(secrets / "ca.crt"),
            "web_ca": str(secrets / "web-ca.crt"),
            "deployment_profile": str(profile),
            "peer": {
                "node_id": "hm-dm-dc02",
                "name": "dc02",
                "address": "192.168.10.253",
                "url": "https://192.168.10.253:9443",
                "certificate_name": "home-center-dc02",
            },
        }
        self.config_path = root / "config.json"
        self.config_path.write_text(json.dumps(config), encoding="utf-8")
        os.chmod(self.config_path, 0o640)

        store = StateStore(self.state_db, self.audit_key, "hm-dm-production")
        store.audit(
            actor="test",
            action="backup.seed",
            target="hm-dm-dc01",
            outcome="accepted",
            correlation_id="backup-test",
        )
        store.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_backup_round_trip_checks_database_and_audit_chain(self) -> None:
        with patch.dict(os.environ, {"HOME_CENTER_CONFIG": str(self.config_path)}):
            archive, sidecar = create_backup(retain=2)

        self.assertTrue(archive.is_file())
        self.assertTrue(sidecar.is_file())
        manifest = verify_backup(archive, audit_key=self.audit_key)
        self.assertEqual(manifest["verification"], {"sqlite_integrity": "ok", "audit_chain": "ok"})
        self.assertRegex(manifest["audit_head"], r"^[0-9a-f]{64}$")
        with self.assertRaises(RuntimeError):
            verify_backup(archive, audit_key=b"wrong-audit-key" * 2)


if __name__ == "__main__":
    unittest.main()
