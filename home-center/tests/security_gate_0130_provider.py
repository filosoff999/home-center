from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "product/control-plane/src/home_center/provider_framework.py"
CONTRACT = ROOT / "contracts/intents/provider-operation-plan.v1.schema.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SECURITY_GATE_0130_PROVIDER_FAIL: {message}")


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def main() -> int:
    require(MODULE.is_file() and CONTRACT.is_file(), "provider source/contract missing")
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
        "provider boundary gained I/O, persistence, process or network import",
    )
    require(
        not calls.intersection({"eval", "exec", "open", "os.system", "subprocess.Popen", "subprocess.run"}),
        "provider boundary gained execution primitive",
    )
    for marker in (
        'PROXMOX = "proxmox"',
        'FILESYSTEM = "filesystem"',
        'STORAGE_SHARE_CREATE = "storage.share.create.v1"',
        'VIRTUAL_MACHINE_CREATE = "virtualization.vm.create.v1"',
        'LXC_CREATE = "virtualization.lxc.create.v1"',
        "reservation.expires_at_epoch <= now_epoch",
        "reservation.request_binding_sha256 != binding",
        '"execution_ticket_created": False',
        "provider_execution_enabled: bool = False",
        "production_mutation_enabled: bool = False",
    ):
        require(marker in source, f"provider safety marker missing: {marker}")
    for forbidden in ("provider.execute", "subprocess", "requests.", "urllib.request", "socket.", "shell=True", "os.system"):
        require(forbidden not in source.casefold(), f"provider execution surface present: {forbidden}")
    print("SECURITY_GATE_0130_PROVIDER=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
