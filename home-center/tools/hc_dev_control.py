#!/usr/bin/env python3
"""Repository-driven development control for Home Center.

The tool intentionally uses only the Python standard library.  It validates the
engineering registry, resolves configured GitHub evidence, renders release
progress, and enforces PR/documentation boundaries.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / ".hc-dev" / "releases.json"
RELEASE_BRANCH_RE = re.compile(r"^release/(?P<version>[0-9]+\.[0-9]+\.[0-9]+)$")
TARGET_RELEASE_RE = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?Target release(?:\*\*)?\s*:\s*`?(?P<target>[A-Za-z0-9._/-]+)`?\s*$"
)
ISSUE_REF_RE = re.compile(r"(?i)(?:closes|fixes|resolves|refs?|tracking issue\s*:?)\s+#\d+")
FORBIDDEN_PRODUCT_DOC_PATTERNS = (
    re.compile(r"\bChatGPT\b", re.IGNORECASE),
    re.compile(r"\bCodex\b", re.IGNORECASE),
    re.compile(r"\bAI Development (?:Infrastructure|Fabric|Orchestrator)\b", re.IGNORECASE),
    re.compile(r"\bCI Fabric\b", re.IGNORECASE),
    re.compile(r"\bdevelopment orchestrator\b", re.IGNORECASE),
    re.compile(r"\bagent topology\b", re.IGNORECASE),
    re.compile(r"\bmodel providers?\b", re.IGNORECASE),
    re.compile(r"\bprompt(?:ing)? workflow\b", re.IGNORECASE),
)


class ControlError(RuntimeError):
    pass


def load_registry(path: Path = DEFAULT_REGISTRY) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ControlError(f"release registry not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ControlError(f"invalid release registry JSON: {exc}") from exc
    validate_registry(payload)
    return payload


def validate_registry(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != 1:
        raise ControlError("unsupported release registry schema_version")
    repository = payload.get("repository")
    if not isinstance(repository, str) or repository.count("/") != 1:
        raise ControlError("repository must be owner/name")
    releases = payload.get("releases")
    if not isinstance(releases, list) or not releases:
        raise ControlError("releases must be a non-empty list")

    versions: set[str] = set()
    branches: set[str] = set()
    for release in releases:
        if not isinstance(release, dict):
            raise ControlError("release entry must be an object")
        version = release.get("version")
        branch = release.get("branch")
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ControlError(f"invalid release version: {version!r}")
        if version in versions:
            raise ControlError(f"duplicate release version: {version}")
        versions.add(version)
        expected_branch = f"release/{version}"
        if branch != expected_branch:
            raise ControlError(f"release {version} branch must be {expected_branch}")
        if branch in branches:
            raise ControlError(f"duplicate release branch: {branch}")
        branches.add(branch)
        if release.get("state") not in {"planned", "active", "frozen", "released", "closed"}:
            raise ControlError(f"release {version} has invalid state")

        workstreams = release.get("workstreams")
        if not isinstance(workstreams, list) or not workstreams:
            raise ControlError(f"release {version} must define workstreams")
        ids: set[str] = set()
        for workstream in workstreams:
            if not isinstance(workstream, dict):
                raise ControlError(f"release {version} workstream must be an object")
            wid = workstream.get("id")
            title = workstream.get("title")
            weight = workstream.get("weight")
            evidence = workstream.get("evidence")
            if not isinstance(wid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", wid):
                raise ControlError(f"release {version} invalid workstream id: {wid!r}")
            if wid in ids:
                raise ControlError(f"release {version} duplicate workstream id: {wid}")
            ids.add(wid)
            if not isinstance(title, str) or not title.strip():
                raise ControlError(f"release {version}/{wid} title is required")
            if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
                raise ControlError(f"release {version}/{wid} weight must be > 0")
            if not isinstance(evidence, list):
                raise ControlError(f"release {version}/{wid} evidence must be a list")
            for item in evidence:
                _validate_evidence(version, wid, item)


def _validate_evidence(version: str, wid: str, item: Any) -> None:
    if not isinstance(item, dict):
        raise ControlError(f"release {version}/{wid} evidence item must be an object")
    kind = item.get("kind")
    number = item.get("number")
    state = item.get("state")
    if kind not in {"pr", "issue"}:
        raise ControlError(f"release {version}/{wid} unsupported evidence kind: {kind!r}")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise ControlError(f"release {version}/{wid} evidence number must be a positive integer")
    allowed = {"merged", "open", "closed"} if kind == "pr" else {"open", "closed"}
    if state not in allowed:
        raise ControlError(f"release {version}/{wid} invalid {kind} evidence state: {state!r}")


class GitHubClient:
    def __init__(self, repository: str, token: str | None = None, api_url: str | None = None) -> None:
        self.repository = repository
        self.token = token or os.environ.get("GITHUB_TOKEN")
        self.api_url = (api_url or os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")

    def get(self, path: str) -> dict[str, Any]:
        url = f"{self.api_url}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "home-center-development-control/1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                data = json.load(response)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            raise ControlError(f"GitHub API request failed for {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ControlError(f"unexpected GitHub API response for {path}")
        return data

    def resolve_evidence(self, item: dict[str, Any]) -> tuple[bool, str]:
        kind = item["kind"]
        number = item["number"]
        wanted = item["state"]
        if kind == "pr":
            data = self.get(f"/repos/{self.repository}/pulls/{number}")
            merged = data.get("merged_at") is not None
            actual = "merged" if merged else str(data.get("state", "unknown"))
        else:
            data = self.get(f"/repos/{self.repository}/issues/{number}")
            actual = str(data.get("state", "unknown"))
        return actual == wanted, actual

    def branch_head(self, branch: str) -> str:
        data = self.get(f"/repos/{self.repository}/branches/{branch}")
        commit = data.get("commit")
        if not isinstance(commit, dict) or not isinstance(commit.get("sha"), str):
            raise ControlError(f"unable to resolve branch head: {branch}")
        return commit["sha"]


def _workstream_progress(workstream: dict[str, Any], client: GitHubClient | None) -> tuple[float, list[str]]:
    evidence = workstream["evidence"]
    if not evidence:
        return 0.0, ["no repository evidence configured"]
    if client is None:
        return 0.0, ["repository evidence unresolved (offline)"]

    satisfied = 0
    details: list[str] = []
    for item in evidence:
        ok, actual = client.resolve_evidence(item)
        if ok:
            satisfied += 1
        details.append(f"{item['kind']} #{item['number']}: {actual} (need {item['state']})")
    return satisfied / len(evidence), details


def calculate_status(registry: dict[str, Any], client: GitHubClient | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for release in registry["releases"]:
        workstreams: list[dict[str, Any]] = []
        weighted_done = 0.0
        total_weight = 0.0
        for workstream in release["workstreams"]:
            fraction, details = _workstream_progress(workstream, client)
            weight = float(workstream["weight"])
            total_weight += weight
            weighted_done += fraction * weight
            workstreams.append(
                {
                    "id": workstream["id"],
                    "title": workstream["title"],
                    "progress": round(fraction * 100),
                    "details": details,
                }
            )
        progress = round((weighted_done / total_weight) * 100) if total_weight else 0
        head = client.branch_head(release["branch"]) if client is not None else None
        result.append(
            {
                "version": release["version"],
                "branch": release["branch"],
                "state": release["state"],
                "progress": progress,
                "head": head,
                "workstreams": workstreams,
            }
        )
    return result


def render_bar(progress: int, width: int = 10) -> str:
    bounded = max(0, min(100, progress))
    filled = int(round((bounded / 100) * width))
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def render_markdown(status: list[dict[str, Any]], live: bool) -> str:
    lines = ["# Home Center development status", "", f"Evidence mode: {'live GitHub' if live else 'offline / unresolved'}", ""]
    for release in status:
        lines.append(f"## HC {release['version']} — {release['progress']}%")
        if release.get("head"):
            lines.append(f"Branch: `{release['branch']}` @ `{release['head'][:12]}`")
        else:
            lines.append(f"Branch: `{release['branch']}`")
        lines.append("")
        for workstream in release["workstreams"]:
            lines.append(f"{render_bar(workstream['progress'])} {workstream['title']} — {workstream['progress']}%")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def validate_pr_event(registry: dict[str, Any], event: dict[str, Any]) -> None:
    pr = event.get("pull_request")
    if not isinstance(pr, dict):
        return
    base = pr.get("base")
    if not isinstance(base, dict) or not isinstance(base.get("ref"), str):
        raise ControlError("pull request base branch is missing")
    base_ref = base["ref"]
    body = pr.get("body") or ""
    if not isinstance(body, str):
        raise ControlError("pull request body must be text")

    match = RELEASE_BRANCH_RE.fullmatch(base_ref)
    expected_target = match.group("version") if match else "main" if base_ref == "main" else None
    if expected_target is None:
        return

    configured = {release["version"] for release in registry["releases"]}
    if match and expected_target not in configured:
        raise ControlError(f"target release {expected_target} is not registered in .hc-dev/releases.json")

    target_match = TARGET_RELEASE_RE.search(body)
    if not target_match:
        raise ControlError("PR body must contain `Target release: <version|main>`")
    actual_target = target_match.group("target")
    if actual_target != expected_target:
        raise ControlError(f"PR target release {actual_target!r} does not match base branch {base_ref!r}")
    if not ISSUE_REF_RE.search(body):
        raise ControlError("PR body must reference a tracking Issue using Closes/Fixes/Resolves/Refs/Tracking issue")


def is_product_doc(path: Path) -> bool:
    rel = path.as_posix()
    if rel.startswith(".hc-dev/") or rel == "AGENTS.md":
        return False
    if rel in {"README.md", "STATUS.md", "SECURITY.md"}:
        return True
    if rel.startswith("docs/"):
        return True
    if rel.startswith("product/") and path.suffix.lower() in {".md", ".rst", ".txt"}:
        return True
    return False


def forbidden_product_doc_terms(text: str) -> list[str]:
    found: list[str] = []
    for pattern in FORBIDDEN_PRODUCT_DOC_PATTERNS:
        match = pattern.search(text)
        if match:
            found.append(match.group(0))
    return found


def changed_files(base_sha: str, head_sha: str) -> list[Path]:
    process = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base_sha}...{head_sha}"],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.returncode != 0:
        raise ControlError(f"git diff failed: {process.stderr.strip()}")
    return [Path(line.strip()) for line in process.stdout.splitlines() if line.strip()]


def check_product_doc_boundary(base_sha: str, head_sha: str) -> None:
    violations: list[str] = []
    for path in changed_files(base_sha, head_sha):
        if not is_product_doc(path):
            continue
        full_path = ROOT / path
        if not full_path.is_file():
            continue
        text = full_path.read_text(encoding="utf-8", errors="replace")
        terms = forbidden_product_doc_terms(text)
        if terms:
            violations.append(f"{path}: {', '.join(sorted(set(terms), key=str.lower))}")
    if violations:
        raise ControlError("internal development details found in product-facing documentation:\n- " + "\n- ".join(violations))


def read_event(path: str | None = None) -> dict[str, Any]:
    event_path = Path(path or os.environ.get("GITHUB_EVENT_PATH", ""))
    if not str(event_path):
        raise ControlError("GITHUB_EVENT_PATH is not set")
    try:
        event = json.loads(event_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlError(f"unable to read GitHub event: {exc}") from exc
    if not isinstance(event, dict):
        raise ControlError("GitHub event payload must be an object")
    return event


def cmd_validate(args: argparse.Namespace) -> None:
    registry = load_registry(Path(args.registry))
    print(f"DEVELOPMENT_CONTROL_REGISTRY=PASS releases={len(registry['releases'])}")


def cmd_status(args: argparse.Namespace) -> None:
    registry = load_registry(Path(args.registry))
    client = None
    if args.github:
        client = GitHubClient(registry["repository"])
        if not client.token:
            raise ControlError("--github requires GITHUB_TOKEN")
    status = calculate_status(registry, client)
    markdown = render_markdown(status, live=client is not None)
    if args.output_markdown:
        Path(args.output_markdown).write_text(markdown, encoding="utf-8")
    else:
        print(markdown, end="")
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_pr_gate(args: argparse.Namespace) -> None:
    registry = load_registry(Path(args.registry))
    validate_pr_event(registry, read_event(args.event))
    print("DEVELOPMENT_CONTROL_PR_GATE=PASS")


def cmd_doc_boundary(args: argparse.Namespace) -> None:
    check_product_doc_boundary(args.base, args.head)
    print("DEVELOPMENT_CONTROL_PRODUCT_DOC_BOUNDARY=PASS")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate")
    validate.set_defaults(func=cmd_validate)

    status = sub.add_parser("status")
    status.add_argument("--github", action="store_true", help="resolve evidence against the live GitHub API")
    status.add_argument("--output-markdown")
    status.add_argument("--output-json")
    status.set_defaults(func=cmd_status)

    pr_gate = sub.add_parser("pr-gate")
    pr_gate.add_argument("--event")
    pr_gate.set_defaults(func=cmd_pr_gate)

    doc_boundary = sub.add_parser("doc-boundary")
    doc_boundary.add_argument("--base", required=True)
    doc_boundary.add_argument("--head", required=True)
    doc_boundary.set_defaults(func=cmd_doc_boundary)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        args.func(args)
    except ControlError as exc:
        print(f"DEVELOPMENT_CONTROL=FAIL: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
