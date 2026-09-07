from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INTENT = ROOT / "product/control-plane/src/home_center/core/intent_engine.py"
INTENT_SERVICE = ROOT / "product/control-plane/src/home_center/intent_service.py"
INTENT_PREFLIGHT = ROOT / "product/control-plane/src/home_center/intent_preflight.py"
PLACEMENT_PLANNER = ROOT / "product/control-plane/src/home_center/placement_planner.py"
RESOURCE_SNAPSHOT = ROOT / "product/control-plane/src/home_center/resource_snapshot.py"
RUNTIME = ROOT / "product/control-plane/src/home_center/runtime.py"
API_V2 = ROOT / "product/control-plane/src/home_center/api_v2.py"
CORE_INIT = ROOT / "product/control-plane/src/home_center/core/__init__.py"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SECURITY_GATE_0130_FAIL: {message}")


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
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
    )
    return {dotted(node.func) for node in ast.walk(function) if isinstance(node, ast.Call)}


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or "").split(".", 1)[0])
    return imports


def require_pure(path: Path, *, label: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    calls = {dotted(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    require(
        not imported_roots(path).intersection(
            {"http", "os", "pathlib", "requests", "socket", "sqlite3", "subprocess", "urllib"}
        ),
        f"{label} gained I/O, persistence, process, or network surface",
    )
    require(
        not calls.intersection({"eval", "exec", "open", "os.system", "subprocess.Popen", "subprocess.run"}),
        f"{label} gained execution or file-write primitive",
    )
    return source


def main() -> int:
    source = INTENT.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(INTENT))

    imports = imported_roots(INTENT)
    calls = {dotted(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    require(
        not imports.intersection({"http", "os", "pathlib", "requests", "socket", "subprocess", "urllib"}),
        "intent planner has I/O, process, or network import surface",
    )
    require(
        not calls.intersection({"eval", "exec", "open", "os.system", "subprocess.Popen", "subprocess.run"}),
        "intent planner has execution or file-write primitive",
    )
    for forbidden in (
        "shell=True",
        "shell = True",
        "requests.",
        "socket.",
        "subprocess.",
        "urllib.",
        "Path(",
        "open(",
    ):
        require(forbidden not in source, f"forbidden intent runtime surface: {forbidden}")

    for marker in (
        "PRODUCTION_EXECUTION_ENABLED = False",
        'mode: str = "plan"',
        'raise IntentEngineError("execution_not_certified")',
        'module="intent-engine"',
        "mode=AccessMode.PLAN",
        "FORBIDDEN_PARAMETER_KEYS",
        '"password"',
        '"secret"',
        '"token"',
        "non_deterministic_step_sequence",
        "production_execution_enabled: bool = False",
        "execution_requires_approval",
    ):
        require(marker in source, f"intent safety marker missing: {marker}")

    service = INTENT_SERVICE.read_text(encoding="utf-8")
    service_imports = imported_roots(INTENT_SERVICE)
    require(
        not service_imports.intersection({"http", "os", "pathlib", "requests", "socket", "subprocess", "urllib"}),
        "intent service gained I/O, process, or network surface",
    )
    for marker in (
        'r"^local-admin:[a-z][a-z0-9._-]{2,63}$"',
        'r"^ad-admin:[a-z0-9][a-z0-9._-]{0,63}@[A-Z0-9][A-Z0-9.-]{2,254}$"',
        'IntentKind.NODE_DRAIN: "intent.node.plan"',
        'IntentKind.STORAGE_SHARE_CREATE: "intent.storage.plan"',
        'IntentKind.VIRTUALIZATION_WORKLOAD_CREATE: "intent.virtualization.plan"',
        'IntentKind.MODULE_INSTALL: "intent.module.plan"',
        'module="intent-engine"',
        "mode=AccessMode.PLAN",
        "if request.actor != actor:",
        "RESOURCE_AWARE_KINDS",
        "resource_snapshot_provider",
        "plan = self._engine.compile(request, permissions={permission})",
        "provider = self._resource_snapshot_provider",
        '"resource_snapshot_unavailable"',
        '"resource_snapshot_invalid"',
        "blockers = resource_preflight(request, snapshot)",
        "decision = placement_for(request, snapshot)",
        "return attach_placement(plan, decision)",
        '"placement_unavailable"',
    ):
        require(marker in service, f"authenticated intent service guard missing: {marker}")
    compile_index = service.index("plan = self._engine.compile(request, permissions={permission})")
    provider_index = service.index("provider = self._resource_snapshot_provider")
    preflight_index = service.index("blockers = resource_preflight(request, snapshot)")
    placement_index = service.index("decision = placement_for(request, snapshot)")
    require(compile_index < provider_index < preflight_index < placement_index, "planning/preflight/placement order is unsafe")

    preflight = require_pure(INTENT_PREFLIGHT, label="intent resource preflight")
    for marker in (
        'snapshot.get("schema") != "home-center.resource-snapshot.v1"',
        'snapshot.get("production_mutation_enabled") is not False',
        'return ("cluster_resources_not_ready",)',
        "required_nodes = 2 if high_availability else 1",
        '"insufficient_cpu_capacity"',
        '"insufficient_memory_capacity"',
        '"insufficient_storage_capacity"',
        'return ("insufficient_combined_capacity",)',
    ):
        require(marker in preflight, f"resource-aware preflight guard missing: {marker}")

    placement = require_pure(PLACEMENT_PLANNER, label="placement planner")
    for marker in (
        'snapshot.get("schema") != "home-center.resource-snapshot.v1"',
        'snapshot.get("planning_ready") is not True',
        'snapshot.get("production_mutation_enabled") is not False',
        "required_nodes = 2 if high_availability else 1",
        'strategy = "storage-headroom-v1"',
        'strategy = "balanced-headroom-v1"',
        "weakest = min(cpu_headroom, memory_headroom, storage_headroom)",
        "ranked.sort()",
        '"reservation_created": False',
        '"production_mutation_enabled": False',
        'payload["placement"] = decision.to_dict()',
        "production_execution_enabled=False",
    ):
        require(marker in placement, f"placement planning safety marker missing: {marker}")
    for forbidden in ("reserve", "requests.", "socket.", "subprocess.", "os.system", "open("):
        if forbidden == "reserve":
            require("def reserve" not in placement and "reserve(" not in placement, "placement planner exposes reservation primitive")
        else:
            require(forbidden not in placement, f"placement planner forbidden surface: {forbidden}")

    resources = require_pure(RESOURCE_SNAPSHOT, label="resource snapshot")
    for marker in (
        'capability.get("schema") != "home-center.node-capability.v1"',
        'raise ResourceSnapshotError("node_identity_mismatch")',
        '"planning_ready": planning_ready',
        '"production_mutation_enabled": False',
        '"root_storage_total_bytes"',
        '"root_storage_free_bytes"',
        '"capabilities": sorted(capabilities)',
    ):
        require(marker in resources, f"resource snapshot safety marker missing: {marker}")

    runtime = RUNTIME.read_text(encoding="utf-8")
    require(
        "IntentPlanningService(resource_snapshot_provider=self.resource_snapshot)" in runtime,
        "runtime intent service is not bound to trusted resource snapshot provider",
    )
    require("def resource_snapshot(self)" in runtime, "runtime trusted resource snapshot method missing")

    api = API_V2.read_text(encoding="utf-8")
    post_calls = function_calls(API_V2, "do_POST")
    get_calls = function_calls(API_V2, "do_GET")
    require(
        not post_calls.intersection({"eval", "exec", "open", "os.system", "subprocess.Popen", "subprocess.run"}),
        "Intent API POST handler gained execution primitive",
    )
    require(
        not get_calls.intersection({"eval", "exec", "open", "os.system", "subprocess.Popen", "subprocess.run"}),
        "read-only API GET handler gained execution primitive",
    )
    for marker in (
        'path != "/api/v1/intents/plan"',
        "self._same_origin_post_allowed(context)",
        "self._require_actor(correlation_id)",
        "IntentRequest.from_mapping(body)",
        "self.runtime.intents.plan(actor=actor, request=request)",
        "max_bytes=16 * 1024",
        '"intent_permission_denied"',
        '"invalid_intent_request"',
        "plan.to_dict()",
        'path == "/api/v1/resources"',
        "self.runtime.resource_snapshot()",
        '"resource_snapshot_unavailable"',
    ):
        require(marker in api, f"0.13 API boundary guard missing: {marker}")
    require('"actor": request.actor' not in api, "audit must not persist client-supplied actor")

    required_contracts = (
        "contracts/intents/intent-request.v1.schema.json",
        "contracts/intents/intent-plan.v1.schema.json",
        "contracts/intents/placement.v1.schema.json",
        "contracts/openapi/home-center-intents.v1.openapi.json",
        "contracts/resources/resource-snapshot.v1.schema.json",
        "contracts/openapi/home-center-resources.v1.openapi.json",
    )
    require(all((ROOT / path).is_file() for path in required_contracts), "0.13 contract missing")

    core_init = CORE_INIT.read_text(encoding="utf-8")
    require("IntentEngine" in core_init and "IntentRequest" in core_init, "intent planner is not exported by core")

    version = (ROOT / "product/control-plane/src/home_center/__init__.py").read_text(encoding="utf-8")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    builder = (ROOT / "deploy/scripts/build-artifact.sh").read_text(encoding="utf-8")
    require('__version__ = "0.13.0"' in version, "runtime version is not 0.13.0")
    require('version = "0.13.0"' in project, "package version is not 0.13.0")
    require('[ "$VERSION" = 0.13.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED' in builder, "artifact gate is not 0.13.0")

    print("SECURITY_GATE_0130=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
