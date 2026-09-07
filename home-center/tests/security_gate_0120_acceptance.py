from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "product/control-plane/src/home_center/local_admin_acceptance.py"
EVIDENCE = ROOT / "contracts/auth/local-admin-recovery-acceptance.v1.schema.json"
RESULT = ROOT / "contracts/auth/local-admin-recovery-acceptance-result.v1.schema.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SECURITY_GATE_0120_ACCEPTANCE_FAIL: {message}")


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def main() -> int:
    require(MODULE.is_file() and EVIDENCE.is_file() and RESULT.is_file(), "acceptance source/contracts missing")
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE))
    roots: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".", 1)[0])
        elif isinstance(node, ast.Call):
            calls.add(dotted(node.func))
    require(
        not roots.intersection({"http", "os", "pathlib", "requests", "socket", "sqlite3", "subprocess", "urllib"}),
        "acceptance verifier gained I/O, persistence, process or network import",
    )
    require(
        not calls.intersection({"eval", "exec", "open", "os.system", "subprocess.Popen", "subprocess.run"}),
        "acceptance verifier gained execution primitive",
    )
    for marker in (
        '"standby", "leader"',
        '"rolled_back"',
        '"recovery_required"',
        '"remote_recovery_enabled"',
        '"ssh_pty_rejected"',
        '"production_mutation_enabled": False',
        "acceptance_independent_credential_state_rejected",
        "FORBIDDEN_KEY_PARTS",
    ):
        require(marker in source, f"acceptance invariant missing: {marker}")
    for forbidden in ("urllib.request", "socket.", "subprocess", "requests.", "shell=True", "os.system"):
        require(forbidden not in source, f"unsafe acceptance surface present: {forbidden}")
    print("SECURITY_GATE_0120_ACCEPTANCE=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
