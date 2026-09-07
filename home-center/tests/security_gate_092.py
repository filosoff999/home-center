from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "product/control-plane/src/home_center"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SECURITY_GATE_092_FAIL: {message}")


def main() -> None:
    policy = read(SRC / "external_access.py")
    api = read(SRC / "api.py")
    config = read(SRC / "config.py")
    runtime = read(SRC / "runtime.py")
    acceptance = read(SRC / "release_candidate.py")
    acceptance_cli = read(ROOT / "deploy/runtime/release-candidate-verify.py")
    openapi = read(ROOT / "contracts/openapi/home-center.v1.openapi.json")
    dc01 = read(ROOT / "deploy/hm-dm/config.dc01.json")
    dc02 = read(ROOT / "deploy/hm-dm/config.dc02.json")
    gateway = read(ROOT / "deploy/external-access/nginx-home-center.example.conf")

    require('CONFIG_SCHEMA = "home-center.config.v4"' in config, "config v4 is not exact")
    for value in (dc01, dc02):
        require('"schema": "home-center.config.v4"' in value, "deployment config is not v4")
        require('"enabled": false' in value, "external access is not disabled by default")
        require('"trusted_proxy_addresses": []' in value, "ambient trusted proxy is configured")
    for marker in (
        "proxy_pass https://192.168.10.254:8443",
        "proxy_next_upstream off",
        "proxy_ssl_verify on",
        "proxy_ssl_name dc01.hm.dm",
        'proxy_set_header Forwarded ""',
        "proxy_set_header X-Forwarded-For $remote_addr",
        "proxy_set_header X-Forwarded-Proto https",
    ):
        require(marker in gateway, f"gateway hardening marker missing: {marker}")
    require("192.168.10.253" not in gateway, "gateway example introduced dc02 automatic failover")
    require("$proxy_add_x_forwarded_for" not in gateway, "gateway appends untrusted forwarding chain")

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
    for marker in (
        'TARGET_VERSION = "0.9.2"',
        "from .upgrade_policy import is_upgrade_allowed",
        'EXPECTED_NODES = ("dc02", "dc01")',
        'ROLLBACK_ORDER = ("dc01", "dc02")',
        'EXPECTED_DOMAIN = "hm.dm"',
        'EXPECTED_DOMAIN_SID = "S-1-5-21-483832520-828804035-215000592"',
        'for kind in ("web_identity", "peer_identity")',
        'raise ReleaseCandidateAcceptanceError(f"acceptance_{kind}_changed")',
        'for kind in ("ad_mutations", "dns_mutations", "dhcp_mutations", "gpo_mutations")',
        'raise ReleaseCandidateAcceptanceError(f"acceptance_{kind}_rejected")',
    ):
        require(marker in acceptance, f"release-candidate acceptance marker missing: {marker}")
    for forbidden in ("subprocess", "socket", "requests.", "urllib.request", "os.system"):
        require(forbidden not in acceptance, f"acceptance verifier gained mutation/network primitive: {forbidden}")
    require("stat.S_ISREG" in acceptance_cli, "acceptance CLI does not reject non-regular evidence")
    require("os.O_NOFOLLOW" in acceptance_cli, "acceptance CLI can follow an evidence symlink")
    require("info.st_nlink != 1" in acceptance_cli, "acceptance CLI accepts aliased evidence")
    require("info.st_mode & 0o022" in acceptance_cli, "acceptance CLI does not protect evidence permissions")
    require("external-health.v1.schema.json" in openapi, "minimal external health contract is absent")
    require("external-access-status.v1.schema.json" in openapi, "external status contract is absent")
    print("SECURITY_GATE_092=PASS")


if __name__ == "__main__":
    main()
