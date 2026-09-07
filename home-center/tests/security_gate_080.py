from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "product/control-plane/src/home_center"
WEB = ROOT / "product/web/static"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SECURITY_GATE_080_FAIL: {message}")


def load_deployment_renderer():
    path = ROOT / "deploy/scripts/render-auth-deployment-v2.py"
    spec = importlib.util.spec_from_file_location("home_center_auth_deployment_v2_security_gate", path)
    require(spec is not None and spec.loader is not None, "deployment v2 renderer cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    auth = read(SRC / "auth.py")
    api = read(SRC / "api.py")
    config = read(SRC / "config.py")
    runtime = read(SRC / "runtime.py")
    local = read(SRC / "local_admin_auth.py")
    ad = read(SRC / "ad_auth.py")
    provision = read(SRC / "local_admin_provision.py")
    provision_cli = read(ROOT / "deploy/runtime/provision-local-admin.py")
    build = read(ROOT / "deploy/scripts/build-artifact.sh")
    index = read(WEB / "index.html")
    browser = read(WEB / "app.js")
    api_tests = read(ROOT / "tests/test_api.py")
    provision_tests = read(ROOT / "tests/test_local_admin_provision.py")
    deployment_tests = read(ROOT / "tests/test_auth_deployment_v2.py")
    openapi = read(ROOT / "contracts/openapi/home-center-auth.v2.openapi.json")
    login_contract = read(ROOT / "contracts/auth/login-request.v2.schema.json")
    credential_contract = read(ROOT / "contracts/auth/local-admin-credential.v1.schema.json")
    ad_contract = read(ROOT / "contracts/auth/ad-provider-config.v1.schema.json")
    providers_contract = read(ROOT / "contracts/auth/auth-providers.v1.schema.json")

    interactive_surface = "\n".join((auth, api, config, runtime, index, browser))
    require("admin_token_file" not in interactive_surface, "runtime interactive auth still references admin_token_file")
    require("verify_admin_token" not in interactive_surface, "legacy token verifier still reachable")
    require('headers.get("Authorization")' not in auth + api, "Authorization header is still an auth source")
    require('"Bearer "' not in auth + api, "Bearer authentication is still accepted")
    require("tokenInput" not in index + browser, "legacy bootstrap-token browser input remains reachable")
    require("JSON.stringify({ token })" not in browser, "legacy bootstrap-token browser request remains reachable")

    require('CONFIG_SCHEMA = "home-center.config.v4"' in config, "0.9 config-v4 successor is not enforced")
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

    require('set(body) != {"provider", "username", "password"}' in api, "login request is not closed to provider/username/password")
    require('provider not in {"local", "ad"}' in api, "authentication provider allowlist missing")
    require('actor = f"{actor_prefix}:{canonical_username}"' in api, "authenticated actor binding missing")
    require("AdAuthenticator" in runtime, "optional AD authenticator is not composed into runtime")
    require("SameSite=Strict" in auth and "HttpOnly" in auth and "Secure" in auth, "session cookie flags weakened")
    require("LoginRateLimiter" in auth + runtime, "login rate limiter is not active")

    require("providerInput" in index and "usernameInput" in index and "passwordInput" in index, "provider login fields missing")
    require("JSON.stringify({ provider, username, password })" in browser, "browser login does not submit explicit provider credentials")
    require('passwordInput.value = ""' in browser, "browser password is not cleared after authentication")

    require('"bootstrapBearer"' not in openapi, "0.8 authentication OpenAPI advertises Bearer authentication")
    require('"sessionCookie"' in openapi, "0.8 authentication OpenAPI lacks session cookie scheme")
    require('"../auth/login-request.v2.schema.json"' in openapi, "0.8 OpenAPI is not bound to the provider login contract")
    require('"provider"' in login_contract and '"password"' in login_contract and '"writeOnly": true' in login_contract, "provider/password contract is incomplete")
    require('"enabled"' in ad_contract and '"allowed_admin_groups"' in ad_contract, "AD provider config contract incomplete")
    require("home-center.auth-providers.v1" in providers_contract, "provider discovery contract missing")
    require('{"id": "ad", "enabled": self.runtime.config.ad_auth.enabled}' in api, "provider discovery is not config-bound")
    for marker in ('"n": {\n      "const": 32768', '"r": {\n      "const": 8', '"p": {\n      "const": 1', '"dklen": {\n      "const": 32'):
        require(marker in credential_contract, f"credential contract KDF marker missing: {marker}")


    for marker in (
        '[KINIT, "-V", "-l", "5m", principal]',
        '[ID, "-Gn", "-z", principal]',
        "input=secret + b",
        "stdout=subprocess.DEVNULL",
        "stderr=subprocess.DEVNULL",
        "timeout=self.config.timeout_seconds",
        '" dns_lookup_kdc = false',
        '" dns_lookup_realm = false',
        "shutil.rmtree",
    ):
        require(marker in ad, f"AD authentication hardening marker missing: {marker}")
    require("shell=True" not in ad, "AD authentication may not invoke a shell")
    require("password" not in ad_contract, "AD provider config contract must not persist passwords")
    for marker in (
        'self.headers.get("Sec-Fetch-Site")',
        'self.headers.get("Origin")',
        '"cross_origin_request_rejected"',
        '"Cross-Origin-Opener-Policy"',
        '"Cross-Origin-Resource-Policy"',
        'action="session.logout"',
    ):
        require(marker in api, f"browser session hardening marker missing: {marker}")
    require("adProviderOption" in index and "disabled hidden" in index, "AD option is not fail-safe by default")
    require("loadAuthProviders" in browser and "auth_provider_catalog_unavailable" in browser, "provider discovery UI wiring missing")

    for marker in (
        "os.O_EXCL",
        "O_NOFOLLOW",
        "os.link(",
        "follow_symlinks=False",
        "os.fsync",
        "credential_file_exists",
        "credential_directory_metadata_rejected",
    ):
        require(marker in provision, f"provisioning hardening marker missing: {marker}")
    require("os.replace" not in provision, "credential publication must not overwrite the destination")
    require("getpass.getpass" in provision_cli, "provisioning CLI does not use hidden TTY input")
    require("sys.stdin.isatty()" in provision_cli, "provisioning CLI accepts non-interactive password input")
    require('parser.add_argument("--username"' in provision_cli, "provisioning CLI username option missing")
    require('parser.add_argument("--password"' not in provision_cli, "password command-line option is forbidden")
    require("os.environ" not in provision_cli, "password/environment credential input is forbidden")
    require("--password-stdin" not in provision_cli, "automation password stdin is forbidden")
    require("provision-local-admin.py" in build, "0.8 provisioner is not preserved in the successor artifact")
    require('# Published predecessor artifact gate: [ "$VERSION" = 0.8.0 ]' in build, "0.8 published predecessor marker is missing")
    require('[ "$VERSION" = 0.9.2 ] || { echo RELEASE_VERSION_NOT_ADMITTED' in build, "0.9 successor artifact version is not admitted exactly")
    require("HOME_CENTER_080_ARTIFACT_NOT_YET_ADMITTED" not in build, "obsolete 0.8 artifact block remains")
    require("HOME_CENTER_092_CONFIG_SCHEMA_NOT_ADMITTED" in build, "0.9 config-v4 release gate missing")
    require("render-release-policy.py" in build, "0.8 release-policy renderer is not invoked")
    require("render-auth-deployment-v2.py" in build, "0.8 auth deployment renderer is not invoked")
    require("render-upgrade-policy-v2.py" in build, "generalized upgrade renderer is not invoked")
    require("/var/lib/home-center/ad-auth" in read(ROOT / "deploy/scripts/render-auth-deployment-v2.py"), "AD cache directory provisioning missing")
    require(
        build.index("render-release-policy.py") < build.index("render-auth-deployment-v2.py")
        < build.index("render-upgrade-policy-v2.py"),
        "deployment renderers execute in an unsafe order",
    )

    renderer = load_deployment_renderer()
    legacy_bootstrap = read(ROOT / "deploy/scripts/bootstrap-hm-dm.sh")
    legacy_installer = read(ROOT / "deploy/scripts/install-node.sh")
    require("/etc/home-center/secrets/admin.token" in legacy_bootstrap, "0.7 bootstrap source boundary unexpectedly changed")
    require("/etc/home-center/secrets/admin.token" in legacy_installer, "0.7 installer source boundary unexpectedly changed")
    rendered_bootstrap = renderer.render_bootstrap(legacy_bootstrap)
    rendered_installer = renderer.render_installer(legacy_installer)
    rendered_deployment = rendered_bootstrap + "\n" + rendered_installer
    for forbidden in (
        "/etc/home-center/secrets/admin.token",
        "Authorization: Bearer",
        "ADMIN_TOKEN",
        "admin_token",
        "AUTH_CONFIG",
        "/api/v1/overview",
        "DC02_ADMIN_TOKEN_MISMATCH",
    ):
        require(forbidden not in rendered_deployment, f"rendered 0.8 deployment retains legacy auth marker: {forbidden}")
    for required in (
        "/etc/home-center/secrets/local-admin.json",
        "LOCAL_ADMIN_DEPLOYMENT_PREFLIGHT=PASS",
        "LOCAL_ADMIN_CLUSTER_PREFLIGHT=PASS",
        "CLUSTER_AUTH_FREE_ACCEPTANCE=PASS",
        "/readyz",
        "/internal/v1/node",
        "verify_bidirectional_peer_identity",
        "fail_rollback",
        "publish_cluster_transaction",
        "DC02_SOFTWARE_CANARY_30S=PASS",
        "HOME_CENTER_CLUSTER_DEPLOY=PASS",
    ):
        require(required in rendered_deployment, f"rendered 0.8 deployment lost safety marker: {required}")

    require("test_bearer_bootstrap_token_cannot_bypass_local_login" in api_tests, "Bearer bypass regression test missing")
    require("test_invalid_login_is_generic_and_legacy_token_shape_is_rejected" in api_tests, "legacy token-shape regression test missing")
    require("test_existing_symlink_is_never_followed_or_replaced" in provision_tests, "provision symlink regression test missing")
    require("test_existing_regular_file_is_never_overwritten" in provision_tests, "provision overwrite regression test missing")
    require("test_cross_origin_login_and_logout_are_rejected_before_authentication" in api_tests, "cross-origin session regression test missing")
    require("test_auth_provider_catalog_is_public_and_disables_ad_by_default" in api_tests, "provider catalog regression test missing")
    require("test_bootstrap_removes_authenticated_overview_probe_but_keeps_ha_gates" in deployment_tests, "deployment auth migration regression test missing")
    require("test_bootstrap_shape_drift_fails_closed" in deployment_tests, "deployment source-drift regression test missing")
    print("SECURITY_GATE_080=PASS")


if __name__ == "__main__":
    main()
