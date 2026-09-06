from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "product/control-plane/src/home_center"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SECURITY_GATE_090_FAIL: {message}")


def main() -> None:
    policy = read(SRC / "external_access.py")
    api = read(SRC / "api.py")
    config = read(SRC / "config.py")
    runtime = read(SRC / "runtime.py")
    openapi = read(ROOT / "contracts/openapi/home-center.v1.openapi.json")
    dc01 = read(ROOT / "deploy/hm-dm/config.dc01.json")
    dc02 = read(ROOT / "deploy/hm-dm/config.dc02.json")

    require('CONFIG_SCHEMA = "home-center.config.v4"' in config, "config v4 is not exact")
    for value in (dc01, dc02):
        require('"schema": "home-center.config.v4"' in value, "deployment config is not v4")
        require('"enabled": false' in value, "external access is not disabled by default")
        require('"trusted_proxy_addresses": []' in value, "ambient trusted proxy is configured")

    for marker in (
        "untrusted_forwarded_headers",
        "forwarded_headers_missing",
        "forwarded_header_cardinality_rejected",
        "forwarded_header_unsupported",
        "forwarded_proto_rejected",
        "forwarded_host_rejected",
        "direct_external_peer_rejected",
        "ExternalRequestRateLimiter",
        "proxy_requests",
    ):
        require(marker in policy, f"external boundary marker missing: {marker}")
    for forbidden in ("subprocess", "socket.create_connection", "requests.", "urllib.request", "os.system"):
        require(forbidden not in policy, f"external policy gained mutation/network primitive: {forbidden}")

    for marker in (
        "self.runtime.external_access.classify",
        "self.runtime.external_request_limiter.allow",
        'path == "/external/healthz"',
        'path == "/api/v1/external-access"',
        '"access_origin": "external" if context.external else "lan"',
        'context.public_hostname if context.external else self.headers.get("Host")',
        'headers={"Retry-After": "60"}',
    ):
        require(marker in api, f"API external gate marker missing: {marker}")
    require("ExternalAccessPolicy" in runtime, "external policy is not composed into runtime")
    require("ExternalRequestRateLimiter" in runtime, "external rate limiter is not composed into runtime")
    require("external-health.v1.schema.json" in openapi, "minimal external health contract is absent")
    require("external-access-status.v1.schema.json" in openapi, "external status contract is absent")
    print("SECURITY_GATE_090=PASS")


if __name__ == "__main__":
    main()
