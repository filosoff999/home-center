#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

[ "$(id -u)" -eq 0 ] || { echo ROOT_REQUIRED >&2; exit 65; }
[ "${1:-}" = "--rollback-point" ] && [ -n "${2:-}" ] || { echo "usage: $0 --rollback-point PATH" >&2; exit 64; }
NODE=$(hostname -s)
[[ "$NODE" =~ ^dc0[12]$ ]] || { echo HOST_IDENTITY_MISMATCH >&2; exit 65; }
EXPECTED_IP=192.168.10.254; [ "$NODE" = dc02 ] && EXPECTED_IP=192.168.10.253
ip -4 -o addr show | awk '{print $4}' | grep -qx "$EXPECTED_IP/24" || { echo IP_IDENTITY_MISMATCH >&2; exit 65; }

LOCK_DIR=/run/home-center-locks
LOCK_FILE=$LOCK_DIR/node-mutation.lock
if [ ! -e "$LOCK_DIR" ] && [ ! -L "$LOCK_DIR" ]; then
  install -d -m 0700 -o root -g root "$LOCK_DIR"
fi
[ ! -L "$LOCK_DIR" ] && [ "$(stat -c '%F:%u:%g:%a' "$LOCK_DIR" 2>/dev/null)" = directory:0:0:700 ] || { echo HOME_CENTER_LOCK_DIRECTORY_REJECTED >&2; exit 66; }
if [ ! -e "$LOCK_FILE" ] && [ ! -L "$LOCK_FILE" ]; then
  install -m 0600 -o root -g root /dev/null "$LOCK_FILE"
fi
[ ! -L "$LOCK_FILE" ] && [ "$(stat -c '%F:%u:%g:%a' "$LOCK_FILE" 2>/dev/null)" = 'regular file:0:0:600' ] || { echo HOME_CENTER_LOCK_FILE_REJECTED >&2; exit 66; }
exec 9<>"$LOCK_FILE"
flock -w 360 9 || { echo HOME_CENTER_NODE_MUTATION_TIMEOUT >&2; exit 75; }

[ ! -L "$2" ] || { echo ROLLBACK_POINT_SYMLINK_REJECTED >&2; exit 66; }
POINT=$(readlink -f -- "$2") || { echo INVALID_ROLLBACK_POINT >&2; exit 65; }
[ "$2" = "$POINT" ] || { echo NON_CANONICAL_ROLLBACK_POINT >&2; exit 65; }
[[ "$POINT" =~ ^/var/backups/home-center-deploy/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-dc0[12]$ ]] || { echo INVALID_ROLLBACK_POINT >&2; exit 65; }
[ "${POINT##*-}" = "$NODE" ] || { echo ROLLBACK_POINT_NODE_MISMATCH >&2; exit 65; }
[ "$(stat -c '%F:%u:%g:%a' "$POINT" 2>/dev/null)" = directory:0:0:700 ] || { echo ROLLBACK_POINT_METADATA_REJECTED >&2; exit 66; }
TRANSACTION_ID=$(basename "$POINT")
TRANSACTION_ID=${TRANSACTION_ID%-$NODE}
TRANSACTION_MARKER=/var/lib/home-center-deploy/transactions/$TRANSACTION_ID-$NODE.json
RECOVERY_AUTHORIZATION=$LOCK_DIR/cluster-recovery-$TRANSACTION_ID
[ ! -L "$RECOVERY_AUTHORIZATION" ] \
  && [ "$(stat -c '%F:%u:%g:%a' "$RECOVERY_AUTHORIZATION" 2>/dev/null)" = 'regular file:0:0:600' ] \
  && [ "$(tr -d '\r\n' <"$RECOVERY_AUTHORIZATION")" = "$TRANSACTION_ID" ] \
  || { echo CLUSTER_RECOVERY_AUTHORIZATION_REJECTED >&2; exit 66; }

transition_transaction_marker() {
  local requested=$1
  /usr/bin/python3 -I - "$TRANSACTION_MARKER" "$TRANSACTION_ID" "$NODE" "$POINT" "$requested" <<'PY'
import json
import os
import stat
import sys
import tempfile

path, transaction_id, node, rollback_point, requested = sys.argv[1:]
info = os.lstat(path)
if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
    raise SystemExit("rollback_transaction_marker_metadata_rejected")
with open(path, encoding="utf-8") as handle:
    value = json.load(handle)
required = {
    "artifact_sha256", "node", "release", "revision", "rollback_point",
    "schema", "status", "transaction_id", "version",
}
if not (
    isinstance(value, dict)
    and set(value) == required
    and value.get("schema") == "home-center.deploy-transaction.v1"
    and value.get("transaction_id") == transaction_id
    and value.get("node") == node
    and value.get("rollback_point") == rollback_point
    and value.get("status") in {"started", "succeeded", "recovery_required", "rolled_back"}
):
    raise SystemExit("rollback_transaction_marker_identity_rejected")
if requested == "validate":
    print(value["status"] + "\t" + value["release"])
    raise SystemExit(0)
if requested not in {"rolled_back", "recovery_required"}:
    raise SystemExit("rollback_transaction_transition_rejected")
value["status"] = requested
directory = os.path.dirname(path)
descriptor, temporary = tempfile.mkstemp(prefix=".transaction.", dir=directory)
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
}

TRANSACTION_IDENTITY=$(transition_transaction_marker validate) || { echo ROLLBACK_TRANSACTION_MARKER_REJECTED >&2; exit 66; }
IFS=$'\t' read -r TRANSACTION_STATUS TRANSACTION_RELEASE <<<"$TRANSACTION_IDENTITY"

require_safe_regular() {
  local path=$1
  [ ! -L "$path" ] && [ "$(stat -c %F "$path" 2>/dev/null)" = 'regular file' ] || return 1
  [ "$(stat -c %u "$path")" -eq 0 ] || return 1
  [ $(( 8#$(stat -c %a "$path") & 022 )) -eq 0 ]
}

validate_slot() {
  local name=$1 backup=$POINT/$1 marker=$POINT/$1.existed
  if [ -e "$marker" ] || [ -L "$marker" ]; then
    require_safe_regular "$marker" || return 1
    require_safe_regular "$backup" || return 1
  else
    [ ! -e "$backup" ] && [ ! -L "$backup" ]
  fi
}

require_safe_regular "$POINT/previous-release" || { echo PREVIOUS_RELEASE_METADATA_REJECTED >&2; exit 66; }
[ "$(wc -l <"$POINT/previous-release")" -eq 1 ] || { echo PREVIOUS_RELEASE_RECORD_REJECTED >&2; exit 66; }
for slot in config.json home-center.service home-center-backup.service home-center-backup.timer \
  helper-policy.json home-center-helper.service home-center-tls-maintenance.service home-center-tls-maintenance.timer; do
  validate_slot "$slot" || { echo "ROLLBACK_SLOT_REJECTED=$slot" >&2; exit 66; }
done

PREVIOUS=$(tr -d '\r\n' <"$POINT/previous-release")
PREVIOUS_VERSION= PREVIOUS_REVISION=
if [ -n "$PREVIOUS" ]; then
  [[ "$PREVIOUS" =~ ^/opt/home-center/releases/[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{12}-[0-9a-f]{12}$ ]] || { echo INVALID_PREVIOUS_RELEASE >&2; exit 66; }
  [ ! -L "$PREVIOUS" ] && [ "$(stat -c '%F:%u' "$PREVIOUS" 2>/dev/null)" = directory:0 ] || { echo PREVIOUS_RELEASE_METADATA_REJECTED >&2; exit 66; }
  require_safe_regular "$PREVIOUS/VERSION" && require_safe_regular "$PREVIOUS/REVISION" || { echo PREVIOUS_RELEASE_IDENTITY_MISSING >&2; exit 66; }
  PREVIOUS_VERSION=$(tr -d '\r\n' <"$PREVIOUS/VERSION")
  PREVIOUS_REVISION=$(tr -d '\r\n' <"$PREVIOUS/REVISION")
  [[ "$PREVIOUS_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] && [[ "$PREVIOUS_REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo PREVIOUS_RELEASE_IDENTITY_REJECTED >&2; exit 66; }
  [[ "$PREVIOUS" == "/opt/home-center/releases/${PREVIOUS_VERSION}-${PREVIOUS_REVISION:0:12}-"* ]] || { echo PREVIOUS_RELEASE_PATH_IDENTITY_MISMATCH >&2; exit 66; }
fi

CURRENT_BEFORE_ROLLBACK=$(readlink -f /opt/home-center/current 2>/dev/null || true)
case "$TRANSACTION_STATUS" in
  succeeded)
    [ "$CURRENT_BEFORE_ROLLBACK" = "$TRANSACTION_RELEASE" ] || { echo ROLLBACK_CURRENT_RELEASE_CAS_REJECTED >&2; exit 66; }
    ;;
  rolled_back)
    [ "$CURRENT_BEFORE_ROLLBACK" = "$PREVIOUS" ] || { echo ROLLBACK_IDEMPOTENT_CURRENT_CAS_REJECTED >&2; exit 66; }
    ;;
  started|recovery_required)
    [ "$CURRENT_BEFORE_ROLLBACK" = "$TRANSACTION_RELEASE" ] || [ "$CURRENT_BEFORE_ROLLBACK" = "$PREVIOUS" ] \
      || { echo ROLLBACK_RECOVERY_CURRENT_CAS_REJECTED >&2; exit 66; }
    ;;
  *)
    echo ROLLBACK_TRANSACTION_STATUS_REJECTED >&2
    exit 66
    ;;
esac

if [ "$TRANSACTION_STATUS" = rolled_back ]; then
  [ -n "$PREVIOUS" ] || { echo ROLLBACK_IDEMPOTENT_PREVIOUS_RELEASE_MISSING >&2; exit 66; }
  [ "$(tr -d '\r\n' </opt/home-center/current/VERSION)" = "$PREVIOUS_VERSION" ]
  [ "$(tr -d '\r\n' </opt/home-center/current/REVISION)" = "$PREVIOUS_REVISION" ]
  systemctl is-active --quiet home-center.service
  echo HOME_CENTER_NODE_ROLLBACK=ALREADY_ROLLED_BACK
  exit 0
fi

restore_slot() {
  local name=$1 target=$2 mode=$3 owner=$4 group=$5
  if [ -f "$POINT/$name.existed" ]; then
    install -m "$mode" -o "$owner" -g "$group" "$POINT/$name" "$target"
  else
    rm -f -- "$target"
  fi
}

ensure_unit_disabled_inactive() {
  local unit=$1 properties load active state
  systemctl disable --now "$unit" >/dev/null 2>&1 || true
  if ! properties=$(systemctl show "$unit" --property=LoadState --property=ActiveState 2>/dev/null); then
    return 1
  fi
  load=$(sed -n 's/^LoadState=//p' <<<"$properties")
  active=$(sed -n 's/^ActiveState=//p' <<<"$properties")
  case "$active" in inactive|failed) ;; *) return 1 ;; esac
  [ "$load" != not-found ] || return 0
  if state=$(systemctl is-enabled "$unit" 2>/dev/null); then :; else :; fi
  case "$state" in disabled|masked|static|indirect|generated|transient) return 0 ;; *) return 1 ;; esac
}

stop_unit_checked() {
  local unit=$1 properties load active
  if ! properties=$(systemctl show "$unit" --property=LoadState --property=ActiveState 2>/dev/null); then
    return 1
  fi
  load=$(sed -n 's/^LoadState=//p' <<<"$properties")
  active=$(sed -n 's/^ActiveState=//p' <<<"$properties")
  if [ "$load" != not-found ]; then
    systemctl stop "$unit" || return 1
    if ! properties=$(systemctl show "$unit" --property=ActiveState 2>/dev/null); then
      return 1
    fi
    active=$(sed -n 's/^ActiveState=//p' <<<"$properties")
  fi
  case "$active" in inactive|failed) return 0 ;; *) return 1 ;; esac
}

MUTATION_STARTED=0
SIGNAL_CAUGHT=0
ROLLBACK_MAIN_BASHPID=$BASHPID
rollback_failure() {
  local rc=${1:-$?}
  if [ "$BASHPID" -ne "$ROLLBACK_MAIN_BASHPID" ]; then
    return "$rc"
  fi
  trap - ERR INT TERM HUP
  if [ "$MUTATION_STARTED" -eq 1 ]; then
    transition_transaction_marker recovery_required >/dev/null 2>&1 || true
    echo "HOME_CENTER_NODE_ROLLBACK=FAILED rc=$rc" >&2
    exit 70
  fi
  exit "$rc"
}
defer_signal() {
  SIGNAL_CAUGHT=1
  echo HOME_CENTER_ROLLBACK_SIGNAL_DEFERRED >&2
}
trap 'rollback_failure $?' ERR
trap defer_signal INT TERM HUP

MUTATION_STARTED=1
for unit in home-center-tls-maintenance.timer home-center-tls-maintenance.service home-center-helper.service \
  home-center-backup.timer home-center-backup.service home-center.service; do
  if ! stop_unit_checked "$unit"; then
    transition_transaction_marker recovery_required >/dev/null 2>&1 || true
    systemctl restart home-center.service >/dev/null 2>&1 || true
    echo "HOME_CENTER_ROLLBACK_STOP_FAILED=$unit" >&2
    exit 70
  fi
done

restore_slot config.json /etc/home-center/config.json 0640 root home-center
restore_slot home-center.service /etc/systemd/system/home-center.service 0644 root root
restore_slot home-center-backup.service /etc/systemd/system/home-center-backup.service 0644 root root
restore_slot home-center-backup.timer /etc/systemd/system/home-center-backup.timer 0644 root root
restore_slot helper-policy.json /etc/home-center/helper-policy.json 0644 root root
restore_slot home-center-helper.service /etc/systemd/system/home-center-helper.service 0644 root root
restore_slot home-center-tls-maintenance.service /etc/systemd/system/home-center-tls-maintenance.service 0644 root root
restore_slot home-center-tls-maintenance.timer /etc/systemd/system/home-center-tls-maintenance.timer 0644 root root

if [ -n "$PREVIOUS" ]; then
  CURRENT_PENDING=/opt/home-center/.current.rollback.$(basename "$POINT")
  if [ -e "$CURRENT_PENDING" ] || [ -L "$CURRENT_PENDING" ]; then
    [ -L "$CURRENT_PENDING" ] && [ "$(readlink "$CURRENT_PENDING")" = "$PREVIOUS" ] || { echo STALE_ROLLBACK_LINK_REJECTED >&2; false; }
    unlink "$CURRENT_PENDING"
  fi
  ln -s "$PREVIOUS" "$CURRENT_PENDING"
  mv -Tf "$CURRENT_PENDING" /opt/home-center/current
  sync -f /opt/home-center
  systemctl daemon-reload
  systemctl enable home-center.service home-center-backup.timer >/dev/null
  systemctl restart home-center.service home-center-backup.timer

  if [ -f "$POINT/home-center-helper.service.existed" ] && [ -f "$PREVIOUS/home_center/privileged_helper.py" ]; then
    systemctl enable home-center-helper.service >/dev/null
    systemctl restart home-center-helper.service
  else
    ensure_unit_disabled_inactive home-center-helper.service
  fi
  if [ -f "$POINT/home-center-tls-maintenance.timer.existed" ] && [ -f "$PREVIOUS/home_center/tls_maintenance.py" ]; then
    systemctl enable home-center-tls-maintenance.timer >/dev/null
    systemctl restart home-center-tls-maintenance.timer
  else
    ensure_unit_disabled_inactive home-center-tls-maintenance.timer
  fi

  HEALTH_CA=/etc/home-center/pki/ca.crt
  if grep -q '"web_ca"' /etc/home-center/config.json \
    && [ -s /etc/home-center/pki/web/current/tls.crt ] \
    && [ -s /etc/home-center/pki/web/current/tls.key ]; then
    if openssl verify -x509_strict -CAfile /etc/home-center/pki/web-ca/ca.crt /etc/home-center/pki/web/current/tls.crt >/dev/null 2>&1; then
      HEALTH_CA=/etc/home-center/pki/web-ca/ca.crt
    elif openssl verify -x509_strict -CAfile /etc/home-center/pki/ca.crt /etc/home-center/pki/web/current/tls.crt >/dev/null 2>&1; then
      HEALTH_CA=/etc/home-center/pki/ca.crt
    else
      echo ROLLBACK_WEB_IDENTITY_CHAIN_REJECTED >&2
      false
    fi
  fi
  HEALTH_FILE=$(mktemp /run/home-center-rollback-health.XXXXXX)
  chmod 0600 "$HEALTH_FILE"
  code=
  for _ in $(seq 1 30); do
    code=$(curl --silent --show-error --cacert "$HEALTH_CA" --output "$HEALTH_FILE" --write-out '%{http_code}' --max-time 3 "https://$EXPECTED_IP:8443/readyz" 2>/dev/null || true)
    [ "$code" = 200 ] && break
    sleep 1
  done
  [ "$code" = 200 ]
  /usr/bin/python3 -I - "$HEALTH_FILE" "$NODE" "$PREVIOUS_VERSION" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
if not (
    value.get("schema") == "home-center.readiness.v1"
    and value.get("status") == "ready"
    and value.get("node_id") == "hm-dm-" + sys.argv[2]
    and value.get("version") == sys.argv[3]
):
    raise SystemExit("rollback_readiness_rejected")
PY
  rm -f -- "$HEALTH_FILE"

  [ "$(readlink -f /opt/home-center/current)" = "$PREVIOUS" ]
  [ "$(tr -d '\r\n' </opt/home-center/current/VERSION)" = "$PREVIOUS_VERSION" ]
  [ "$(tr -d '\r\n' </opt/home-center/current/REVISION)" = "$PREVIOUS_REVISION" ]
  systemctl is-active --quiet home-center.service
  systemctl is-enabled --quiet home-center.service
  systemctl is-active --quiet home-center-backup.timer
  systemctl is-enabled --quiet home-center-backup.timer
  if [ -f "$POINT/home-center-helper.service.existed" ]; then
    systemctl is-active --quiet home-center-helper.service
    systemctl is-enabled --quiet home-center-helper.service
    /usr/sbin/runuser -u home-center -- /usr/bin/python3 -I - "$NODE" <<'PY'
import json, secrets, socket, sys
request = {
    "schema": "home-center.helper.request.v1",
    "request_id": "rollback-probe-" + sys.argv[1] + "-" + secrets.token_hex(4),
    "action": "helper.probe.v1",
    "params": {},
    "nonce": secrets.token_hex(16),
}
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.settimeout(5)
    client.connect("/run/home-center-helper/helper.sock")
    client.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
    response = b""
    while b"\n" not in response and len(response) < 65536:
        chunk = client.recv(4096)
        if not chunk:
            break
        response += chunk
value = json.loads(response.split(b"\n", 1)[0])
if not (
    value.get("schema") == "home-center.helper.result.v1"
    and value.get("request_id") == request["request_id"]
    and value.get("status") == "succeeded"
    and value.get("exit_code") == 0
):
    raise SystemExit("rollback_helper_probe_rejected")
PY
  else
    ensure_unit_disabled_inactive home-center-helper.service
  fi
  if [ -f "$POINT/home-center-tls-maintenance.timer.existed" ]; then
    systemctl is-active --quiet home-center-tls-maintenance.timer
    systemctl is-enabled --quiet home-center-tls-maintenance.timer
  else
    ensure_unit_disabled_inactive home-center-tls-maintenance.timer
  fi
  systemctl start home-center-backup.service
  ROLLBACK_MODE=PREVIOUS_RELEASE
else
  [ ! -L /opt/home-center/current ] || unlink /opt/home-center/current
  sync -f /opt/home-center
  systemctl daemon-reload
  ensure_unit_disabled_inactive home-center-tls-maintenance.timer
  ensure_unit_disabled_inactive home-center-helper.service
  ensure_unit_disabled_inactive home-center.service
  ensure_unit_disabled_inactive home-center-backup.timer
  ! systemctl is-active --quiet home-center-backup.service
  if [ -e /opt/home-center/current ] || [ -L /opt/home-center/current ]; then
    echo INITIAL_ROLLBACK_CURRENT_STILL_PRESENT >&2
    false
  fi
  ROLLBACK_MODE=INITIAL_DEPLOY_DISABLED
fi

transition_transaction_marker rolled_back
trap - ERR INT TERM HUP
[ "$SIGNAL_CAUGHT" -eq 0 ] || echo HOME_CENTER_ROLLBACK_SIGNAL_RECOVERED=PASS
echo ROLLBACK_MODE="$ROLLBACK_MODE"
if [ -n "$PREVIOUS" ]; then
  echo PREVIOUS_RELEASE="$PREVIOUS"
  echo PREVIOUS_VERSION="$PREVIOUS_VERSION"
  echo PREVIOUS_REVISION="$PREVIOUS_REVISION"
fi
echo HOME_CENTER_NODE_ROLLBACK=PASS
