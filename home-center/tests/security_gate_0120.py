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
    recovery_path = package / "local_admin_recovery.py"
    cluster_path = package / "local_admin_cluster.py"
    store_path = package / "store.py"
    recovery_cli_path = ROOT / "deploy/runtime/recover-local-admin.py"
    rotation = rotation_path.read_text(encoding="utf-8")
    helper = helper_path.read_text(encoding="utf-8")
    client = client_path.read_text(encoding="utf-8")
    api = api_path.read_text(encoding="utf-8")
    recovery = recovery_path.read_text(encoding="utf-8")
    cluster = cluster_path.read_text(encoding="utf-8")
    store = store_path.read_text(encoding="utf-8")
    recovery_cli = recovery_cli_path.read_text(encoding="utf-8")

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
        "def reset(",
        "commit_hook",
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
    require('"policy": "local-admin-password-v1"' in api, "secret-free success audit policy missing")
    require('"transaction_id": result["transaction_id"]' in api, "cluster success audit identity missing")
    require('"nodes": result["nodes"]' in api, "cluster success audit node evidence missing")
    require("authenticate_local_admin" in api, "local login does not reload an offline recovery credential")

    for marker in (
        'PEER_PATH = "/internal/v1/local-admin-transaction"',
        "ssl.TLSVersion.TLSv1_3",
        "context.load_cert_chain",
        'self.config.cluster_ca',
        'result["node_id"] != self.config.peer.node_id',
        "CANARY_ATTEMPTS = 3",
        'self._expect(self._remote(prepare), "prepared")',
        'self._expect(self._remote(commit), "committed")',
        'self._expect(self._local(commit), "committed")',
        'raise LocalAdminClusterError("cluster_rotation_recovery_required")',
        '"commit_order": [self.config.peer.node_id, self.config.node_id]',
    ):
        require(marker in cluster, f"two-node transaction guard missing: {marker}")
    require(
        cluster.index('self._expect(self._remote(commit), "committed")')
        < cluster.index('self._expect(self._local(commit), "committed")'),
        "leader commits before standby",
    )
    cluster_imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(ast.parse(cluster))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    require("subprocess" not in cluster_imports, "cluster transaction has process execution surface")
    require("logging" not in cluster_imports and "print(" not in cluster, "cluster transaction has output/log surface")
    for forbidden in ("os.environ", "shell=True", '"path":', '"command":', '"executable":'):
        require(forbidden not in cluster, f"cluster transaction forbidden surface present: {forbidden}")
    ledger_start = store.index("CREATE TABLE IF NOT EXISTS local_admin_transactions")
    ledger_sql = store[ledger_start:store.index('""",', ledger_start)]
    for forbidden in ("current_password", "new_password", "salt_b64", "verifier_b64", "request_hash"):
        require(forbidden not in ledger_sql, f"cluster ledger can persist secret-derived material: {forbidden}")
    for marker in (
        "local_admin_transactions",
        "one_active_local_admin_transaction",
        "recovery_required",
        "process_restart",
        "begin_local_admin_transaction",
        "transition_local_admin_transaction",
    ):
        require(marker in store, f"cluster ledger guard missing: {marker}")
    peer_handler = api[api.index("class PeerRequestHandler"):]
    require(
        peer_handler.index("if not self._peer_identity_matches()")
        < peer_handler.index('urlsplit(self.path).path != "/internal/v1/local-admin-transaction"')
        < peer_handler.index("decode_cluster_command(data)"),
        "peer identity/path gates do not precede secret parsing",
    )

    recovery_cli_imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(ast.parse(recovery_cli))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    require(
        not recovery_cli_imports.intersection({"http", "requests", "socket", "subprocess", "urllib"}),
        "recovery CLI has remote or process execution surface",
    )
    for marker in (
        "os.geteuid() != 0",
        "LOCAL_CONSOLE.fullmatch",
        "sys.stdin, sys.stdout, sys.stderr",
        "getpass.getpass",
        'phrase = f"RESET admin {challenge}"',
        "LocalAdminCredentialRotator",
        "rotator.reset(password, commit_hook=commit_evidence)",
        'CONFIG_PATH = Path("/etc/home-center/config.json")',
        'CREDENTIAL_PATH = Path("/etc/home-center/secrets/local-admin.json")',
        'EVIDENCE_PATH = Path("/var/lib/home-center-recovery/events.jsonl")',
    ):
        require(marker in recovery_cli, f"local-console recovery guard missing: {marker}")
    for forbidden in (
        'add_argument("--password"',
        "--password-stdin",
        "os.environ",
        "shell=True",
        "subprocess.",
        "/dev/pts",
        "ssh",
    ):
        require(forbidden not in recovery_cli.lower(), f"recovery CLI forbidden surface present: {forbidden}")

    for marker in (
        "fcntl.flock",
        "os.O_APPEND",
        "os.O_EXCL",
        'getattr(os, "O_NOFOLLOW"',
        "os.fsync",
        "MAX_EVIDENCE_BYTES",
        "SAFE_REASONS",
        'material.pop("entry_hash", None)',
        'previous_hash = "0" * 64',
    ):
        require(marker in recovery, f"recovery evidence guard missing: {marker}")
    for forbidden in ("current_password", "new_password", "salt_b64", "verifier_b64"):
        require(forbidden not in recovery, f"recovery evidence can name secret material: {forbidden}")

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
        "contracts/auth/local-admin-recovery-evidence.v1.schema.json",
        "contracts/auth/local-admin-password-change-result.v2.schema.json",
        "contracts/cluster/local-admin-transaction-command.v1.schema.json",
        "contracts/cluster/local-admin-transaction-result.v1.schema.json",
    )
    require(all((ROOT / path).is_file() for path in required_contracts), "0.12 credential contract missing")

    version = (package / "__init__.py").read_text(encoding="utf-8")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    builder = (ROOT / "deploy/scripts/build-artifact.sh").read_text(encoding="utf-8")
    installer = (ROOT / "deploy/scripts/install-node.sh").read_text(encoding="utf-8")
    require('__version__ = "0.12.0"' in version, "runtime version is not 0.12.0")
    require('version = "0.12.0"' in project, "package version is not 0.12.0")
    require('[ "$VERSION" = 0.12.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED' in builder, "artifact gate is not 0.12.0")
    require('"$ROOT/deploy/runtime/recover-local-admin.py"' in builder, "recovery CLI is absent from artifact")
    require("RECOVERY_EVIDENCE_DIRECTORY=/var/lib/home-center-recovery" in installer, "recovery evidence directory is not provisioned")
    require("local-admin.password.validate.v1" in helper, "cluster prepare is not a dedicated helper validation action")
    require("local-admin.password.validate.v1" in client, "cluster prepare helper client action missing")

    print("SECURITY_GATE_0120=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
