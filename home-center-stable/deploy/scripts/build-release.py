#!/usr/bin/env python3
"""Build and verify a versioned Home Center public release candidate."""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence


PRODUCT = "home-center"
SOURCE_MANIFEST = "SOURCE-MANIFEST.sha256"
SEMVER = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class PublicReleaseError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _tool_version() -> str:
    path = Path(__file__).resolve().parents[2] / "VERSION"
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_size > 64:
            raise PublicReleaseError("release_version_invalid")
        payload = path.read_bytes()
        value = payload.decode("ascii").removesuffix("\n")
    except (OSError, UnicodeDecodeError) as exc:
        raise PublicReleaseError("release_version_invalid") from exc
    if payload != (value + "\n").encode("ascii") or SEMVER.fullmatch(value) is None:
        raise PublicReleaseError("release_version_invalid")
    return value


VERSION = _tool_version()
TAG = f"v{VERSION}"
RUNTIME_ARCHIVE = f"home-center-{VERSION}-linux-amd64.tar.gz"
SOURCE_ARCHIVE = f"home-center-{VERSION}-source.tar.gz"
SBOM_NAME = f"home-center-{VERSION}.spdx.json"
ACCEPTANCE_NAME = f"home-center-{VERSION}.acceptance.json"
RELEASE_MANIFEST_NAME = f"home-center-{VERSION}.release-manifest.json"
CHECKSUMS_NAME = "SHA256SUMS"
PAYLOAD_NAMES = (RUNTIME_ARCHIVE, SOURCE_ARCHIVE, SBOM_NAME, ACCEPTANCE_NAME, RELEASE_MANIFEST_NAME)
RELEASE_NAMES = (*PAYLOAD_NAMES, CHECKSUMS_NAME)
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000
MAX_ARCHIVE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 256 * 1024 * 1024
EXCLUDED_TREE_PARTS = frozenset({".git", "__pycache__"})
FORBIDDEN_TREE_PARTS = frozenset({".hc-dev", ".idea", ".vscode", "dist", "docs", "ops", "secrets"})


def _fail(code: str) -> None:
    raise PublicReleaseError(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        _fail(code)


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def _duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        _require(key not in result, "json_duplicate_key")
        result[key] = value
    return result


def read_json(path: Path) -> Any:
    info = _regular(path, "json_file_rejected")
    _require(info.st_size <= MAX_JSON_BYTES, "json_file_too_large")
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicReleaseError("json_invalid") from exc


def write_json(path: Path, value: object) -> None:
    _write(path, canonical_json(value))


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    _regular(path, "hash_input_rejected")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PublicReleaseError("hash_input_rejected") from exc
    return digest.hexdigest()


def _safe_relative(value: str, code: str = "path_rejected") -> PurePosixPath:
    _require(bool(value) and "\\" not in value and "\x00" not in value, code)
    path = PurePosixPath(value)
    _require(not path.is_absolute() and path.parts and all(part not in {"", ".", ".."} for part in path.parts), code)
    return path


def _regular(path: Path, code: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise PublicReleaseError(code) from exc
    _require(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode), code)
    return info


def _directory(path: Path, code: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise PublicReleaseError(code) from exc
    _require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), code)
    return info


def _write(path: Path, payload: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _require(not path.exists() and not path.is_symlink(), "output_collision")
    try:
        path.write_bytes(payload)
        path.chmod(mode)
    except OSError as exc:
        raise PublicReleaseError("output_write_failed") from exc


def _write_text(path: Path, value: str) -> None:
    _write(path, value.encode("utf-8"))


def iter_source_files(root: Path, *, omit: frozenset[str] = frozenset()) -> Iterator[tuple[str, Path]]:
    _directory(root, "source_root_rejected")
    found: list[tuple[str, Path]] = []
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(directory)
        names.sort()
        files.sort()
        kept: list[str] = []
        for name in names:
            relative = (base / name).relative_to(root)
            if set(relative.parts).intersection(EXCLUDED_TREE_PARTS):
                continue
            info = (base / name).lstat()
            _require(not stat.S_ISLNK(info.st_mode) and stat.S_ISDIR(info.st_mode), "source_tree_special_file")
            _require(not set(relative.parts).intersection(FORBIDDEN_TREE_PARTS), "source_tree_forbidden_path")
            kept.append(name)
        names[:] = kept
        for name in files:
            path = base / name
            relative_path = path.relative_to(root)
            if set(relative_path.parts).intersection(EXCLUDED_TREE_PARTS):
                continue
            _require(path.suffix not in {".pyc", ".pyo"}, "source_tree_bytecode_rejected")
            _regular(path, "source_tree_special_file")
            _require(not set(relative_path.parts).intersection(FORBIDDEN_TREE_PARTS), "source_tree_forbidden_path")
            relative = relative_path.as_posix()
            _safe_relative(relative, "source_tree_path_rejected")
            if name not in omit:
                found.append((relative, path))
    yield from sorted(found, key=lambda item: item[0])


def _parse_sums(value: str, code: str) -> dict[str, str]:
    result: dict[str, str] = {}
    previous = ""
    for line in value.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\r\n]+)", line)
        _require(match is not None, code)
        digest, name = match.groups()
        _safe_relative(name, code)
        _require(name not in result and (not previous or previous < name), code)
        result[name] = digest
        previous = name
    _require(bool(result), code)
    return result


def create_source_manifest(root: Path) -> Path:
    target = root / SOURCE_MANIFEST
    _require(not target.exists() and not target.is_symlink(), "source_manifest_collision")
    _reject_release_cycle(root)
    rows = [
        f"{sha256_file(path)}  {relative}"
        for relative, path in iter_source_files(root, omit=frozenset({SOURCE_MANIFEST}))
    ]
    _write_text(target, "\n".join(rows) + "\n")
    verify_source_manifest(root)
    return target


def verify_source_manifest(root: Path) -> None:
    _require(strict_release_version(root) == VERSION, "release_version_mismatch")
    target = root / SOURCE_MANIFEST
    info = _regular(target, "source_manifest_rejected")
    _require(info.st_size <= MAX_JSON_BYTES, "source_manifest_too_large")
    sums = _parse_sums(target.read_text(encoding="ascii"), "source_manifest_invalid")
    expected = {
        relative: sha256_file(path)
        for relative, path in iter_source_files(root, omit=frozenset({SOURCE_MANIFEST}))
    }
    _require(sums == expected, "source_manifest_mismatch")
    _reject_release_cycle(root)


def _reject_release_cycle(root: Path) -> None:
    forbidden = set(RELEASE_NAMES) | {"REVISION", "MANIFEST.sha256"}
    for relative, _ in iter_source_files(root):
        _require(PurePosixPath(relative).name not in forbidden, "release_hash_cycle_rejected")


def strict_source_version(root: Path) -> str:
    path = root / "product/control-plane/src/home_center/__init__.py"
    _regular(path, "runtime_version_file_rejected")
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise PublicReleaseError("runtime_version_invalid") from exc
    values: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            claims_version = any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
            if claims_version:
                _require(
                    len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str),
                    "runtime_version_invalid",
                )
                values.append(node.value.value)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            if isinstance(node.target, ast.Name) and node.target.id == "__version__":
                _fail("runtime_version_invalid")
    _require(len(values) == 1, "runtime_version_invalid")
    _require(SEMVER.fullmatch(values[0]) is not None, "runtime_version_invalid")
    return values[0]


def strict_release_version(root: Path) -> str:
    """Read the canonical VERSION and require all source identities to match it."""

    path = root / "VERSION"
    info = _regular(path, "release_version_file_rejected")
    _require(info.st_size <= 64, "release_version_invalid")
    try:
        payload = path.read_bytes()
        version = payload.decode("ascii").removesuffix("\n")
    except (OSError, UnicodeDecodeError) as exc:
        raise PublicReleaseError("release_version_invalid") from exc
    _require(
        payload == (version + "\n").encode("ascii")
        and SEMVER.fullmatch(version) is not None,
        "release_version_invalid",
    )
    _require(strict_source_version(root) == version, "runtime_version_mismatch")
    pyproject_path = root / "pyproject.toml"
    _regular(pyproject_path, "project_metadata_rejected")
    try:
        project = tomllib.loads(pyproject_path.read_text(encoding="utf-8")).get("project")
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PublicReleaseError("project_metadata_rejected") from exc
    _require(
        isinstance(project, dict) and project.get("version") == version,
        "project_version_mismatch",
    )
    return version


def require_exact_public_head(root: Path, revision: str) -> None:
    _require(HEX40.fullmatch(revision) is not None, "revision_rejected")
    _require(os.environ.get("GITHUB_SHA") == revision, "github_sha_mismatch")
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True, timeout=10
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise PublicReleaseError("git_identity_failed") from exc
    _require(head == revision, "exact_head_mismatch")
    _require(not dirty.strip(), "source_tree_dirty")


def _copy_tree(source: Path, target: Path) -> None:
    _require(not target.exists(), "stage_collision")
    target.mkdir(parents=True)
    for relative, path in iter_source_files(source):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _write(destination, path.read_bytes())


def _content_manifest(root: Path) -> None:
    target = root / "MANIFEST.sha256"
    _require(not target.exists(), "content_manifest_collision")
    rows = [
        f"{sha256_file(path)}  {relative}"
        for relative, path in iter_source_files(root, omit=frozenset({target.name}))
    ]
    _write_text(target, "\n".join(rows) + "\n")


def _archive(root: Path, target: Path, epoch: int, prefix: str | None = None) -> None:
    _require(epoch >= 1, "source_date_epoch_rejected")
    _require(not target.exists() and not target.is_symlink(), "archive_collision")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                    for relative, path in iter_source_files(root):
                        name = f"{prefix}/{relative}" if prefix else relative
                        _safe_relative(name, "archive_path_rejected")
                        payload = path.read_bytes()
                        member = tarfile.TarInfo(name)
                        member.size = len(payload)
                        member.mode = 0o644
                        member.uid = member.gid = 0
                        member.uname = member.gname = ""
                        member.mtime = epoch
                        archive.addfile(member, io.BytesIO(payload))
    except (OSError, tarfile.TarError, ValueError) as exc:
        raise PublicReleaseError("archive_build_failed") from exc


def _archive_files(path: Path) -> tuple[dict[str, bytes], int]:
    _regular(path, "archive_rejected")
    result: dict[str, bytes] = {}
    common_epoch: int | None = None
    expanded_bytes = 0
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            _require(0 < len(members) <= MAX_ARCHIVE_MEMBERS, "archive_member_count_rejected")
            for member in members:
                _safe_relative(member.name, "archive_member_path_rejected")
                _require(member.isfile() and not member.issym() and not member.islnk(), "archive_member_type_rejected")
                _require(member.name not in result, "archive_duplicate_member")
                _require(0 <= member.size <= MAX_ARCHIVE_MEMBER_BYTES, "archive_member_size_rejected")
                expanded_bytes += member.size
                _require(expanded_bytes <= MAX_ARCHIVE_EXPANDED_BYTES, "archive_expanded_size_rejected")
                _require(member.uid == 0 and member.gid == 0 and member.mode == 0o644, "archive_metadata_rejected")
                _require(isinstance(member.mtime, int), "archive_metadata_rejected")
                common_epoch = member.mtime if common_epoch is None else common_epoch
                _require(member.mtime == common_epoch, "archive_mtime_mismatch")
                stream = archive.extractfile(member)
                _require(stream is not None, "archive_member_read_failed")
                remaining = member.size
                chunks: list[bytes] = []
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    _require(bool(chunk), "archive_member_size_mismatch")
                    chunks.append(chunk)
                    remaining -= len(chunk)
                _require(stream.read(1) == b"", "archive_member_size_mismatch")
                payload = b"".join(chunks)
                result[member.name] = payload
    except (OSError, tarfile.TarError) as exc:
        raise PublicReleaseError("archive_rejected") from exc
    _require(common_epoch is not None, "archive_empty")
    return result, common_epoch


def _verify_embedded_manifest(files: Mapping[str, bytes]) -> None:
    _require("MANIFEST.sha256" in files, "content_manifest_missing")
    try:
        sums = _parse_sums(files["MANIFEST.sha256"].decode("ascii"), "content_manifest_invalid")
    except UnicodeDecodeError as exc:
        raise PublicReleaseError("content_manifest_invalid") from exc
    expected = {name: sha256_bytes(value) for name, value in files.items() if name != "MANIFEST.sha256"}
    _require(sums == expected, "content_manifest_mismatch")


def verify_runtime_archive(path: Path, revision: str, epoch: int) -> dict[str, bytes]:
    files, actual_epoch = _archive_files(path)
    _require(actual_epoch == epoch, "runtime_epoch_mismatch")
    _verify_embedded_manifest(files)
    _require(files.get("VERSION") == (VERSION + "\n").encode("ascii"), "runtime_version_mismatch")
    _require(files.get("REVISION") == (revision + "\n").encode("ascii"), "runtime_revision_mismatch")
    return files


def verify_source_archive(path: Path, revision: str, epoch: int) -> dict[str, bytes]:
    files, actual_epoch = _archive_files(path)
    _require(actual_epoch == epoch, "source_epoch_mismatch")
    prefix = f"home-center-{VERSION}/"
    _require(all(name.startswith(prefix) for name in files), "source_prefix_mismatch")
    stripped = {name[len(prefix) :]: value for name, value in files.items()}
    _verify_embedded_manifest(stripped)
    _require(stripped.get("REVISION") == (revision + "\n").encode("ascii"), "source_revision_mismatch")
    _require(stripped.get("VERSION") == (VERSION + "\n").encode("ascii"), "source_version_mismatch")
    return stripped


RUNTIME_TREE_MAPPINGS = (
    ("product/control-plane/src/home_center", "home_center"),
    ("product/web/static", "web"),
    ("contracts", "contracts"),
    ("deploy/systemd", "deploy/systemd"),
    ("deploy/config", "deploy/config"),
    ("deploy/profiles", "deploy/profiles"),
)
RUNTIME_FILE_MAPPINGS = (
    ("deploy/runtime/run.py", "run.py"),
    ("deploy/runtime/backup-run.py", "backup-run.py"),
    ("deploy/runtime/provision-local-admin.py", "provision-local-admin.py"),
    ("deploy/runtime/recover-local-admin.py", "recover-local-admin.py"),
    ("deploy/scripts/install.sh", "deploy/scripts/install.sh"),
    ("deploy/scripts/verify-artifact.sh", "deploy/scripts/verify-artifact.sh"),
)


def build_archives(source_root: Path, output_root: Path, revision: str, epoch: int, *, check_head: bool = True) -> tuple[Path, Path]:
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    _require(HEX40.fullmatch(revision) is not None, "revision_rejected")
    _require(strict_release_version(source_root) == VERSION, "release_version_mismatch")
    if check_head:
        require_exact_public_head(source_root, revision)
    verify_source_manifest(source_root)
    _require(not output_root.exists() and not output_root.is_symlink(), "output_must_not_exist")
    _require(output_root != source_root and source_root not in output_root.parents, "output_inside_source_rejected")
    output_root.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="home-center-release-") as value:
        temporary = Path(value)
        runtime_root = temporary / "runtime"
        runtime_root.mkdir()
        for source_name, target_name in RUNTIME_TREE_MAPPINGS:
            _copy_tree(source_root / source_name, runtime_root / target_name)
        for source_name, target_name in RUNTIME_FILE_MAPPINGS:
            source = source_root / source_name
            _regular(source, "runtime_file_rejected")
            _write(runtime_root / target_name, source.read_bytes())
        _write_text(runtime_root / "VERSION", VERSION + "\n")
        _write_text(runtime_root / "REVISION", revision + "\n")
        _content_manifest(runtime_root)
        runtime_path = output_root / RUNTIME_ARCHIVE
        _archive(runtime_root, runtime_path, epoch)

        source_stage = temporary / "source"
        _copy_tree(source_root, source_stage)
        _write_text(source_stage / "REVISION", revision + "\n")
        _content_manifest(source_stage)
        source_path = output_root / SOURCE_ARCHIVE
        _archive(source_stage, source_path, epoch, prefix=f"home-center-{VERSION}")
    verify_runtime_archive(runtime_path, revision, epoch)
    verify_source_archive(source_path, revision, epoch)
    return runtime_path, source_path


def _timestamp(epoch: int) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _artifact(path: Path, media_type: str | None = None) -> dict[str, object]:
    info = _regular(path, "artifact_rejected")
    value: dict[str, object] = {"name": path.name, "sha256": sha256_file(path), "size": info.st_size}
    if media_type is not None:
        value["media_type"] = media_type
    return value


def generate_spdx(runtime_path: Path, output_path: Path, revision: str, epoch: int) -> None:
    archive_files = verify_runtime_archive(runtime_path, revision, epoch)
    archive_digest = sha256_file(runtime_path)
    package_id = "SPDXRef-Package-home-center"
    files: list[dict[str, object]] = []
    relationships: list[dict[str, str]] = []
    for name in sorted(archive_files):
        file_id = "SPDXRef-File-" + sha256_bytes(name.encode("utf-8"))[:24]
        files.append(
            {
                "SPDXID": file_id,
                "checksums": [{"algorithm": "SHA256", "checksumValue": sha256_bytes(archive_files[name])}],
                "copyrightText": "NOASSERTION",
                "fileName": "./" + name,
                "licenseConcluded": "NOASSERTION",
                "licenseInfoInFiles": ["NOASSERTION"],
            }
        )
        relationships.append({"relatedSpdxElement": file_id, "relationshipType": "CONTAINS", "spdxElementId": package_id})
    value = {
        "SPDXID": "SPDXRef-DOCUMENT",
        "creationInfo": {"created": _timestamp(epoch), "creators": [f"Tool: home-center-public-release-{VERSION}"]},
        "dataLicense": "CC0-1.0",
        "documentDescribes": [package_id],
        "documentNamespace": f"https://spdx.org/spdxdocs/home-center-{VERSION}-{archive_digest}",
        "files": files,
        "name": f"Home Center {VERSION} runtime SBOM",
        "packages": [
            {
                "SPDXID": package_id,
                "checksums": [{"algorithm": "SHA256", "checksumValue": archive_digest}],
                "copyrightText": "NOASSERTION",
                "downloadLocation": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceLocator": f"pkg:generic/home-center@{VERSION}?arch=amd64",
                        "referenceType": "purl",
                    }
                ],
                "filesAnalyzed": True,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "licenseInfoFromFiles": ["NOASSERTION"],
                "name": PRODUCT,
                "packageFileName": runtime_path.name,
                "versionInfo": VERSION,
            }
        ],
        "relationships": relationships,
        "spdxVersion": "SPDX-2.3",
    }
    write_json(output_path, value)
    validate_spdx(output_path, runtime_path, revision, epoch)


def validate_spdx(path: Path, runtime_path: Path, revision: str, epoch: int) -> None:
    value = read_json(path)
    _require(isinstance(value, dict), "spdx_invalid")
    _require(value.get("spdxVersion") == "SPDX-2.3" and value.get("dataLicense") == "CC0-1.0", "spdx_identity_mismatch")
    _require(value.get("creationInfo") == {"created": _timestamp(epoch), "creators": [f"Tool: home-center-public-release-{VERSION}"]}, "spdx_creation_mismatch")
    packages = value.get("packages")
    _require(isinstance(packages, list) and len(packages) == 1 and isinstance(packages[0], dict), "spdx_package_invalid")
    _require(
        packages[0].get("checksums") == [{"algorithm": "SHA256", "checksumValue": sha256_file(runtime_path)}]
        and packages[0].get("packageFileName") == runtime_path.name
        and packages[0].get("versionInfo") == VERSION,
        "spdx_package_mismatch",
    )
    expected = {"./" + name: sha256_bytes(payload) for name, payload in verify_runtime_archive(runtime_path, revision, epoch).items()}
    actual: dict[str, str] = {}
    files = value.get("files")
    _require(isinstance(files, list), "spdx_files_invalid")
    for record in files:
        _require(isinstance(record, dict) and isinstance(record.get("fileName"), str), "spdx_files_invalid")
        checksums = record.get("checksums")
        _require(isinstance(checksums, list) and len(checksums) == 1 and isinstance(checksums[0], dict), "spdx_files_invalid")
        _require(checksums[0].get("algorithm") == "SHA256" and HEX64.fullmatch(str(checksums[0].get("checksumValue"))), "spdx_files_invalid")
        _require(str(record["fileName"]) not in actual, "spdx_files_invalid")
        actual[str(record["fileName"])] = str(checksums[0]["checksumValue"])
    _require(actual == expected, "spdx_file_digest_mismatch")
    _require(path.read_bytes() == canonical_json(value), "spdx_not_canonical")


def _qualification_id(value: Mapping[str, object]) -> str:
    unsigned = dict(value)
    unsigned.pop("qualification_id", None)
    return "sha256:" + sha256_bytes(canonical_json(unsigned))


def generate_acceptance(root: Path, runtime_a: Path, runtime_b: Path, source_a: Path, source_b: Path, revision: str, epoch: int) -> None:
    _require(runtime_a.read_bytes() == runtime_b.read_bytes(), "runtime_not_reproducible")
    _require(source_a.read_bytes() == source_b.read_bytes(), "source_not_reproducible")
    validate_spdx(root / SBOM_NAME, root / RUNTIME_ARCHIVE, revision, epoch)
    value: dict[str, object] = {
        "artifacts": [_artifact(root / name) for name in (RUNTIME_ARCHIVE, SOURCE_ARCHIVE, SBOM_NAME)],
        "checks": {
            "exact_public_head": True,
            "hash_cycle_free": True,
            "runtime_manifest": True,
            "runtime_reproducible": True,
            "source_manifest": True,
            "source_reproducible": True,
            "spdx_2_3": True,
        },
        "generated_at": _timestamp(epoch),
        "product": PRODUCT,
        "qualification_id": "",
        "revision": revision,
        "schema": "home-center.public-release-acceptance.v1",
        "source_date_epoch": epoch,
        "status": "PASS",
        "version": VERSION,
    }
    value["qualification_id"] = _qualification_id(value)
    write_json(root / ACCEPTANCE_NAME, value)
    validate_acceptance(root / ACCEPTANCE_NAME, root, revision, epoch)


def _validate_records(value: object, root: Path, names: Sequence[str], media: Mapping[str, str] | None = None) -> None:
    _require(isinstance(value, list) and len(value) == len(names), "artifact_records_invalid")
    expected = [_artifact(root / name, media[name] if media else None) for name in names]
    _require(value == expected, "artifact_records_mismatch")


def validate_acceptance(path: Path, root: Path, revision: str, epoch: int) -> dict[str, object]:
    value = read_json(path)
    _require(isinstance(value, dict), "acceptance_invalid")
    _require(
        set(value)
        == {"artifacts", "checks", "generated_at", "product", "qualification_id", "revision", "schema", "source_date_epoch", "status", "version"},
        "acceptance_invalid",
    )
    _require(
        value.get("schema") == "home-center.public-release-acceptance.v1"
        and value.get("product") == PRODUCT
        and value.get("version") == VERSION
        and value.get("revision") == revision
        and value.get("source_date_epoch") == epoch
        and value.get("generated_at") == _timestamp(epoch)
        and value.get("status") == "PASS",
        "acceptance_identity_mismatch",
    )
    _require(value.get("checks") == {
        "exact_public_head": True,
        "hash_cycle_free": True,
        "runtime_manifest": True,
        "runtime_reproducible": True,
        "source_manifest": True,
        "source_reproducible": True,
        "spdx_2_3": True,
    }, "acceptance_checks_invalid")
    _validate_records(value.get("artifacts"), root, (RUNTIME_ARCHIVE, SOURCE_ARCHIVE, SBOM_NAME))
    _require(value.get("qualification_id") == _qualification_id(value), "acceptance_id_mismatch")
    _require(path.read_bytes() == canonical_json(value), "acceptance_not_canonical")
    return value


MEDIA_TYPES = {
    RUNTIME_ARCHIVE: "application/gzip",
    SOURCE_ARCHIVE: "application/gzip",
    SBOM_NAME: "application/spdx+json",
    ACCEPTANCE_NAME: "application/vnd.home-center.acceptance+json",
}


def generate_release_manifest(root: Path, revision: str, epoch: int) -> None:
    acceptance = validate_acceptance(root / ACCEPTANCE_NAME, root, revision, epoch)
    value = {
        "acceptance": {"name": ACCEPTANCE_NAME, "qualification_id": acceptance["qualification_id"]},
        "artifacts": [_artifact(root / name, MEDIA_TYPES[name]) for name in MEDIA_TYPES],
        "product": PRODUCT,
        "publication": {"annotated_tag_required": True, "artifact_reuse_required": True, "no_clobber_required": True},
        "revision": revision,
        "schema": "home-center.public-release-manifest.v1",
        "source_date_epoch": epoch,
        "status": "qualified",
        "tag": TAG,
        "version": VERSION,
    }
    write_json(root / RELEASE_MANIFEST_NAME, value)
    validate_release_manifest(root / RELEASE_MANIFEST_NAME, root, revision, epoch)


def validate_release_manifest(path: Path, root: Path, revision: str, epoch: int) -> None:
    value = read_json(path)
    _require(isinstance(value, dict), "release_manifest_invalid")
    _require(
        set(value) == {"acceptance", "artifacts", "product", "publication", "revision", "schema", "source_date_epoch", "status", "tag", "version"},
        "release_manifest_invalid",
    )
    _require(
        value.get("schema") == "home-center.public-release-manifest.v1"
        and value.get("product") == PRODUCT
        and value.get("version") == VERSION
        and value.get("tag") == TAG
        and value.get("revision") == revision
        and value.get("source_date_epoch") == epoch
        and value.get("status") == "qualified",
        "release_manifest_identity_mismatch",
    )
    _validate_records(value.get("artifacts"), root, tuple(MEDIA_TYPES), MEDIA_TYPES)
    acceptance = validate_acceptance(root / ACCEPTANCE_NAME, root, revision, epoch)
    _require(value.get("acceptance") == {"name": ACCEPTANCE_NAME, "qualification_id": acceptance["qualification_id"]}, "release_manifest_acceptance_mismatch")
    _require(value.get("publication") == {"annotated_tag_required": True, "artifact_reuse_required": True, "no_clobber_required": True}, "release_manifest_publication_mismatch")
    _require(path.read_bytes() == canonical_json(value), "release_manifest_not_canonical")


def generate_checksums(root: Path) -> None:
    target = root / CHECKSUMS_NAME
    _require(not target.exists() and not target.is_symlink(), "checksums_collision")
    rows = [f"{sha256_file(root / name)}  {name}" for name in sorted(PAYLOAD_NAMES)]
    _write_text(target, "\n".join(rows) + "\n")
    verify_checksums(root)


def verify_checksums(root: Path) -> None:
    sums = _parse_sums((root / CHECKSUMS_NAME).read_text(encoding="ascii"), "checksums_invalid")
    expected = {name: sha256_file(root / name) for name in sorted(PAYLOAD_NAMES)}
    _require(sums == expected, "checksums_mismatch")


def build_candidate(source_root: Path, output_root: Path, revision: str, epoch: int) -> None:
    require_exact_public_head(source_root, revision)
    _require(not output_root.exists() and not output_root.is_symlink(), "output_must_not_exist")
    with tempfile.TemporaryDirectory(prefix="home-center-candidate-") as value:
        temporary = Path(value)
        first = temporary / "first"
        second = temporary / "second"
        runtime_a, source_a = build_archives(source_root, first, revision, epoch, check_head=False)
        runtime_b, source_b = build_archives(source_root, second, revision, epoch, check_head=False)
        _require(runtime_a.read_bytes() == runtime_b.read_bytes(), "runtime_not_reproducible")
        _require(source_a.read_bytes() == source_b.read_bytes(), "source_not_reproducible")
        output_root.mkdir(parents=True)
        shutil.copyfile(runtime_a, output_root / RUNTIME_ARCHIVE)
        shutil.copyfile(source_a, output_root / SOURCE_ARCHIVE)
        generate_spdx(output_root / RUNTIME_ARCHIVE, output_root / SBOM_NAME, revision, epoch)
        generate_acceptance(output_root, runtime_a, runtime_b, source_a, source_b, revision, epoch)
        generate_release_manifest(output_root, revision, epoch)
        generate_checksums(output_root)
    verify_bundle(output_root, revision, epoch)


def verify_release_json(path: Path) -> None:
    value = read_json(path)
    _require(isinstance(value, dict), "release_report_invalid")
    _require(value.get("tag_name") == TAG and value.get("draft") is False and value.get("prerelease") is False, "release_identity_mismatch")
    assets = value.get("assets")
    _require(isinstance(assets, list), "release_assets_invalid")
    names = sorted(str(item.get("name")) for item in assets if isinstance(item, dict))
    _require(names == sorted(RELEASE_NAMES), "release_assets_mismatch")


def verify_bundle(root: Path, revision: str, epoch: int, release_json: Path | None = None) -> None:
    actual = sorted(relative for relative, _ in iter_source_files(root))
    _require(actual == sorted(RELEASE_NAMES), "release_bundle_shape_mismatch")
    verify_checksums(root)
    verify_runtime_archive(root / RUNTIME_ARCHIVE, revision, epoch)
    verify_source_archive(root / SOURCE_ARCHIVE, revision, epoch)
    validate_spdx(root / SBOM_NAME, root / RUNTIME_ARCHIVE, revision, epoch)
    validate_acceptance(root / ACCEPTANCE_NAME, root, revision, epoch)
    validate_release_manifest(root / RELEASE_MANIFEST_NAME, root, revision, epoch)
    if release_json is not None:
        verify_release_json(release_json)


def _positive(value: str) -> int:
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    candidate = commands.add_parser("candidate")
    candidate.add_argument("--source-root", type=Path, required=True)
    candidate.add_argument("--output", type=Path, required=True)
    candidate.add_argument("--revision", required=True)
    candidate.add_argument("--source-date-epoch", type=_positive, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--directory", type=Path, required=True)
    verify.add_argument("--revision", required=True)
    verify.add_argument("--source-date-epoch", type=_positive, required=True)
    verify.add_argument("--release-json", type=Path)
    source = commands.add_parser("verify-source")
    source.add_argument("--source-root", type=Path, required=True)
    version = commands.add_parser("source-version")
    version.add_argument("--source-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "candidate":
            build_candidate(args.source_root, args.output, args.revision, args.source_date_epoch)
        elif args.command == "verify":
            verify_bundle(args.directory, args.revision, args.source_date_epoch, args.release_json)
        elif args.command == "verify-source":
            verify_source_manifest(args.source_root)
        elif args.command == "source-version":
            version = strict_release_version(args.source_root)
            _require(version == VERSION, "release_version_mismatch")
            print(version)
            return 0
        else:  # pragma: no cover
            _fail("command_rejected")
    except PublicReleaseError as exc:
        print(f"PUBLIC_RELEASE=FAIL version={VERSION} code={exc.code}", file=sys.stderr)
        return 1
    print(f"PUBLIC_RELEASE=PASS version={VERSION} command={args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
