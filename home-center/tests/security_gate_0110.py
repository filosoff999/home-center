from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "product/control-plane/src/home_center/module_artifact.py"
MANIFEST = ROOT / "product/control-plane/src/home_center/module_manifest.py"
PLANNER = ROOT / "product/control-plane/src/home_center/module_admission.py"


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
    tree = ast.parse(source, filename=str(MODULE))
    planner_tree = ast.parse(planner, filename=str(PLANNER))

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

    contracts = {
        "module-manifest.v2.schema.json",
        "module-provenance.v1.schema.json",
        "module-dsse-envelope.v1.schema.json",
        "module-trust-policy.v1.schema.json",
        "module-admission-request.v1.schema.json",
        "module-admission-result.v1.schema.json",
    }
    present = {path.name for path in (ROOT / "contracts/modules").glob("*.json")}
    require(contracts <= present, "module supply-chain contract missing")

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
