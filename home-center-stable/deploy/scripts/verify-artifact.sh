#!/usr/bin/env bash
set -Eeuo pipefail

artifact_root="${1:-.}"
python3 - "$artifact_root" <<'PY'
from __future__ import annotations

import ast
import hashlib
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath

root = Path(sys.argv[1]).resolve()
if not root.is_dir() or root.is_symlink():
    raise SystemExit("ARTIFACT_ROOT_REJECTED")
manifest = root / "MANIFEST.sha256"
if not manifest.is_file() or manifest.is_symlink():
    raise SystemExit("ARTIFACT_MANIFEST_REJECTED")

expected: dict[str, str] = {}
previous = ""
for line in manifest.read_text(encoding="ascii").splitlines():
    match = re.fullmatch(r"([0-9a-f]{64})  ([^\r\n]+)", line)
    if match is None:
        raise SystemExit("ARTIFACT_MANIFEST_INVALID")
    digest, name = match.groups()
    relative = PurePosixPath(name)
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise SystemExit("ARTIFACT_PATH_REJECTED")
    if name in expected or (previous and previous >= name):
        raise SystemExit("ARTIFACT_MANIFEST_ORDER_REJECTED")
    expected[name] = digest
    previous = name

actual: dict[str, Path] = {}
for directory, names, files in os.walk(root, topdown=True, followlinks=False):
    base = Path(directory)
    names.sort()
    files.sort()
    for name in names:
        info = (base / name).lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise SystemExit("ARTIFACT_SPECIAL_FILE_REJECTED")
    for name in files:
        path = base / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise SystemExit("ARTIFACT_SPECIAL_FILE_REJECTED")
        relative = path.relative_to(root).as_posix()
        if relative != "MANIFEST.sha256":
            actual[relative] = path

if set(actual) != set(expected):
    raise SystemExit("ARTIFACT_SHAPE_MISMATCH")
for name, path in actual.items():
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected[name]:
        raise SystemExit("ARTIFACT_DIGEST_MISMATCH")
version_payload = (root / "VERSION").read_bytes()
try:
    version = version_payload.decode("ascii").removesuffix("\n")
except UnicodeDecodeError as exc:
    raise SystemExit("ARTIFACT_VERSION_MISMATCH") from exc
if (
    version_payload != (version + "\n").encode("ascii")
    or re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version) is None
):
    raise SystemExit("ARTIFACT_VERSION_MISMATCH")
revision_payload = (root / "REVISION").read_bytes()
try:
    revision = revision_payload.decode("ascii").removesuffix("\n")
except UnicodeDecodeError as exc:
    raise SystemExit("ARTIFACT_REVISION_REJECTED") from exc
if revision_payload != (revision + "\n").encode("ascii") or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
    raise SystemExit("ARTIFACT_REVISION_REJECTED")
runtime_version_path = root / "home_center/__init__.py"
if not runtime_version_path.is_file() or runtime_version_path.is_symlink():
    raise SystemExit("ARTIFACT_RUNTIME_VERSION_REJECTED")
try:
    tree = ast.parse(runtime_version_path.read_text(encoding="utf-8"), filename=str(runtime_version_path))
except (OSError, UnicodeDecodeError, SyntaxError) as exc:
    raise SystemExit("ARTIFACT_RUNTIME_VERSION_REJECTED") from exc
runtime_versions = []
for node in tree.body:
    if isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
    ):
        if (
            len(node.targets) != 1
            or not isinstance(node.targets[0], ast.Name)
            or not isinstance(node.value, ast.Constant)
            or not isinstance(node.value.value, str)
        ):
            raise SystemExit("ARTIFACT_RUNTIME_VERSION_REJECTED")
        runtime_versions.append(node.value.value)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
        if node.target.id == "__version__":
            raise SystemExit("ARTIFACT_RUNTIME_VERSION_REJECTED")
if runtime_versions != [version]:
    raise SystemExit("ARTIFACT_RUNTIME_VERSION_MISMATCH")
print(f"HOME_CENTER_ARTIFACT=PASS version={version} revision={revision}")
PY
