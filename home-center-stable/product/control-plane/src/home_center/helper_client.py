from __future__ import annotations

import json
import secrets
import socket
import time
from pathlib import Path
from typing import Any

ACTIVATION_HELPER_TIMEOUT_SECONDS = 360

HELPER_SOCKET = Path("/run/home-center-helper/helper.sock")
MAX_RESPONSE_BYTES = 64 * 1024
DEFAULT_HELPER_TIMEOUT_SECONDS = 15.0
TLS_HELPER_TIMEOUT_SECONDS = float(ACTIVATION_HELPER_TIMEOUT_SECONDS + 15)
TLS_RECONCILE_TIMEOUT_SECONDS = 75.0


class HelperClientError(RuntimeError):
    pass


def _exchange(request: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
    payload = json.dumps(request, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
    deadline = time.monotonic() + timeout_seconds

    def set_remaining_timeout(client: socket.socket) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HelperClientError("helper response timeout")
        client.settimeout(remaining)

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            set_remaining_timeout(client)
            client.connect(str(HELPER_SOCKET))
            set_remaining_timeout(client)
            client.sendall(payload)
            data = bytearray()
            while b"\n" not in data:
                set_remaining_timeout(client)
                chunk = client.recv(4096)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise HelperClientError("helper response too large")
    except OSError as exc:
        raise HelperClientError("helper unavailable") from exc
    if b"\n" not in data:
        raise HelperClientError("incomplete helper response")
    try:
        value = json.loads(bytes(data).split(b"\n", 1)[0])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HelperClientError("invalid helper response") from exc
    if not isinstance(value, dict):
        raise HelperClientError("invalid helper response")
    return value


def call_helper(action: str, *, timeout_seconds: float | None = None, request_prefix: str = "runtime") -> dict[str, Any]:
    if action not in {"helper.probe.v1", "tls.web.activate.v1", "tls.web.reconcile.v1"}:
        raise HelperClientError("helper action is not admitted by this client")
    if timeout_seconds is None:
        if action == "tls.web.activate.v1":
            timeout_seconds = TLS_HELPER_TIMEOUT_SECONDS
        elif action == "tls.web.reconcile.v1":
            timeout_seconds = TLS_RECONCILE_TIMEOUT_SECONDS
        else:
            timeout_seconds = DEFAULT_HELPER_TIMEOUT_SECONDS
    request = {
        "schema": "home-center.helper.request.v1",
        "request_id": f"{request_prefix}-{int(time.time() * 1000)}-{secrets.token_hex(4)}",
        "action": action,
        "params": {},
        "nonce": secrets.token_hex(16),
    }
    value = _exchange(request, timeout_seconds=timeout_seconds)
    if value.get("schema") != "home-center.helper.result.v1":
        raise HelperClientError("invalid helper response")
    if value.get("request_id") != request["request_id"] or value.get("action") != action:
        raise HelperClientError("helper response identity mismatch")
    return value


def rotate_local_admin_password(username: str, current_password: str, new_password: str) -> dict[str, Any]:
    """Send secrets only through the protected, non-persisted helper path."""

    request = {
        "schema": "home-center.helper.secret-request.v1",
        "request_id": f"local-admin-{int(time.time() * 1000)}-{secrets.token_hex(4)}",
        "action": "local-admin.password.rotate.v1",
        "params": {
            "username": username,
            "current_password": current_password,
            "new_password": new_password,
        },
        "nonce": secrets.token_hex(16),
    }
    value = _exchange(request, timeout_seconds=DEFAULT_HELPER_TIMEOUT_SECONDS)
    if value.get("schema") != "home-center.helper.secret-result.v1":
        raise HelperClientError("invalid secret helper response")
    if value.get("request_id") != request["request_id"] or value.get("action") != request["action"]:
        raise HelperClientError("secret helper response identity mismatch")
    return value
