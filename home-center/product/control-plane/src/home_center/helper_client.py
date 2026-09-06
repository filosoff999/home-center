from __future__ import annotations

import json
import secrets
import socket
import time
from pathlib import Path
from typing import Any

HELPER_SOCKET = Path("/run/home-center-helper/helper.sock")
MAX_RESPONSE_BYTES = 64 * 1024


class HelperClientError(RuntimeError):
    pass


def call_helper(action: str, *, timeout_seconds: float = 75.0, request_prefix: str = "runtime") -> dict[str, Any]:
    if action not in {"helper.probe.v1", "tls.web.activate.v1"}:
        raise HelperClientError("helper action is not admitted by this client")
    request = {
        "schema": "home-center.helper.request.v1",
        "request_id": f"{request_prefix}-{int(time.time() * 1000)}-{secrets.token_hex(4)}",
        "action": action,
        "params": {},
        "nonce": secrets.token_hex(16),
    }
    payload = json.dumps(request, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout_seconds)
        client.connect(str(HELPER_SOCKET))
        client.sendall(payload)
        data = bytearray()
        while b"\n" not in data:
            chunk = client.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_RESPONSE_BYTES:
                raise HelperClientError("helper response too large")
    if b"\n" not in data:
        raise HelperClientError("incomplete helper response")
    value = json.loads(bytes(data).split(b"\n", 1)[0])
    if not isinstance(value, dict) or value.get("schema") != "home-center.helper.result.v1":
        raise HelperClientError("invalid helper response")
    if value.get("request_id") != request["request_id"] or value.get("action") != action:
        raise HelperClientError("helper response identity mismatch")
    return value
