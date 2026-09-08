from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "product/control-plane/src/home_center"
SOURCE_MANIFEST = ROOT / "SOURCE-MANIFEST.sha256"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SECURITY_GATE_PUBLIC_0141_FAIL: {message}")


def main() -> int:
    expected_web = {"app.css", "app.js", "index.html", "planning.css", "planning.js", "release.js"}
    require({path.name for path in (ROOT / "product/web/static").iterdir()} == expected_web, "public Web shape")
    require(
        {path.name for path in (ROOT / "contracts/openapi").iterdir()} == {"home-center.v1.openapi.json"},
        "OpenAPI is not consolidated",
    )

    forbidden_modules = {
        "module_artifact.py",
        "privileged_helper_v2.py",
        "qualification_0140.py",
        "release_candidate.py",
        "release_channel.py",
        "release_manager.py",
        "tls_activate.py",
        "tls_maintenance.py",
        "tls_reconcile.py",
        "update_reconcile.py",
        "upgrade_policy.py",
    }
    require(not forbidden_modules.intersection(path.name for path in RUNTIME.iterdir()), "forbidden runtime module")

    boundary = (RUNTIME / "product_boundary.py").read_text(encoding="utf-8")
    require("_HOME_CENTER_MODULE_CATEGORIES" in boundary, "positive product taxonomy missing")
    require("_HOME_CENTER_CAPABILITIES" in boundary, "closed capability taxonomy missing")
    require("_DENIED_TOKEN_HASHES" not in boundary, "denylist product policy present")

    fragments = (
        "192.168.10." + "254",
        "192.168.10." + "253",
        "hm" + ".dm",
        "hm" + "-dm",
        "dc" + "01",
        "dc" + "02",
        "Control" + " Center",
    )
    failures: list[str] = []
    require(SOURCE_MANIFEST.is_file() and not SOURCE_MANIFEST.is_symlink(), "source manifest missing")
    manifest_paths: list[str] = []
    for line in SOURCE_MANIFEST.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"[0-9a-f]{64}  ([A-Za-z0-9._/-]+)", line)
        require(match is not None, "source manifest line")
        manifest_paths.append(match.group(1))
    require(manifest_paths == sorted(set(manifest_paths)), "source manifest paths")
    for relative in manifest_paths:
        lowered = relative.casefold()
        for fragment in fragments:
            if fragment.casefold() in lowered:
                failures.append(f"{SOURCE_MANIFEST.relative_to(ROOT)}:{relative}:{fragment}")

    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.is_symlink() or path in {Path(__file__), SOURCE_MANIFEST}:
            continue
        try:
            value = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lowered = value.casefold()
        for fragment in fragments:
            if fragment.casefold() in lowered:
                failures.append(f"{path.relative_to(ROOT)}:{fragment}")
    require(not failures, "deployment or cross-product marker: " + ",".join(failures[:10]))

    for tool in (ROOT / "deploy/scripts/build-release.py",):
        tree = ast.parse(tool.read_text(encoding="utf-8"), filename=str(tool))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            require(name not in {"eval", "exec", "extract", "extractall", "system"}, f"unsafe primitive: {name}")
            if name in {"run", "Popen", "call", "check_call", "check_output"}:
                require(
                    not any(
                        keyword.arg == "shell"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                        for keyword in node.keywords
                    ),
                    "subprocess shell=True",
                )

    workflows = "\n".join(path.read_text(encoding="utf-8") for path in sorted((ROOT / ".github/workflows").glob("*.yml")))
    uses = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", workflows, re.MULTILINE)
    require(bool(uses), "workflow actions missing")
    require(all(re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", item) for item in uses), "workflow action not pinned")
    require("workflow_dispatch" not in workflows, "manual release entrypoint present")
    require("github.repository == 'ControlCenterSoft/home-center-stable'" in workflows, "repository guard missing")

    print("SECURITY_GATE_PUBLIC_0141=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
