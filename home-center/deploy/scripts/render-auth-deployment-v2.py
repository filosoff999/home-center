#!/usr/bin/env python3
"""Render the reviewed 0.7 deployment scripts into the 0.8 auth model.

The large 0.7 rollout/rollback implementation remains immutable for its release
line. This renderer performs a deliberately small, fail-closed migration of the
credential and probe surface for 0.8. Any source-shape drift aborts rendering.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


LEGACY_CREDENTIAL = "/etc/home-center/secrets/admin.token"
LOCAL_ADMIN_CREDENTIAL = "/etc/home-center/secrets/local-admin.json"

CREDENTIAL_VALIDATOR = r'''/usr/bin/python3 -I - "$1" <<'PY'
import base64
import binascii
import grp
import json
import os
import re
import stat
import sys

path = sys.argv[1]
info = os.lstat(path)
expected_gid = grp.getgrnam("home-center").gr_gid
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
    raise SystemExit("local_admin_credential_metadata_rejected")
with open(path, "rb") as handle:
    raw = handle.read(16385)
if len(raw) != info.st_size or len(raw) > 16384:
    raise SystemExit("local_admin_credential_read_rejected")
try:
    pairs = json.loads(raw.decode("utf-8"), object_pairs_hook=lambda items: items)
except (UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise SystemExit("local_admin_credential_json_rejected") from exc
if not isinstance(pairs, list):
    raise SystemExit("local_admin_credential_shape_rejected")
keys = [item[0] for item in pairs]
if len(keys) != len(set(keys)):
    raise SystemExit("local_admin_credential_duplicate_key_rejected")
value = dict(pairs)
required = {"schema", "username", "kdf", "n", "r", "p", "dklen", "salt_b64", "verifier_b64"}
if set(value) != required:
    raise SystemExit("local_admin_credential_shape_rejected")
if value.get("schema") != "home-center.local-admin-credential.v1" or value.get("kdf") != "scrypt":
    raise SystemExit("local_admin_credential_schema_rejected")
if any(isinstance(value.get(name), bool) for name in ("n", "r", "p", "dklen")):
    raise SystemExit("local_admin_credential_kdf_rejected")
if (value.get("n"), value.get("r"), value.get("p"), value.get("dklen")) != (32768, 8, 1, 32):
    raise SystemExit("local_admin_credential_kdf_rejected")
username = value.get("username")
if not isinstance(username, str) or re.fullmatch(r"[a-z][a-z0-9._-]{2,63}", username) is None:
    raise SystemExit("local_admin_credential_username_rejected")
for name in ("salt_b64", "verifier_b64"):
    encoded = value.get(name)
    if not isinstance(encoded, str) or len(encoded) > 44:
        raise SystemExit("local_admin_credential_material_rejected")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SystemExit("local_admin_credential_material_rejected") from exc
    if len(decoded) != 32:
        raise SystemExit("local_admin_credential_material_rejected")
print("LOCAL_ADMIN_CREDENTIAL=PASS")
PY'''


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"auth_v2_{label}_shape_rejected:{count}")
    return source.replace(old, new, 1)


def _regex_once(source: str, pattern: str, replacement: str, label: str) -> str:
    rendered, count = re.subn(pattern, replacement, source, count=1, flags=re.DOTALL)
    if count != 1:
        raise ValueError(f"auth_v2_{label}_shape_rejected:{count}")
    return rendered


def _credential_validator_function() -> str:
    return "validate_local_admin_credential() {\n" + CREDENTIAL_VALIDATOR + "\n}\n"


def render_installer(source: str) -> str:
    count = source.count(LEGACY_CREDENTIAL)
    if count < 1:
        raise ValueError("auth_v2_installer_legacy_credential_missing")
    rendered = source.replace(LEGACY_CREDENTIAL, LOCAL_ADMIN_CREDENTIAL)

    anchor = "id home-center >/dev/null 2>&1 || useradd --system --gid home-center --home-dir /var/lib/home-center --shell /usr/sbin/nologin home-center\n"
    rendered = _replace_once(
        rendered,
        anchor,
        anchor + _credential_validator_function(),
        "installer_validator_function",
    )

    preflight_anchor = "BACKUP_READY=1\npublish_transaction started"
    rendered = _replace_once(
        rendered,
        preflight_anchor,
        f'validate_local_admin_credential "{LOCAL_ADMIN_CREDENTIAL}"\n'
        "echo LOCAL_ADMIN_DEPLOYMENT_PREFLIGHT=PASS\n"
        + preflight_anchor,
        "installer_credential_preflight",
    )

    forbidden = (LEGACY_CREDENTIAL, "Authorization: Bearer", "ADMIN_TOKEN", "admin_token")
    if any(item in rendered for item in forbidden):
        raise ValueError("auth_v2_installer_legacy_auth_remaining")
    required = (
        LOCAL_ADMIN_CREDENTIAL,
        "validate_local_admin_credential",
        "/readyz",
        "LOCAL_ADMIN_DEPLOYMENT_PREFLIGHT=PASS",
    )
    if any(item not in rendered for item in required):
        raise ValueError("auth_v2_installer_required_surface_missing")
    return rendered


def _bootstrap_validator_functions() -> str:
    remote_validator = CREDENTIAL_VALIDATOR.replace('"$1"', f'"{LOCAL_ADMIN_CREDENTIAL}"', 1)
    return (
        _credential_validator_function()
        + "validate_remote_local_admin_credential() {\n"
        + "  \"${SSH[@]}\" sudo -n /usr/bin/python3 -I - /etc/home-center/secrets/local-admin.json <<'PY'\n"
        + remote_validator.split("<<'PY'\n", 1)[1]
        + "\n}\n"
    )


def render_bootstrap(source: str) -> str:
    if source.count(LEGACY_CREDENTIAL) < 3:
        raise ValueError("auth_v2_bootstrap_legacy_credential_shape_rejected")
    rendered = source.replace(LEGACY_CREDENTIAL, LOCAL_ADMIN_CREDENTIAL)

    rendered = _replace_once(
        rendered,
        "  local local_snapshot remote_snapshot local_ready remote_ready auth_file admin_token token_length\n  local local_overview remote_overview\n",
        "  local local_snapshot remote_snapshot local_ready remote_ready\n",
        "bootstrap_restore_locals",
    )

    rendered = _regex_once(
        rendered,
        r'  auth_file=\$\(mktemp "\$TMP/\.rollback-auth\.XXXXXX"\).*?\nPY\n}',
        "  echo CLUSTER_SOURCE_AUTH_FREE_PROBE=PASS\n}",
        "bootstrap_restore_overview",
    )

    rendered = _regex_once(
        rendered,
        r'  RECOVERY_AUTH_CONFIG=\$\(mktemp "\$TMP/\.prior-recovery-auth\.XXXXXX"\).*?\nPY\n  clear_cluster_recovery_authorization',
        "  echo PRIOR_CLUSTER_RECOVERY_AUTH_FREE_PROBE=PASS\n  clear_cluster_recovery_authorization",
        "bootstrap_recovery_overview",
    )

    bootstrap_function_anchor = 'REMOTE_HOST=$("${SSH[@]}" hostname -s)\n'
    rendered = _replace_once(
        rendered,
        bootstrap_function_anchor,
        _bootstrap_validator_functions() + bootstrap_function_anchor,
        "bootstrap_validator_functions",
    )

    credential_equality = f'''REMOTE_ADMIN_SHA=$("${{SSH[@]}}" sudo -n sha256sum {LOCAL_ADMIN_CREDENTIAL} | awk '{{print $1}}')
[ "$REMOTE_ADMIN_SHA" = "$(sha256sum {LOCAL_ADMIN_CREDENTIAL} | awk '{{print $1}}')" ] || {{ echo DC02_ADMIN_TOKEN_MISMATCH >&2; false; }}'''
    rendered = _replace_once(
        rendered,
        credential_equality,
        f'validate_local_admin_credential "{LOCAL_ADMIN_CREDENTIAL}"\n'
        "validate_remote_local_admin_credential\n"
        "echo LOCAL_ADMIN_CLUSTER_PREFLIGHT=PASS",
        "bootstrap_credential_equality",
    )

    rendered = _regex_once(
        rendered,
        r'AUTH_CONFIG="\$TMP/curl-auth\.conf"\nIFS= read -r ADMIN_TOKEN .*?\nchmod 0600 "\$AUTH_CONFIG"\n',
        "echo AUTHENTICATED_OVERVIEW_PROBE_REMOVED=PASS\n",
        "bootstrap_final_auth_config",
    )

    rendered = _regex_once(
        rendered,
        r'for _ in \$\(seq 1 30\); do\n  LOCAL_CLUSTER=\$\(curl --config "\$AUTH_CONFIG".*?\nPY\n(?=curl --fail --silent --show-error --cert /etc/home-center/pki/node\.crt)',
        "echo CLUSTER_AUTH_FREE_ACCEPTANCE=PASS\n",
        "bootstrap_final_overview",
    )

    forbidden = (
        LEGACY_CREDENTIAL,
        "Authorization: Bearer",
        "ADMIN_TOKEN",
        "admin_token",
        "AUTH_CONFIG",
        "/api/v1/overview",
        "REMOTE_ADMIN_SHA",
        "DC02_ADMIN_TOKEN_MISMATCH",
    )
    if any(item in rendered for item in forbidden):
        raise ValueError("auth_v2_bootstrap_legacy_auth_remaining")
    required = (
        LOCAL_ADMIN_CREDENTIAL,
        "validate_local_admin_credential",
        "validate_remote_local_admin_credential",
        "LOCAL_ADMIN_CLUSTER_PREFLIGHT=PASS",
        "CLUSTER_AUTH_FREE_ACCEPTANCE=PASS",
        "/readyz",
        "/internal/v1/node",
        "verify_bidirectional_peer_identity",
        "rollback_cluster",
        "publish_cluster_transaction",
    )
    if any(item not in rendered for item in required):
        raise ValueError("auth_v2_bootstrap_required_surface_missing")
    return rendered


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".rendered")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def render(bootstrap_source: Path, installer_source: Path, bootstrap_output: Path, installer_output: Path) -> None:
    try:
        bootstrap = bootstrap_source.read_text(encoding="utf-8")
        installer = installer_source.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit("auth_v2_deployment_source_unavailable") from exc
    try:
        rendered_bootstrap = render_bootstrap(bootstrap)
        rendered_installer = render_installer(installer)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    _write_atomic(bootstrap_output, rendered_bootstrap)
    _write_atomic(installer_output, rendered_installer)


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit("usage: render-auth-deployment-v2.py BOOTSTRAP_SOURCE INSTALLER_SOURCE BOOTSTRAP_OUTPUT INSTALLER_OUTPUT")
    render(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))


if __name__ == "__main__":
    main()
