from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "product/control-plane/src/home_center"
WEB = ROOT / "product/web/static"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SECURITY_GATE_080_FAIL: {message}")


def main() -> None:
    auth = read(SRC / "auth.py")
    api = read(SRC / "api.py")
    config = read(SRC / "config.py")
    runtime = read(SRC / "runtime.py")
    local = read(SRC / "local_admin_auth.py")
    index = read(WEB / "index.html")
    browser = read(WEB / "app.js")
    api_tests = read(ROOT / "tests/test_api.py")
    openapi = read(ROOT / "contracts/openapi/home-center-auth.v2.openapi.json")
    login_contract = read(ROOT / "contracts/auth/login-request.v1.schema.json")
    credential_contract = read(ROOT / "contracts/auth/local-admin-credential.v1.schema.json")

    interactive_surface = "\n".join((auth, api, config, runtime, index, browser))
    require("admin_token_file" not in interactive_surface, "runtime interactive auth still references admin_token_file")
    require("verify_admin_token" not in interactive_surface, "legacy token verifier still reachable")
    require('headers.get("Authorization")' not in auth + api, "Authorization header is still an auth source")
    require('"Bearer "' not in auth + api, "Bearer authentication is still accepted")
    require("tokenInput" not in index + browser, "legacy bootstrap-token browser input remains reachable")
    require("JSON.stringify({ token })" not in browser, "legacy bootstrap-token browser request remains reachable")

    require('CONFIG_SCHEMA = "home-center.config.v2"' in config, "config v2 is not enforced")
    require("local_admin_credentials_file" in config + runtime, "local administrator credential path is not wired")
    require("LocalAdminCredentialStore" in runtime, "local credential verifier is not composed into runtime")

    for marker in (
        "hashlib.scrypt",
        "hmac.compare_digest",
        "O_NOFOLLOW",
        "st_nlink != 1",
        "expected_uid",
        "expected_gid",
        "expected_mode",
        "credential_kdf_parameters_rejected",
    ):
        require(marker in local, f"credential hardening marker missing: {marker}")

    require('set(body) != {"username", "password"}' in api, "login request is not closed to username/password")
    require('actor = f"local-admin:{canonical_username}"' in api, "local administrator actor binding missing")
    require("SameSite=Strict" in auth and "HttpOnly" in auth and "Secure" in auth, "session cookie flags weakened")
    require("LoginRateLimiter" in auth + runtime, "login rate limiter is not active")

    require("usernameInput" in index and "passwordInput" in index, "local login fields missing")
    require("JSON.stringify({ username, password })" in browser, "browser login does not submit local credentials")
    require('passwordInput.value = ""' in browser, "browser password is not cleared after authentication")

    require('"bootstrapBearer"' not in openapi, "0.8 authentication OpenAPI advertises Bearer authentication")
    require('"sessionCookie"' in openapi, "0.8 authentication OpenAPI lacks session cookie scheme")
    require('"../auth/login-request.v1.schema.json"' in openapi, "0.8 OpenAPI is not bound to the login contract")
    require('"password"' in login_contract and '"writeOnly": true' in login_contract, "password contract is not write-only")
    for marker in ('"n": {\n      "const": 32768', '"r": {\n      "const": 8', '"p": {\n      "const": 1', '"dklen": {\n      "const": 32'):
        require(marker in credential_contract, f"credential contract KDF marker missing: {marker}")

    require("test_bearer_bootstrap_token_cannot_bypass_local_login" in api_tests, "Bearer bypass regression test missing")
    require("test_invalid_login_is_generic_and_legacy_token_shape_is_rejected" in api_tests, "legacy token-shape regression test missing")
    print("SECURITY_GATE_080=PASS")


if __name__ == "__main__":
    main()
