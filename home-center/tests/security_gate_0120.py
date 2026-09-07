from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SECURITY_GATE_0120_FAIL: {message}")


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def function_calls(path: Path, function_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function = next(
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
    )
    return {dotted(node.func) for node in ast.walk(function) if isinstance(node, ast.Call)}


def main() -> int:
    package = ROOT / "product/control-plane/src/home_center"
    rotation_path = package / "local_admin_rotation.py"
    helper_path = package / "privileged_helper.py"
    client_path = package / "helper_client.py"
    api_path = package / "api.py"
    rotation = rotation_path.read_text(encoding="utf-8")
    helper = helper_path.read_text(encoding="utf-8")
    client = client_path.read_text(encoding="utf-8")
    api = api_path.read_text(encoding="utf-8")

    imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(ast.parse(rotation))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    require(not imports.intersection({"http", "requests", "socket", "subprocess", "urllib"}), "rotation has remote or process surface")
    for marker in (
        "fcntl.flock",
        '"O_NOFOLLOW"',
        "os.O_EXCL",
        "os.replace",
        "os.fsync",
        "credential_rotation_rollback_failed",
        "ROLLBACK_NAME",
        "NEXT_NAME",
    ):
        require(marker in rotation, f"rotation guard missing: {marker}")

    secret_calls = function_calls(helper_path, "_execute_secret_request")
    require("_sha256" not in secret_calls, "secret request is hashed into evidence")
    require("subprocess.run" not in secret_calls, "secret request is passed to a process")
    require("HelperEngine.execute" not in secret_calls, "secret request enters durable helper engine")
    require("/etc/home-center/secrets/local-admin.json" in helper, "credential target is not compile-time fixed")
    require("home-center.helper.secret-request.v1" in helper and "home-center.helper.secret-result.v1" in helper, "secret protocol missing")
    require("request_sha256" not in helper[helper.index("def _secret_result"):helper.index("def _execute_secret_request")], "secret result exposes derived request material")
    require("logging" not in client and "print(" not in client, "secret client has output/log surface")

    require('actor != f"local-admin:{self.runtime.local_admin.username}"' in api, "local-admin actor gate missing")
    require('/api/v1/auth/local-admin/password/change' in api, "password API missing")
    require('details={"policy": "local-admin-password-v1"' in api, "secret-free success audit missing")

    service = (ROOT / "deploy/systemd/home-center-helper.service").read_text(encoding="utf-8")
    web_service = (ROOT / "deploy/systemd/home-center.service").read_text(encoding="utf-8")
    require("ReadWritePaths=/etc/home-center/pki/web /etc/home-center/secrets" in service, "helper secret write scope missing")
    require("ReadOnlyPaths=/etc/home-center /opt/home-center" in web_service, "Web runtime gained secret write access")

    required_contracts = (
        "contracts/auth/local-admin-password-change.v1.schema.json",
        "contracts/auth/local-admin-password-change-result.v1.schema.json",
        "contracts/helper/helper-secret-request.v1.schema.json",
        "contracts/helper/helper-secret-result.v1.schema.json",
        "contracts/openapi/home-center-auth.v3.openapi.json",
    )
    require(all((ROOT / path).is_file() for path in required_contracts), "0.12 credential contract missing")

    version = (package / "__init__.py").read_text(encoding="utf-8")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    builder = (ROOT / "deploy/scripts/build-artifact.sh").read_text(encoding="utf-8")
    require('__version__ = "0.12.0"' in version, "runtime version is not 0.12.0")
    require('version = "0.12.0"' in project, "package version is not 0.12.0")
    require('[ "$VERSION" = 0.12.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED' in builder, "artifact gate is not 0.12.0")

    print("SECURITY_GATE_0120=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
