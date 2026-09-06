#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

usage() { echo "usage: $0 --artifact PATH --sha256 HEX --installer PATH --rollback PATH" >&2; exit 64; }
ARTIFACT= SHA256= INSTALLER= ROLLBACK=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --artifact) ARTIFACT=${2:-}; shift 2 ;;
    --sha256) SHA256=${2:-}; shift 2 ;;
    --installer) INSTALLER=${2:-}; shift 2 ;;
    --rollback) ROLLBACK=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done
[ -f "$ARTIFACT" ] && [ -f "$INSTALLER" ] && [ -f "$ROLLBACK" ] && [[ "$SHA256" =~ ^[0-9a-f]{64}$ ]] || usage
[ "$(id -u)" -eq 0 ] || { echo ROOT_REQUIRED >&2; exit 65; }
[ "$(hostname -s)" = dc01 ] || { echo ORCHESTRATOR_MUST_RUN_ON_DC01 >&2; exit 65; }
ip -4 -o addr show | awk '{print $4}' | grep -qx '192.168.10.254/24' || { echo DC01_IP_IDENTITY_MISMATCH >&2; exit 65; }
[ ! -L "$ARTIFACT" ] && [ ! -L "$INSTALLER" ] && [ ! -L "$ROLLBACK" ] \
  && [ "$(stat -c %F "$ARTIFACT")" = 'regular file' ] \
  && [ "$(stat -c %F "$INSTALLER")" = 'regular file' ] \
  && [ "$(stat -c %F "$ROLLBACK")" = 'regular file' ] \
  || { echo DEPLOY_INPUT_METADATA_REJECTED >&2; exit 66; }

# Pin every executable and the archive in a root-only directory before
# admission. All later execution and remote copies use these snapshots.
INPUT_ARTIFACT=$ARTIFACT
INPUT_INSTALLER=$INSTALLER
INPUT_ROLLBACK=$ROLLBACK
TMP=$(mktemp -d /run/home-center-bootstrap.XXXXXX)
chmod 0700 "$TMP"
trap 'rm -rf -- "$TMP"' EXIT
PINNED_ARTIFACT=$TMP/artifact.tar.gz
PINNED_INSTALLER=$TMP/install-node.sh
PINNED_ROLLBACK=$TMP/rollback-node.sh
cp -- "$INPUT_ARTIFACT" "$PINNED_ARTIFACT"
chown root:root "$PINNED_ARTIFACT"
chmod 0400 "$PINNED_ARTIFACT"
sync -f "$PINNED_ARTIFACT"
ARTIFACT=$PINNED_ARTIFACT
[ "$(sha256sum "$ARTIFACT" | awk '{print $1}')" = "$SHA256" ] || { echo ARTIFACT_CHECKSUM_MISMATCH >&2; exit 66; }
ARCHIVE_LIST=$(tar -tzf "$ARTIFACT")
if grep -Eq '(^/|(^|/)\.\.(/|$))' <<<"$ARCHIVE_LIST"; then
  echo UNSAFE_ARCHIVE_PATH >&2
  exit 66
fi
[ "$(grep -c '^\./deploy/install-node.sh$' <<<"$ARCHIVE_LIST")" -eq 1 ] \
  && [ "$(grep -c '^\./deploy/rollback-node.sh$' <<<"$ARCHIVE_LIST")" -eq 1 ] \
  && [ "$(grep -c '^\./VERSION$' <<<"$ARCHIVE_LIST")" -eq 1 ] \
  && [ "$(grep -c '^\./REVISION$' <<<"$ARCHIVE_LIST")" -eq 1 ] \
  || { echo DEPLOY_ARTIFACT_REQUIRED_ENTRY_REJECTED >&2; exit 66; }
tar -xOf "$ARTIFACT" ./deploy/install-node.sh >"$PINNED_INSTALLER"
tar -xOf "$ARTIFACT" ./deploy/rollback-node.sh >"$PINNED_ROLLBACK"
chown root:root "$PINNED_INSTALLER" "$PINNED_ROLLBACK"
chmod 0500 "$PINNED_INSTALLER" "$PINNED_ROLLBACK"
sync -f "$PINNED_INSTALLER"
sync -f "$PINNED_ROLLBACK"
[ "$(sha256sum "$INPUT_INSTALLER" | awk '{print $1}')" = "$(sha256sum "$PINNED_INSTALLER" | awk '{print $1}')" ] \
  || { echo INSTALLER_NOT_FROM_ARTIFACT >&2; exit 66; }
[ "$(sha256sum "$INPUT_ROLLBACK" | awk '{print $1}')" = "$(sha256sum "$PINNED_ROLLBACK" | awk '{print $1}')" ] \
  || { echo ROLLBACK_NOT_FROM_ARTIFACT >&2; exit 66; }
INSTALLER=$PINNED_INSTALLER
ROLLBACK=$PINNED_ROLLBACK
TARGET_VERSION=$(tar -xOf "$ARTIFACT" ./VERSION | tr -d '\r\n')
TARGET_REVISION=$(tar -xOf "$ARTIFACT" ./REVISION | tr -d '\r\n')
[ "$TARGET_VERSION" = 0.4.3 ] || { echo RELEASE_VERSION_NOT_ADMITTED >&2; exit 66; }
[[ "$TARGET_REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo RELEASE_REVISION_NOT_ADMITTED >&2; exit 66; }
ADMITTED_SOURCE_V030_VERSION=0.3.0
ADMITTED_SOURCE_V030_REVISION=6b0c0db144bfd2a7b7a7db1a868d649f20825721
ADMITTED_SOURCE_V042_VERSION=0.4.2
ADMITTED_SOURCE_V042_REVISION=9f376e3d39eb29b2c8e402d085cba8b9fee4258d

source_identity_admitted() {
  local version=$1 revision=$2
  case "$version:$revision" in
    "$ADMITTED_SOURCE_V030_VERSION:$ADMITTED_SOURCE_V030_REVISION" | \
      "$ADMITTED_SOURCE_V042_VERSION:$ADMITTED_SOURCE_V042_REVISION") return 0 ;;
    *) return 1 ;;
  esac
}

LOCK_DIR=/run/home-center-locks
LOCK_FILE=$LOCK_DIR/cluster-rollout.lock
if [ ! -e "$LOCK_DIR" ] && [ ! -L "$LOCK_DIR" ]; then
  install -d -m 0700 -o root -g root "$LOCK_DIR"
fi
[ ! -L "$LOCK_DIR" ] && [ "$(stat -c '%F:%u:%g:%a' "$LOCK_DIR" 2>/dev/null)" = directory:0:0:700 ] || { echo HOME_CENTER_LOCK_DIRECTORY_REJECTED >&2; exit 66; }
if [ ! -e "$LOCK_FILE" ] && [ ! -L "$LOCK_FILE" ]; then
  install -m 0600 -o root -g root /dev/null "$LOCK_FILE"
fi
[ ! -L "$LOCK_FILE" ] && [ -f "$LOCK_FILE" ] \
  && [ "$(stat -c '%u:%g:%a' "$LOCK_FILE" 2>/dev/null)" = '0:0:600' ] \
  || { echo HOME_CENTER_LOCK_FILE_REJECTED >&2; exit 66; }
exec 8<>"$LOCK_FILE"
flock -n 8 || { echo HOME_CENTER_CLUSTER_ROLLOUT_ALREADY_RUNNING >&2; exit 75; }
TRANSACTION_ID=$(date -u +%Y%m%dT%H%M%SZ)-$(openssl rand -hex 6)
[[ "$TRANSACTION_ID" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$ ]] || { echo TRANSACTION_ID_GENERATION_FAILED >&2; exit 66; }

SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes dc02)
SCP=(scp -q -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes)
REMOTE_HOST=$("${SSH[@]}" hostname -s)
[ "$REMOTE_HOST" = dc02 ] || { echo DC02_IDENTITY_MISMATCH >&2; exit 65; }
"${SSH[@]}" ip -4 -o addr show | awk '{print $4}' | grep -qx '192.168.10.253/24'
"${SSH[@]}" sudo -n true

for host in local remote; do
  if [ "$host" = local ]; then
    py=$(/usr/bin/python3 -I --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
    openssl version >/dev/null
  else
    py=$("${SSH[@]}" /usr/bin/python3 -I --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
    "${SSH[@]}" openssl version >/dev/null
  fi
  /usr/bin/python3 -I - "$py" <<'PY'
import sys
if tuple(map(int, sys.argv[1].split('.'))) < (3, 12):
    raise SystemExit('python_version_rejected')
PY
done

authorize_cluster_recovery() {
  local transaction_id=$1 auth=/run/home-center-locks/cluster-recovery-$1
  [[ "$transaction_id" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$ ]] || return 1
  [ ! -L /run/home-center-locks ] && [ "$(stat -c '%F:%u:%g:%a' /run/home-center-locks)" = directory:0:0:700 ] || return 1
  local pending=/run/home-center-locks/.cluster-recovery-$transaction_id.pending
  if [ -e "$pending" ] || [ -L "$pending" ]; then
    [ ! -L "$pending" ] && [ "$(stat -c '%F:%u:%g:%a' "$pending")" = 'regular file:0:0:600' ] || return 1
    rm -f -- "$pending" || return 1
  fi
  if [ -e "$auth" ] || [ -L "$auth" ]; then
    [ ! -L "$auth" ] && [ "$(stat -c '%F:%u:%g:%a' "$auth")" = 'regular file:0:0:600' ] \
      && [ "$(cat "$auth")" = "$transaction_id" ] || return 1
  else
    printf '%s\n' "$transaction_id" >"$pending" || return 1
    chmod 0600 "$pending" || return 1
    chown root:root "$pending" || return 1
    sync -f "$pending" || return 1
    mv -T "$pending" "$auth" || return 1
    sync -f /run/home-center-locks || return 1
  fi
  "${SSH[@]}" "sudo -n bash -s -- '$transaction_id'" <<'REMOTE_AUTH'
set -Eeuo pipefail
transaction_id=$1
lock_dir=/run/home-center-locks
auth=$lock_dir/cluster-recovery-$transaction_id
pending=$lock_dir/.cluster-recovery-$transaction_id.pending
if [ ! -e "$lock_dir" ] && [ ! -L "$lock_dir" ]; then
  install -d -m 0700 -o root -g root "$lock_dir"
fi
test ! -L "$lock_dir"
test "$(stat -c '%F:%u:%g:%a' "$lock_dir")" = directory:0:0:700
if test -e "$pending" || test -L "$pending"; then
  test ! -L "$pending"
  test "$(stat -c '%F:%u:%g:%a' "$pending")" = 'regular file:0:0:600'
  rm -f -- "$pending"
fi
if test -e "$auth" || test -L "$auth"; then
  test ! -L "$auth"
  test "$(stat -c '%F:%u:%g:%a' "$auth")" = 'regular file:0:0:600'
  test "$(cat "$auth")" = "$transaction_id"
else
  umask 077
  printf '%s\n' "$transaction_id" >"$pending"
  chmod 0600 "$pending"
  chown root:root "$pending"
  sync -f "$pending"
  mv -T "$pending" "$auth"
  sync -f "$lock_dir"
fi
REMOTE_AUTH
}

clear_cluster_recovery_authorization() {
  local transaction_id=$1 auth=/run/home-center-locks/cluster-recovery-$1
  rm -f -- "$auth" "/run/home-center-locks/.cluster-recovery-$transaction_id.pending"
  sync -f /run/home-center-locks
  [ ! -e "$auth" ] && [ ! -L "$auth" ] || return 1
  "${SSH[@]}" "set -Eeuo pipefail; sudo -n rm -f -- '$auth' '/run/home-center-locks/.cluster-recovery-$transaction_id.pending'; sudo -n sync -f /run/home-center-locks; sudo -n test ! -e '$auth'; sudo -n test ! -L '$auth'"
}

/usr/bin/python3 -I - /var/lib/home-center-deploy/cluster-transactions <<'PY'
import os
import re
import stat
import sys
from pathlib import Path

directory = Path(sys.argv[1])
if not directory.exists():
    raise SystemExit(0)
info = directory.lstat()
if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
    raise SystemExit("cluster_transaction_directory_rejected")
changed = False
for path in directory.iterdir():
    if not re.fullmatch(r"\.(?:cluster-transaction|cluster-recovery)\.[A-Za-z0-9_-]{6,32}", path.name):
        continue
    entry = path.lstat()
    if not stat.S_ISREG(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or entry.st_uid != 0 or stat.S_IMODE(entry.st_mode) != 0o600:
        raise SystemExit("cluster_transaction_temporary_rejected")
    path.unlink()
    changed = True
if changed:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
PY

PRIOR_CLUSTER_RECOVERY_JSON=$(/usr/bin/python3 -I - /var/lib/home-center-deploy/cluster-transactions "$SHA256" "$TARGET_VERSION" "$TARGET_REVISION" <<'PY'
import json
import re
import stat
import sys
from pathlib import Path

directory = Path(sys.argv[1])
expected_artifact, expected_version, expected_revision = sys.argv[2:]
if not directory.exists():
    raise SystemExit(0)
info = directory.lstat()
if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
    raise SystemExit("cluster_transaction_directory_rejected")
unresolved = []
required = {"artifact_sha256", "peer_identity", "rollback_points", "schema", "source", "status", "target", "transaction_id"}
for path in directory.iterdir():
    entry = path.lstat()
    if not stat.S_ISREG(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or entry.st_uid != 0 or stat.S_IMODE(entry.st_mode) != 0o600:
        raise SystemExit("cluster_transaction_entry_metadata_rejected")
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    transaction_id = value.get("transaction_id") if isinstance(value, dict) else None
    if not (
        isinstance(value, dict)
        and set(value) == required
        and value.get("schema") == "home-center.cluster-deploy-transaction.v1"
        and isinstance(transaction_id, str)
        and re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", transaction_id)
        and path.name == f"{transaction_id}.json"
        and value.get("status") in {"started", "recovery_required", "rolled_back", "succeeded"}
        and re.fullmatch(r"[0-9a-f]{64}", str(value.get("artifact_sha256")))
        and isinstance(value.get("target"), dict)
        and set(value["target"]) == {"revision", "version"}
        and re.fullmatch(r"[0-9a-f]{40}", str(value["target"].get("revision")))
        and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", str(value["target"].get("version")))
        and value.get("rollback_points") == {
            "dc01": f"/var/backups/home-center-deploy/{transaction_id}-dc01",
            "dc02": f"/var/backups/home-center-deploy/{transaction_id}-dc02",
        }
    ):
        raise SystemExit("cluster_transaction_entry_identity_rejected")
    for node in ("dc01", "dc02"):
        source = value.get("source", {}).get(node, {})
        if not (
            isinstance(source, dict)
            and set(source) == {"release", "revision", "version"}
            and re.fullmatch(r"/opt/home-center/releases/[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{12}-[0-9a-f]{12}", str(source.get("release")))
            and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", str(source.get("version")))
            and re.fullmatch(r"[0-9a-f]{40}", str(source.get("revision")))
        ):
            raise SystemExit("cluster_transaction_source_identity_rejected")
        peer_identity = value.get("peer_identity", {}).get(node, {})
        if not (
            isinstance(peer_identity, dict)
            and set(peer_identity) == {"ca_sha256", "certificate_sha256", "public_key_sha256"}
            and all(re.fullmatch(r"[0-9a-f]{64}", str(item)) for item in peer_identity.values())
        ):
            raise SystemExit("cluster_transaction_peer_identity_rejected")
    if value["status"] in {"started", "recovery_required"}:
        if value["target"] != {"revision": expected_revision, "version": expected_version}:
            raise SystemExit("prior_cluster_target_mismatch_recovery_required")
        if value["artifact_sha256"] != expected_artifact:
            raise SystemExit("prior_cluster_artifact_mismatch_recovery_required")
        unresolved.append(value)
if len(unresolved) > 1:
    raise SystemExit("multiple_unresolved_cluster_transactions")
if unresolved:
    print(json.dumps(unresolved[0], sort_keys=True, separators=(",", ":")))
PY
)
trap '' INT TERM HUP
if [ -n "$PRIOR_CLUSTER_RECOVERY_JSON" ]; then
  mapfile -t RECOVERY_FIELDS < <(/usr/bin/python3 -I - "$PRIOR_CLUSTER_RECOVERY_JSON" <<'PY'
import json, sys
value = json.loads(sys.argv[1])
print(value["transaction_id"])
for node in ("dc01", "dc02"):
    print(value["rollback_points"][node])
    print(value["source"][node]["release"])
    print(value["source"][node]["version"])
    print(value["source"][node]["revision"])
for node in ("dc01", "dc02"):
    identity = value["peer_identity"][node]
    print(":".join((identity["ca_sha256"], identity["certificate_sha256"], identity["public_key_sha256"])))
PY
  )
  [ "${#RECOVERY_FIELDS[@]}" -eq 11 ] || { echo PRIOR_CLUSTER_RECOVERY_FIELDS_REJECTED >&2; exit 70; }
  RECOVERY_TRANSACTION_ID=${RECOVERY_FIELDS[0]}
  RECOVERY_LOCAL_POINT=${RECOVERY_FIELDS[1]}
  RECOVERY_LOCAL_RELEASE=${RECOVERY_FIELDS[2]}
  RECOVERY_LOCAL_VERSION=${RECOVERY_FIELDS[3]}
  RECOVERY_LOCAL_REVISION=${RECOVERY_FIELDS[4]}
  RECOVERY_REMOTE_POINT=${RECOVERY_FIELDS[5]}
  RECOVERY_REMOTE_RELEASE=${RECOVERY_FIELDS[6]}
  RECOVERY_REMOTE_VERSION=${RECOVERY_FIELDS[7]}
  RECOVERY_REMOTE_REVISION=${RECOVERY_FIELDS[8]}
  RECOVERY_LOCAL_PEER_STATE=${RECOVERY_FIELDS[9]}
  RECOVERY_REMOTE_PEER_STATE=${RECOVERY_FIELDS[10]}
  RECOVERY_LOCAL_MARKER=/var/lib/home-center-deploy/transactions/$RECOVERY_TRANSACTION_ID-dc01.json
  RECOVERY_REMOTE_MARKER=/var/lib/home-center-deploy/transactions/$RECOVERY_TRANSACTION_ID-dc02.json
  authorize_cluster_recovery "$RECOVERY_TRANSACTION_ID"
  if [ -e "$RECOVERY_LOCAL_MARKER" ] || [ -L "$RECOVERY_LOCAL_MARKER" ]; then
    bash "$ROLLBACK" --rollback-point "$RECOVERY_LOCAL_POINT"
  else
    [ "$(readlink -f /opt/home-center/current)" = "$RECOVERY_LOCAL_RELEASE" ] \
      && [ "$(tr -d '\r\n' <"$RECOVERY_LOCAL_RELEASE/VERSION")" = "$RECOVERY_LOCAL_VERSION" ] \
      && [ "$(tr -d '\r\n' <"$RECOVERY_LOCAL_RELEASE/REVISION")" = "$RECOVERY_LOCAL_REVISION" ] \
      || { echo DC01_PRIOR_TRANSACTION_STATE_UNKNOWN >&2; exit 70; }
  fi
  RECOVERY_REMOTE_MARKER_STATE=$("${SSH[@]}" "set -Eeuo pipefail; \
    if sudo -n test -e '$RECOVERY_REMOTE_MARKER'; then \
      sudo -n test ! -L '$RECOVERY_REMOTE_MARKER'; printf '%s\\n' present; \
    elif sudo -n test ! -e '$RECOVERY_REMOTE_MARKER' && sudo -n test ! -L '$RECOVERY_REMOTE_MARKER'; then \
      printf '%s\\n' absent; \
    else exit 66; fi") || { echo DC02_PRIOR_TRANSACTION_MARKER_STATE_UNKNOWN >&2; exit 70; }
  if [ "$RECOVERY_REMOTE_MARKER_STATE" = present ]; then
    RECOVERY_REMOTE_DIR=$("${SSH[@]}" 'umask 077; mktemp -d /tmp/home-center-recovery.XXXXXX')
    case "$RECOVERY_REMOTE_DIR" in /tmp/home-center-recovery.??????) ;; *) echo DC02_RECOVERY_TEMP_REJECTED >&2; exit 70 ;; esac
    RECOVERY_REMOTE_SCRIPT=$RECOVERY_REMOTE_DIR/rollback-node.sh
    "${SCP[@]}" "$ROLLBACK" "dc02:$RECOVERY_REMOTE_SCRIPT"
    "${SSH[@]}" "set -Eeuo pipefail; trap 'rm -rf -- \"$RECOVERY_REMOTE_DIR\"' EXIT; chmod 0600 '$RECOVERY_REMOTE_SCRIPT'; sudo -n bash '$RECOVERY_REMOTE_SCRIPT' --rollback-point '$RECOVERY_REMOTE_POINT'"
  elif [ "$RECOVERY_REMOTE_MARKER_STATE" = absent ]; then
    "${SSH[@]}" "set -Eeuo pipefail; test \"\$(readlink -f /opt/home-center/current)\" = '$RECOVERY_REMOTE_RELEASE'; test \"\$(tr -d '\\r\\n' <'$RECOVERY_REMOTE_RELEASE/VERSION')\" = '$RECOVERY_REMOTE_VERSION'; test \"\$(tr -d '\\r\\n' <'$RECOVERY_REMOTE_RELEASE/REVISION')\" = '$RECOVERY_REMOTE_REVISION'"
  else
    echo DC02_PRIOR_TRANSACTION_MARKER_STATE_REJECTED >&2
    exit 70
  fi
  [ "$(readlink -f /opt/home-center/current)" = "$RECOVERY_LOCAL_RELEASE" ]
  "${SSH[@]}" "test \"\$(readlink -f /opt/home-center/current)\" = '$RECOVERY_REMOTE_RELEASE'"
  RECOVERY_LOCAL_PEER_STATE_AFTER=$(set -Eeuo pipefail
    ca_sha=$(openssl x509 -in /etc/home-center/pki/ca.crt -outform DER | sha256sum | awk '{print $1}')
    certificate_sha=$(openssl x509 -in /etc/home-center/pki/node.crt -outform DER | sha256sum | awk '{print $1}')
    key_public_sha=$(openssl pkey -in /etc/home-center/pki/node.key -pubout -outform DER | sha256sum | awk '{print $1}')
    certificate_public_sha=$(openssl x509 -in /etc/home-center/pki/node.crt -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | awk '{print $1}')
    [ "$key_public_sha" = "$certificate_public_sha" ]
    printf '%s:%s:%s\n' "$ca_sha" "$certificate_sha" "$key_public_sha"
  )
  RECOVERY_REMOTE_PEER_STATE_AFTER=$("${SSH[@]}" 'set -Eeuo pipefail
ca_sha=$(sudo -n openssl x509 -in /etc/home-center/pki/ca.crt -outform DER | sha256sum | awk "{print \$1}")
certificate_sha=$(sudo -n openssl x509 -in /etc/home-center/pki/node.crt -outform DER | sha256sum | awk "{print \$1}")
key_public_sha=$(sudo -n openssl pkey -in /etc/home-center/pki/node.key -pubout -outform DER | sha256sum | awk "{print \$1}")
certificate_public_sha=$(sudo -n openssl x509 -in /etc/home-center/pki/node.crt -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | awk "{print \$1}")
test "$key_public_sha" = "$certificate_public_sha"
printf "%s:%s:%s\n" "$ca_sha" "$certificate_sha" "$key_public_sha"')
  [ "$RECOVERY_LOCAL_PEER_STATE_AFTER" = "$RECOVERY_LOCAL_PEER_STATE" ] \
    && [ "$RECOVERY_REMOTE_PEER_STATE_AFTER" = "$RECOVERY_REMOTE_PEER_STATE" ] \
    || { echo PRIOR_CLUSTER_RECOVERY_PEER_STATE_CHANGED >&2; exit 70; }
  systemctl is-active --quiet home-center.service
  "${SSH[@]}" systemctl is-active --quiet home-center.service
  RECOVERY_LOCAL_READY=$(curl --fail --silent --show-error --cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.254:8443/readyz)
  RECOVERY_REMOTE_READY=$(curl --fail --silent --show-error --cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.253:8443/readyz)
  /usr/bin/python3 -I - "$RECOVERY_LOCAL_READY" "$RECOVERY_REMOTE_READY" "$RECOVERY_LOCAL_VERSION" "$RECOVERY_REMOTE_VERSION" <<'PY'
import json, sys
for node, raw, version in zip(("dc01", "dc02"), sys.argv[1:3], sys.argv[3:5]):
    value = json.loads(raw)
    if not (
        value.get("schema") == "home-center.readiness.v1"
        and value.get("status") == "ready"
        and value.get("node_id") == "hm-dm-" + node
        and value.get("version") == version
    ):
        raise SystemExit("prior_cluster_recovery_readiness_rejected")
PY
  RECOVERY_DC02_PEER=$(curl --fail --silent --show-error --cert /etc/home-center/pki/node.crt --key /etc/home-center/pki/node.key --cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.253:9443/internal/v1/node)
  RECOVERY_DC01_PEER=$("${SSH[@]}" 'sudo -n curl --fail --silent --show-error --cert /etc/home-center/pki/node.crt --key /etc/home-center/pki/node.key --cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.254:9443/internal/v1/node')
  /usr/bin/python3 -I - "$RECOVERY_DC02_PEER" "$RECOVERY_DC01_PEER" <<'PY'
import json, sys
for node, raw in zip(("dc02", "dc01"), sys.argv[1:]):
    value = json.loads(raw)
    if not (
        value.get("cluster_id") == "hm-dm-production"
        and value.get("capability", {}).get("node", {}).get("id") == "hm-dm-" + node
    ):
        raise SystemExit("prior_cluster_recovery_peer_identity_rejected")
PY
  RECOVERY_AUTH_CONFIG=$(mktemp "$TMP/.prior-recovery-auth.XXXXXX")
  chmod 0600 "$RECOVERY_AUTH_CONFIG"
  IFS= read -r RECOVERY_ADMIN_TOKEN </etc/home-center/secrets/admin.token
  RECOVERY_TOKEN_LENGTH=$(printf '%s' "$RECOVERY_ADMIN_TOKEN" | wc -c)
  [ "$RECOVERY_TOKEN_LENGTH" -ge 32 ] || { echo PRIOR_CLUSTER_RECOVERY_ADMIN_TOKEN_REJECTED >&2; exit 70; }
  printf 'header = "Authorization: Bearer %s"\n' "$RECOVERY_ADMIN_TOKEN" >"$RECOVERY_AUTH_CONFIG"
  unset RECOVERY_ADMIN_TOKEN
  RECOVERY_LOCAL_OVERVIEW=$(curl --config "$RECOVERY_AUTH_CONFIG" --fail --silent --show-error \
    --cacert /etc/home-center/pki/ca.crt --max-time 5 \
    https://192.168.10.254:8443/api/v1/overview)
  RECOVERY_REMOTE_OVERVIEW=$(curl --config "$RECOVERY_AUTH_CONFIG" --fail --silent --show-error \
    --cacert /etc/home-center/pki/ca.crt --max-time 5 \
    https://192.168.10.253:8443/api/v1/overview)
  rm -f -- "$RECOVERY_AUTH_CONFIG"
  /usr/bin/python3 -I - "$RECOVERY_LOCAL_OVERVIEW" "$RECOVERY_REMOTE_OVERVIEW" <<'PY'
import json
import sys

for role, raw in zip(("leader", "standby"), sys.argv[1:]):
    value = json.loads(raw)
    cluster = value.get("cluster", {})
    if not (
        value.get("schema") == "home-center.overview.v1"
        and cluster.get("status") == "healthy"
        and cluster.get("ready_nodes") == 2
        and cluster.get("expected_nodes") == 2
        and cluster.get("local_role") == role
        and cluster.get("automatic_failover") is False
        and cluster.get("split_brain_policy") == "single-writer-manual-failover"
        and {node.get("name") for node in value.get("nodes", [])} == {"dc01", "dc02"}
    ):
        raise SystemExit("prior_cluster_recovery_overview_rejected")
PY
  clear_cluster_recovery_authorization "$RECOVERY_TRANSACTION_ID"
  /usr/bin/python3 -I - "/var/lib/home-center-deploy/cluster-transactions/$RECOVERY_TRANSACTION_ID.json" <<'PY'
import json, os, stat, sys, tempfile
path = sys.argv[1]
info = os.lstat(path)
if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
    raise SystemExit("cluster_recovery_marker_rejected")
with open(path, encoding="utf-8") as handle:
    value = json.load(handle)
if value.get("status") not in {"started", "recovery_required"}:
    raise SystemExit("cluster_recovery_transition_rejected")
value["status"] = "rolled_back"
directory = os.path.dirname(path)
descriptor, temporary = tempfile.mkstemp(prefix=".cluster-recovery.", dir=directory)
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PY
  echo PRIOR_CLUSTER_TRANSACTION_RECOVERED="$RECOVERY_TRANSACTION_ID"
fi
trap - INT TERM HUP

systemctl is-active --quiet home-center.service || { echo DC01_UPGRADE_SOURCE_NOT_ACTIVE >&2; exit 67; }
"${SSH[@]}" systemctl is-active --quiet home-center.service || { echo DC02_UPGRADE_SOURCE_NOT_ACTIVE >&2; exit 67; }
LOCAL_MODE=upgrade
REMOTE_MODE=upgrade

current_release_snapshot() {
  local current=/opt/home-center/current release version revision
  [ -L "$current" ] || return 1
  release=$(readlink -f "$current") || return 1
  [[ "$release" =~ ^/opt/home-center/releases/[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{12}-[0-9a-f]{12}$ ]] || return 1
  [ ! -L "$release" ] && [ "$(stat -c '%F:%u' "$release")" = directory:0 ] || return 1
  [ ! -L "$release/VERSION" ] && [ ! -L "$release/REVISION" ] || return 1
  version=$(tr -d '\r\n' <"$release/VERSION") || return 1
  revision=$(tr -d '\r\n' <"$release/REVISION") || return 1
  printf '%s|%s|%s\n' "$release" "$version" "$revision"
}

LOCAL_SOURCE_SNAPSHOT=$(current_release_snapshot) || { echo DC01_SOURCE_RELEASE_REJECTED >&2; exit 66; }
REMOTE_SOURCE_SNAPSHOT=$("${SSH[@]}" 'set -Eeuo pipefail
current=/opt/home-center/current
test -L "$current"
release=$(readlink -f "$current")
[[ "$release" =~ ^/opt/home-center/releases/[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{12}-[0-9a-f]{12}$ ]]
test ! -L "$release"
test "$(stat -c "%F:%u" "$release")" = "directory:0"
test ! -L "$release/VERSION"
test ! -L "$release/REVISION"
version=$(tr -d "\r\n" <"$release/VERSION")
revision=$(tr -d "\r\n" <"$release/REVISION")
printf "%s|%s|%s\n" "$release" "$version" "$revision"') || { echo DC02_SOURCE_RELEASE_REJECTED >&2; exit 66; }
IFS='|' read -r LOCAL_SOURCE_RELEASE LOCAL_SOURCE_VERSION LOCAL_SOURCE_REVISION <<<"$LOCAL_SOURCE_SNAPSHOT"
IFS='|' read -r REMOTE_SOURCE_RELEASE REMOTE_SOURCE_VERSION REMOTE_SOURCE_REVISION <<<"$REMOTE_SOURCE_SNAPSHOT"
[ "$LOCAL_SOURCE_VERSION" = "$REMOTE_SOURCE_VERSION" ] \
  && [ "$LOCAL_SOURCE_REVISION" = "$REMOTE_SOURCE_REVISION" ] \
  && [ "$LOCAL_SOURCE_RELEASE" = "$REMOTE_SOURCE_RELEASE" ] \
  && source_identity_admitted "$LOCAL_SOURCE_VERSION" "$LOCAL_SOURCE_REVISION" \
  || { echo CLUSTER_SOURCE_BASELINE_NOT_ADMITTED >&2; exit 66; }
echo CLUSTER_SOURCE_BASELINE=PASS

audit_transaction_directory() {
  /usr/bin/python3 -I - "$1" <<'PY'
import json
import os
import re
import stat
import sys
from pathlib import Path

directory = Path(sys.argv[1])
if not directory.exists():
    raise SystemExit(0)
info = directory.lstat()
if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
    raise SystemExit("transaction_directory_metadata_rejected")
for path in directory.iterdir():
    entry = path.lstat()
    if not stat.S_ISREG(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or entry.st_uid != 0 or stat.S_IMODE(entry.st_mode) != 0o600:
        raise SystemExit("transaction_entry_metadata_rejected")
    if re.fullmatch(r"\.(?:transaction|cluster-transaction|cluster-recovery)\.[A-Za-z0-9_-]{6,32}", path.name):
        path.unlink()
        continue
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict) or value.get("status") not in {"succeeded", "rolled_back"}:
        raise SystemExit("unresolved_transaction_recovery_required")
    schema = value.get("schema")
    if schema == "home-center.deploy-transaction.v1":
        required = {"artifact_sha256", "node", "release", "revision", "rollback_point", "schema", "status", "transaction_id", "version"}
        if set(value) != required or path.name != f'{value.get("transaction_id")}-{value.get("node")}.json':
            raise SystemExit("transaction_entry_identity_rejected")
    elif schema == "home-center.cluster-deploy-transaction.v1":
        required = {"artifact_sha256", "peer_identity", "rollback_points", "schema", "source", "status", "target", "transaction_id"}
        if set(value) != required or path.name != f'{value.get("transaction_id")}.json':
            raise SystemExit("cluster_transaction_entry_identity_rejected")
    else:
        raise SystemExit("transaction_entry_schema_rejected")
PY
}

audit_transaction_directory /var/lib/home-center-deploy/cluster-transactions
audit_transaction_directory /var/lib/home-center-deploy/transactions
"${SSH[@]}" sudo -n /usr/bin/python3 -I - /var/lib/home-center-deploy/transactions <<'PY'
import json
import os
import re
import stat
import sys
from pathlib import Path

directory = Path(sys.argv[1])
if not directory.exists():
    raise SystemExit(0)
info = directory.lstat()
if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
    raise SystemExit("transaction_directory_metadata_rejected")
for path in directory.iterdir():
    entry = path.lstat()
    if not stat.S_ISREG(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or entry.st_uid != 0 or stat.S_IMODE(entry.st_mode) != 0o600:
        raise SystemExit("transaction_entry_metadata_rejected")
    if re.fullmatch(r"\.transaction\.[A-Za-z0-9_-]{6,32}", path.name):
        path.unlink()
        continue
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict) or value.get("status") not in {"succeeded", "rolled_back"}:
        raise SystemExit("unresolved_transaction_recovery_required")
    required = {"artifact_sha256", "node", "release", "revision", "rollback_point", "schema", "status", "transaction_id", "version"}
    if (
        value.get("schema") != "home-center.deploy-transaction.v1"
        or set(value) != required
        or path.name != f'{value.get("transaction_id")}-{value.get("node")}.json'
    ):
        raise SystemExit("transaction_entry_identity_rejected")
PY
echo PRIOR_TRANSACTION_AUDIT=PASS

getent group home-center >/dev/null || { echo DC01_HOME_CENTER_GROUP_MISSING >&2; exit 66; }
id home-center >/dev/null 2>&1 || { echo DC01_HOME_CENTER_USER_MISSING >&2; exit 66; }
HOME_CENTER_GID=$(getent group home-center | awk -F: '{print $3}')
for directory in /etc/home-center /etc/home-center/secrets /etc/home-center/pki; do
  [ ! -L "$directory" ] && [ "$(stat -c '%F:%u:%g:%a' "$directory" 2>/dev/null)" = "directory:0:$HOME_CENTER_GID:750" ] \
    || { echo "DC01_UPGRADE_DIRECTORY_REJECTED=$directory" >&2; exit 66; }
done
for private_file in /etc/home-center/secrets/admin.token /etc/home-center/secrets/session.key \
  /etc/home-center/secrets/audit.key /etc/home-center/pki/node.key; do
  [ -s "$private_file" ] && [ ! -L "$private_file" ] \
    && [ "$(stat -c '%F:%u:%g:%a' "$private_file")" = "regular file:0:$HOME_CENTER_GID:640" ] \
    || { echo "DC01_UPGRADE_PRIVATE_FILE_REJECTED=$private_file" >&2; exit 66; }
done
[ "$(stat -c '%F:%u:%a' /etc/home-center/pki/ca.key 2>/dev/null)" = 'regular file:0:600' ] \
  || { echo DC01_CLUSTER_CA_KEY_REJECTED >&2; exit 66; }
for certificate in /etc/home-center/pki/node.crt /etc/home-center/pki/ca.crt; do
  [ -s "$certificate" ] && [ ! -L "$certificate" ] \
    && [ "$(stat -c '%F:%u:%a' "$certificate")" = 'regular file:0:644' ] \
    || { echo "DC01_UPGRADE_CERTIFICATE_REJECTED=$certificate" >&2; exit 66; }
done
"${SSH[@]}" 'set -Eeuo pipefail
getent group home-center >/dev/null
id home-center >/dev/null 2>&1
gid=$(getent group home-center | awk -F: "{print \$3}")
for directory in /etc/home-center /etc/home-center/secrets /etc/home-center/pki; do
  sudo -n test ! -L "$directory"
  test "$(sudo -n stat -c "%F:%u:%g:%a" "$directory")" = "directory:0:$gid:750"
done'

WEB_CA_STAGE=
LOCAL_ROLLBACK= REMOTE_ROLLBACK= REMOTE_DEPLOYED=0 LOCAL_DEPLOYED=0
REMOTE_MAY_HAVE_MUTATED=0 LOCAL_MAY_HAVE_MUTATED=0
ROLLBACK_RUNNING=0
BOOTSTRAP_MAIN_BASHPID=$BASHPID
CLUSTER_TRANSACTION_DIR=/var/lib/home-center-deploy/cluster-transactions
CLUSTER_TRANSACTION_FILE=$CLUSTER_TRANSACTION_DIR/$TRANSACTION_ID.json
CLUSTER_TRANSACTION_OWNED=0

publish_cluster_transaction() {
  local requested=$1
  /usr/bin/python3 -I - "$CLUSTER_TRANSACTION_DIR" "$CLUSTER_TRANSACTION_FILE" "$TRANSACTION_ID" "$requested" \
    "$SHA256" "$TARGET_VERSION" "$TARGET_REVISION" \
    "$LOCAL_SOURCE_RELEASE" "$LOCAL_SOURCE_VERSION" "$LOCAL_SOURCE_REVISION" \
    "$REMOTE_SOURCE_RELEASE" "$REMOTE_SOURCE_VERSION" "$REMOTE_SOURCE_REVISION" \
    "$LOCAL_PEER_STATE_BEFORE" "$REMOTE_PEER_STATE_BEFORE" <<'PY'
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

(directory_raw, path_raw, transaction_id, requested, artifact_sha256, target_version,
 target_revision, dc01_release, dc01_version, dc01_revision, dc02_release,
 dc02_version, dc02_revision, dc01_peer_state, dc02_peer_state) = sys.argv[1:]
directory = Path(directory_raw)
path = Path(path_raw)
root = directory.parent
for candidate in (root, directory):
    if not candidate.exists():
        candidate.mkdir(mode=0o700)
    info = candidate.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise SystemExit("cluster_transaction_directory_rejected")
def parse_peer_state(raw):
    fields = raw.split(":")
    if len(fields) != 3 or any(len(field) != 64 or any(char not in "0123456789abcdef" for char in field) for field in fields):
        raise SystemExit("cluster_transaction_peer_identity_rejected")
    return {"ca_sha256": fields[0], "certificate_sha256": fields[1], "public_key_sha256": fields[2]}


expected = {
    "artifact_sha256": artifact_sha256,
    "peer_identity": {
        "dc01": parse_peer_state(dc01_peer_state),
        "dc02": parse_peer_state(dc02_peer_state),
    },
    "rollback_points": {
        "dc01": f"/var/backups/home-center-deploy/{transaction_id}-dc01",
        "dc02": f"/var/backups/home-center-deploy/{transaction_id}-dc02",
    },
    "schema": "home-center.cluster-deploy-transaction.v1",
    "source": {
        "dc01": {"release": dc01_release, "revision": dc01_revision, "version": dc01_version},
        "dc02": {"release": dc02_release, "revision": dc02_revision, "version": dc02_version},
    },
    "status": requested,
    "target": {"revision": target_revision, "version": target_version},
    "transaction_id": transaction_id,
}
if path.exists() or path.is_symlink():
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise SystemExit("cluster_transaction_marker_rejected")
    with path.open(encoding="utf-8") as handle:
        current = json.load(handle)
    immutable = dict(current)
    current_status = immutable.pop("status", None)
    expected_immutable = dict(expected)
    expected_immutable.pop("status")
    if immutable != expected_immutable or current_status not in {"started", "rolled_back", "recovery_required", "succeeded"}:
        raise SystemExit("cluster_transaction_identity_rejected")
elif requested != "started":
    raise SystemExit("cluster_transaction_marker_missing")
descriptor, temporary = tempfile.mkstemp(prefix=".cluster-transaction.", dir=directory)
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(expected, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PY
}

peer_public_state_local() {
  local ca_sha certificate_sha key_public_sha certificate_public_sha
  ca_sha=$(openssl x509 -in /etc/home-center/pki/ca.crt -outform DER | sha256sum | awk '{print $1}') || return 1
  certificate_sha=$(openssl x509 -in /etc/home-center/pki/node.crt -outform DER | sha256sum | awk '{print $1}') || return 1
  key_public_sha=$(openssl pkey -in /etc/home-center/pki/node.key -pubout -outform DER | sha256sum | awk '{print $1}') || return 1
  certificate_public_sha=$(openssl x509 -in /etc/home-center/pki/node.crt -pubkey -noout |
    openssl pkey -pubin -outform DER | sha256sum | awk '{print $1}') || return 1
  [ "$key_public_sha" = "$certificate_public_sha" ] || return 1
  printf '%s:%s:%s\n' "$ca_sha" "$certificate_sha" "$key_public_sha"
}

peer_public_state_remote() {
  "${SSH[@]}" 'set -Eeuo pipefail
ca_sha=$(sudo -n openssl x509 -in /etc/home-center/pki/ca.crt -outform DER | sha256sum | awk "{print \$1}")
certificate_sha=$(sudo -n openssl x509 -in /etc/home-center/pki/node.crt -outform DER | sha256sum | awk "{print \$1}")
key_public_sha=$(sudo -n openssl pkey -in /etc/home-center/pki/node.key -pubout -outform DER | sha256sum | awk "{print \$1}")
certificate_public_sha=$(sudo -n openssl x509 -in /etc/home-center/pki/node.crt -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | awk "{print \$1}")
test "$key_public_sha" = "$certificate_public_sha"
printf "%s:%s:%s\n" "$ca_sha" "$certificate_sha" "$key_public_sha"'
}

verify_bidirectional_peer_identity() {
  local dc02_peer dc01_peer
  dc02_peer=$(curl --fail --silent --show-error \
    --cert /etc/home-center/pki/node.crt --key /etc/home-center/pki/node.key \
    --cacert /etc/home-center/pki/ca.crt --max-time 5 \
    https://192.168.10.253:9443/internal/v1/node) || return 1
  dc01_peer=$("${SSH[@]}" 'sudo -n curl --fail --silent --show-error --cert /etc/home-center/pki/node.crt --key /etc/home-center/pki/node.key --cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.254:9443/internal/v1/node') || return 1
  /usr/bin/python3 -I - "$dc02_peer" "$dc01_peer" <<'PY'
import json, sys
for node, raw in zip(("dc02", "dc01"), sys.argv[1:]):
    value = json.loads(raw)
    if not (
        value.get("cluster_id") == "hm-dm-production"
        and value.get("capability", {}).get("node", {}).get("id") == "hm-dm-" + node
    ):
        raise SystemExit("cluster_peer_identity_rejected")
PY
}

verify_cluster_source_restored() {
  local local_snapshot remote_snapshot local_ready remote_ready auth_file admin_token token_length
  local local_overview remote_overview
  local_snapshot=$(current_release_snapshot) || return 1
  [ "$local_snapshot" = "$LOCAL_SOURCE_SNAPSHOT" ] || return 1
  remote_snapshot=$("${SSH[@]}" 'set -Eeuo pipefail; release=$(readlink -f /opt/home-center/current); version=$(tr -d "\r\n" <"$release/VERSION"); revision=$(tr -d "\r\n" <"$release/REVISION"); printf "%s|%s|%s\n" "$release" "$version" "$revision"') || return 1
  [ "$remote_snapshot" = "$REMOTE_SOURCE_SNAPSHOT" ] || return 1
  local_ready=$(curl --fail --silent --show-error --cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.254:8443/readyz) || return 1
  remote_ready=$(curl --fail --silent --show-error --cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.253:8443/readyz) || return 1
  /usr/bin/python3 -I - "$local_ready" "$remote_ready" "$LOCAL_SOURCE_VERSION" <<'PY' || return 1
import json, sys
for node, raw in zip(("dc01", "dc02"), sys.argv[1:3]):
    value = json.loads(raw)
    if not (
        value.get("schema") == "home-center.readiness.v1"
        and value.get("status") == "ready"
        and value.get("node_id") == "hm-dm-" + node
        and value.get("version") == sys.argv[3]
    ):
        raise SystemExit("cluster_source_readiness_rejected")
PY
  [ "$(peer_public_state_local)" = "$LOCAL_PEER_STATE_BEFORE" ] || return 1
  [ "$(peer_public_state_remote)" = "$REMOTE_PEER_STATE_BEFORE" ] || return 1
  verify_bidirectional_peer_identity || return 1
  auth_file=$(mktemp "$TMP/.rollback-auth.XXXXXX") || return 1
  chmod 0600 "$auth_file" || return 1
  IFS= read -r admin_token </etc/home-center/secrets/admin.token || return 1
  token_length=$(printf '%s' "$admin_token" | wc -c) || return 1
  [ "$token_length" -ge 32 ] || return 1
  printf 'header = "Authorization: Bearer %s"\n' "$admin_token" >"$auth_file" || return 1
  unset admin_token
  local_overview=$(curl --config "$auth_file" --fail --silent --show-error \
    --cacert /etc/home-center/pki/ca.crt --max-time 5 \
    https://192.168.10.254:8443/api/v1/overview) || return 1
  remote_overview=$(curl --config "$auth_file" --fail --silent --show-error \
    --cacert /etc/home-center/pki/ca.crt --max-time 5 \
    https://192.168.10.253:8443/api/v1/overview) || return 1
  rm -f -- "$auth_file" || return 1
  /usr/bin/python3 -I - "$local_overview" "$remote_overview" <<'PY' || return 1
import json
import sys

for role, raw in zip(("leader", "standby"), sys.argv[1:]):
    value = json.loads(raw)
    cluster = value.get("cluster", {})
    if not (
        value.get("schema") == "home-center.overview.v1"
        and cluster.get("status") == "healthy"
        and cluster.get("ready_nodes") == 2
        and cluster.get("expected_nodes") == 2
        and cluster.get("local_role") == role
        and cluster.get("automatic_failover") is False
        and cluster.get("split_brain_policy") == "single-writer-manual-failover"
        and {node.get("name") for node in value.get("nodes", [])} == {"dc01", "dc02"}
    ):
        raise SystemExit("cluster_source_overview_rejected")
PY
}

cleanup() {
  rm -rf -- "$TMP"
  [ -z "$WEB_CA_STAGE" ] || rm -rf -- "$WEB_CA_STAGE"
}
fail_rollback() {
  local rc=${1:-$?}
  if [ "$BASHPID" -ne "$BOOTSTRAP_MAIN_BASHPID" ]; then
    return "$rc"
  fi
  if [ "$ROLLBACK_RUNNING" -eq 1 ]; then
    echo HOME_CENTER_CLUSTER_DEPLOY=REENTRANT_ROLLBACK_FAILURE >&2
    exit 70
  fi
  ROLLBACK_RUNNING=1
  trap - ERR EXIT
  trap '' INT TERM HUP
  set +e
  rollback_ok=1
  recovery_authorized=0
  if [ "$LOCAL_MAY_HAVE_MUTATED" -eq 1 ] || [ "$REMOTE_MAY_HAVE_MUTATED" -eq 1 ]; then
    if [ "$CLUSTER_TRANSACTION_OWNED" -eq 1 ]; then
      publish_cluster_transaction recovery_required || rollback_ok=0
      if [ "$rollback_ok" -eq 1 ]; then
        authorize_cluster_recovery "$TRANSACTION_ID" || rollback_ok=0
        [ "$rollback_ok" -eq 0 ] || recovery_authorized=1
      fi
    else
      rollback_ok=0
    fi
  fi
  if [ "$rollback_ok" -eq 1 ] && [ "$LOCAL_MAY_HAVE_MUTATED" -eq 1 ]; then
    if [ -d "$LOCAL_ROLLBACK" ]; then
      bash "$ROLLBACK" --rollback-point "$LOCAL_ROLLBACK" || rollback_ok=0
    else
      rollback_ok=0
    fi
  fi
  remote_rollback_present=0
  if [ "$rollback_ok" -eq 1 ] && [ "$REMOTE_MAY_HAVE_MUTATED" -eq 1 ]; then
    if "${SSH[@]}" sudo -n test -d "$REMOTE_ROLLBACK"; then
      remote_rollback_present=1
    else
      rollback_ok=0
    fi
  fi
  if [ "$rollback_ok" -eq 1 ] && [ "$remote_rollback_present" -eq 1 ]; then
    remote_rollback_dir=$("${SSH[@]}" 'umask 077; mktemp -d /tmp/home-center-rollback.XXXXXX') || remote_rollback_dir=
    case "$remote_rollback_dir" in /tmp/home-center-rollback.??????) ;; *) remote_rollback_dir= ;; esac
    remote_rollback_script=$remote_rollback_dir/rollback-node.sh
    if [ -z "$remote_rollback_dir" ]; then
      rollback_ok=0
    elif ! "${SCP[@]}" "$ROLLBACK" "dc02:$remote_rollback_script"; then
      "${SSH[@]}" "rm -rf -- '$remote_rollback_dir'" || true
      rollback_ok=0
    elif ! "${SSH[@]}" "set -Eeuo pipefail; trap 'rm -rf -- \"$remote_rollback_dir\"' EXIT; chmod 0600 '$remote_rollback_script'; sudo -n bash '$remote_rollback_script' --rollback-point '$REMOTE_ROLLBACK'"; then
      rollback_ok=0
    fi
  fi
  if [ "$CLUSTER_TRANSACTION_OWNED" -eq 1 ] && [ "$rollback_ok" -eq 1 ]; then
    verify_cluster_source_restored || rollback_ok=0
  fi
  if [ "$CLUSTER_TRANSACTION_OWNED" -eq 1 ]; then
    if [ "$rollback_ok" -eq 1 ]; then
      if [ "$recovery_authorized" -eq 1 ]; then
        clear_cluster_recovery_authorization "$TRANSACTION_ID" || rollback_ok=0
      fi
    fi
    if [ "$rollback_ok" -eq 1 ]; then
      publish_cluster_transaction rolled_back || rollback_ok=0
    fi
    if [ "$rollback_ok" -ne 1 ]; then
      publish_cluster_transaction recovery_required || true
    fi
  fi
  cleanup
  if [ "$CLUSTER_TRANSACTION_OWNED" -eq 0 ] && [ "$REMOTE_MAY_HAVE_MUTATED" -eq 0 ] && [ "$LOCAL_MAY_HAVE_MUTATED" -eq 0 ]; then
    echo "HOME_CENTER_CLUSTER_DEPLOY=ABORTED_BEFORE_MUTATION rc=$rc" >&2
    exit "$rc"
  fi
  if [ "$rollback_ok" -eq 1 ]; then
    echo "HOME_CENTER_CLUSTER_DEPLOY=ROLLED_BACK rc=$rc" >&2
    exit "$rc"
  fi
  echo "HOME_CENTER_CLUSTER_DEPLOY=ROLLBACK_FAILED original_rc=$rc" >&2
  exit 70
}
trap cleanup EXIT
trap 'fail_rollback $?' ERR
trap 'fail_rollback 130' INT
trap 'fail_rollback 143' TERM
trap 'fail_rollback 129' HUP

# Provision the independent browser-compatible Web trust anchor before either
# node starts a release whose config requires it. The private signing key never
# leaves dc01 and survives version rollback so a failed upgrade can be retried.
WEB_CA_DIR=/etc/home-center/pki/web-ca
WEB_CA_CERT=$WEB_CA_DIR/ca.crt
WEB_CA_KEY=$WEB_CA_DIR/ca.key
WEB_CA_MIN_VALIDITY_SECONDS=$((427 * 86400))
if [ ! -e "$WEB_CA_CERT" ] && [ ! -e "$WEB_CA_KEY" ]; then
  LOCAL_PREWEB_IDENTITY_COUNT=0
  for path in /etc/home-center/pki/web/current/tls.crt /etc/home-center/pki/web/current/tls.key; do
    [ ! -s "$path" ] || LOCAL_PREWEB_IDENTITY_COUNT=$((LOCAL_PREWEB_IDENTITY_COUNT + 1))
  done
  REMOTE_PREWEB_IDENTITY_COUNT=$("${SSH[@]}" 'set -Eeuo pipefail
count=0
for path in /etc/home-center/pki/web/current/tls.crt /etc/home-center/pki/web/current/tls.key; do
  sudo -n test ! -s "$path" || count=$((count + 1))
done
printf "%s\n" "$count"')
  if [ "$LOCAL_PREWEB_IDENTITY_COUNT" -eq 2 ] && [ "$REMOTE_PREWEB_IDENTITY_COUNT" -eq 2 ]; then
    openssl verify -x509_strict -CAfile /etc/home-center/pki/ca.crt /etc/home-center/pki/web/current/tls.crt >/dev/null
    [ "$(openssl pkey -in /etc/home-center/pki/web/current/tls.key -pubout -outform DER | sha256sum | awk '{print $1}')" = \
      "$(openssl x509 -in /etc/home-center/pki/web/current/tls.crt -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | awk '{print $1}')" ]
    "${SSH[@]}" 'set -Eeuo pipefail
sudo -n openssl verify -x509_strict -CAfile /etc/home-center/pki/ca.crt /etc/home-center/pki/web/current/tls.crt >/dev/null
key_sha=$(sudo -n openssl pkey -in /etc/home-center/pki/web/current/tls.key -pubout -outform DER | sha256sum | awk "{print \$1}")
cert_sha=$(sudo -n openssl x509 -in /etc/home-center/pki/web/current/tls.crt -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | awk "{print \$1}")
test "$key_sha" = "$cert_sha"'
    echo QUARANTINED_PEER_SIGNED_WEB_IDENTITIES=ADMITTED_FOR_MIGRATION
  elif [ "$LOCAL_PREWEB_IDENTITY_COUNT" -ne 0 ] || [ "$REMOTE_PREWEB_IDENTITY_COUNT" -ne 0 ]; then
    echo CLUSTER_PREWEB_IDENTITY_STATE_REJECTED >&2
    false
  fi
  "${SSH[@]}" 'set -Eeuo pipefail
sudo -n test ! -e /etc/home-center/pki/web-ca/ca.crt
sudo -n test ! -L /etc/home-center/pki/web-ca/ca.crt
sudo -n test ! -e /etc/home-center/pki/web-ca/ca.key
sudo -n test ! -L /etc/home-center/pki/web-ca/ca.key' || { echo DC02_WEB_CA_STATE_AMBIGUOUS_RECOVERY_REQUIRED >&2; false; }
  [ ! -e "$WEB_CA_DIR" ] || { echo HOME_CENTER_WEB_CA_DIRECTORY_NOT_EMPTY >&2; false; }
  WEB_CA_STAGE=$(mktemp -d /etc/home-center/pki/.web-ca.XXXXXX)
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:prime256v1 -out "$WEB_CA_STAGE/ca.key"
  openssl req -x509 -new -sha256 -key "$WEB_CA_STAGE/ca.key" -out "$WEB_CA_STAGE/ca.crt" -days 3650 \
    -subj '/CN=Home Center HM.DM Web CA/O=Home Center' \
    -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
    -addext 'keyUsage=critical,keyCertSign,cRLSign' \
    -addext 'subjectKeyIdentifier=hash' \
    -addext 'authorityKeyIdentifier=keyid:always'
  chown root:root "$WEB_CA_STAGE/ca.key"
  chown root:home-center "$WEB_CA_STAGE" "$WEB_CA_STAGE/ca.crt"
  chmod 0750 "$WEB_CA_STAGE"
  chmod 0600 "$WEB_CA_STAGE/ca.key"
  chmod 0644 "$WEB_CA_STAGE/ca.crt"
  sync -f "$WEB_CA_STAGE/ca.key"
  sync -f "$WEB_CA_STAGE/ca.crt"
  sync -f "$WEB_CA_STAGE"
  mv -T "$WEB_CA_STAGE" "$WEB_CA_DIR"
  sync -f /etc/home-center/pki
  WEB_CA_STAGE=
elif [ ! -s "$WEB_CA_CERT" ] || [ ! -s "$WEB_CA_KEY" ]; then
  echo HOME_CENTER_WEB_CA_PARTIAL_STATE_REJECTED >&2
  false
fi
[ ! -L "$WEB_CA_CERT" ] && [ ! -L "$WEB_CA_KEY" ] || { echo HOME_CENTER_WEB_CA_SYMLINK_REJECTED >&2; false; }
[ "$(stat -c '%F:%u:%a' "$WEB_CA_DIR")" = directory:0:750 ] || { echo HOME_CENTER_WEB_CA_DIRECTORY_METADATA_REJECTED >&2; false; }
[ "$(stat -c '%F:%u:%a' "$WEB_CA_CERT")" = 'regular file:0:644' ] || { echo HOME_CENTER_WEB_CA_CERTIFICATE_METADATA_REJECTED >&2; false; }
[ "$(stat -c '%F:%u:%a' "$WEB_CA_KEY")" = 'regular file:0:600' ] || { echo HOME_CENTER_WEB_CA_KEY_METADATA_REJECTED >&2; false; }
[ "$(stat -c %u "$WEB_CA_CERT")" -eq 0 ] && [ "$(stat -c %u "$WEB_CA_KEY")" -eq 0 ] || { echo HOME_CENTER_WEB_CA_OWNER_REJECTED >&2; false; }
[ $(( 8#$(stat -c %a "$WEB_CA_KEY") & 077 )) -eq 0 ] || { echo HOME_CENTER_WEB_CA_KEY_PERMISSIONS_REJECTED >&2; false; }
openssl verify -x509_strict -CAfile "$WEB_CA_CERT" "$WEB_CA_CERT" >/dev/null
openssl x509 -in "$WEB_CA_CERT" -noout -checkend "$WEB_CA_MIN_VALIDITY_SECONDS" >/dev/null || { echo HOME_CENTER_WEB_CA_RENEWAL_REQUIRED >&2; false; }
WEB_CA_TEXT=$(openssl x509 -in "$WEB_CA_CERT" -noout -text)
grep -q 'Public Key Algorithm: id-ecPublicKey' <<<"$WEB_CA_TEXT"
grep -q 'ASN1 OID: prime256v1' <<<"$WEB_CA_TEXT"
grep -q 'Signature Algorithm: ecdsa-with-SHA256' <<<"$WEB_CA_TEXT"
grep -q 'CA:TRUE' <<<"$WEB_CA_TEXT"
WEB_CA_KEY_PUB=$(openssl pkey -in "$WEB_CA_KEY" -pubout -outform DER | sha256sum | awk '{print $1}')
WEB_CA_CERT_PUB=$(openssl x509 -in "$WEB_CA_CERT" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | awk '{print $1}')
[ "$WEB_CA_KEY_PUB" = "$WEB_CA_CERT_PUB" ] || { echo HOME_CENTER_WEB_CA_KEY_MISMATCH >&2; false; }
[ "$(openssl x509 -in "$WEB_CA_CERT" -outform DER | sha256sum | awk '{print $1}')" != "$(openssl x509 -in /etc/home-center/pki/ca.crt -outform DER | sha256sum | awk '{print $1}')" ] || { echo WEB_AND_PEER_CA_MUST_DIFFER >&2; false; }

openssl verify -x509_strict -CAfile /etc/home-center/pki/ca.crt /etc/home-center/pki/node.crt >/dev/null
openssl x509 -in /etc/home-center/pki/node.crt -noout -checkend 2592000 >/dev/null
LOCAL_CERT_CN=$(openssl x509 -in /etc/home-center/pki/node.crt -noout -subject -nameopt RFC2253 | sed -n 's/^subject=.*CN=\([^,]*\).*$/\1/p')
[ "$LOCAL_CERT_CN" = home-center-dc01 ] || { echo DC01_CERT_IDENTITY_MISMATCH >&2; false; }

REMOTE_SECRET_COUNT=$("${SSH[@]}" 'set -Eeuo pipefail
count=0
for path in \
  /etc/home-center/secrets/admin.token \
  /etc/home-center/secrets/session.key \
  /etc/home-center/secrets/audit.key \
  /etc/home-center/pki/node.key \
  /etc/home-center/pki/node.crt \
  /etc/home-center/pki/ca.crt
do
  if sudo -n test -e "$path" || sudo -n test -L "$path"; then
    sudo -n test -s "$path"
    sudo -n test ! -L "$path"
    test "$(sudo -n stat -c "%F:%u" "$path")" = "regular file:0"
    count=$((count + 1))
  fi
done
printf "%s\n" "$count"')
[[ "$REMOTE_SECRET_COUNT" =~ ^[0-6]$ ]] || { echo DC02_SECRET_SNAPSHOT_REJECTED >&2; false; }
[ "$REMOTE_SECRET_COUNT" = 6 ] || {
  echo PARTIAL_DC02_SECRET_STATE_REJECTED >&2
  false
}
"${SSH[@]}" 'set -Eeuo pipefail
gid=$(getent group home-center | awk -F: "{print \$3}")
test ! -e /etc/home-center/pki/ca.key
test ! -L /etc/home-center/pki/ca.key
for private_file in /etc/home-center/secrets/admin.token /etc/home-center/secrets/session.key /etc/home-center/secrets/audit.key /etc/home-center/pki/node.key; do
  test "$(sudo -n stat -c "%F:%u:%g:%a" "$private_file")" = "regular file:0:$gid:640"
done
for certificate in /etc/home-center/pki/node.crt /etc/home-center/pki/ca.crt; do
  test "$(sudo -n stat -c "%F:%u:%a" "$certificate")" = "regular file:0:644"
done'
REMOTE_CA_SHA=$("${SSH[@]}" sudo -n sha256sum /etc/home-center/pki/ca.crt | awk '{print $1}')
[ "$REMOTE_CA_SHA" = "$(sha256sum /etc/home-center/pki/ca.crt | awk '{print $1}')" ] || { echo DC02_CA_MISMATCH >&2; false; }
REMOTE_ADMIN_SHA=$("${SSH[@]}" sudo -n sha256sum /etc/home-center/secrets/admin.token | awk '{print $1}')
[ "$REMOTE_ADMIN_SHA" = "$(sha256sum /etc/home-center/secrets/admin.token | awk '{print $1}')" ] || { echo DC02_ADMIN_TOKEN_MISMATCH >&2; false; }

REMOTE_WEB_CA_DIR=$("${SSH[@]}" 'umask 077; mktemp -d /tmp/home-center-web-ca.XXXXXX')
case "$REMOTE_WEB_CA_DIR" in /tmp/home-center-web-ca.??????) ;; *) echo DC02_WEB_CA_TEMP_PATH_REJECTED >&2; false ;; esac
REMOTE_WEB_CA=$REMOTE_WEB_CA_DIR/ca.crt
if ! "${SCP[@]}" "$WEB_CA_CERT" "dc02:$REMOTE_WEB_CA"; then
  "${SSH[@]}" "rm -rf -- '$REMOTE_WEB_CA_DIR'" || true
  false
fi
WEB_CA_SHA=$(sha256sum "$WEB_CA_CERT" | awk '{print $1}')
REMOTE_WEB_CA_PENDING=$WEB_CA_DIR/.ca.crt.pending.$WEB_CA_SHA
"${SSH[@]}" "set -Eeuo pipefail; \
  trap 'rm -rf -- \"$REMOTE_WEB_CA_DIR\"' EXIT; \
  sudo -n test ! -e '$WEB_CA_KEY'; sudo -n test ! -L '$WEB_CA_KEY'; \
  sudo -n test ! -L '$WEB_CA_DIR'; \
  sudo -n install -d -m 0750 -o root -g home-center '$WEB_CA_DIR'; \
  test \"\$(sudo -n stat -c '%F:%u:%a' '$WEB_CA_DIR')\" = 'directory:0:750'; \
  if sudo -n test -e '$WEB_CA_CERT' || sudo -n test -L '$WEB_CA_CERT'; then \
    test \"\$(sudo -n stat -c '%F:%u:%a' '$WEB_CA_CERT')\" = 'regular file:0:644'; \
    test \"\$(sudo -n sha256sum '$WEB_CA_CERT' | awk '{print \$1}')\" = '$WEB_CA_SHA'; \
  else \
    test \"\$(sha256sum '$REMOTE_WEB_CA' | awk '{print \$1}')\" = '$WEB_CA_SHA'; \
    if sudo -n test -e '$WEB_CA_DIR/.ca.crt.pending' || sudo -n test -L '$WEB_CA_DIR/.ca.crt.pending'; then \
      sudo -n test ! -L '$WEB_CA_DIR/.ca.crt.pending'; \
      test \"\$(sudo -n stat -c '%F:%u:%a' '$WEB_CA_DIR/.ca.crt.pending')\" = 'regular file:0:644'; \
      sudo -n rm -f -- '$WEB_CA_DIR/.ca.crt.pending'; \
    fi; \
    if sudo -n test -e '$REMOTE_WEB_CA_PENDING' || sudo -n test -L '$REMOTE_WEB_CA_PENDING'; then \
      sudo -n test ! -L '$REMOTE_WEB_CA_PENDING'; \
      test \"\$(sudo -n stat -c '%F:%u:%a' '$REMOTE_WEB_CA_PENDING')\" = 'regular file:0:644'; \
      if test \"\$(sudo -n sha256sum '$REMOTE_WEB_CA_PENDING' | awk '{print \$1}')\" != '$WEB_CA_SHA'; then \
        sudo -n rm -f -- '$REMOTE_WEB_CA_PENDING'; \
      fi; \
    fi; \
    if sudo -n test ! -e '$REMOTE_WEB_CA_PENDING' && sudo -n test ! -L '$REMOTE_WEB_CA_PENDING'; then \
      sudo -n install -m 0644 -o root -g home-center '$REMOTE_WEB_CA' '$REMOTE_WEB_CA_PENDING'; \
      test \"\$(sudo -n sha256sum '$REMOTE_WEB_CA_PENDING' | awk '{print \$1}')\" = '$WEB_CA_SHA'; \
      sudo -n sync -f '$REMOTE_WEB_CA_PENDING'; \
    fi; \
    sudo -n mv -T '$REMOTE_WEB_CA_PENDING' '$WEB_CA_CERT'; \
    sudo -n sync -f '$WEB_CA_DIR'; \
  fi; \
  test \"\$(sudo -n stat -c '%F:%u:%a' '$WEB_CA_CERT')\" = 'regular file:0:644'; \
  sudo -n openssl verify -x509_strict -CAfile '$WEB_CA_CERT' '$WEB_CA_CERT' >/dev/null; \
  sudo -n openssl x509 -in '$WEB_CA_CERT' -noout -checkend '$WEB_CA_MIN_VALIDITY_SECONDS' >/dev/null; \
  sudo -n openssl x509 -in '$WEB_CA_CERT' -noout -text | grep -q 'Public Key Algorithm: id-ecPublicKey'; \
  sudo -n openssl x509 -in '$WEB_CA_CERT' -noout -text | grep -q 'ASN1 OID: prime256v1'; \
  sudo -n openssl x509 -in '$WEB_CA_CERT' -noout -text | grep -q 'Signature Algorithm: ecdsa-with-SHA256'"
REMOTE_WEB_CA_SHA=$("${SSH[@]}" sudo -n sha256sum "$WEB_CA_CERT" | awk '{print $1}')
[ "$REMOTE_WEB_CA_SHA" = "$WEB_CA_SHA" ] || { echo DC02_WEB_CA_MISMATCH >&2; false; }
"${SSH[@]}" 'set -Eeuo pipefail; sudo -n openssl verify -x509_strict -CAfile /etc/home-center/pki/ca.crt /etc/home-center/pki/node.crt >/dev/null; sudo -n openssl x509 -in /etc/home-center/pki/node.crt -noout -checkend 2592000 >/dev/null'
REMOTE_CERT_CN=$("${SSH[@]}" sudo -n openssl x509 -in /etc/home-center/pki/node.crt -noout -subject -nameopt RFC2253 | sed -n 's/^subject=.*CN=\([^,]*\).*$/\1/p')
[ "$REMOTE_CERT_CN" = home-center-dc02 ] || { echo DC02_CERT_IDENTITY_MISMATCH >&2; false; }
LOCAL_PEER_STATE_BEFORE=$(peer_public_state_local) || { echo DC01_PEER_KEY_CERT_MISMATCH >&2; false; }
REMOTE_PEER_STATE_BEFORE=$(peer_public_state_remote) || { echo DC02_PEER_KEY_CERT_MISMATCH >&2; false; }
verify_bidirectional_peer_identity || { echo CLUSTER_PEER_PREFLIGHT_REJECTED >&2; false; }
echo CLUSTER_PEER_PREFLIGHT=PASS

CLUSTER_TRANSACTION_OWNED=1
publish_cluster_transaction started
echo CLUSTER_DEPLOY_TRANSACTION_STARTED="$TRANSACTION_ID"

REMOTE_DEPLOY_DIR=$("${SSH[@]}" 'umask 077; mktemp -d /tmp/home-center-deploy.XXXXXX')
case "$REMOTE_DEPLOY_DIR" in /tmp/home-center-deploy.??????) ;; *) echo DC02_DEPLOY_TEMP_PATH_REJECTED >&2; false ;; esac
REMOTE_ARTIFACT=$REMOTE_DEPLOY_DIR/artifact.tar.gz
REMOTE_INSTALLER=$REMOTE_DEPLOY_DIR/install-node.sh
if ! "${SCP[@]}" "$ARTIFACT" "dc02:$REMOTE_ARTIFACT" || ! "${SCP[@]}" "$INSTALLER" "dc02:$REMOTE_INSTALLER"; then
  "${SSH[@]}" "rm -rf -- '$REMOTE_DEPLOY_DIR'" || true
  false
fi
REMOTE_ROLLBACK=/var/backups/home-center-deploy/$TRANSACTION_ID-dc02
REMOTE_TRANSACTION_FILE=/var/lib/home-center-deploy/transactions/$TRANSACTION_ID-dc02.json
REMOTE_OUTPUT=
REMOTE_COMMAND_OK=0
REMOTE_MAY_HAVE_MUTATED=1
if REMOTE_OUTPUT=$("${SSH[@]}" "set -Eeuo pipefail; trap 'rm -rf -- \"$REMOTE_DEPLOY_DIR\"' EXIT; chmod 0600 '$REMOTE_ARTIFACT' '$REMOTE_INSTALLER'; sudo -n bash '$REMOTE_INSTALLER' --artifact '$REMOTE_ARTIFACT' --sha256 '$SHA256' --node dc02 --transaction-id '$TRANSACTION_ID' --expected-current-release '$REMOTE_SOURCE_RELEASE' --expected-current-version '$REMOTE_SOURCE_VERSION' --expected-current-revision '$REMOTE_SOURCE_REVISION'"); then
  REMOTE_COMMAND_OK=1
fi
REMOTE_TRANSACTION=
REMOTE_TRANSACTION_STATUS=
for _ in $(seq 1 180); do
  if REMOTE_TRANSACTION=$("${SSH[@]}" "set -Eeuo pipefail; sudo -n test ! -L '$REMOTE_TRANSACTION_FILE'; sudo -n test \"\$(sudo -n stat -c '%F:%u:%g:%a' '$REMOTE_TRANSACTION_FILE' 2>/dev/null)\" = 'regular file:0:0:600'; sudo -n cat '$REMOTE_TRANSACTION_FILE'" 2>/dev/null); then
    if REMOTE_TRANSACTION_STATUS=$(/usr/bin/python3 -I - "$REMOTE_TRANSACTION" "$TRANSACTION_ID" "$TARGET_VERSION" "$TARGET_REVISION" "$SHA256" <<'PY'
import json, sys
try:
    value = json.loads(sys.argv[1])
except json.JSONDecodeError as exc:
    raise SystemExit("dc02_deploy_outcome_unknown") from exc
expected = {
    "schema": "home-center.deploy-transaction.v1",
    "transaction_id": sys.argv[2],
    "node": "dc02",
    "version": sys.argv[3],
    "revision": sys.argv[4],
    "artifact_sha256": sys.argv[5],
    "release": f"/opt/home-center/releases/{sys.argv[3]}-{sys.argv[4][:12]}-{sys.argv[5][:12]}",
    "rollback_point": f"/var/backups/home-center-deploy/{sys.argv[2]}-dc02",
}
if not isinstance(value, dict) or set(value) != set(expected) | {"status"}:
    raise SystemExit("dc02_deploy_transaction_shape_rejected")
status = value.pop("status")
if value != expected or status not in {"started", "succeeded", "rolled_back", "recovery_required"}:
    raise SystemExit("dc02_deploy_transaction_rejected")
print(status)
PY
    ); then
      case "$REMOTE_TRANSACTION_STATUS" in
        succeeded|rolled_back|recovery_required) break ;;
        started) ;;
        *) REMOTE_TRANSACTION_STATUS= ;;
      esac
    fi
  fi
  sleep 1
done
[ "$REMOTE_TRANSACTION_STATUS" = succeeded ] || {
  echo "DC02_DEPLOY_TERMINAL_STATUS=${REMOTE_TRANSACTION_STATUS:-unknown}" >&2
  false
}
if [ "$REMOTE_COMMAND_OK" -ne 1 ]; then
  echo DC02_SSH_RESULT_RECOVERED_FROM_DURABLE_TRANSACTION=PASS
fi
REMOTE_DEPLOYED=1
printf '%s\n' "$REMOTE_OUTPUT"

# The standby is a real software canary: exact durable release evidence,
# readiness, service controls, backup result (installer gate), peer mTLS and a
# bounded soak all pass before the leader installer is invoked.
REMOTE_CANARY_CA=/etc/home-center/pki/ca.crt
REMOTE_WEB_IDENTITY_COUNT=$("${SSH[@]}" 'count=0; for path in /etc/home-center/pki/web/current/tls.crt /etc/home-center/pki/web/current/tls.key; do sudo -n test ! -s "$path" || count=$((count + 1)); done; printf "%s\n" "$count"')
if [ "$REMOTE_WEB_IDENTITY_COUNT" -eq 2 ]; then
  REMOTE_CANARY_ANCHOR=$("${SSH[@]}" 'set -Eeuo pipefail
if sudo -n openssl verify -x509_strict -CAfile /etc/home-center/pki/web-ca/ca.crt /etc/home-center/pki/web/current/tls.crt >/dev/null 2>&1; then
  printf "%s\n" web
elif sudo -n openssl verify -x509_strict -CAfile /etc/home-center/pki/ca.crt /etc/home-center/pki/web/current/tls.crt >/dev/null 2>&1; then
  printf "%s\n" peer
else
  exit 66
fi')
  case "$REMOTE_CANARY_ANCHOR" in
    web) REMOTE_CANARY_CA=$WEB_CA_CERT ;;
    peer) REMOTE_CANARY_CA=/etc/home-center/pki/ca.crt ;;
    *) echo DC02_WEB_IDENTITY_CHAIN_REJECTED >&2; false ;;
  esac
elif [ "$REMOTE_WEB_IDENTITY_COUNT" -ne 0 ]; then
  echo DC02_PARTIAL_WEB_IDENTITY_REJECTED >&2
  false
fi
dc02_software_canary() {
  curl --fail --silent --show-error --cacert "$REMOTE_CANARY_CA" --max-time 5 https://192.168.10.253:8443/readyz |
    /usr/bin/python3 -I -c "import json,sys; d=json.load(sys.stdin); ok=d.get('schema')=='home-center.readiness.v1' and d.get('status')=='ready' and d.get('node_id')=='hm-dm-dc02' and d.get('version')=='$TARGET_VERSION'; sys.exit('dc02_software_canary_readiness_rejected') if not ok else None"
  curl --fail --silent --show-error --cert /etc/home-center/pki/node.crt --key /etc/home-center/pki/node.key \
    --cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.253:9443/internal/v1/node |
    /usr/bin/python3 -I -c "import json,sys; d=json.load(sys.stdin); ok=d.get('cluster_id')=='hm-dm-production' and d.get('capability',{}).get('node',{}).get('id')=='hm-dm-dc02'; sys.exit('dc02_software_canary_peer_mtls_rejected') if not ok else None"
  "${SSH[@]}" 'set -Eeuo pipefail; systemctl is-active --quiet home-center.service; systemctl is-active --quiet home-center-helper.service; systemctl is-active --quiet home-center-tls-maintenance.timer; systemctl is-active --quiet home-center-backup.timer; systemctl is-enabled --quiet home-center.service; systemctl is-enabled --quiet home-center-helper.service; systemctl is-enabled --quiet home-center-tls-maintenance.timer; systemctl is-enabled --quiet home-center-backup.timer'
}
dc02_software_canary
sleep 30
dc02_software_canary
echo DC02_SOFTWARE_CANARY_30S=PASS

LOCAL_ROLLBACK=/var/backups/home-center-deploy/$TRANSACTION_ID-dc01
LOCAL_TRANSACTION_FILE=/var/lib/home-center-deploy/transactions/$TRANSACTION_ID-dc01.json
LOCAL_MAY_HAVE_MUTATED=1
LOCAL_OUTPUT=$(bash "$INSTALLER" --artifact "$ARTIFACT" --sha256 "$SHA256" --node dc01 --transaction-id "$TRANSACTION_ID" --expected-current-release "$LOCAL_SOURCE_RELEASE" --expected-current-version "$LOCAL_SOURCE_VERSION" --expected-current-revision "$LOCAL_SOURCE_REVISION")
[ ! -L "$LOCAL_TRANSACTION_FILE" ] \
  && [ "$(stat -c '%F:%u:%g:%a' "$LOCAL_TRANSACTION_FILE")" = 'regular file:0:0:600' ] \
  || { echo DC01_DEPLOY_TRANSACTION_METADATA_REJECTED >&2; false; }
/usr/bin/python3 -I - "$LOCAL_TRANSACTION_FILE" "$TRANSACTION_ID" "$TARGET_VERSION" "$TARGET_REVISION" "$SHA256" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
expected = {
    "schema": "home-center.deploy-transaction.v1",
    "status": "succeeded",
    "transaction_id": sys.argv[2],
    "node": "dc01",
    "version": sys.argv[3],
    "revision": sys.argv[4],
    "artifact_sha256": sys.argv[5],
    "release": f"/opt/home-center/releases/{sys.argv[3]}-{sys.argv[4][:12]}-{sys.argv[5][:12]}",
    "rollback_point": f"/var/backups/home-center-deploy/{sys.argv[2]}-dc01",
}
if value != expected:
    raise SystemExit("dc01_deploy_transaction_rejected")
PY
LOCAL_DEPLOYED=1
printf '%s\n' "$LOCAL_OUTPUT"

AUTH_CONFIG="$TMP/curl-auth.conf"
IFS= read -r ADMIN_TOKEN </etc/home-center/secrets/admin.token
[ "${#ADMIN_TOKEN}" -ge 32 ] || { echo ADMIN_TOKEN_INVALID >&2; false; }
printf 'header = "Authorization: Bearer %s"\n' "$ADMIN_TOKEN" >"$AUTH_CONFIG"
unset ADMIN_TOKEN
chmod 0600 "$AUTH_CONFIG"
WEB_CURL_CA=/etc/home-center/pki/ca.crt
LOCAL_WEB_IDENTITY_COUNT=0
for web_identity_file in /etc/home-center/pki/web/current/tls.crt /etc/home-center/pki/web/current/tls.key; do
  [ ! -s "$web_identity_file" ] || LOCAL_WEB_IDENTITY_COUNT=$((LOCAL_WEB_IDENTITY_COUNT + 1))
done
REMOTE_WEB_IDENTITY_COUNT=$("${SSH[@]}" 'count=0; for path in /etc/home-center/pki/web/current/tls.crt /etc/home-center/pki/web/current/tls.key; do sudo -n test ! -s "$path" || count=$((count + 1)); done; printf "%s\n" "$count"')
if [ "$LOCAL_WEB_IDENTITY_COUNT" -eq 2 ] && [ "$REMOTE_WEB_IDENTITY_COUNT" -eq 2 ]; then
  [ -s /etc/home-center/pki/web-ca/ca.crt ] || { echo DC01_WEB_CA_MISSING >&2; false; }
  REMOTE_WEB_CA_SHA=$("${SSH[@]}" sudo -n sha256sum /etc/home-center/pki/web-ca/ca.crt | awk '{print $1}')
  [ "$REMOTE_WEB_CA_SHA" = "$(sha256sum /etc/home-center/pki/web-ca/ca.crt | awk '{print $1}')" ] || { echo DC02_WEB_CA_MISMATCH >&2; false; }
  if openssl verify -x509_strict -CAfile /etc/home-center/pki/web-ca/ca.crt /etc/home-center/pki/web/current/tls.crt >/dev/null 2>&1; then
    LOCAL_WEB_ANCHOR=web
  elif openssl verify -x509_strict -CAfile /etc/home-center/pki/ca.crt /etc/home-center/pki/web/current/tls.crt >/dev/null 2>&1; then
    LOCAL_WEB_ANCHOR=peer
  else
    echo DC01_WEB_IDENTITY_CHAIN_REJECTED >&2
    false
  fi
  REMOTE_WEB_ANCHOR=$("${SSH[@]}" 'set -Eeuo pipefail
if sudo -n openssl verify -x509_strict -CAfile /etc/home-center/pki/web-ca/ca.crt /etc/home-center/pki/web/current/tls.crt >/dev/null 2>&1; then
  printf "%s\n" web
elif sudo -n openssl verify -x509_strict -CAfile /etc/home-center/pki/ca.crt /etc/home-center/pki/web/current/tls.crt >/dev/null 2>&1; then
  printf "%s\n" peer
else
  exit 66
fi')
  [ "$LOCAL_WEB_ANCHOR" = "$REMOTE_WEB_ANCHOR" ] || { echo CLUSTER_WEB_ANCHOR_STATE_MISMATCH >&2; false; }
  [ "$LOCAL_WEB_ANCHOR" != web ] || WEB_CURL_CA=/etc/home-center/pki/web-ca/ca.crt
elif [ "$LOCAL_WEB_IDENTITY_COUNT" -ne 0 ] || [ "$REMOTE_WEB_IDENTITY_COUNT" -ne 0 ]; then
  echo CLUSTER_WEB_IDENTITY_STATE_MISMATCH >&2
  false
fi
for _ in $(seq 1 30); do
  LOCAL_CLUSTER=$(curl --config "$AUTH_CONFIG" --fail --silent --show-error --cacert "$WEB_CURL_CA" --max-time 5 https://192.168.10.254:8443/api/v1/overview 2>/dev/null || true)
  REMOTE_CLUSTER=$(curl --config "$AUTH_CONFIG" --fail --silent --show-error --cacert "$WEB_CURL_CA" --max-time 5 https://192.168.10.253:8443/api/v1/overview 2>/dev/null || true)
  if /usr/bin/python3 -I - "$LOCAL_CLUSTER" "$REMOTE_CLUSTER" <<'PY' 2>/dev/null
import json,sys
for raw in sys.argv[1:]:
    d=json.loads(raw)
    cluster=d.get('cluster', {})
    if not (
        d.get('schema') == 'home-center.overview.v1'
        and cluster.get('status') == 'healthy'
        and cluster.get('ready_nodes') == 2
        and cluster.get('expected_nodes') == 2
        and cluster.get('automatic_failover') is False
        and {n.get('name') for n in d.get('nodes', [])} == {'dc01', 'dc02'}
    ):
        raise SystemExit('cluster_overview_rejected')
PY
  then break; fi
  sleep 2
done
/usr/bin/python3 -I - "$LOCAL_CLUSTER" "$REMOTE_CLUSTER" <<'PY'
import json,sys
for expected,raw in zip(('leader','standby'),sys.argv[1:]):
    d=json.loads(raw)
    cluster=d.get('cluster', {})
    if not (
        d.get('schema') == 'home-center.overview.v1'
        and cluster.get('status') == 'healthy'
        and cluster.get('ready_nodes') == 2
        and cluster.get('local_role') == expected
        and cluster.get('automatic_failover') is False
        and cluster.get('split_brain_policy') == 'single-writer-manual-failover'
    ):
        raise SystemExit('cluster_acceptance_rejected')
print('CLUSTER_OVERVIEW_BOTH_NODES=PASS')
PY

curl --fail --silent --show-error --cert /etc/home-center/pki/node.crt --key /etc/home-center/pki/node.key --cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.253:9443/internal/v1/node | /usr/bin/python3 -I -c "import json,sys; d=json.load(sys.stdin); ok=d.get('cluster_id')=='hm-dm-production' and d.get('capability',{}).get('node',{}).get('id')=='hm-dm-dc02'; sys.exit('dc02_peer_identity_rejected') if not ok else print('DC02_MTLS_IDENTITY=PASS')"
"${SSH[@]}" "sudo -n curl --fail --silent --show-error --cert /etc/home-center/pki/node.crt --key /etc/home-center/pki/node.key --cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.254:9443/internal/v1/node" | /usr/bin/python3 -I -c "import json,sys; d=json.load(sys.stdin); ok=d.get('cluster_id')=='hm-dm-production' and d.get('capability',{}).get('node',{}).get('id')=='hm-dm-dc01'; sys.exit('dc01_peer_identity_rejected') if not ok else print('DC01_MTLS_IDENTITY_FROM_DC02=PASS')"

systemctl is-active --quiet home-center.service
systemctl is-enabled --quiet home-center-backup.timer
"${SSH[@]}" 'set -Eeuo pipefail; systemctl is-active --quiet home-center.service; systemctl is-enabled --quiet home-center-backup.timer'
LOCAL_BACKUPS=$(find /var/backups/home-center -maxdepth 1 -name 'home-center-*.manifest.json' -type f | wc -l)
REMOTE_BACKUPS=$("${SSH[@]}" "sudo -n find /var/backups/home-center -maxdepth 1 -name 'home-center-*.manifest.json' -type f | wc -l")
[ "$LOCAL_BACKUPS" -ge 1 ] && [ "$REMOTE_BACKUPS" -ge 1 ] \
  || { echo CLUSTER_BACKUP_EVIDENCE_REJECTED >&2; false; }

EXPECTED_TARGET_RELEASE=/opt/home-center/releases/$TARGET_VERSION-${TARGET_REVISION:0:12}-${SHA256:0:12}
[ "$(current_release_snapshot)" = "$EXPECTED_TARGET_RELEASE|$TARGET_VERSION|$TARGET_REVISION" ] \
  || { echo DC01_FINAL_RELEASE_IDENTITY_REJECTED >&2; false; }
FINAL_REMOTE_SNAPSHOT=$("${SSH[@]}" 'set -Eeuo pipefail
release=$(readlink -f /opt/home-center/current)
version=$(tr -d "\r\n" <"$release/VERSION")
revision=$(tr -d "\r\n" <"$release/REVISION")
printf "%s|%s|%s\n" "$release" "$version" "$revision"')
[ "$FINAL_REMOTE_SNAPSHOT" = "$EXPECTED_TARGET_RELEASE|$TARGET_VERSION|$TARGET_REVISION" ] \
  || { echo DC02_FINAL_RELEASE_IDENTITY_REJECTED >&2; false; }
[ "$(peer_public_state_local)" = "$LOCAL_PEER_STATE_BEFORE" ] \
  || { echo DC01_FINAL_PEER_IDENTITY_CHANGED >&2; false; }
[ "$(peer_public_state_remote)" = "$REMOTE_PEER_STATE_BEFORE" ] \
  || { echo DC02_FINAL_PEER_IDENTITY_CHANGED >&2; false; }
verify_bidirectional_peer_identity || { echo FINAL_PEER_IDENTITY_PROOF_REJECTED >&2; false; }
FINAL_LOCAL_READY=$(curl --fail --silent --show-error --cacert "$WEB_CURL_CA" --max-time 5 https://192.168.10.254:8443/readyz)
FINAL_REMOTE_READY=$(curl --fail --silent --show-error --cacert "$WEB_CURL_CA" --max-time 5 https://192.168.10.253:8443/readyz)
/usr/bin/python3 -I - "$FINAL_LOCAL_READY" "$FINAL_REMOTE_READY" "$TARGET_VERSION" <<'PY'
import json, sys
for node, raw in zip(("dc01", "dc02"), sys.argv[1:3]):
    value = json.loads(raw)
    if not (
        value.get("schema") == "home-center.readiness.v1"
        and value.get("status") == "ready"
        and value.get("node_id") == "hm-dm-" + node
        and value.get("version") == sys.argv[3]
    ):
        raise SystemExit("final_cluster_readiness_rejected")
PY
FINAL_REMOTE_TRANSACTION=$("${SSH[@]}" "set -Eeuo pipefail; sudo -n test ! -L '$REMOTE_TRANSACTION_FILE'; sudo -n cat '$REMOTE_TRANSACTION_FILE'")
/usr/bin/python3 -I - "$LOCAL_TRANSACTION_FILE" "$FINAL_REMOTE_TRANSACTION" "$TRANSACTION_ID" "$TARGET_VERSION" "$TARGET_REVISION" "$SHA256" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    values = [json.load(handle), json.loads(sys.argv[2])]
for node, value in zip(("dc01", "dc02"), values):
    expected = {
        "schema": "home-center.deploy-transaction.v1",
        "status": "succeeded",
        "transaction_id": sys.argv[3],
        "node": node,
        "version": sys.argv[4],
        "revision": sys.argv[5],
        "artifact_sha256": sys.argv[6],
        "release": f"/opt/home-center/releases/{sys.argv[4]}-{sys.argv[5][:12]}-{sys.argv[6][:12]}",
        "rollback_point": f"/var/backups/home-center-deploy/{sys.argv[3]}-{node}",
    }
    if value != expected:
        raise SystemExit("final_node_transaction_rejected")
PY
systemctl is-active --quiet home-center.service
systemctl is-active --quiet home-center-helper.service
systemctl is-active --quiet home-center-tls-maintenance.timer
systemctl is-active --quiet home-center-backup.timer
"${SSH[@]}" 'set -Eeuo pipefail
systemctl is-active --quiet home-center.service
systemctl is-active --quiet home-center-helper.service
systemctl is-active --quiet home-center-tls-maintenance.timer
systemctl is-active --quiet home-center-backup.timer'
echo FINAL_EXACT_RELEASE_AND_PEER_INVARIANTS=PASS

publish_cluster_transaction succeeded
trap - ERR INT TERM HUP
echo HOME_CENTER_CLUSTER_DEPLOY=PASS
echo DEPLOY_ORDER=dc02_then_dc01
echo DC01_MODE="$LOCAL_MODE"
echo DC02_MODE="$REMOTE_MODE"
echo ARTIFACT_SHA256="$SHA256"
echo BOTH_NODES_READY=PASS
echo PEER_MTLS=PASS
echo BACKUP_CREATE_VERIFY=PASS
echo WEB_CA_PROVISIONING=PASS
echo AUTOMATIC_FAILOVER=DISABLED_NO_WITNESS
echo SAMBA_AD_MUTATIONS=NONE
