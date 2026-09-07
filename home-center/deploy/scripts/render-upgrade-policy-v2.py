#!/usr/bin/env python3
# Generalize staged upgrade admission and bridge legacy local auth safely.

from __future__ import annotations

import sys
from pathlib import Path

TARGET_VERSION = "0.10.0"
IMMEDIATE_SOURCE_VERSION = "0.9.2"
IMMEDIATE_SOURCE_REVISION = "689d90995a4f365e2f19640486b136b525f29c6d"
IMMEDIATE_SOURCE_RELEASE = "/opt/home-center/releases/0.9.2-689d90995a4f-90d5cb0b657a"
LOCAL_ADMIN = "/etc/home-center/secrets/local-admin.json"
LEGACY = "/etc/home-center/secrets/admin.token"

SEMVER_FUNCTION = r'''UPGRADE_POLICY_SCHEMA=home-center.upgrade-policy.v2
source_identity_admitted() {
  local version=$1 revision=$2 release=$3
  /usr/bin/python3 -I - "$version" "$revision" "$release" "$TARGET_VERSION" <<'PY'
import re
import sys

source_text, revision, release, target_text = sys.argv[1:]
pattern = re.compile(r"^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)$")
source_match = pattern.fullmatch(source_text)
target_match = pattern.fullmatch(target_text)
if source_match is None or target_match is None:
    raise SystemExit("UPGRADE_SEMVER_REJECTED")
source = tuple(int(value) for value in source_match.groups())
target = tuple(int(value) for value in target_match.groups())
if source >= target:
    raise SystemExit("UPGRADE_DIRECTION_REJECTED")
if not (target[1] == 0 and target[2] == 0) and source[0] != target[0]:
    raise SystemExit("UPGRADE_LINE_REJECTED")
if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
    raise SystemExit("UPGRADE_SOURCE_REVISION_REJECTED")
release_pattern = re.compile(
    r"^/opt/home-center/releases/"
    + re.escape(source_text)
    + r"-"
    + re.escape(revision[:12])
    + r"-[0-9a-f]{12}$"
)
if release_pattern.fullmatch(release) is None:
    raise SystemExit("UPGRADE_SOURCE_RELEASE_REJECTED")
PY
}'''

MIGRATION_PY = r'''import base64
import grp
import hashlib
import json
import os
import stat
import sys

target, legacy = sys.argv[1:]
expected_gid = grp.getgrnam("home-center").gr_gid
if os.path.lexists(target):
    info = os.lstat(target)
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != 0
        or info.st_gid != expected_gid
        or stat.S_IMODE(info.st_mode) != 0o640
        or info.st_size < 1
        or info.st_size > 16384
    ):
        raise SystemExit("local_admin_existing_metadata_rejected")
    print("LOCAL_ADMIN_MIGRATION=EXISTING")
    raise SystemExit(0)

info = os.lstat(legacy)
if (
    not stat.S_ISREG(info.st_mode)
    or stat.S_ISLNK(info.st_mode)
    or info.st_nlink != 1
    or info.st_uid != 0
    or info.st_gid != expected_gid
    or stat.S_IMODE(info.st_mode) != 0o640
    or info.st_size < 12
    or info.st_size > 4096
):
    raise SystemExit("legacy_local_admin_metadata_rejected")
flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
fd = os.open(legacy, flags)
try:
    raw = os.read(fd, 4097)
finally:
    os.close(fd)
if len(raw) != info.st_size or len(raw) > 4096:
    raise SystemExit("legacy_local_admin_read_rejected")
if raw.endswith(b"\n"):
    raw = raw[:-1]
if raw.endswith(b"\r"):
    raw = raw[:-1]
if not 12 <= len(raw) <= 256 or any(value in raw for value in (0, 10, 13)):
    raise SystemExit("legacy_local_admin_secret_shape_rejected")

salt = os.urandom(32)
verifier = hashlib.scrypt(raw, salt=salt, n=32768, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
document = {
    "schema": "home-center.local-admin-credential.v1",
    "username": "admin",
    "kdf": "scrypt",
    "n": 32768,
    "r": 8,
    "p": 1,
    "dklen": 32,
    "salt_b64": base64.b64encode(salt).decode("ascii"),
    "verifier_b64": base64.b64encode(verifier).decode("ascii"),
}
payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
parent = os.path.dirname(target)
tmp = os.path.join(parent, ".local-admin.json.migrating")
descriptor = os.open(
    tmp,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    0o600,
)
try:
    os.fchown(descriptor, 0, expected_gid)
    os.fchmod(descriptor, 0o640)
    written = os.write(descriptor, payload)
    if written != len(payload):
        raise SystemExit("local_admin_migration_short_write")
    os.fsync(descriptor)
finally:
    os.close(descriptor)
os.replace(tmp, target)
directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
print("LOCAL_ADMIN_MIGRATION=CREATED")'''


def local_migration_function() -> str:
    return (
        f"LEGACY_LOCAL_ADMIN_MIGRATION_SOURCE={LEGACY}\n"
        "migrate_local_admin_credential() {\n"
        f'  /usr/bin/python3 -I - "{LOCAL_ADMIN}" "$LEGACY_LOCAL_ADMIN_MIGRATION_SOURCE" <<\'PY\'\n'
        + MIGRATION_PY
        + "\nPY\n}\n"
    )


def remote_migration_function() -> str:
    return (
        "migrate_remote_local_admin_credential() {\n"
        f'  "${{SSH[@]}}" sudo -n /usr/bin/python3 -I - "{LOCAL_ADMIN}" "{LEGACY}" <<\'PY\'\n'
        + MIGRATION_PY
        + "\nPY\n}\n"
    )


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"upgrade_policy_{label}_shape_rejected:{count}")
    return source.replace(old, new, 1)


def render(bootstrap_path: Path, installer_path: Path) -> None:
    bootstrap = bootstrap_path.read_text(encoding="utf-8")
    installer = installer_path.read_text(encoding="utf-8")

    exact = (
        f"ADMITTED_SOURCE_V092_VERSION={IMMEDIATE_SOURCE_VERSION}\n"
        f"ADMITTED_SOURCE_V092_REVISION={IMMEDIATE_SOURCE_REVISION}\n"
        f"ADMITTED_SOURCE_V092_RELEASE={IMMEDIATE_SOURCE_RELEASE}\n\n"
        "source_identity_admitted() {\n"
        "  local version=$1 revision=$2 release=$3\n"
        '  [ "$version" = "$ADMITTED_SOURCE_V092_VERSION" ] \\\n'
        '    && [ "$revision" = "$ADMITTED_SOURCE_V092_REVISION" ] \\\n'
        '    && [ "$release" = "$ADMITTED_SOURCE_V092_RELEASE" ]\n'
        "}"
    )
    bootstrap = replace_once(bootstrap, exact, SEMVER_FUNCTION, "source_admission")

    bootstrap = replace_once(
        bootstrap,
        "validate_local_admin_credential() {\n",
        local_migration_function() + remote_migration_function() + "validate_local_admin_credential() {\n",
        "bootstrap_migration_functions",
    )
    bootstrap = replace_once(
        bootstrap,
        f'validate_local_admin_credential "{LOCAL_ADMIN}"\n'
        "validate_remote_local_admin_credential\n"
        "echo LOCAL_ADMIN_CLUSTER_PREFLIGHT=PASS",
        "migrate_local_admin_credential\n"
        "migrate_remote_local_admin_credential\n"
        "echo LOCAL_ADMIN_MIGRATION_FROM_LEGACY=PASS\n"
        "echo LEGACY_TOKEN_RETAINED_FOR_ROLLBACK_ONLY=PASS\n"
        f'validate_local_admin_credential "{LOCAL_ADMIN}"\n'
        "validate_remote_local_admin_credential\n"
        "echo LOCAL_ADMIN_CLUSTER_PREFLIGHT=PASS",
        "bootstrap_migration_call",
    )

    installer = replace_once(
        installer,
        "validate_local_admin_credential() {\n",
        local_migration_function() + "validate_local_admin_credential() {\n",
        "installer_migration_function",
    )
    installer = replace_once(
        installer,
        f'validate_local_admin_credential "{LOCAL_ADMIN}"\n'
        "echo LOCAL_ADMIN_DEPLOYMENT_PREFLIGHT=PASS",
        "migrate_local_admin_credential\n"
        "echo LOCAL_ADMIN_MIGRATION_FROM_LEGACY=PASS\n"
        "echo LEGACY_TOKEN_RETAINED_FOR_ROLLBACK_ONLY=PASS\n"
        f'validate_local_admin_credential "{LOCAL_ADMIN}"\n'
        "echo LOCAL_ADMIN_DEPLOYMENT_PREFLIGHT=PASS",
        "installer_migration_call",
    )

    combined = bootstrap + "\n" + installer
    for forbidden in ("Authorization: Bearer", "ADMIN_TOKEN", "AUTH_CONFIG", "/api/v1/overview"):
        if forbidden in combined:
            raise SystemExit(f"upgrade_policy_legacy_runtime_auth_remaining:{forbidden}")
    for required in (
        "UPGRADE_POLICY_SCHEMA=home-center.upgrade-policy.v2",
        "LOCAL_ADMIN_MIGRATION_FROM_LEGACY=PASS",
        "LEGACY_TOKEN_RETAINED_FOR_ROLLBACK_ONLY=PASS",
        "LEGACY_LOCAL_ADMIN_MIGRATION_SOURCE=/etc/home-center/secrets/admin.token",
        "/etc/home-center/secrets/local-admin.json",
        "LOCAL_ADMIN_CLUSTER_PREFLIGHT=PASS",
        "LOCAL_ADMIN_DEPLOYMENT_PREFLIGHT=PASS",
        "DC02_SOFTWARE_CANARY_30S=PASS",
        "HOME_CENTER_CLUSTER_DEPLOY=PASS",
    ):
        if required not in combined:
            raise SystemExit(f"upgrade_policy_required_surface_missing:{required}")

    bootstrap_path.write_text(bootstrap, encoding="utf-8", newline="\n")
    installer_path.write_text(installer, encoding="utf-8", newline="\n")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: render-upgrade-policy-v2.py BOOTSTRAP INSTALLER")
    render(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()

