from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "product/control-plane/src/home_center/module_release_qualification.py"
EVIDENCE = ROOT / "contracts/modules/module-release-qualification.v1.schema.json"
RESULT = ROOT / "contracts/modules/module-release-qualification-result.v1.schema.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SECURITY_GATE_0110_QUALIFICATION_FAIL: {message}")


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
        '"lifecycle_executor_unavailable"',
        '"placement_unresolved"',
        'safety["acknowledgement_consumed"] is not False',
        'safety["lifecycle_execution_enabled"] is not False',
        'safety["production_activation_enabled"] is not False',
        '"qualification_evidence_stale"',
        '"qualification_evidence_equivocation"',
    ):
        require(marker in source, f"qualification safety marker missing: {marker}")
    print("SECURITY_GATE_0110_QUALIFICATION=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
