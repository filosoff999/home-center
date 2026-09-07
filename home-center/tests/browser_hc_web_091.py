#!/usr/bin/env python3
"""Real-Chrome acceptance for an immutable Home Center 0.9.1 candidate.

The harness intentionally does not import Home Center source code. It serves
the extracted release artifact over local TLS, supplies a bounded mock API, and
drives desktop plus Android-like Chrome through the raw W3C WebDriver protocol.
No third-party Python or JavaScript package is required.
"""

from __future__ import annotations

import argparse
import contextlib
import http.cookies
import http.server
import json
import mimetypes
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterator


VALID_USERNAME = "admin"
VALID_PASSWORD = "candidate-browser-fixture-password"
COOKIE_VALUE = "candidate-091-browser-session"
ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"


class Results:
    def __init__(self) -> None:
        self.failures: list[dict[str, str]] = []
        self.passes: list[str] = []

    def check(self, check_id: str, condition: bool, detail: str) -> None:
        if condition:
            self.passes.append(check_id)
            print(f"PASS {check_id}")
        else:
            self.failures.append({"check": check_id, "detail": detail})
            print(f"FAIL {check_id}: {detail}")


class CandidateMockHandler(http.server.BaseHTTPRequestHandler):
    server: "CandidateHttpsServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _is_authenticated(self) -> bool:
        cookies = http.cookies.SimpleCookie()
        cookies.load(self.headers.get("Cookie", ""))
        session = cookies.get("home_center_session")
        return session is not None and session.value == COOKIE_VALUE

    def _reply(
        self,
        status: int,
        body: bytes = b"",
        *,
        content_type: str = "application/json; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(
        self,
        status: int,
        value: object,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._reply(status, body, headers=headers)

    def _static(self, path: str) -> None:
        relative = "index.html" if path in ("/", "/index.html") else path.removeprefix("/static/")
        candidate = (self.server.web_root / relative).resolve()
        try:
            candidate.relative_to(self.server.web_root)
        except ValueError:
            self._json(404, {"error": {"code": "not_found"}})
            return
        if not candidate.is_file() or candidate.is_symlink():
            self._json(404, {"error": {"code": "not_found"}})
            return
        media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if media_type.startswith("text/") or media_type == "application/javascript":
            media_type += "; charset=utf-8"
        self._reply(200, candidate.read_bytes(), content_type=media_type)

    @staticmethod
    def _nodes() -> list[dict[str, object]]:
        now = "2026-09-06T21:00:00Z"
        nodes: list[dict[str, object]] = []
        for name, role, address in (
            ("dc01", "leader", "192.168.10.254"),
            ("dc02", "standby", "192.168.10.253"),
        ):
            nodes.append(
                {
                    "name": name,
                    "role": role,
                    "address": address,
                    "status": "ready",
                    "last_seen": now,
                    "capabilities": {
                        "node": {"machine_identity_hash": f"{name}-candidate-browser"},
                        "operating_system": {"id": "debian", "version": "13", "kernel": "6.12"},
                        "hardware": {"cpu_count": 4, "memory_bytes": 8589934592},
                        "storage": {"root": {"used_bytes": 10737418240, "total_bytes": 42949672960}},
                        "services": {"samba-ad-dc.service": "active", "home-center.service": "active"},
                    },
                }
            )
        return nodes

    def _api_value(self, raw_path: str) -> object | None:
        now = "2026-09-06T21:00:00Z"
        nodes = self._nodes()
        if raw_path == "/api/v1/overview":
            return {
                "observed_at": now,
                "cluster": {"status": "healthy", "expected_nodes": 2},
                "nodes": nodes,
                "jobs": [],
            }
        if raw_path == "/api/v1/deployment-profile":
            return {"profile_id": "hm-dm-two-node", "nodes": nodes}
        if raw_path == "/api/v1/backups":
            return {"items": []}
        if raw_path.startswith("/api/v1/audit?"):
            return {"items": []}
        if raw_path == "/api/v1/tls":
            return {
                "web": {
                    "chain_valid": True,
                    "hostname_match": True,
                    "profile_valid": True,
                    "san_policy_valid": True,
                    "mode": "separate-web-identity",
                    "days_remaining": 89,
                    "expected_hostname": "localhost",
                    "san_dns": ["localhost"],
                },
                "web_ca": {"profile_valid": True, "days_remaining": 1095},
                "renewal": {"due": False, "threshold_days": 30, "web_ca_threshold_days": 427},
                "candidate": {"complete": False, "partial": False, "invalid": False},
                "operational": {"healthy": True, "recovery_required": False, "state": "ready"},
            }
        return None

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/v1/auth/providers":
            self._json(200, {"schema": "home-center.auth-providers.v1", "providers": [{"id": "local", "enabled": True}, {"id": "ad", "enabled": False}]})
            return
        if path == "/api/v1/session":
            if self._is_authenticated():
                self._json(200, {"authenticated": True})
            else:
                self._json(401, {"error": {"code": "authentication_required"}})
            return
        if path.startswith("/api/v1/"):
            if not self._is_authenticated():
                self._json(401, {"error": {"code": "authentication_required"}})
                return
            value = self._api_value(self.path)
            if value is None:
                self._json(404, {"error": {"code": "not_found"}})
            else:
                self._json(200, value)
            return
        self._static(path)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 4096:
            self._json(413, {"error": {"code": "request_too_large"}})
            return
        raw = self.rfile.read(length)
        if path == "/api/v1/session":
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                value = None
            if value == {"provider": "local", "username": VALID_USERNAME, "password": VALID_PASSWORD}:
                self.server.successful_logins += 1
                self._reply(
                    204,
                    headers={
                        "Set-Cookie": (
                            f"home_center_session={COOKIE_VALUE}; Path=/; Secure; HttpOnly; SameSite=Strict"
                        )
                    },
                )
            else:
                self.server.invalid_logins += 1
                self._json(
                    401,
                    {
                        "error": {
                            "code": "invalid_credentials",
                            "message": "SENSITIVE CREDENTIAL BACKEND DETAIL MUST NEVER BE RENDERED",
                        }
                    },
                )
            return
        if path == "/api/v1/session/logout":
            self.server.logouts += 1
            self._reply(
                204,
                headers={
                    "Set-Cookie": (
                        "home_center_session=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict"
                    )
                },
            )
            return
        self._json(404, {"error": {"code": "not_found"}})


class CandidateHttpsServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, web_root: Path, certificate: Path, private_key: Path) -> None:
        super().__init__(("127.0.0.1", 0), CandidateMockHandler)
        self.web_root = web_root.resolve()
        self.invalid_logins = 0
        self.successful_logins = 0
        self.logouts = 0
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(certificate, private_key)
        self.socket = tls.wrap_socket(self.socket, server_side=True)


def certificate(directory: Path) -> tuple[Path, Path]:
    openssl = shutil.which("openssl")
    if openssl is None:
        raise RuntimeError("openssl is required for the ephemeral HTTPS fixture")
    cert = directory / "localhost.crt"
    key = directory / "localhost.key"
    command = [
        openssl,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-sha256",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=localhost",
        "-addext",
        "subjectAltName=DNS:localhost,IP:127.0.0.1",
        "-keyout",
        os.fspath(key),
        "-out",
        os.fspath(cert),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=20)
    if completed.returncode != 0:
        raise RuntimeError(f"openssl failed: {completed.stderr.strip()}")
    return cert, key


@contextlib.contextmanager
def serve(web_root: Path, directory: Path) -> Iterator[tuple[str, CandidateHttpsServer]]:
    cert, key = certificate(directory)
    server = CandidateHttpsServer(web_root, cert, key)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_port}/", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class Driver:
    def __init__(self, binary: str, directory: Path, *, mobile: bool) -> None:
        self.port = unused_port()
        suffix = "mobile" if mobile else "desktop"
        self.log_path = directory / f"chromedriver-{suffix}.log"
        self.log_handle = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [binary, f"--port={self.port}", "--allowed-ips=127.0.0.1"],
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
        )
        self.session_id: str | None = None
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    break
                try:
                    self._request("GET", "/status")
                    break
                except (OSError, urllib.error.URLError, json.JSONDecodeError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("chromedriver readiness timeout")
            if self.process.poll() is not None:
                raise RuntimeError("chromedriver exited before session creation")

            options: dict[str, Any] = {
                "args": [
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--ignore-certificate-errors",
                    "--allow-insecure-localhost",
                    "--hide-scrollbars",
                ]
            }
            if mobile:
                options["mobileEmulation"] = {
                    "deviceMetrics": {"width": 412, "height": 915, "pixelRatio": 2.625, "touch": True},
                    "userAgent": (
                        "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36"
                    ),
                }
            else:
                options["args"].append("--window-size=1440,1000")
            value = self._request(
                "POST",
                "/session",
                {
                    "capabilities": {
                        "alwaysMatch": {
                            "browserName": "chrome",
                            "acceptInsecureCerts": True,
                            "goog:chromeOptions": options,
                        }
                    }
                },
            )
            if not isinstance(value, dict) or not isinstance(value.get("sessionId"), str):
                raise RuntimeError("chromedriver returned no W3C session id")
            self.session_id = value["sessionId"]
        except BaseException:
            self.close()
            raise

    def _request(self, method: str, path: str, value: object | None = None) -> Any:
        data = None if value is None else json.dumps(value, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"webdriver HTTP {error.code}: {detail}") from error
        payload = json.loads(raw.decode("utf-8")) if raw else {"value": None}
        result = payload.get("value")
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError(f"webdriver {result.get('error')}: {result.get('message', '')}")
        return result

    def _session(self, suffix: str) -> str:
        if self.session_id is None:
            raise RuntimeError("webdriver session is not active")
        return f"/session/{self.session_id}{suffix}"

    def navigate(self, url: str) -> None:
        self._request("POST", self._session("/url"), {"url": url})

    def refresh(self) -> None:
        self._request("POST", self._session("/refresh"), {})

    def execute(self, script: str) -> Any:
        return self._request("POST", self._session("/execute/sync"), {"script": script, "args": []})

    def element(self, selector: str) -> str:
        value = self._request(
            "POST",
            self._session("/element"),
            {"using": "css selector", "value": selector},
        )
        if not isinstance(value, dict) or not isinstance(value.get(ELEMENT_KEY), str):
            raise RuntimeError(f"element not found: {selector}")
        return value[ELEMENT_KEY]

    def displayed(self, selector: str) -> bool:
        element = self.element(selector)
        return bool(self._request("GET", self._session(f"/element/{element}/displayed")))

    def clear(self, selector: str) -> None:
        element = self.element(selector)
        self._request("POST", self._session(f"/element/{element}/clear"), {})

    def type(self, selector: str, value: str) -> None:
        element = self.element(selector)
        self._request(
            "POST",
            self._session(f"/element/{element}/value"),
            {"text": value, "value": list(value)},
        )

    def click(self, selector: str) -> None:
        element = self.element(selector)
        self._request("POST", self._session(f"/element/{element}/click"), {})

    def close(self) -> None:
        try:
            if self.session_id is not None:
                self._request("DELETE", self._session(""))
        finally:
            self.session_id = None
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.log_handle.close()


def wait_for(predicate: Callable[[], bool], description: str, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except RuntimeError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"timeout waiting for {description}")


def overlay(driver: Driver) -> dict[str, object]:
    value = driver.execute(
        """
        const element = document.querySelector('#loginLayer');
        const rect = element.getBoundingClientRect();
        return {
          hidden: element.hidden,
          display: getComputedStyle(element).display,
          width: rect.width,
          height: rect.height,
        };
        """
    )
    if not isinstance(value, dict):
        raise RuntimeError("overlay state unavailable")
    return value


def browser_run(
    driver_binary: str,
    directory: Path,
    base_url: str,
    server: CandidateHttpsServer,
    results: Results,
    *,
    mobile: bool,
    expected_version: str,
    expected_revision: str,
) -> None:
    mode = "mobile" if mobile else "desktop"
    driver: Driver | None = None
    startup_error: BaseException | None = None
    for _attempt in range(3):
        try:
            driver = Driver(driver_binary, directory, mobile=mobile)
            break
        except (RuntimeError, urllib.error.URLError) as error:
            startup_error = error
            time.sleep(0.2)
    if driver is None:
        raise RuntimeError(f"chromedriver startup failed after three attempts: {startup_error}")
    try:
        driver.navigate(base_url)
        wait_for(lambda: driver.displayed("#loginLayer"), f"{mode} initial overlay")
        initial = overlay(driver)
        results.check(
            f"{mode}.initial_overlay",
            initial["hidden"] is False and initial["display"] == "grid",
            f"unexpected overlay state: {initial}",
        )
        release_identity = driver.execute(
            "return window.HOME_CENTER_RELEASE ? {"
            "version: window.HOME_CENTER_RELEASE.version, "
            "revision: window.HOME_CENTER_RELEASE.revision} : null"
        )
        results.check(
            f"{mode}.artifact_identity",
            isinstance(release_identity, dict)
            and release_identity.get("version") == expected_version
            and release_identity.get("revision") == expected_revision,
            f"unexpected artifact identity: {release_identity}",
        )

        invalid_before = server.invalid_logins
        driver.type("#usernameInput", VALID_USERNAME)
        driver.type("#passwordInput", "wrong-password")
        driver.click("#loginForm button[type='submit']")
        wait_for(lambda: server.invalid_logins > invalid_before, f"{mode} invalid login request")
        wait_for(
            lambda: not bool(driver.execute("return document.querySelector('#loginForm button').disabled")),
            f"{mode} invalid login completion",
        )
        error_text = str(driver.execute("return document.querySelector('#loginError').textContent")).strip()
        results.check(
            f"{mode}.invalid_generic_error_visible",
            bool(error_text)
            and len(error_text) <= 160
            and "SENSITIVE" not in error_text
            and "BACKEND" not in error_text,
            f"expected a non-empty bounded generic error, rendered {error_text!r}",
        )
        results.check(
            f"{mode}.invalid_error_bounded",
            len(error_text) <= 160 and "SENSITIVE" not in error_text and "BACKEND" not in error_text,
            f"unsafe error text: {error_text!r}",
        )

        driver.type("#passwordInput", VALID_PASSWORD)
        driver.click("#loginForm button[type='submit']")
        wait_for(lambda: overlay(driver)["display"] == "none", f"{mode} successful credential login")
        logged_in = overlay(driver)
        results.check(
            f"{mode}.actual_css_overlay_hidden",
            logged_in["hidden"] is True and logged_in["display"] == "none",
            f"successful login left overlay in layout: {logged_in}",
        )
        wait_for(
            lambda: driver.execute("return document.querySelector('#nodesMetric').textContent") == "2 / 2",
            f"{mode} usable overview",
        )
        results.check(f"{mode}.usable_ui", driver.displayed("#refreshButton"), "refresh control is not usable")

        driver.refresh()
        wait_for(lambda: overlay(driver)["display"] == "none", f"{mode} persisted session")
        wait_for(
            lambda: driver.execute("return document.querySelector('#nodesMetric').textContent") == "2 / 2",
            f"{mode} refreshed overview",
        )
        results.check(
            f"{mode}.reload_session",
            overlay(driver)["hidden"] is True,
            "cookie-backed session did not survive reload",
        )

        if mobile:
            driver.click("[data-view='nodes']")
            wait_for(
                lambda: driver.execute("return document.querySelectorAll('#nodeCards > .node-card').length") == 2,
                "mobile node cards",
            )
            layout = driver.execute(
                """
                const cards = [...document.querySelectorAll('#nodeCards > .node-card')];
                return cards.map((card) => {
                  const rect = card.getBoundingClientRect();
                  return {left: rect.left, top: rect.top, bottom: rect.bottom, width: rect.width};
                });
                """
            )
            vertical = (
                isinstance(layout, list)
                and len(layout) == 2
                and layout[1]["top"] > layout[0]["bottom"]
                and abs(layout[0]["left"] - layout[1]["left"]) < 1
                and abs(layout[0]["width"] - layout[1]["width"]) < 1
            )
            results.check("mobile.nodes_one_column", vertical, f"node card geometry: {layout}")

        logout_selector = "#mobileLogoutButton" if mobile else "#logoutButton"
        logout_visible = driver.displayed(logout_selector)
        results.check(
            f"{mode}.user_logout_visible",
            logout_visible,
            f"user logout control is not visible: {logout_selector}",
        )
        if logout_visible:
            logouts_before = server.logouts
            driver.click(logout_selector)
            wait_for(lambda: server.logouts > logouts_before, f"{mode} logout request")
            wait_for(lambda: driver.displayed("#loginLayer"), f"{mode} logout overlay")
            state = overlay(driver)
            results.check(
                f"{mode}.user_logout_restores_overlay",
                state["hidden"] is False and state["display"] == "grid",
                f"logout did not restore overlay: {state}",
            )
        else:
            results.check(
                f"{mode}.user_logout_restores_overlay",
                False,
                "no user-visible logout path exists",
            )
    finally:
        driver.close()


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-root", required=True, type=Path)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--chromedriver", default=shutil.which("chromedriver"))
    return parser.parse_args()


def main() -> int:
    args = arguments()
    if args.expected_version != "0.9.1":
        raise RuntimeError("browser gate only admits Home Center 0.9.1")
    if len(args.expected_revision) != 40 or any(
        character not in "0123456789abcdef" for character in args.expected_revision
    ):
        raise RuntimeError("expected revision must be an exact lowercase Git SHA-1")
    web_root = args.web_root.resolve()
    for asset in ("index.html", "app.css", "app.js", "release.js"):
        if not (web_root / asset).is_file():
            raise RuntimeError(f"extracted candidate web asset missing: {asset}")
    if args.chromedriver is None:
        raise RuntimeError("chromedriver is not available on PATH")

    results = Results()
    with tempfile.TemporaryDirectory(prefix="hc-candidate-browser-") as temporary:
        directory = Path(temporary)
        with serve(web_root, directory) as (base_url, server):
            for mobile in (False, True):
                browser_run(
                    args.chromedriver,
                    directory,
                    base_url,
                    server,
                    results,
                    mobile=mobile,
                    expected_version=args.expected_version,
                    expected_revision=args.expected_revision,
                )

    print(json.dumps({"passes": results.passes, "failures": results.failures}, ensure_ascii=False, indent=2))
    if results.failures:
        print("HC_091_BROWSER_ACCEPTANCE=FAIL")
        return 1
    print("HC_091_BROWSER_ACCEPTANCE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
