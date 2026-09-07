from __future__ import annotations

import base64
import hashlib
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
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center.ad_auth import AdAuthError  # noqa: E402
from home_center.api_v2 import RuntimeRequestHandlerV2  # noqa: E402
from home_center.config import Config, Peer  # noqa: E402
from home_center.external_access import ExternalAccessPolicy, ExternalRequestRateLimiter  # noqa: E402
from home_center.local_admin_auth import (  # noqa: E402
    CREDENTIAL_SCHEMA,
    KDF_DKLEN,
    KDF_MAXMEM,
    KDF_N,
    KDF_P,
    KDF_R,
    SALT_BYTES,
)
from home_center.local_admin_rotation import LocalAdminCredentialRotator, LocalAdminRotationError  # noqa: E402
from home_center.runtime import Runtime  # noqa: E402
from home_center.server import HomeCenterServer  # noqa: E402


USERNAME = "admin"
PASSWORD = "correct horse battery staple"


def local_admin_document() -> dict[str, object]:
    salt = b"s" * SALT_BYTES
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
        "username": USERNAME,
        "kdf": "scrypt",
        "n": KDF_N,
        "r": KDF_R,
        "p": KDF_P,
        "dklen": KDF_DKLEN,
        "salt_b64": base64.b64encode(salt).decode("ascii"),
        "verifier_b64": base64.b64encode(verifier).decode("ascii"),
    }


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        web = root / "web"
        web.mkdir()
        (web / "index.html").write_text("<!doctype html><title>test</title>", encoding="utf-8")
        secrets = root / "secrets"
        secrets.mkdir()
        os.chmod(secrets, 0o750)
        for name, value in (("session.key", b"s" * 32), ("audit.key", b"a" * 32), ("node.key", b"not-used")):
            path = secrets / name
            path.write_bytes(value)
            os.chmod(path, 0o600)
        local_admin = secrets / "local-admin.json"
        local_admin.write_text(json.dumps(local_admin_document(), sort_keys=True), encoding="utf-8")
        os.chmod(local_admin, 0o640)
        (secrets / "node.crt").write_text("not-used", encoding="utf-8")
        (secrets / "ca.crt").write_text("peer-ca-must-not-be-public", encoding="utf-8")
        web_ca = root / "web-ca.crt"
        web_ca.write_text("not-used", encoding="utf-8")
        profile = root / "profile.json"
        profile.write_text(
            (ROOT / "deploy/profiles/hm-dm-two-node.v1.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        cfg = Config(
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
        self.runtime = Runtime(cfg, local_admin_expected_uid=os.geteuid())
        local_rotator = LocalAdminCredentialRotator(
            local_admin,
            expected_uid=os.geteuid(),
            expected_gid=os.getegid(),
            expected_mode=0o640,
            expected_directory_uid=os.geteuid(),
            expected_directory_gid=os.getegid(),
            expected_directory_mode=0o750,
        )

        def rotate_fixture(username: str, current_password: str, new_password: str) -> dict[str, object]:
            try:
                local_rotator.rotate(username, current_password, new_password)
            except LocalAdminRotationError as exc:
                return {"status": "rejected", "reason": exc.code}
            return {"status": "succeeded", "reason": None}

        self.runtime.local_admin_password_rotator = rotate_fixture
        self.action_calls = 0
        self._session_cookie: str | None = None

        def action_runner(args, **kwargs):
            self.action_calls += 1
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="LoadState=loaded\nActiveState=active\nSubState=running\nUnitFileState=enabled\n",
                stderr="",
            )

        self.runtime.actions._runner = action_runner
        self.server = HomeCenterServer(("127.0.0.1", 0), RuntimeRequestHandlerV2, self.runtime)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.runtime.store.close()
        self.tmp.cleanup()

    def request(
        self,
        path: str,
        *,
        authenticated: bool = False,
        bearer: bool = False,
        method: str = "GET",
        body: dict | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        headers = {"Accept": "application/json"}
        headers.update(extra_headers or {})
        data = None
        if authenticated:
            headers["Cookie"] = self.session_cookie()
        if bearer:
            headers["Authorization"] = "Bearer " + "t" * 64
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        return urllib.request.urlopen(
            urllib.request.Request(self.base + path, headers=headers, method=method, data=data), timeout=3
        )

    def session_cookie(self) -> str:
        if self._session_cookie is None:
            with self.request(
                "/api/v1/session",
                method="POST",
                body={"provider": "local", "username": USERNAME, "password": PASSWORD},
            ) as response:
                cookie = response.headers["Set-Cookie"]
            self._session_cookie = cookie.split(";", 1)[0]
        return self._session_cookie

    @staticmethod
    def external_headers() -> dict[str, str]:
        return {
            "X-Forwarded-For": "203.0.113.18",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "home.example.net",
        }

    def test_public_health_and_security_headers(self) -> None:
        with self.request("/healthz") as response:
            value = json.load(response)
            self.assertEqual(value["status"], "ok")
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_auth_provider_catalog_is_public_and_disables_ad_by_default(self) -> None:
        with self.request("/api/v1/auth/providers") as response:
            value = json.load(response)
        self.assertEqual(value["schema"], "home-center.auth-providers.v1")
        self.assertEqual(value["providers"], [{"enabled": True, "id": "local"}, {"enabled": False, "id": "ad"}])

    def test_cross_origin_login_and_logout_are_rejected_before_authentication(self) -> None:
        hostile_headers = {"Origin": "https://attacker.invalid", "Sec-Fetch-Site": "cross-site"}
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(
                "/api/v1/session",
                method="POST",
                body={"provider": "local", "username": USERNAME, "password": PASSWORD},
                extra_headers=hostile_headers,
            )
        self.assertEqual(caught.exception.code, 403)
        payload = caught.exception.read().decode("utf-8")
        self.assertIn("cross_origin_request_rejected", payload)
        self.assertNotIn(PASSWORD, payload)

        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(
                "/api/v1/session/logout",
                authenticated=True,
                method="POST",
                extra_headers=hostile_headers,
            )
        self.assertEqual(caught.exception.code, 403)
        with self.request("/api/v1/session", authenticated=True) as response:
            self.assertTrue(json.load(response)["authenticated"])

    def test_same_origin_browser_login_and_authenticated_logout(self) -> None:
        origin = f"https://127.0.0.1:{self.server.server_port}"
        headers = {"Origin": origin, "Sec-Fetch-Site": "same-origin"}
        with self.request(
            "/api/v1/session",
            method="POST",
            body={"provider": "local", "username": USERNAME, "password": PASSWORD},
            extra_headers=headers,
        ) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        self._session_cookie = cookie
        with self.request(
            "/api/v1/session/logout", authenticated=True, method="POST", extra_headers=headers
        ) as response:
            self.assertFalse(json.load(response)["authenticated"])
            self.assertIn("Max-Age=0", response.headers["Set-Cookie"])

    def test_api_is_fail_closed_without_session(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/v1/nodes")
        self.assertEqual(caught.exception.code, 401)

    def test_bearer_bootstrap_token_cannot_bypass_local_login(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/v1/nodes", bearer=True)
        self.assertEqual(caught.exception.code, 401)

    def test_local_login_does_not_echo_password_and_sets_secure_cookie(self) -> None:
        with self.request(
            "/api/v1/session",
            method="POST",
            body={"provider": "local", "username": "Admin", "password": PASSWORD},
        ) as response:
            payload = response.read().decode()
            self.assertNotIn(PASSWORD, payload)
            self.assertIn('"actor":"local-admin:admin"', payload)
            cookie = response.headers["Set-Cookie"]
            self.assertIn("Secure", cookie)
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Strict", cookie)

    def test_invalid_login_is_generic_and_legacy_token_shape_is_rejected(self) -> None:
        for body in (
            {"provider": "local", "username": USERNAME, "password": "wrong-password-value"},
            {"provider": "unknown", "username": USERNAME, "password": PASSWORD},
            {"token": "t" * 64},
        ):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/api/v1/session", method="POST", body=body)
            self.assertEqual(caught.exception.code, 401)
            payload = caught.exception.read().decode("utf-8")
            self.assertIn("invalid_credentials", payload)
            self.assertNotIn("wrong-password-value", payload)
            self.assertNotIn("t" * 32, payload)

    def test_local_admin_password_change_rotates_credential_and_audits_no_secret(self) -> None:
        new_password = "new secure password 42"
        before = json.loads(self.runtime.config.local_admin_credentials_file.read_text(encoding="utf-8"))
        with self.request(
            "/api/v1/auth/local-admin/password/change",
            authenticated=True,
            method="POST",
            body={
                "schema": "home-center.local-admin-password-change.v1",
                "current_password": PASSWORD,
                "new_password": new_password,
            },
        ) as response:
            value = json.load(response)
        self.assertEqual(value["status"], "changed")
        after = json.loads(self.runtime.config.local_admin_credentials_file.read_text(encoding="utf-8"))
        self.assertNotEqual(before["salt_b64"], after["salt_b64"])
        self.assertNotEqual(before["verifier_b64"], after["verifier_b64"])
        self._session_cookie = None
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(
                "/api/v1/session",
                method="POST",
                body={"provider": "local", "username": USERNAME, "password": PASSWORD},
            )
        self.assertEqual(caught.exception.code, 401)
        with self.request(
            "/api/v1/session",
            method="POST",
            body={"provider": "local", "username": USERNAME, "password": new_password},
        ) as response:
            self.assertEqual(json.load(response)["actor"], "local-admin:admin")
        audit = next(
            event
            for event in self.runtime.store.audit_events(20)
            if event["action"] == "local-admin.password.change"
        )
        self.assertEqual(audit["outcome"], "accepted")
        serialized = json.dumps(audit)
        self.assertNotIn(PASSWORD, serialized)
        self.assertNotIn(new_password, serialized)
        self.assertNotIn("salt_b64", serialized)
        self.assertNotIn("verifier_b64", serialized)

    def test_password_change_policy_and_wrong_current_password_fail_closed(self) -> None:
        cases = (
            ("wrong current 1", "new secure password 42", 403, "current_password_invalid"),
            (PASSWORD, "short1", 400, "password_too_short"),
            (PASSWORD, "12345678", 400, "password_letter_required"),
            (PASSWORD, "onlyletters", 400, "password_digit_required"),
        )
        for current_password, new_password, status, code in cases:
            with self.subTest(code=code), self.assertRaises(urllib.error.HTTPError) as caught:
                self.request(
                    "/api/v1/auth/local-admin/password/change",
                    authenticated=True,
                    method="POST",
                    body={
                        "schema": "home-center.local-admin-password-change.v1",
                        "current_password": current_password,
                        "new_password": new_password,
                    },
                )
            self.assertEqual(caught.exception.code, status)
            payload = caught.exception.read().decode("utf-8")
            self.assertIn(code, payload)
            self.assertNotIn(current_password, payload)
            self.assertNotIn(new_password, payload)
            self.assertTrue(self.runtime.local_admin.verify(USERNAME, PASSWORD))

    def test_password_change_is_authenticated_and_cross_origin_protected(self) -> None:
        body = {
            "schema": "home-center.local-admin-password-change.v1",
            "current_password": PASSWORD,
            "new_password": "new secure password 42",
        }
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/v1/auth/local-admin/password/change", method="POST", body=body)
        self.assertEqual(caught.exception.code, 401)
        original_rotator = self.runtime.local_admin_password_rotator
        self.runtime.local_admin_password_rotator = Mock(wraps=original_rotator)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(
                "/api/v1/auth/local-admin/password/change",
                authenticated=True,
                method="POST",
                body=body,
                extra_headers={"Origin": "https://attacker.invalid", "Sec-Fetch-Site": "cross-site"},
            )
        self.assertEqual(caught.exception.code, 403)
        self.runtime.local_admin_password_rotator.assert_not_called()


    def test_optional_ad_login_success_and_failure_are_generic(self) -> None:
        class FixtureAd:
            def __init__(self) -> None:
                self.unavailable = False

            def authenticate(self, username: str, password: str) -> str | None:
                self.assert_secret_absent = password not in username
                if self.unavailable:
                    raise AdAuthError("fixture")
                if username == "Pavel" and password == PASSWORD:
                    return "pavel@HM.DM"
                return None

        fixture = FixtureAd()
        self.runtime.ad_auth = fixture
        with self.request(
            "/api/v1/session",
            method="POST",
            body={"provider": "ad", "username": "Pavel", "password": PASSWORD},
        ) as response:
            payload = response.read().decode()
            self.assertIn('"actor":"ad-admin:pavel@HM.DM"', payload)
            self.assertNotIn(PASSWORD, payload)

        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(
                "/api/v1/session",
                method="POST",
                body={"provider": "ad", "username": "Pavel", "password": "wrong-password"},
            )
        self.assertEqual(caught.exception.code, 401)
        self.assertIn("invalid_credentials", caught.exception.read().decode())

        fixture.unavailable = True
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(
                "/api/v1/session",
                method="POST",
                body={"provider": "ad", "username": "Pavel", "password": PASSWORD},
            )
        self.assertEqual(caught.exception.code, 503)
        payload = caught.exception.read().decode()
        self.assertIn("authentication_unavailable", payload)
        self.assertNotIn("fixture", payload)

    def test_tls_trust_anchor_is_public_but_private_key_is_not_exposed(self) -> None:
        with patch("home_center.api_v2._validated_web_ca", return_value=b"not-used"):
            with self.request("/api/v1/tls/ca.crt") as response:
                data = response.read()
                self.assertEqual(data, b"not-used")
                self.assertNotIn(b"peer-ca", data)
                self.assertEqual(response.headers["Content-Type"], "application/x-pem-file")
                self.assertIn("attachment", response.headers["Content-Disposition"])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/v1/tls", authenticated=False)
        self.assertEqual(caught.exception.code, 401)

    def test_tls_trust_anchor_download_follows_separate_web_pki(self) -> None:
        web_ca = self.runtime.config.web_ca
        web_ca.write_bytes(b"browser-compatible-web-ca")
        with patch("home_center.api_v2._validated_web_ca", return_value=b"browser-compatible-web-ca"):
            with self.request("/api/v1/tls/ca.crt") as response:
                self.assertEqual(response.read(), b"browser-compatible-web-ca")
                self.assertIn("web-ca.crt", response.headers["Content-Disposition"])

    def test_invalid_web_ca_is_fail_closed(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/v1/tls/ca.crt")
        self.assertEqual(caught.exception.code, 503)

    def test_authenticated_tls_status_is_versioned_and_metadata_only(self) -> None:
        value = {
            "schema": "home-center.tls-status.v1",
            "node_id": "hm-dm-dc01",
            "web": {"fingerprint_sha256": "a" * 64, "chain_valid": True, "hostname_match": True},
            "candidate": {"certificate_present": False, "private_key_present": False, "complete": False, "partial": False},
            "renewal": {"due": False, "threshold_days": 30},
        }
        with patch("home_center.api_v2.tls_status", return_value=value):
            with self.request("/api/v1/tls", authenticated=True) as response:
                result = json.load(response)
        self.assertEqual(result, value)
        serialized = json.dumps(result)
        self.assertNotIn("PRIVATE KEY", serialized)
        self.assertNotIn("tls.key", serialized)

    def test_typed_action_api_is_authenticated_persisted_and_idempotent(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/v1/actions")
        self.assertEqual(caught.exception.code, 401)

        with self.request("/api/v1/actions", authenticated=True) as response:
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
            authenticated=True,
            method="POST",
            body=body,
        ) as response:
            first = json.load(response)
        self.assertEqual(first["job"]["state"], "succeeded")
        self.assertFalse(first["idempotent_replay"])

        with self.request(
            "/api/v1/actions/service.state.read.v1",
            authenticated=True,
            method="POST",
            body=body,
        ) as response:
            second = json.load(response)
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(second["job"]["job_id"], first["job"]["job_id"])
        self.assertEqual(self.action_calls, 1)

        with self.request("/api/v1/jobs", authenticated=True) as response:
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
                authenticated=True,
                method="POST",
                body=body,
            )
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.action_calls, 0)

        body["input"]["service"] = "home-center.service"
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(
                "/api/v1/actions/unknown.action.v1",
                authenticated=True,
                method="POST",
                body=body,
            )
        self.assertEqual(caught.exception.code, 404)
        self.assertEqual(self.action_calls, 0)

    def test_unimplemented_mutation_is_denied(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/v1/nodes", authenticated=True, method="DELETE")
        self.assertEqual(caught.exception.code, 405)

    def test_external_access_is_disabled_and_status_requires_authentication(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/external/healthz")
        self.assertEqual(caught.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/healthz", extra_headers=self.external_headers())
        self.assertEqual(caught.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/v1/external-access")
        self.assertEqual(caught.exception.code, 401)
        with self.request("/api/v1/external-access", authenticated=True) as response:
            value = json.load(response)
        self.assertFalse(value["configured_enabled"])
        self.assertFalse(value["effective_enabled"])
        self.assertEqual(value["gateway_configuration"], "operator-managed")

    def test_enabled_external_boundary_uses_public_origin_and_hides_internal_endpoints(self) -> None:
        self.runtime.external_access = ExternalAccessPolicy(True, "home.example.net", ("127.0.0.1",), True)
        forwarded = self.external_headers()
        for path in ("/healthz", "/readyz", "/api/v1/meta", "/api/v1/tls/ca.crt", "/internal/v1/node"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as caught:
                self.request(path, extra_headers=forwarded)
            self.assertEqual(caught.exception.code, 404)

        with patch.object(self.runtime, "ready", return_value=(True, [])):
            with self.request("/external/healthz", extra_headers=forwarded) as response:
                self.assertEqual(json.load(response), {"schema": "home-center.external-health.v1", "status": "ok"})
        with patch.object(self.runtime, "ready", return_value=(False, ["private-reason"])):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/external/healthz", extra_headers=forwarded)
            self.assertEqual(caught.exception.code, 503)
            self.assertEqual(
                json.load(caught.exception),
                {"schema": "home-center.external-health.v1", "status": "unavailable"},
            )

        browser_headers = {
            **forwarded,
            "Origin": "https://home.example.net",
            "Sec-Fetch-Site": "same-origin",
        }
        with self.request(
            "/api/v1/session",
            method="POST",
            body={"provider": "local", "username": USERNAME, "password": PASSWORD},
            extra_headers=browser_headers,
        ) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        with self.request(
            "/api/v1/external-access",
            extra_headers={**forwarded, "Cookie": cookie},
        ) as response:
            self.assertTrue(json.load(response)["effective_enabled"])
        login = next(event for event in self.runtime.store.audit_events(20) if event["action"] == "session.login")
        self.assertEqual(login["details"]["access_origin"], "external")
        self.assertEqual(login["details"]["remote_address"], "203.0.113.18")
        self.assertEqual(login["details"]["proxy_address"], "127.0.0.1")

    def test_external_forwarding_spoof_and_wrong_origin_fail_closed(self) -> None:
        self.runtime.external_access = ExternalAccessPolicy(True, "home.example.net", ("127.0.0.2",), True)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/", extra_headers=self.external_headers())
        self.assertEqual(caught.exception.code, 404)

        self.runtime.external_access = ExternalAccessPolicy(True, "home.example.net", ("127.0.0.1",), True)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(
                "/api/v1/session",
                method="POST",
                body={"provider": "local", "username": USERNAME, "password": PASSWORD},
                extra_headers={
                    **self.external_headers(),
                    "Origin": "https://attacker.invalid",
                    "Sec-Fetch-Site": "same-origin",
                },
            )
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(caught.exception.headers.get("Connection"), "close")

    def test_external_proxy_wide_rate_limit_is_not_bypassable_by_client_rotation(self) -> None:
        self.runtime.external_access = ExternalAccessPolicy(True, "home.example.net", ("127.0.0.1",), True)
        self.runtime.external_request_limiter = ExternalRequestRateLimiter(
            client_requests=2,
            proxy_requests=2,
            window_seconds=60,
        )
        with patch.object(self.runtime, "ready", return_value=(True, [])):
            with self.request("/external/healthz", extra_headers=self.external_headers()):
                pass
            rotated = {**self.external_headers(), "X-Forwarded-For": "198.51.100.7"}
            with self.request("/external/healthz", extra_headers=rotated):
                pass
            rotated_again = {**self.external_headers(), "X-Forwarded-For": "192.0.2.9"}
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/external/healthz", extra_headers=rotated_again)
        self.assertEqual(caught.exception.code, 429)
        self.assertEqual(caught.exception.headers["Retry-After"], "60")


if __name__ == "__main__":
    unittest.main()

