from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "product/control-plane/src/home_center/module_artifact.py"
MANIFEST = ROOT / "product/control-plane/src/home_center/module_manifest.py"


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
    tree = ast.parse(source, filename=str(MODULE))

    imports: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or "").split(".", 1)[0])
        elif isinstance(node, ast.Call):
            calls.add(dotted(node.func))

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

    contracts = {
        "module-manifest.v2.schema.json",
        "module-provenance.v1.schema.json",
        "module-dsse-envelope.v1.schema.json",
        "module-trust-policy.v1.schema.json",
    }
    present = {path.name for path in (ROOT / "contracts/modules").glob("*.json")}
    require(contracts <= present, "module supply-chain contract missing")

    version = (ROOT / "product/control-plane/src/home_center/__init__.py").read_text(encoding="utf-8")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    builder = (ROOT / "deploy/scripts/build-artifact.sh").read_text(encoding="utf-8")
    require('__version__ = "0.13.0"' in version, "0.11 safeguards are not inherited by 0.13.0")
    require('version = "0.13.0"' in project, "package version is not 0.13.0")
    require('[ "$VERSION" = 0.13.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED' in builder, "artifact gate is not 0.13.0")

    print("SECURITY_GATE_0110=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

