from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from home_center.api_v2 import RuntimeRequestHandlerV2
from home_center.auth import SessionManager
from home_center.config import Config, Peer
from home_center.local_admin_auth import (
    CREDENTIAL_SCHEMA,
    KDF_DKLEN,
    KDF_MAXMEM,
    KDF_N,
    KDF_P,
    KDF_R,
    SALT_BYTES,
)
from home_center.runtime import Runtime
from home_center.server import HomeCenterServer


PASSWORD = "intent api fixture password 123"


def _credential() -> dict[str, object]:
    salt = b"i" * SALT_BYTES
    verifier = hashlib.scrypt(
        PASSWORD.encode("utf-8"),
        salt=salt,
        n=KDF_N,
        r=KDF_R,
        p=KDF_P,
        maxmem=KDF_MAXMEM,
        dklen=KDF_DKLEN,
    )
    return {
        "schema": CREDENTIAL_SCHEMA,
        "username": "admin",
        "kdf": "scrypt",
        "n": KDF_N,
        "r": KDF_R,
        "p": KDF_P,
        "dklen": KDF_DKLEN,
        "salt_b64": base64.b64encode(salt).decode("ascii"),
        "verifier_b64": base64.b64encode(verifier).decode("ascii"),
    }


def _capability(*, node_id: str, name: str, role: str, address: str, cpu_count: int, memory_bytes: int) -> dict[str, object]:
    return {
        "schema": "home-center.node-capability.v1",
        "observed_at": "2026-09-07T13:00:00Z",
        "node": {
            "id": node_id,
            "name": name,
            "role": role,
            "address": address,
            "machine_identity_hash": "a" * 24,
        },
        "operating_system": {"id": "ubuntu", "version": "26.04", "kernel": "test", "architecture": "x86_64"},
        "hardware": {"cpu_count": cpu_count, "memory_bytes": memory_bytes},
        "storage": {"root": {"total_bytes": 1000, "used_bytes": 400, "free_bytes": 500}},
        "services": {},
        "capabilities": ["inventory.v1", "health.v1"],
    }


class IntentApi0130Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        web = root / "web"
        web.mkdir()
        (web / "index.html").write_text("<!doctype html><title>intent-test</title>", encoding="utf-8")
        secrets = root / "secrets"
        secrets.mkdir()
        os.chmod(secrets, 0o750)
        for name, payload in (("session.key", b"s" * 32), ("audit.key", b"a" * 32), ("node.key", b"unused")):
            path = secrets / name
            path.write_bytes(payload)
            os.chmod(path, 0o600)
        local_admin = secrets / "local-admin.json"
        local_admin.write_text(json.dumps(_credential(), sort_keys=True), encoding="utf-8")
        os.chmod(local_admin, 0o640)
        (secrets / "node.crt").write_text("unused", encoding="utf-8")
        (secrets / "ca.crt").write_text("unused-peer-ca", encoding="utf-8")
        web_ca = root / "web-ca.crt"
        web_ca.write_text("unused-web-ca", encoding="utf-8")
        profile = root / "profile.json"
        profile.write_text(
            (Path(__file__).resolve().parents[1] / "deploy/profiles/hm-dm-two-node.v1.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        config = Config(
            cluster_id="hm-dm-production",
            node_id="hm-dm-dc01",
            node_name="dc01",
            role="leader",
            management_address="127.0.0.1",
            web_port=8443,
            peer_port=9443,
            state_db=root / "state.sqlite3",
            backup_dir=root / "backups",
            web_root=web,
            local_admin_credentials_file=local_admin,
            session_key_file=secrets / "session.key",
            audit_key_file=secrets / "audit.key",
            tls_certificate=secrets / "node.crt",
            tls_private_key=secrets / "node.key",
            cluster_ca=secrets / "ca.crt",
            web_ca=web_ca,
            deployment_profile=profile,
            peer=Peer(
                node_id="hm-dm-dc02",
                name="dc02",
                address="127.0.0.2",
                url="https://127.0.0.2:9443",
                certificate_name="home-center-dc02",
            ),
            reconcile_interval_seconds=15,
            peer_timeout_seconds=1,
        )
        self.runtime = Runtime(config, local_admin_expected_uid=os.geteuid())
        self.server = HomeCenterServer(("127.0.0.1", 0), RuntimeRequestHandlerV2, self.runtime)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        token, expires = self.runtime.sessions.new_session("local-admin:admin")
        self.cookie = SessionManager.cookie(token, expires - int(time.time())).split(";", 1)[0]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.runtime.store.close()
        self.tmp.cleanup()

    def _request(self, body: dict[str, object], *, authenticated: bool = True, extra_headers: dict[str, str] | None = None):
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if authenticated:
            headers["Cookie"] = self.cookie
        headers.update(extra_headers or {})
        return urllib.request.urlopen(
            urllib.request.Request(
                self.base + "/api/v1/intents/plan",
                method="POST",
                headers=headers,
                data=json.dumps(body).encode("utf-8"),
            ),
            timeout=3,
        )

    def _get(self, path: str, *, authenticated: bool = True):
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Cookie"] = self.cookie
        return urllib.request.urlopen(
            urllib.request.Request(self.base + path, method="GET", headers=headers),
            timeout=3,
        )

    @staticmethod
    def _intent(kind: str, target: str, parameters: dict[str, object], *, actor: str = "local-admin:admin", mode: str = "plan") -> dict[str, object]:
        return {
            "schema": "home-center.intent-request.v1",
            "intent_id": str(uuid.uuid4()),
            "idempotency_key": "intent-api-0001",
            "correlation_id": "intent-api-0001",
            "actor": actor,
            "reason": "validate authenticated intent planning",
            "kind": kind,
            "target_id": target,
            "parameters": parameters,
            "mode": mode,
        }

    def test_authenticated_storage_intent_returns_plan_only_result(self) -> None:
        body = self._intent(
            "storage.share.create",
            "family-share",
            {"capacity_gib": 5120, "protocol": "smb", "high_availability": True, "backup_enabled": True},
        )
        with self._request(body) as response:
            value = json.load(response)
        self.assertEqual(value["schema"], "home-center.intent-plan.v1")
        self.assertEqual(value["state"], "planned")
        self.assertFalse(value["production_execution_enabled"])
        self.assertEqual([step["sequence"] for step in value["steps"]], [1, 2, 3])

    def test_unsafe_drain_is_valid_blocked_plan_with_no_steps(self) -> None:
        body = self._intent(
            "node.drain",
            "node-a",
            {"quorum_safe": False, "mandatory_services_safe": True},
        )
        with self._request(body) as response:
            value = json.load(response)
        self.assertEqual(value["state"], "blocked")
        self.assertEqual(value["blockers"], ["quorum_not_safe"])
        self.assertEqual(value["steps"], [])

    def test_authentication_and_actor_binding_fail_closed(self) -> None:
        body = self._intent(
            "module.install",
            "storage-module",
            {"module_id": "storage", "version": "1.0.0", "permissions_acknowledged": True},
        )
        with self.assertRaises(urllib.error.HTTPError) as unauthenticated:
            self._request(body, authenticated=False)
        self.assertEqual(unauthenticated.exception.code, 401)

        body["actor"] = "local-admin:other"
        with self.assertRaises(urllib.error.HTTPError) as spoofed:
            self._request(body)
        self.assertEqual(spoofed.exception.code, 403)
        error = json.loads(spoofed.exception.read())
        self.assertEqual(error["error"]["code"], "intent_permission_denied")

    def test_execution_mode_and_secret_parameters_are_rejected_without_echo(self) -> None:
        execute = self._intent(
            "virtualization.workload.create",
            "minecraft-server",
            {"runtime": "lxc", "vcpu": 4, "memory_mib": 8192, "disk_gib": 100, "high_availability": False},
            mode="execute",
        )
        with self.assertRaises(urllib.error.HTTPError) as invalid_mode:
            self._request(execute)
        self.assertEqual(invalid_mode.exception.code, 400)

        secret_value = "must-never-be-echoed"
        secret = self._intent(
            "module.install",
            "storage-module",
            {
                "module_id": "storage",
                "version": "1.0.0",
                "permissions_acknowledged": True,
                "token": secret_value,
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as invalid_secret:
            self._request(secret)
        self.assertEqual(invalid_secret.exception.code, 400)
        response_bytes = invalid_secret.exception.read()
        self.assertNotIn(secret_value.encode("utf-8"), response_bytes)
        self.assertNotIn(secret_value, json.dumps(self.runtime.store.audit_events(100), sort_keys=True))

    def test_cross_origin_is_rejected_before_planning(self) -> None:
        body = self._intent(
            "storage.share.create",
            "family-share",
            {"capacity_gib": 10, "protocol": "smb", "high_availability": False, "backup_enabled": False},
        )
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            self._request(body, extra_headers={"Origin": "https://attacker.invalid", "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(rejected.exception.code, 403)
        error = json.loads(rejected.exception.read())
        self.assertEqual(error["error"]["code"], "cross_origin_request_rejected")

    def test_resource_snapshot_requires_auth_and_returns_only_trusted_capacity(self) -> None:
        self.runtime.store.upsert_node(
            _capability(
                node_id="hm-dm-dc01",
                name="dc01",
                role="leader",
                address="127.0.0.1",
                cpu_count=4,
                memory_bytes=8 * 1024**3,
            ),
            "ready",
        )
        self.runtime.store.upsert_node(
            _capability(
                node_id="hm-dm-dc02",
                name="dc02",
                role="standby",
                address="127.0.0.2",
                cpu_count=8,
                memory_bytes=16 * 1024**3,
            ),
            "ready",
        )
        with self.assertRaises(urllib.error.HTTPError) as unauthenticated:
            self._get("/api/v1/resources", authenticated=False)
        self.assertEqual(unauthenticated.exception.code, 401)

        with self._get("/api/v1/resources") as response:
            value = json.load(response)
        self.assertEqual(value["schema"], "home-center.resource-snapshot.v1")
        self.assertTrue(value["planning_ready"])
        self.assertFalse(value["production_mutation_enabled"])
        self.assertEqual(value["totals"]["cpu_count"], 12)
        self.assertEqual(value["totals"]["memory_bytes"], 24 * 1024**3)
        encoded = json.dumps(value, sort_keys=True)
        self.assertNotIn("127.0.0.1", encoded)
        self.assertNotIn("machine_identity_hash", encoded)


if __name__ == "__main__":
    unittest.main()
