from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "product/control-plane/src/home_center/module_lifecycle_authorization.py"
CONTRACT = ROOT / "contracts/modules/module-lifecycle-authorization.v1.schema.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SECURITY_GATE_0110_AUTHORIZATION_FAIL: {message}")


def imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".", 1)[0])
    return roots


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def main() -> int:
    require(MODULE.is_file() and CONTRACT.is_file(), "authorization source/contract missing")
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE))
    imports = imported_roots(tree)
    calls = {dotted(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    require(
        not imports.intersection({"http", "os", "pathlib", "requests", "socket", "sqlite3", "subprocess", "urllib"}),
        "authorization boundary gained I/O, persistence, process or network import",
    )
    require(
        not calls.intersection({"eval", "exec", "open", "os.system", "subprocess.Popen", "subprocess.run"}),
        "authorization boundary gained execution primitive",
    )
    for marker in (
        '"authorization_contract_unavailable"',
        '"lifecycle_executor_unavailable"',
        '"placement_unresolved"',
        '"acknowledgement_consumed": False',
        '"lifecycle_persistence_enabled": False',
        '"lifecycle_execution_enabled": False',
        '"production_activation_enabled": False',
        "actor_binding_sha256",
        "hmac.compare_digest",
        "authorization_plan_identity_rejected",
    ):
        require(marker in source, f"authorization safety marker missing: {marker}")
    for forbidden in ("subprocess", "socket.", "requests.", "urllib.request", "shell=True", "os.system"):
        require(forbidden not in source, f"authorization unsafe surface present: {forbidden}")
    print("SECURITY_GATE_0110_AUTHORIZATION=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
