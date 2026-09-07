from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "product/control-plane/src/home_center/core"
FORBIDDEN_CALLS = {"eval", "exec", "os.system", "subprocess.call", "subprocess.run", "subprocess.Popen"}
FORBIDDEN_IMPORTS = {"http", "os", "pathlib", "requests", "socket", "subprocess", "urllib"}


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def main() -> int:
    required = {
        "node_manager.py",
        "upgrade_engine.py",
        "configuration_engine.py",
        "service_manager.py",
        "policy_engine.py",
        "contracts.py",
        "action_contracts.py",
    }
    present = {path.name for path in CORE.glob("*.py")}
    if not required <= present:
        print("SECURITY_GATE_0100=FAIL missing_core_modules", file=sys.stderr)
        return 1
    for path in sorted(CORE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name.split(".", 1)[0] in FORBIDDEN_IMPORTS for alias in node.names):
                    print(f"SECURITY_GATE_0100=FAIL forbidden_import:{path.name}", file=sys.stderr)
                    return 1
            if isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".", 1)[0] in FORBIDDEN_IMPORTS:
                    print(f"SECURITY_GATE_0100=FAIL forbidden_import:{path.name}", file=sys.stderr)
                    return 1
            if isinstance(node, ast.Call) and dotted(node.func) in FORBIDDEN_CALLS:
                print(f"SECURITY_GATE_0100=FAIL forbidden_call:{path.name}", file=sys.stderr)
                return 1
    from home_center.core.policy_engine import PRODUCTION_MUTATION_ENABLED
    from home_center.core.upgrade_engine import PRODUCTION_ACTIVATION_ENABLED

    if PRODUCTION_MUTATION_ENABLED or PRODUCTION_ACTIVATION_ENABLED:
        print("SECURITY_GATE_0100=FAIL production_activation_enabled", file=sys.stderr)
        return 1
    print("SECURITY_GATE_0100=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
