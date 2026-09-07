from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "product/control-plane/src/home_center/reservation_scheduler.py"
CONTRACT = ROOT / "contracts/intents/reservation.v1.schema.json"
RELEASE = ROOT / "contracts/intents/reservation-release.v1.schema.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SECURITY_GATE_0130_RESERVATION_FAIL: {message}")


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def main() -> int:
    require(MODULE.is_file() and CONTRACT.is_file() and RELEASE.is_file(), "reservation source/contracts missing")
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
        "reservation boundary gained provider/I/O/persistence/process/network import",
    )
    require(
        not calls.intersection({"eval", "exec", "open", "os.system", "subprocess.Popen", "subprocess.run"}),
        "reservation boundary gained execution primitive",
    )
    for marker in (
        "placement_for(request, resource_snapshot)",
        'placement.get("reservation_created") is not False',
        'placement.get("production_mutation_enabled") is not False',
        '"durable": False',
        '"provider_execution_enabled": False',
        '"production_mutation_enabled": False',
        "reservation_capacity_unavailable",
        "reservation_conflict",
        "MIN_LEASE_SECONDS = 5",
        "MAX_LEASE_SECONDS = 900",
        "threading.Lock()",
    ):
        require(marker in source, f"reservation invariant missing: {marker}")
    for forbidden in (
        "proxmox",
        "libvirt",
        "qemu",
        "pct ",
        "qm ",
        "requests.",
        "urllib.request",
        "subprocess",
        "socket.",
        "shell=True",
    ):
        require(forbidden not in source.casefold(), f"provider/execution surface present: {forbidden}")
    print("SECURITY_GATE_0130_RESERVATION=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
