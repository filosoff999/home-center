from __future__ import annotations

import ast
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "product/control-plane/src/home_center"
SOURCE_MANIFEST = ROOT / "SOURCE-MANIFEST.sha256"
APPROVED_SOURCE = ROOT / "APPROVED-SOURCE.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SECURITY_GATE_PUBLIC_RELEASE_FAIL: {message}")


def main() -> int:
    base_web = {"app.css", "app.js", "index.html", "planning.css", "planning.js", "release.js"}
    member_change_web = base_web | {"member-change.css", "member-change.js"}
    device_registration_web = member_change_web | {"device-registration.js"}
    device_management_web = device_registration_web | {"device-management-plan.js"}
    device_enrollment_web = device_management_web | {"device-enrollment.js"}
    device_provider_resolution_web = device_enrollment_web | {"device-provider-resolution.js"}
    actual_web = {path.name for path in (ROOT / "product/web/static").iterdir()}
    require(
        actual_web == base_web
        or actual_web == member_change_web
        or actual_web == device_registration_web
        or actual_web == device_management_web
        or actual_web == device_enrollment_web
        or actual_web == device_provider_resolution_web,
        "public Web shape",
    )
    index_html = (ROOT / "product/web/static/index.html").read_text(encoding="utf-8")
    if actual_web in (device_enrollment_web, device_provider_resolution_web):
        require('/static/device-registration.js' in index_html, "device registration UI is not loaded")
        require('/static/device-management-plan.js' in index_html, "device management UI is not loaded")
        require('/static/device-enrollment.js' in index_html, "device enrollment UI is not loaded")
    if actual_web == device_provider_resolution_web:
        require('/static/device-provider-resolution.js' in index_html, "device provider-resolution UI is not loaded")
    require(
        {path.name for path in (ROOT / "contracts/openapi").iterdir()} == {"home-center.v1.openapi.json"},
        "OpenAPI is not consolidated",
    )
    release_contracts = {
        "approved-source-provenance.v1.schema.json",
        "public-release-acceptance.v1.schema.json",
        "public-release-manifest.v1.schema.json",
    }
    require(
        {path.name for path in (ROOT / "contracts/releases").iterdir()} == release_contracts,
        "release contracts are not generic",
    )
    for name in release_contracts:
        json.loads((ROOT / "contracts/releases" / name).read_text(encoding="utf-8"))

    provenance = json.loads(APPROVED_SOURCE.read_text(encoding="utf-8"))
    require(provenance.get("version") == (ROOT / "VERSION").read_text(encoding="ascii").strip(), "source provenance version")
    require(re.fullmatch(r"[0-9a-f]{40}", str(provenance.get("approved_revision"))) is not None, "source provenance revision")

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
        if not path.is_file() or path.is_symlink() or path in {Path(__file__), SOURCE_MANIFEST, APPROVED_SOURCE}:
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

    build_tool = ROOT / "deploy/scripts/build-release.py"
    for tool in (build_tool,):
        tree = ast.parse(tool.read_text(encoding="utf-8"), filename=str(tool))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
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

    concrete_semver = re.compile(
        r"(?<![0-9])(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?![0-9])"
    )
    for path in (
        ROOT / "deploy/scripts/build-release.py",
        ROOT / "deploy/scripts/install.sh",
        ROOT / "deploy/scripts/verify-artifact.sh",
    ):
        require(concrete_semver.search(path.read_text(encoding="utf-8")) is None, f"hardcoded version: {path.name}")

    current_version = (ROOT / "VERSION").read_text(encoding="ascii").removesuffix("\n")
    release_workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    verify_workflow = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
    workflows = release_workflow + "\n" + verify_workflow
    uses = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", workflows, re.MULTILINE)
    require(bool(uses), "workflow actions missing")
    require(all(re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", item) for item in uses), "workflow action not pinned")
    require("workflow_dispatch" not in workflows, "manual release entrypoint present")
    require("pull_request_target" not in workflows, "privileged pull request trigger present")
    require(
        "github.repository == 'ControlCenterSoft/home-center-stable'" in release_workflow
        and "github.repository == 'ControlCenterSoft/home-center-stable'" in verify_workflow,
        "repository guard missing",
    )
    version_specific_release_tokens = (
        f"release/{current_version}",
        f"Home Center {current_version}",
        f"home-center-{current_version}-linux",
        f"home-center-{current_version}-source",
    )
    require(
        not any(token in release_workflow for token in version_specific_release_tokens),
        "release workflow hardcodes current version identity",
    )
    require("startsWith(github.ref_name, 'release/')" in release_workflow, "release branch gate missing")
    require('release_branch="release/$version"' in release_workflow, "VERSION-derived branch missing")
    require('release_tag="v$version"' in release_workflow, "VERSION-derived tag missing")
    require(release_workflow.count("main_record=") == 2, "exact main is not checked twice")
    require(release_workflow.count("require_tag_vacant \"") == 2, "tag vacancy is not checked twice")
    require(release_workflow.count("require_release_vacant \"") == 2, "release vacancy is not checked twice")
    candidate_build = release_workflow.index("build-release.py candidate")
    tag_mutation = release_workflow.index("- name: Create annotated release tag")
    release_mutation = release_workflow.index("- name: Create and publish release from reused assets")
    require(
        release_workflow.index('require_tag_vacant "$release_tag"') < candidate_build
        and release_workflow.index('require_release_vacant "$release_tag"') < candidate_build,
        "qualification vacancy check occurs after candidate build",
    )
    require(
        release_workflow.rindex('require_tag_vacant "$RELEASE_TAG"') < tag_mutation
        and release_workflow.rindex('require_release_vacant "$RELEASE_TAG"') < tag_mutation
        and tag_mutation < release_mutation,
        "publication mutation occurs before final vacancy check",
    )
    require("artifact-ids: ${{ needs.qualify.outputs.artifact_id }}" in release_workflow, "qualified artifact ID is not reused")
    require(release_workflow.count("build-release.py candidate") == 1, "candidate must be built exactly once")
    require("deploy/scripts/install.sh" not in release_workflow, "publication workflow performs deployment")
    require('python-version: ["3.12", "3.13", "3.14"]' in release_workflow, "release Python matrix incomplete")
    require('python-version: ["3.12", "3.13", "3.14"]' in verify_workflow, "Python matrix incomplete")

    print(f"SECURITY_GATE_PUBLIC_RELEASE=PASS version={current_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
