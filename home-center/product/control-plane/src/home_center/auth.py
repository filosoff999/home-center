"""Bootstrap-token authentication and short-lived signed browser sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from http.cookies import SimpleCookie
from pathlib import Path

from .util import canonical_json


SESSION_COOKIE = "hc_session"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class SessionManager:
    def __init__(self, admin_token_file: Path, session_key_file: Path, lifetime_seconds: int = 8 * 3600) -> None:
        self._admin_token = admin_token_file.read_text(encoding="utf-8").strip()
        self._session_key = session_key_file.read_bytes()
        if len(self._admin_token) < 32 or len(self._session_key) < 32:
            raise ValueError("authentication secret is too short")
        self._lifetime = lifetime_seconds

    def verify_admin_token(self, candidate: str) -> bool:
        candidate_digest = hashlib.sha256(candidate.encode("utf-8")).digest()
        expected_digest = hashlib.sha256(self._admin_token.encode("utf-8")).digest()
        return hmac.compare_digest(candidate_digest, expected_digest)

    def new_session(self) -> tuple[str, int]:
        expires = int(time.time()) + self._lifetime
        payload = {"actor": "bootstrap-admin", "exp": expires, "nonce": secrets.token_hex(16), "v": 1}
        encoded = _b64(canonical_json(payload).encode("utf-8"))
        signature = _b64(hmac.new(self._session_key, encoded.encode("ascii"), hashlib.sha256).digest())
        return f"{encoded}.{signature}", expires

    def actor_from_headers(self, authorization: str | None, cookie_header: str | None) -> str | None:
        if authorization and authorization.startswith("Bearer "):
            if self.verify_admin_token(authorization.removeprefix("Bearer ").strip()):
                return "bootstrap-admin"
        if not cookie_header:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
            token = cookie[SESSION_COOKIE].value
            encoded, supplied = token.split(".", 1)
            expected = _b64(hmac.new(self._session_key, encoded.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(supplied, expected):
                return None
            payload = json.loads(_unb64(encoded))
            if payload.get("v") != 1 or int(payload.get("exp", 0)) < int(time.time()):
                return None
            actor = payload.get("actor")
            return actor if actor == "bootstrap-admin" else None
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            return None

    @staticmethod
    def cookie(token: str, max_age: int) -> str:
        return f"{SESSION_COOKIE}={token}; Path=/; Max-Age={max_age}; Secure; HttpOnly; SameSite=Strict"

    @staticmethod
    def expired_cookie() -> str:
        return f"{SESSION_COOKIE}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict"


class LoginRateLimiter:
    """Small in-memory fail-closed limiter; production identity replaces it later."""

    def __init__(self, attempts: int = 5, window_seconds: int = 60) -> None:
        self._attempts = attempts
        self._window = window_seconds
        self._entries: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, address: str) -> bool:
        now = time.monotonic()
        with self._lock:
            entries = self._entries[address]
            while entries and entries[0] < now - self._window:
                entries.popleft()
            if len(entries) >= self._attempts:
                return False
            entries.append(now)
            return True

    def clear(self, address: str) -> None:
        with self._lock:
            self._entries.pop(address, None)
