from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "product/control-plane/src/home_center/local_admin_release_qualification.py"
EVIDENCE = ROOT / "contracts/auth/local-admin-release-qualification.v1.schema.json"
RESULT = ROOT / "contracts/auth/local-admin-release-qualification-result.v1.schema.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SECURITY_GATE_0120_QUALIFICATION_FAIL: {message}")


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def main() -> int:
    require(MODULE.is_file() and EVIDENCE.is_file() and RESULT.is_file(), "qualification source/contracts missing")
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
        "qualification verifier gained I/O, persistence, process, or network import",
    )
    require(
        not calls.intersection({"eval", "exec", "open", "os.system", "subprocess.Popen", "subprocess.run"}),
        "qualification verifier gained execution primitive",
    )
    for marker in (
        'acceptance["rollback_verified"] is not True',
        'acceptance["ambiguous_recovery_verified"] is not True',
        'acceptance["protected_recovery_verified"] is not True',
        'safety["credential_mutation_enabled"] is not False',
        'safety["remote_recovery_enabled"] is not False',
        'safety["automatic_retry_after_ambiguous"] is not False',
        'safety["local_console_recovery_required"] is not True',
        '"qualification_evidence_stale"',
    ):
        require(marker in source, f"qualification safety marker missing: {marker}")
    print("SECURITY_GATE_0120_QUALIFICATION=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
