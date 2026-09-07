from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "product/control-plane/src/home_center/release_qualification.py"
API = ROOT / "product/control-plane/src/home_center/api_v2.py"
CONTRACT = ROOT / "contracts/release/release-qualification.v1.schema.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SECURITY_GATE_0130_QUALIFICATION_FAIL: {message}")


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def main() -> int:
    require(MODULE.is_file() and API.is_file() and CONTRACT.is_file(), "qualification source/contract missing")
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
        "qualification boundary gained I/O, persistence, process, or network import",
    )
    require(
        not calls.intersection({"eval", "exec", "open", "os.system", "subprocess.Popen", "subprocess.run"}),
        "qualification boundary gained execution or file-write primitive",
    )

    for marker in (
        "resource_preflight(request, resource_snapshot)",
        "placement_for(request, resource_snapshot)",
        "IntentPlanningService(",
        "resource_snapshot_provider=lambda: resource_snapshot",
        "dict(api_plan) != expected_api_plan",
        "ReservationLedger().reserve(",
        "reservation.expires_at_epoch <= now_epoch",
        "prepare_provider_operation(",
        "expected_provider_plan.state is not ProviderPlanState.PLANNED",
        '"execution_ticket_created": False',
        '"provider_execution_enabled": False',
        '"production_execution_enabled": False',
        '"production_mutation_enabled": False',
    ):
        require(marker in source, f"qualification safety marker missing: {marker}")
    for forbidden in (
        "shell=true",
        "requests.",
        "urllib.request",
        "socket.",
        "subprocess.",
        "os.system",
        "provider.execute",
        "provider_url",
        "credential",
        "private_key",
        "password",
        "secret",
    ):
        require(forbidden not in source.casefold(), f"qualification forbidden surface present: {forbidden}")

    api = API.read_text(encoding="utf-8")
    for marker in (
        'path != "/api/v1/intents/plan"',
        'path == "/api/v1/resources"',
        "IntentRequest.from_mapping(body)",
        "self.runtime.intents.plan(actor=actor, request=request)",
        "plan.to_dict()",
        "self.runtime.resource_snapshot()",
        "self._same_origin_post_allowed(context)",
        "self._require_actor(correlation_id)",
    ):
        require(marker in api, f"authenticated API chain marker missing: {marker}")

    dependent = {
        "resource snapshot": ROOT / "product/control-plane/src/home_center/resource_snapshot.py",
        "resource preflight": ROOT / "product/control-plane/src/home_center/intent_preflight.py",
        "placement": ROOT / "product/control-plane/src/home_center/placement_planner.py",
        "reservation": ROOT / "product/control-plane/src/home_center/reservation_scheduler.py",
        "provider": ROOT / "product/control-plane/src/home_center/provider_framework.py",
    }
    require(all(path.is_file() for path in dependent.values()), "qualified chain dependency missing")
    for label, path in dependent.items():
        value = path.read_text(encoding="utf-8").casefold()
        require("production_mutation_enabled" in value, f"{label} mutation boundary marker missing")
    require(
        "production_execution_enabled"
        in (ROOT / "product/control-plane/src/home_center/core/intent_engine.py").read_text(encoding="utf-8"),
        "intent execution boundary marker missing",
    )
    require(
        "provider_execution_enabled" in dependent["reservation"].read_text(encoding="utf-8"),
        "reservation provider boundary marker missing",
    )
    require(
        "provider_execution_enabled" in dependent["provider"].read_text(encoding="utf-8"),
        "provider execution boundary marker missing",
    )

    print("SECURITY_GATE_0130_QUALIFICATION=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
