from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from home_center.config import Config, _peer, load_config


ROOT = Path(__file__).resolve().parents[1]


class ConfigTopologyTests(unittest.TestCase):
    def _runtime_config(self, root: Path) -> dict[str, object]:
        secrets = root / "secrets"
        secrets.mkdir()
        secret_paths: dict[str, str] = {}
        for name in ("local-admin.json", "session.key", "audit.key", "node.key"):
            path = secrets / name
            path.write_text("test\n", encoding="utf-8")
            path.chmod(0o640)
            secret_paths[name] = str(path)
        public_paths: dict[str, str] = {}
        for name in ("node.crt", "cluster-ca.crt", "web-ca.crt"):
            path = root / name
            path.write_text("test\n", encoding="utf-8")
            public_paths[name] = str(path)
        return {
            "schema": "home-center.config.v5",
            "cluster_id": "example-lab",
            "node_id": "node-a",
            "node_name": "node-a",
            "role": "control-plane",
            "management_address": "192.0.2.10",
            "web_port": 8443,
            "peer_port": 9443,
            "state_db": str(root / "state.sqlite3"),
            "backup_dir": str(root / "backups"),
            "web_root": str(ROOT / "product/web/static"),
            "local_admin_credentials_file": secret_paths["local-admin.json"],
            "session_key_file": secret_paths["session.key"],
            "audit_key_file": secret_paths["audit.key"],
            "tls_certificate": public_paths["node.crt"],
            "tls_private_key": secret_paths["node.key"],
            "cluster_ca": public_paths["cluster-ca.crt"],
            "web_ca": public_paths["web-ca.crt"],
            "deployment_profile": str(ROOT / "deploy/examples/deployment-profile.example.json"),
            "peers": [],
            "reconcile_interval_seconds": 15,
            "peer_timeout_seconds": 3,
            "ad_auth": {
                "enabled": False,
                "realm": "EXAMPLE.INVALID",
                "kdc_hosts": ["auth.example.invalid"],
                "allowed_admin_groups": ["home-center-admins@example.invalid"],
                "timeout_seconds": 5,
                "cache_root": str(root / "ad-auth"),
            },
            "external_access": {
                "enabled": False,
                "mode": "trusted-reverse-proxy",
                "public_hostname": None,
                "trusted_proxy_addresses": [],
            },
        }

    def test_shipped_configs_use_v5_peer_collections(self) -> None:
        for name in ("config.node-a.example.json", "config.node-b.example.json"):
            with self.subTest(name=name):
                value = json.loads((ROOT / "deploy/config" / name).read_text(encoding="utf-8"))
                self.assertEqual(value["schema"], "home-center.config.v5")
                self.assertNotIn("peer", value)
                self.assertIsInstance(value["peers"], list)
                self.assertEqual(len(value["peers"]), 1)

    def test_runtime_config_surface_is_collection_based(self) -> None:
        fields = Config.__dataclass_fields__
        self.assertIn("peers", fields)
        self.assertNotIn("peer", fields)

    def test_ipv6_peer_url_requires_bracketed_literal(self) -> None:
        peer = _peer(
            {
                "node_id": "node-v6",
                "name": "node-v6",
                "address": "2001:db8::10",
                "url": "https://[2001:db8::10]:9443",
                "certificate_name": "node-v6.example.invalid",
            },
            9443,
        )
        self.assertEqual(peer.address, "2001:db8::10")
        with self.assertRaisesRegex(ValueError, "peer URL"):
            _peer(
                {
                    "node_id": "node-v6",
                    "name": "node-v6",
                    "address": "2001:db8::10",
                    "url": "https://2001:db8::10:9443",
                    "certificate_name": "node-v6.example.invalid",
                },
                9443,
            )

    def test_v5_rejects_legacy_peer_field_instead_of_ignoring_it(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            config = self._runtime_config(root)
            config["peer"] = {
                "node_id": "node-b",
                "name": "node-b",
                "address": "192.0.2.11",
                "url": "https://192.0.2.11:9443",
                "certificate_name": "node-b.example.invalid",
            }
            path = root / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            path.chmod(0o640)
            with self.assertRaisesRegex(ValueError, "legacy peer"):
                load_config(path)

    def test_v5_allows_explicit_empty_peer_collection_for_single_node(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            path = root / "config.json"
            path.write_text(json.dumps(self._runtime_config(root)), encoding="utf-8")
            path.chmod(0o640)
            config = load_config(path)
            self.assertEqual(config.peers, ())


if __name__ == "__main__":
    unittest.main()
