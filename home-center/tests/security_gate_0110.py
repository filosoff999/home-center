from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "product/control-plane/src/home_center/module_artifact.py"
MANIFEST = ROOT / "product/control-plane/src/home_center/module_manifest.py"
PLANNER = ROOT / "product/control-plane/src/home_center/module_admission.py"
REVIEW = ROOT / "product/control-plane/src/home_center/module_permission_review.py"
ACKNOWLEDGEMENT = ROOT / "product/control-plane/src/home_center/module_permission_acknowledgement.py"
LIFECYCLE = ROOT / "product/control-plane/src/home_center/module_lifecycle.py"
API = ROOT / "product/control-plane/src/home_center/api.py"
STORE = ROOT / "product/control-plane/src/home_center/store.py"
RUNTIME = ROOT / "product/control-plane/src/home_center/runtime.py"
WEB_INDEX = ROOT / "product/web/static/index.html"
WEB_SCRIPT = ROOT / "product/web/static/app.js"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SECURITY_GATE_0110_FAIL: {message}")


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def main() -> int:
    source = MODULE.read_text(encoding="utf-8")
    manifest = MANIFEST.read_text(encoding="utf-8")
    planner = PLANNER.read_text(encoding="utf-8")
    review = REVIEW.read_text(encoding="utf-8")
    acknowledgement = ACKNOWLEDGEMENT.read_text(encoding="utf-8")
    lifecycle = LIFECYCLE.read_text(encoding="utf-8")
    api = API.read_text(encoding="utf-8")
    store = STORE.read_text(encoding="utf-8")
    runtime = RUNTIME.read_text(encoding="utf-8")
    web_index = WEB_INDEX.read_text(encoding="utf-8")
    web_script = WEB_SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE))
    planner_tree = ast.parse(planner, filename=str(PLANNER))
    review_tree = ast.parse(review, filename=str(REVIEW))
    acknowledgement_tree = ast.parse(acknowledgement, filename=str(ACKNOWLEDGEMENT))
    lifecycle_tree = ast.parse(lifecycle, filename=str(LIFECYCLE))

    imports: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or "").split(".", 1)[0])
        elif isinstance(node, ast.Call):
            calls.add(dotted(node.func))

    planner_imports: set[str] = set()
    planner_calls: set[str] = set()
    for node in ast.walk(planner_tree):
        if isinstance(node, ast.Import):
            planner_imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            planner_imports.add((node.module or "").split(".", 1)[0])
        elif isinstance(node, ast.Call):
            planner_calls.add(dotted(node.func))

    review_imports: set[str] = set()
    review_calls: set[str] = set()
    for node in ast.walk(review_tree):
        if isinstance(node, ast.Import):
            review_imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            review_imports.add((node.module or "").split(".", 1)[0])
        elif isinstance(node, ast.Call):
            review_calls.add(dotted(node.func))

    acknowledgement_imports: set[str] = set()
    acknowledgement_calls: set[str] = set()
    for node in ast.walk(acknowledgement_tree):
        if isinstance(node, ast.Import):
            acknowledgement_imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            acknowledgement_imports.add((node.module or "").split(".", 1)[0])
        elif isinstance(node, ast.Call):
            acknowledgement_calls.add(dotted(node.func))

    lifecycle_imports: set[str] = set()
    lifecycle_calls: set[str] = set()
    for node in ast.walk(lifecycle_tree):
        if isinstance(node, ast.Import):
            lifecycle_imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            lifecycle_imports.add((node.module or "").split(".", 1)[0])
        elif isinstance(node, ast.Call):
            lifecycle_calls.add(dotted(node.func))

    require(not imports.intersection({"http", "requests", "socket", "urllib"}), "network client imported")
    require(not calls.intersection({"eval", "exec", "os.system", "subprocess.Popen"}), "unsafe execution primitive")
    require(not any(call.endswith((".extract", ".extractall")) for call in calls), "archive extraction enabled")
    require(source.count("subprocess.run(") == 1, "unexpected process execution surface")
    for forbidden in ("shell=True", "shell = True", "os.replace(", "download", "install_module", "activate_module"):
        require(forbidden not in source, f"forbidden runtime surface: {forbidden}")

    for marker in (
        'OPENSSL = "/usr/bin/openssl"',
        "MAX_ARTIFACT_BYTES = 256 * 1024 * 1024",
        "manifest_binding_sha256",
        "_verify_signatures",
        "_validate_archive",
        "os.O_NOFOLLOW",
        "os.O_EXCL",
        "os.link(",
        "follow_symlinks=False",
        "os.fsync",
        "verify_module_artifact",
        "stage_module_artifact",
        "artifact_bytes: bytes",
    ):
        require(marker in source, f"module supply-chain guard missing: {marker}")
    for marker in ("manifest_duplicate_key", "manifest_float_rejected", "manifest_constant_rejected"):
        require(marker in manifest, f"manifest JSON guard missing: {marker}")

    require(
        not planner_imports.intersection(
            {"http", "os", "pathlib", "requests", "shutil", "socket", "subprocess", "tempfile", "urllib"}
        ),
        "planner I/O or execution module imported",
    )
    require(
        not planner_calls.intersection(
            {"eval", "exec", "open", "io.open", "os.system", "subprocess.Popen", "subprocess.run"}
        ),
        "planner I/O or execution primitive enabled",
    )
    for marker in (
        "PRODUCTION_ACTIVATION_ENABLED = False",
        "MAX_ADMISSION_REQUEST_BYTES = 1024 * 1024",
        "MAX_REQUESTED_PERMISSIONS = 128",
        "planner_candidate_ambiguous",
        "planner_dependency_cycle",
        "planner_module_conflict",
        "planner_requested_module_already_installed",
        "production_activation_enabled",
        "load_and_plan_module_admission",
    ):
        require(marker in planner, f"module admission guard missing: {marker}")
    for forbidden_field in ('"nodes"', '"placement"', '"rollout"', '"execute"', '"granted_permissions"'):
        require(forbidden_field not in planner, f"planner authority field enabled: {forbidden_field}")

    require(
        not review_imports.intersection(
            {"http", "os", "pathlib", "requests", "shutil", "socket", "subprocess", "tempfile", "urllib"}
        ),
        "permission review I/O or execution module imported",
    )
    require(
        not review_calls.intersection(
            {"eval", "exec", "open", "io.open", "os.system", "subprocess.Popen", "subprocess.run"}
        ),
        "permission review I/O or execution primitive enabled",
    )
    for marker in (
        "PERMISSION_GRANTS_APPLIED = False",
        "DECISION_PERSISTENCE_ENABLED = False",
        "PRODUCTION_ACTIVATION_ENABLED = False",
        "MAX_REVIEW_PERMISSIONS = 128",
        "MAX_REVIEW_ACTIONS = 2048",
        'risk = "unclassified"',
        "load_and_build_module_permission_review",
    ):
        require(marker in review, f"permission review guard missing: {marker}")
    require('path == "/api/v1/modules/permission-review/preview"' in api, "permission preview API missing")
    require('path == "/api/v1/modules/permission-review"' in api, "permission status API missing")
    require("MAX_ADMISSION_REQUEST_BYTES" in api, "permission preview body is not bounded")
    require('data-panel="modules"' in web_index, "permission review UI missing")
    require("function renderModulePermissionReview()" in web_script, "permission review renderer missing")
    require("innerHTML" not in web_script, "permission review UI enables HTML injection surface")
    for forbidden_route in ("permission-review/approve", "permission-review/grant", "permission-review/install"):
        require(forbidden_route not in api + web_index + web_script, f"permission authority route enabled: {forbidden_route}")

    require(
        not acknowledgement_imports.intersection(
            {"http", "os", "pathlib", "requests", "shutil", "socket", "subprocess", "tempfile", "urllib"}
        ),
        "permission acknowledgement I/O or execution module imported",
    )
    require(
        not acknowledgement_calls.intersection(
            {"eval", "exec", "open", "io.open", "os.system", "subprocess.Popen", "subprocess.run"}
        ),
        "permission acknowledgement I/O or execution primitive enabled",
    )
    for marker in (
        "ACKNOWLEDGEMENT_TTL_SECONDS = 15 * 60",
        "ACKNOWLEDGEMENT_PERSISTENCE_ENABLED = True",
        "AUTHORIZATION_DECISION_PERSISTED = False",
        "PERMISSION_GRANTS_APPLIED = False",
        "LIFECYCLE_EXECUTION_ENABLED = False",
        "PRODUCTION_ACTIVATION_ENABLED = False",
        '"consumable": False',
        "load_and_prepare_module_permission_acknowledgement",
    ):
        require(marker in acknowledgement, f"module acknowledgement guard missing: {marker}")
    for marker in (
        "module_permission_acknowledgements",
        "BEGIN IMMEDIATE",
        "UNIQUE(actor, idempotency_key)",
        "module-permission-acknowledgement.v1\\0",
        "verify_module_permission_acknowledgements",
    ):
        require(marker in store, f"module acknowledgement persistence guard missing: {marker}")
    require(
        "self.store.verify_module_permission_acknowledgements()" in runtime,
        "module acknowledgement readiness integrity gate missing",
    )
    require(
        'path == "/api/v1/modules/permission-review/acknowledgements"' in api,
        "module acknowledgement collection API missing",
    )
    require(
        'path.startswith("/api/v1/modules/permission-review/acknowledgements/")' in api,
        "module acknowledgement item API missing",
    )
    require(
        "MAX_PERMISSION_ACKNOWLEDGEMENT_REQUEST_BYTES" in api,
        "module acknowledgement request body is not bounded",
    )
    require("moduleAcknowledgementHistory" in web_index, "module acknowledgement UI history missing")
    require(
        "function renderModulePermissionAcknowledgements()" in web_script,
        "module acknowledgement UI renderer missing",
    )
    for forbidden_route in (
        "acknowledgements/approve",
        "acknowledgements/grant",
        "acknowledgements/install",
        "acknowledgements/consume",
    ):
        require(
            forbidden_route not in api + web_index + web_script,
            f"module acknowledgement authority route enabled: {forbidden_route}",
        )

    require(
        not lifecycle_imports.intersection(
            {"http", "os", "pathlib", "requests", "shutil", "socket", "sqlite3", "subprocess", "tempfile", "urllib"}
        ),
        "module lifecycle I/O, persistence or execution module imported",
    )
    require(
        not lifecycle_calls.intersection(
            {"eval", "exec", "open", "io.open", "os.system", "subprocess.Popen", "subprocess.run"}
        ),
        "module lifecycle I/O or execution primitive enabled",
    )
    for marker in (
        "ACKNOWLEDGEMENT_CONSUMPTION_ENABLED = False",
        "AUTHORIZATION_DECISIONS_ENABLED = False",
        "ARTIFACT_MUTATION_ENABLED = False",
        "LIFECYCLE_PERSISTENCE_ENABLED = False",
        "LIFECYCLE_EXECUTION_ENABLED = False",
        "PRODUCTION_ACTIVATION_ENABLED = False",
        '"status": "blocked"',
        '"state": "blocked"',
        '"strategy": "reverse-order-rollback"',
        '"data_policy": "preserve"',
        "acknowledgement_request_hash",
        "lifecycle_admission_binding_rejected",
        "plan_module_install_lifecycle",
    ):
        require(marker in lifecycle, f"module lifecycle boundary missing: {marker}")
    for forbidden_field in ('"command"', '"argv"', '"path"', '"execute"', '"granted_permissions"'):
        require(forbidden_field not in lifecycle, f"module lifecycle authority field enabled: {forbidden_field}")
    require('path == "/api/v1/modules/lifecycle"' in api, "module lifecycle status API missing")
    require(
        'path == "/api/v1/modules/lifecycle/preview"' in api,
        "module lifecycle preview API missing",
    )
    require("MAX_MODULE_LIFECYCLE_REQUEST_BYTES" in api, "module lifecycle body is not bounded")
    require("moduleLifecyclePlan" in web_index, "module lifecycle UI missing")
    require("function renderModuleLifecycle()" in web_script, "module lifecycle renderer missing")
    for forbidden_route in (
        "modules/lifecycle/start",
        "modules/lifecycle/execute",
        "modules/lifecycle/resume",
        "modules/lifecycle/authorize",
        "modules/lifecycle/consume",
    ):
        require(
            forbidden_route not in api + web_index + web_script,
            f"module lifecycle execution route enabled: {forbidden_route}",
        )

    contracts = {
        "module-manifest.v2.schema.json",
        "module-provenance.v1.schema.json",
        "module-dsse-envelope.v1.schema.json",
        "module-trust-policy.v1.schema.json",
        "module-admission-request.v1.schema.json",
        "module-admission-result.v1.schema.json",
        "module-permission-review.v1.schema.json",
        "module-permission-review-status.v1.schema.json",
        "module-permission-acknowledgement-request.v1.schema.json",
        "module-permission-acknowledgement.v1.schema.json",
        "module-permission-acknowledgement-result.v1.schema.json",
        "module-permission-acknowledgement-list.v1.schema.json",
        "module-install-lifecycle-request.v1.schema.json",
        "module-install-lifecycle-plan.v1.schema.json",
        "module-lifecycle-status.v1.schema.json",
    }
    present = {path.name for path in (ROOT / "contracts/modules").glob("*.json")}
    require(contracts <= present, "module supply-chain contract missing")
    require(
        (ROOT / "contracts/openapi/home-center-modules.v1.openapi.json").is_file(),
        "module review OpenAPI contract missing",
    )

    version = (ROOT / "product/control-plane/src/home_center/__init__.py").read_text(encoding="utf-8")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    builder = (ROOT / "deploy/scripts/build-artifact.sh").read_text(encoding="utf-8")
    require('__version__ = "0.11.0"' in version, "runtime version is not 0.11.0")
    require('version = "0.11.0"' in project, "package version is not 0.11.0")
    require('[ "$VERSION" = 0.11.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED' in builder, "artifact gate is not 0.11.0")

    print("SECURITY_GATE_0110=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
