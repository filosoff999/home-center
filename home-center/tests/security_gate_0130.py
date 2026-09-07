from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INTENT = ROOT / "product/control-plane/src/home_center/core/intent_engine.py"
INTENT_SERVICE = ROOT / "product/control-plane/src/home_center/intent_service.py"
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


def main() -> int:
    source = INTENT.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(INTENT))

    imports: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or "").split(".", 1)[0])
        elif isinstance(node, ast.Call):
            calls.add(dotted(node.func))

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
    service_tree = ast.parse(service, filename=str(INTENT_SERVICE))
    service_imports: set[str] = set()
    for node in ast.walk(service_tree):
        if isinstance(node, ast.Import):
            service_imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            service_imports.add((node.module or "").split(".", 1)[0])
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
    ):
        require(marker in service, f"authenticated intent service guard missing: {marker}")

    api = API_V2.read_text(encoding="utf-8")
    post_calls = function_calls(API_V2, "do_POST")
    require(
        not post_calls.intersection({"eval", "exec", "open", "os.system", "subprocess.Popen", "subprocess.run"}),
        "Intent API POST handler gained execution primitive",
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
    ):
        require(marker in api, f"Intent API boundary guard missing: {marker}")
    require("request.actor" not in api[api.index("details={\"intent_id\""):], "audit must not trust client actor")

    required_contracts = (
        "contracts/intents/intent-request.v1.schema.json",
        "contracts/intents/intent-plan.v1.schema.json",
        "contracts/openapi/home-center-intents.v1.openapi.json",
    )
    require(all((ROOT / path).is_file() for path in required_contracts), "0.13 intent contract missing")

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
