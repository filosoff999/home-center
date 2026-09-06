from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center.api import RuntimeRequestHandler  # noqa: E402
from home_center.config import Config, Peer  # noqa: E402
from home_center.runtime import Runtime  # noqa: E402
from home_center.server import HomeCenterServer  # noqa: E402


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        web = root / "web"; web.mkdir(); (web / "index.html").write_text("<!doctype html><title>test</title>", encoding="utf-8")
        secrets = root / "secrets"; secrets.mkdir()
        for name, value in (("admin.token", b"t" * 64), ("session.key", b"s" * 32), ("audit.key", b"a" * 32), ("node.key", b"not-used")):
            path = secrets / name; path.write_bytes(value); os.chmod(path, 0o600)
        for name in ("node.crt", "ca.crt"):
            (secrets / name).write_text("not-used", encoding="utf-8")
        profile = root / "profile.json"; profile.write_text((ROOT / "deploy/profiles/hm-dm-two-node.v1.json").read_text(encoding="utf-8"), encoding="utf-8")
        cfg = Config(cluster_id="hm-dm-production", node_id="hm-dm-dc01", node_name="dc01", role="leader", management_address="127.0.0.1", web_port=8443, peer_port=9443, state_db=root / "state.sqlite3", backup_dir=root / "backups", web_root=web, admin_token_file=secrets / "admin.token", session_key_file=secrets / "session.key", audit_key_file=secrets / "audit.key", tls_certificate=secrets / "node.crt", tls_private_key=secrets / "node.key", cluster_ca=secrets / "ca.crt", deployment_profile=profile, peer=Peer(node_id="hm-dm-dc02", name="dc02", address="127.0.0.2", url="https://127.0.0.2:9443", certificate_name="home-center-dc02"), reconcile_interval_seconds=15, peer_timeout_seconds=1)
        self.runtime = Runtime(cfg)
        self.action_calls = 0

        def action_runner(args, **kwargs):
            self.action_calls += 1
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="LoadState=loaded\nActiveState=active\nSubState=running\nUnitFileState=enabled\n",
                stderr="",
            )

        self.runtime.actions._runner = action_runner
        self.server = HomeCenterServer(("127.0.0.1", 0), RuntimeRequestHandler, self.runtime)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown(); self.server.server_close(); self.runtime.store.close(); self.tmp.cleanup()

    def request(self, path: str, *, token: bool = False, method: str = "GET", body: dict | None = None):
        headers = {"Accept": "application/json"}
        data = None
        if token: headers["Authorization"] = "Bearer " + "t" * 64
        if body is not None: headers["Content-Type"] = "application/json"; data = json.dumps(body).encode()
        return urllib.request.urlopen(urllib.request.Request(self.base + path, headers=headers, method=method, data=data), timeout=3)

    def test_public_health_and_security_headers(self) -> None:
        with self.request("/healthz") as response:
            value = json.load(response)
            self.assertEqual(value["status"], "ok")
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_api_is_fail_closed_without_session(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/v1/nodes")
        self.assertEqual(caught.exception.code, 401)

    def test_bearer_token_allows_versioned_read(self) -> None:
        with self.request("/api/v1/nodes", token=True) as response:
            self.assertEqual(json.load(response)["schema"], "home-center.nodes.v1")

    def test_login_does_not_echo_token_and_sets_secure_cookie(self) -> None:
        with self.request("/api/v1/session", method="POST", body={"token": "t" * 64}) as response:
            payload = response.read().decode()
            self.assertNotIn("t" * 32, payload)
            cookie = response.headers["Set-Cookie"]
            self.assertIn("Secure", cookie); self.assertIn("HttpOnly", cookie); self.assertIn("SameSite=Strict", cookie)

    def test_typed_action_api_is_authenticated_persisted_and_idempotent(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/v1/actions")
        self.assertEqual(caught.exception.code, 401)

        with self.request("/api/v1/actions", token=True) as response:
            self.assertEqual(json.load(response)["schema"], "home-center.action-registry.v1")

        body = {
            "schema": "home-center.action-request.v1",
            "idempotency_key": "api-request-0001",
            "target_node_id": "hm-dm-dc01",
            "reason": "verify owned service",
            "input": {"service": "home-center.service"},
        }
        with self.request(
            "/api/v1/actions/service.state.read.v1",
            token=True,
            method="POST",
            body=body,
        ) as response:
            first = json.load(response)
        self.assertEqual(first["job"]["state"], "succeeded")
        self.assertFalse(first["idempotent_replay"])

        with self.request(
            "/api/v1/actions/service.state.read.v1",
            token=True,
            method="POST",
            body=body,
        ) as response:
            second = json.load(response)
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(second["job"]["job_id"], first["job"]["job_id"])
        self.assertEqual(self.action_calls, 1)

        with self.request("/api/v1/jobs", token=True) as response:
            jobs = json.load(response)["items"]
        self.assertEqual(jobs[0]["job_id"], first["job"]["job_id"])

    def test_action_injection_unknown_action_and_conflict_are_denied(self) -> None:
        body = {
            "schema": "home-center.action-request.v1",
            "idempotency_key": "api-request-0002",
            "target_node_id": "hm-dm-dc01",
            "reason": "verify owned service",
            "input": {"service": "home-center.service;reboot"},
        }
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(
                "/api/v1/actions/service.state.read.v1",
                token=True,
                method="POST",
                body=body,
            )
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.action_calls, 0)

        body["input"]["service"] = "home-center.service"
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(
                "/api/v1/actions/unknown.action.v1",
                token=True,
                method="POST",
                body=body,
            )
        self.assertEqual(caught.exception.code, 404)
        self.assertEqual(self.action_calls, 0)

    def test_unimplemented_mutation_is_denied(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/v1/nodes", token=True, method="DELETE")
        self.assertEqual(caught.exception.code, 405)


if __name__ == "__main__":
    unittest.main()
