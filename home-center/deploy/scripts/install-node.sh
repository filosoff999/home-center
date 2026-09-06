#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

usage() { echo "usage: $0 --artifact PATH --sha256 HEX --node dc01|dc02 --transaction-id ID --expected-current-release PATH --expected-current-version VERSION --expected-current-revision SHA" >&2; exit 64; }
ARTIFACT= SHA256= NODE= TRANSACTION_ID= EXPECTED_CURRENT_RELEASE= EXPECTED_CURRENT_VERSION= EXPECTED_CURRENT_REVISION=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --artifact) ARTIFACT=${2:-}; shift 2 ;;
    --sha256) SHA256=${2:-}; shift 2 ;;
    --node) NODE=${2:-}; shift 2 ;;
    --transaction-id) TRANSACTION_ID=${2:-}; shift 2 ;;
    --expected-current-release) EXPECTED_CURRENT_RELEASE=${2:-}; shift 2 ;;
    --expected-current-version) EXPECTED_CURRENT_VERSION=${2:-}; shift 2 ;;
    --expected-current-revision) EXPECTED_CURRENT_REVISION=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done
[ -f "$ARTIFACT" ] && [[ "$SHA256" =~ ^[0-9a-f]{64}$ ]] && [[ "$NODE" =~ ^dc0[12]$ ]] \
  && [[ "$TRANSACTION_ID" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$ ]] \
  && [[ "$EXPECTED_CURRENT_RELEASE" =~ ^/opt/home-center/releases/[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{12}-[0-9a-f]{12}$ ]] \
  && [[ "$EXPECTED_CURRENT_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
  && [[ "$EXPECTED_CURRENT_REVISION" =~ ^[0-9a-f]{40}$ ]] || usage
[ "$(hostname -s)" = "$NODE" ] || { echo HOST_IDENTITY_MISMATCH >&2; exit 65; }
EXPECTED_IP=192.168.10.254; [ "$NODE" = dc02 ] && EXPECTED_IP=192.168.10.253
ip -4 -o addr show | awk '{print $4}' | grep -qx "$EXPECTED_IP/24" || { echo IP_IDENTITY_MISMATCH >&2; exit 65; }
[ "$(sha256sum "$ARTIFACT" | awk '{print $1}')" = "$SHA256" ] || { echo ARTIFACT_CHECKSUM_MISMATCH >&2; exit 66; }
ARCHIVE_LIST=$(tar -tzf "$ARTIFACT")
if grep -Eq '(^/|(^|/)\.\.(/|$))' <<<"$ARCHIVE_LIST"; then
  echo UNSAFE_ARCHIVE_PATH >&2
  exit 66
fi
STAMP=$TRANSACTION_ID
VERSION=$(tar -xOf "$ARTIFACT" ./VERSION | tr -d '\r\n')
REVISION=$(tar -xOf "$ARTIFACT" ./REVISION | tr -d '\r\n')
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo INVALID_VERSION >&2; exit 66; }
[ "$VERSION" = 0.4.2 ] || { echo RELEASE_VERSION_NOT_ADMITTED >&2; exit 66; }
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo INVALID_REVISION >&2; exit 66; }

LOCK_DIR=/run/home-center-locks
LOCK_FILE=$LOCK_DIR/node-mutation.lock
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
exec 9<>"$LOCK_FILE"
flock -n 9 || { echo HOME_CENTER_NODE_MUTATION_ALREADY_RUNNING >&2; exit 75; }

RELEASE="/opt/home-center/releases/${VERSION}-${REVISION:0:12}-${SHA256:0:12}"
BACKUP="/var/backups/home-center-deploy/$STAMP-$NODE"
TRANSACTION_DIR=/var/lib/home-center-deploy/transactions
TRANSACTION_FILE=$TRANSACTION_DIR/$TRANSACTION_ID-$NODE.json
TRANSACTION_FILE_OWNED=0
STAGE=
PREVIOUS=
PREVIOUS_VERSION=
PREVIOUS_REVISION=
BACKUP_READY=0
ROLLBACK_RUNNING=0
INSTALL_MAIN_BASHPID=$BASHPID
if [ -e /opt/home-center/current ] || [ -L /opt/home-center/current ]; then
  [ -L /opt/home-center/current ] || { echo CURRENT_RELEASE_NOT_SYMLINK >&2; exit 66; }
  PREVIOUS=$(readlink -f /opt/home-center/current)
  case "$PREVIOUS" in /opt/home-center/releases/*) ;; *) echo INVALID_CURRENT_RELEASE >&2; exit 66 ;; esac
  [ -d "$PREVIOUS" ] && [ ! -L "$PREVIOUS" ] && [ "$(stat -c %u "$PREVIOUS")" -eq 0 ] || { echo CURRENT_RELEASE_MISSING_OR_UNSAFE >&2; exit 66; }
  [ -f "$PREVIOUS/VERSION" ] && [ ! -L "$PREVIOUS/VERSION" ] && [ -f "$PREVIOUS/REVISION" ] && [ ! -L "$PREVIOUS/REVISION" ] || { echo CURRENT_RELEASE_IDENTITY_MISSING >&2; exit 66; }
  PREVIOUS_VERSION=$(tr -d '\r\n' <"$PREVIOUS/VERSION")
  PREVIOUS_REVISION=$(tr -d '\r\n' <"$PREVIOUS/REVISION")
  [[ "$PREVIOUS_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] && [[ "$PREVIOUS_REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo CURRENT_RELEASE_IDENTITY_REJECTED >&2; exit 66; }
  [[ "$PREVIOUS" == "/opt/home-center/releases/${PREVIOUS_VERSION}-${PREVIOUS_REVISION:0:12}-"* ]] || { echo CURRENT_RELEASE_PATH_IDENTITY_MISMATCH >&2; exit 66; }
fi
[ "$PREVIOUS" = "$EXPECTED_CURRENT_RELEASE" ] \
  && [ "$PREVIOUS_VERSION" = "$EXPECTED_CURRENT_VERSION" ] \
  && [ "$PREVIOUS_REVISION" = "$EXPECTED_CURRENT_REVISION" ] \
  || { echo CURRENT_RELEASE_CHANGED_SINCE_CLUSTER_PREFLIGHT >&2; exit 66; }
[ ! -e /opt/home-center/.current.rollback ] && [ ! -L /opt/home-center/.current.rollback ] || {
  [ -L /opt/home-center/.current.rollback ] || { echo STALE_CURRENT_ROLLBACK_PATH_REJECTED >&2; exit 66; }
  unlink /opt/home-center/.current.rollback
}

restore_optional_file() {
  local name=$1 target=$2
  if [ -f "$BACKUP/$name.existed" ]; then
    install -m 0644 -o root -g root "$BACKUP/$name" "$target" || return
  else
    rm -f "$target" || return
  fi
}

restore_optional_files() {
  restore_optional_file helper-policy.json /etc/home-center/helper-policy.json || return
  restore_optional_file home-center-helper.service /etc/systemd/system/home-center-helper.service || return
  restore_optional_file home-center-tls-maintenance.service /etc/systemd/system/home-center-tls-maintenance.service || return
  restore_optional_file home-center-tls-maintenance.timer /etc/systemd/system/home-center-tls-maintenance.timer || return
}

restore_core_files() {
  if [ -f "$BACKUP/config.json.existed" ]; then
    install -m 0640 -o root -g home-center "$BACKUP/config.json" /etc/home-center/config.json || return
  else
    rm -f /etc/home-center/config.json || return
  fi
  for unit in home-center.service home-center-backup.service home-center-backup.timer; do
    if [ -f "$BACKUP/$unit.existed" ]; then
      install -m 0644 -o root -g root "$BACKUP/$unit" "/etc/systemd/system/$unit" || return
    else
      rm -f "/etc/systemd/system/$unit" || return
    fi
  done
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

probe_helper_checked() {
  /usr/sbin/runuser -u home-center -- /usr/bin/python3 -I - "$NODE" <<'PY'
import json, secrets, socket, sys
request = {
    "schema": "home-center.helper.request.v1",
    "request_id": "install-rollback-probe-" + sys.argv[1] + "-" + secrets.token_hex(4),
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
    raise SystemExit("install_rollback_helper_probe_rejected")
PY
}

verify_previous_runtime() {
  local health_ca=/etc/home-center/pki/ca.crt health_file code
  [ "$(readlink -f /opt/home-center/current)" = "$PREVIOUS" ] || return 1
  [ "$(tr -d '\r\n' </opt/home-center/current/VERSION)" = "$PREVIOUS_VERSION" ] || return 1
  [ "$(tr -d '\r\n' </opt/home-center/current/REVISION)" = "$PREVIOUS_REVISION" ] || return 1
  if grep -q '"web_ca"' /etc/home-center/config.json \
    && [ -s /etc/home-center/pki/web/current/tls.crt ] \
    && [ -s /etc/home-center/pki/web/current/tls.key ]; then
    if openssl verify -x509_strict -CAfile /etc/home-center/pki/web-ca/ca.crt /etc/home-center/pki/web/current/tls.crt >/dev/null 2>&1; then
      health_ca=/etc/home-center/pki/web-ca/ca.crt
    elif openssl verify -x509_strict -CAfile /etc/home-center/pki/ca.crt /etc/home-center/pki/web/current/tls.crt >/dev/null 2>&1; then
      health_ca=/etc/home-center/pki/ca.crt
    else
      return 1
    fi
  fi
  health_file=$(mktemp /run/home-center-install-rollback-health.XXXXXX) || return 1
  chmod 0600 "$health_file" || return 1
  code=
  for _ in $(seq 1 30); do
    code=$(curl --silent --show-error --cacert "$health_ca" --output "$health_file" --write-out '%{http_code}' --max-time 3 "https://$EXPECTED_IP:8443/readyz" 2>/dev/null || true)
    [ "$code" = 200 ] && break
    sleep 1
  done
  [ "$code" = 200 ] || { rm -f -- "$health_file"; return 1; }
  if ! /usr/bin/python3 -I - "$health_file" "$NODE" "$PREVIOUS_VERSION" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
if not (
    value.get("schema") == "home-center.readiness.v1"
    and value.get("status") == "ready"
    and value.get("node_id") == "hm-dm-" + sys.argv[2]
    and value.get("version") == sys.argv[3]
):
    raise SystemExit("install_rollback_readiness_rejected")
PY
  then
    rm -f -- "$health_file"
    return 1
  fi
  rm -f -- "$health_file" || return 1
  systemctl is-active --quiet home-center.service || return 1
  systemctl is-enabled --quiet home-center.service || return 1
  systemctl is-active --quiet home-center-backup.timer || return 1
  systemctl is-enabled --quiet home-center-backup.timer || return 1
  if [ -f "$BACKUP/home-center-helper.service.existed" ] && [ -f "$PREVIOUS/home_center/privileged_helper.py" ]; then
    systemctl is-active --quiet home-center-helper.service || return 1
    systemctl is-enabled --quiet home-center-helper.service || return 1
    probe_helper_checked || return 1
  else
    ensure_unit_disabled_inactive home-center-helper.service || return 1
  fi
  if [ -f "$BACKUP/home-center-tls-maintenance.timer.existed" ] && [ -f "$PREVIOUS/home_center/tls_maintenance.py" ]; then
    systemctl is-active --quiet home-center-tls-maintenance.timer || return 1
    systemctl is-enabled --quiet home-center-tls-maintenance.timer || return 1
  else
    ensure_unit_disabled_inactive home-center-tls-maintenance.timer || return 1
  fi
  systemctl start home-center-backup.service
}

publish_transaction() {
  local status=$1 transaction_stage
  transaction_stage=$(mktemp "$TRANSACTION_DIR/.transaction.XXXXXX") || return
  if ! chmod 0600 "$transaction_stage"; then
    rm -f -- "$transaction_stage"
    return 1
  fi
  if ! printf '{"artifact_sha256":"%s","node":"%s","release":"%s","revision":"%s","rollback_point":"%s","schema":"home-center.deploy-transaction.v1","status":"%s","transaction_id":"%s","version":"%s"}\n' \
    "$SHA256" "$NODE" "$RELEASE" "$REVISION" "$BACKUP" "$status" "$TRANSACTION_ID" "$VERSION" >"$transaction_stage"; then
    rm -f -- "$transaction_stage"
    return 1
  fi
  if ! sync -f "$transaction_stage"; then
    rm -f -- "$transaction_stage"
    return 1
  fi
  TRANSACTION_FILE_OWNED=1
  if ! mv -T "$transaction_stage" "$TRANSACTION_FILE"; then
    rm -f -- "$transaction_stage"
    return 1
  fi
  sync -f "$TRANSACTION_FILE" || return
  sync -f "$TRANSACTION_DIR"
}

rollback() {
  local rc=${1:-$?}
  if [ "$BASHPID" -ne "$INSTALL_MAIN_BASHPID" ]; then
    return "$rc"
  fi
  if [ "$ROLLBACK_RUNNING" -eq 1 ]; then
    echo DEPLOY_ROLLBACK=REENTRANT_FAILURE >&2
    exit 70
  fi
  ROLLBACK_RUNNING=1
  set +e
  trap - ERR EXIT INT TERM HUP
  if [ "$BACKUP_READY" != 1 ]; then
    [ -z "$STAGE" ] || rm -rf -- "$STAGE"
    echo "DEPLOY_ABORTED_BEFORE_MUTATION rc=$rc" >&2
    exit "$rc"
  fi
  rollback_ok=1
  for unit in home-center-tls-maintenance.timer home-center-tls-maintenance.service home-center-helper.service \
    home-center-backup.timer home-center-backup.service home-center.service; do
    stop_unit_checked "$unit" || rollback_ok=0
  done
  if [ "$rollback_ok" = 1 ] && [ "$BACKUP_READY" = 1 ]; then
    restore_core_files || rollback_ok=0
    restore_optional_files || rollback_ok=0
  fi
  if [ "$rollback_ok" = 1 ] && [ -n "$PREVIOUS" ] && [ -d "$PREVIOUS" ] && [ ! -L "$PREVIOUS" ]; then
    ln -s "$PREVIOUS" /opt/home-center/.current.rollback || rollback_ok=0
    [ "$rollback_ok" = 0 ] || mv -Tf /opt/home-center/.current.rollback /opt/home-center/current || rollback_ok=0
    [ "$rollback_ok" = 0 ] || sync -f /opt/home-center || rollback_ok=0
    systemctl daemon-reload || rollback_ok=0
    systemctl restart home-center.service || rollback_ok=0
    systemctl enable --now home-center-backup.timer >/dev/null 2>&1 || rollback_ok=0
    if [ -f /etc/systemd/system/home-center-helper.service ] && [ -f "$PREVIOUS/home_center/privileged_helper.py" ]; then
      systemctl enable --now home-center-helper.service >/dev/null 2>&1 || rollback_ok=0
      systemctl is-active --quiet home-center-helper.service || rollback_ok=0
    else
      ensure_unit_disabled_inactive home-center-helper.service || rollback_ok=0
    fi
    if [ -f /etc/systemd/system/home-center-tls-maintenance.timer ] && [ -f "$PREVIOUS/home_center/tls_maintenance.py" ]; then
      systemctl enable --now home-center-tls-maintenance.timer >/dev/null 2>&1 || rollback_ok=0
      systemctl is-active --quiet home-center-tls-maintenance.timer || rollback_ok=0
    else
      ensure_unit_disabled_inactive home-center-tls-maintenance.timer || rollback_ok=0
    fi
    [ "$rollback_ok" = 0 ] || verify_previous_runtime || rollback_ok=0
  elif [ "$rollback_ok" = 1 ] && [ -z "$PREVIOUS" ]; then
    ensure_unit_disabled_inactive home-center-tls-maintenance.timer || rollback_ok=0
    ensure_unit_disabled_inactive home-center-helper.service || rollback_ok=0
    ensure_unit_disabled_inactive home-center.service || rollback_ok=0
    ensure_unit_disabled_inactive home-center-backup.timer || rollback_ok=0
    ! systemctl is-active --quiet home-center-backup.service || rollback_ok=0
    [ ! -L /opt/home-center/current ] || unlink /opt/home-center/current
    sync -f /opt/home-center || rollback_ok=0
    systemctl daemon-reload || rollback_ok=0
    [ ! -e /opt/home-center/current ] && [ ! -L /opt/home-center/current ] || rollback_ok=0
  elif [ -n "$PREVIOUS" ]; then
    rollback_ok=0
  fi
  [ -z "$STAGE" ] || rm -rf -- "$STAGE"
  if [ "$TRANSACTION_FILE_OWNED" -eq 1 ]; then
    if [ "$rollback_ok" = 1 ]; then
      publish_transaction rolled_back || rollback_ok=0
    else
      publish_transaction recovery_required || true
    fi
  fi
  if [ "$rollback_ok" = 1 ]; then
    echo "DEPLOY_ROLLBACK=COMPLETE rc=$rc" >&2
    exit "$rc"
  fi
  echo "DEPLOY_ROLLBACK=FAILED original_rc=$rc" >&2
  exit 70
}
trap 'rollback $?' ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM
trap 'rollback 129' HUP

getent group home-center >/dev/null || groupadd --system home-center
id home-center >/dev/null 2>&1 || useradd --system --gid home-center --home-dir /var/lib/home-center --shell /usr/sbin/nologin home-center
for release_directory in /opt/home-center /opt/home-center/releases; do
  if [ ! -e "$release_directory" ] && [ ! -L "$release_directory" ]; then
    install -d -m 0755 -o root -g root "$release_directory"
  fi
  [ ! -L "$release_directory" ] && [ "$(stat -c '%F:%u:%g:%a' "$release_directory")" = directory:0:0:755 ] \
    || { echo "RELEASE_DIRECTORY_METADATA_REJECTED=$release_directory" >&2; false; }
done
STAGE=$(mktemp -d /opt/home-center/releases/.stage.XXXXXX)
for runtime_directory in /var/lib/home-center /var/backups/home-center; do
  if [ ! -e "$runtime_directory" ] && [ ! -L "$runtime_directory" ]; then
    install -d -m 0750 -o home-center -g home-center "$runtime_directory"
  fi
  [ ! -L "$runtime_directory" ] && [ "$(stat -c '%F:%U:%G:%a' "$runtime_directory")" = directory:home-center:home-center:750 ] \
    || { echo "RUNTIME_DIRECTORY_METADATA_REJECTED=$runtime_directory" >&2; false; }
done
if [ ! -e /var/lib/home-center-deploy ] && [ ! -L /var/lib/home-center-deploy ]; then
  install -d -m 0700 -o root -g root /var/lib/home-center-deploy
fi
[ ! -L /var/lib/home-center-deploy ] && [ "$(stat -c '%F:%u:%g:%a' /var/lib/home-center-deploy)" = directory:0:0:700 ] || { echo TRANSACTION_ROOT_METADATA_REJECTED >&2; false; }
if [ ! -e "$TRANSACTION_DIR" ] && [ ! -L "$TRANSACTION_DIR" ]; then
  install -d -m 0700 -o root -g root "$TRANSACTION_DIR"
fi
[ ! -L "$TRANSACTION_DIR" ] && [ "$(stat -c '%F:%u:%g:%a' "$TRANSACTION_DIR")" = directory:0:0:700 ] || { echo TRANSACTION_DIRECTORY_METADATA_REJECTED >&2; false; }
/usr/bin/python3 -I - "$TRANSACTION_DIR" <<'PY'
import json
import re
import stat
import sys
from pathlib import Path

for path in Path(sys.argv[1]).iterdir():
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise SystemExit("transaction_entry_metadata_rejected")
    if re.fullmatch(r"\.transaction\.[A-Za-z0-9_-]{6,32}", path.name):
        path.unlink()
        continue
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    required = {"artifact_sha256", "node", "release", "revision", "rollback_point", "schema", "status", "transaction_id", "version"}
    if not (
        isinstance(value, dict)
        and value.get("schema") == "home-center.deploy-transaction.v1"
        and set(value) == required
        and value.get("status") in {"succeeded", "rolled_back"}
        and path.name == f'{value.get("transaction_id")}-{value.get("node")}.json'
    ):
        raise SystemExit("unresolved_transaction_recovery_required")
PY
[ ! -e "$TRANSACTION_FILE" ] && [ ! -L "$TRANSACTION_FILE" ] || { echo TRANSACTION_ALREADY_RECORDED >&2; false; }
if [ ! -e /var/backups/home-center-deploy ] && [ ! -L /var/backups/home-center-deploy ]; then
  install -d -m 0700 -o root -g root /var/backups/home-center-deploy
fi
[ ! -L /var/backups/home-center-deploy ] && [ "$(stat -c '%F:%u:%g:%a' /var/backups/home-center-deploy)" = directory:0:0:700 ] \
  || { echo DEPLOY_BACKUP_ROOT_METADATA_REJECTED >&2; false; }
install -d -m 0700 -o root -g root "$BACKUP"
for protected_target in /etc/home-center/config.json /etc/home-center/helper-policy.json \
  /etc/systemd/system/home-center.service /etc/systemd/system/home-center-backup.service \
  /etc/systemd/system/home-center-backup.timer /etc/systemd/system/home-center-helper.service \
  /etc/systemd/system/home-center-tls-maintenance.service /etc/systemd/system/home-center-tls-maintenance.timer; do
  if [ -e "$protected_target" ] || [ -L "$protected_target" ]; then
    [ ! -L "$protected_target" ] && [ "$(stat -c %F "$protected_target")" = 'regular file' ] && [ "$(stat -c %u "$protected_target")" -eq 0 ] \
      || { echo "PROTECTED_TARGET_METADATA_REJECTED=$protected_target" >&2; false; }
  fi
done
for path in /etc/home-center/config.json /etc/systemd/system/home-center.service /etc/systemd/system/home-center-backup.service /etc/systemd/system/home-center-backup.timer; do
  if [ -e "$path" ]; then
    cp -a "$path" "$BACKUP/$(basename "$path")"
    : >"$BACKUP/$(basename "$path").existed"
  fi
done
if [ -e /etc/home-center/helper-policy.json ]; then
  cp -a /etc/home-center/helper-policy.json "$BACKUP/helper-policy.json"
  : >"$BACKUP/helper-policy.json.existed"
fi
for unit in home-center-helper.service home-center-tls-maintenance.service home-center-tls-maintenance.timer; do
  if [ -e "/etc/systemd/system/$unit" ]; then
    cp -a "/etc/systemd/system/$unit" "$BACKUP/$unit"
    : >"$BACKUP/$unit.existed"
  fi
done
printf '%s\n' "$PREVIOUS" >"$BACKUP/previous-release"
find "$BACKUP" -type f -exec sync -f {} +
sync -f "$BACKUP"
sync -f /var/backups/home-center-deploy
BACKUP_READY=1
publish_transaction started

# No timer, backup, or old helper invocation may race the release/helper switch.
# The node mutation lock above also rejects an already-running certificate action.
for quiesced_unit in home-center-tls-maintenance.timer home-center-tls-maintenance.service \
  home-center-backup.timer home-center-backup.service home-center-helper.service; do
  if systemctl cat "$quiesced_unit" >/dev/null 2>&1; then
    systemctl stop "$quiesced_unit"
  fi
done

tar -xzf "$ARTIFACT" -C "$STAGE" --no-same-owner --no-same-permissions
(cd "$STAGE" && sha256sum -c MANIFEST.sha256)
if find "$STAGE" \( -type l -o -type b -o -type c -o -type p -o -type s \) -print -quit | grep -q .; then
  echo UNSAFE_ARTIFACT_TYPE >&2
  false
fi
find "$STAGE" -type d -exec chmod 0755 {} +
find "$STAGE" -type f -exec chmod 0644 {} +
chown -R root:root "$STAGE"
if [ -e "$RELEASE" ] || [ -L "$RELEASE" ]; then
  [ ! -L "$RELEASE" ] && [ "$(stat -c %F "$RELEASE")" = directory ] && [ "$(stat -c %u "$RELEASE")" -eq 0 ] \
    && [ $(( 8#$(stat -c %a "$RELEASE") & 022 )) -eq 0 ] || { echo EXISTING_RELEASE_METADATA_REJECTED >&2; false; }
  diff -qr "$STAGE" "$RELEASE" >/dev/null || { echo EXISTING_RELEASE_CONTENT_MISMATCH >&2; false; }
  rm -rf -- "$STAGE"
  STAGE=
else
  mv "$STAGE" "$RELEASE"
  STAGE=
fi

install -d -m 0750 -o root -g home-center /etc/home-center
install -m 0640 -o root -g home-center "$RELEASE/deploy/config.$NODE.json" /etc/home-center/config.json
install -m 0644 -o root -g root "$RELEASE/deploy/helper-policy.v1.json" /etc/home-center/helper-policy.json
for secret in /etc/home-center/secrets/admin.token /etc/home-center/secrets/session.key /etc/home-center/secrets/audit.key /etc/home-center/pki/node.key /etc/home-center/pki/node.crt /etc/home-center/pki/ca.crt /etc/home-center/pki/web-ca/ca.crt; do
  [ -s "$secret" ] && [ ! -L "$secret" ] && [ "$(stat -c %F "$secret")" = 'regular file' ] && [ "$(stat -c %u "$secret")" -eq 0 ] \
    || { echo "REQUIRED_FILE_METADATA_REJECTED=$secret" >&2; false; }
done
for private_file in /etc/home-center/secrets/admin.token /etc/home-center/secrets/session.key /etc/home-center/secrets/audit.key /etc/home-center/pki/node.key; do
  [ $(( 8#$(stat -c %a "$private_file") & 037 )) -eq 0 ] || { echo "PRIVATE_FILE_PERMISSIONS_REJECTED=$private_file" >&2; false; }
done
[ ! -L /etc/home-center/pki/web-ca ] && [ "$(stat -c %F /etc/home-center/pki/web-ca)" = directory ] && [ "$(stat -c %u /etc/home-center/pki/web-ca)" -eq 0 ] \
  || { echo WEB_CA_DIRECTORY_METADATA_REJECTED >&2; false; }
if [ "$NODE" = dc01 ]; then
  [ -s /etc/home-center/pki/web-ca/ca.key ] && [ ! -L /etc/home-center/pki/web-ca/ca.key ] \
    && [ "$(stat -c '%F:%u:%a' /etc/home-center/pki/web-ca/ca.key)" = 'regular file:0:600' ] \
    || { echo MISSING_OR_UNSAFE_DC01_WEB_CA_PRIVATE_KEY >&2; false; }
else
  [ ! -e /etc/home-center/pki/ca.key ] && [ ! -L /etc/home-center/pki/ca.key ] || { echo DC02_PEER_CA_PRIVATE_KEY_FORBIDDEN >&2; false; }
  [ ! -e /etc/home-center/pki/web-ca/ca.key ] && [ ! -L /etc/home-center/pki/web-ca/ca.key ] || { echo DC02_WEB_CA_PRIVATE_KEY_FORBIDDEN >&2; false; }
fi
chmod 0750 /etc/home-center /etc/home-center/secrets /etc/home-center/pki
chown root:home-center /etc/home-center /etc/home-center/secrets /etc/home-center/pki
chown root:home-center /etc/home-center/config.json \
  /etc/home-center/secrets/admin.token /etc/home-center/secrets/session.key /etc/home-center/secrets/audit.key \
  /etc/home-center/pki/node.key /etc/home-center/pki/node.crt /etc/home-center/pki/ca.crt \
  /etc/home-center/pki/web-ca /etc/home-center/pki/web-ca/ca.crt
chmod 0640 /etc/home-center/config.json \
  /etc/home-center/secrets/admin.token /etc/home-center/secrets/session.key /etc/home-center/secrets/audit.key \
  /etc/home-center/pki/node.key
chmod 0644 /etc/home-center/pki/node.crt /etc/home-center/pki/ca.crt
chmod 0750 /etc/home-center/pki/web-ca
chmod 0644 /etc/home-center/pki/web-ca/ca.crt
if [ "$NODE" = dc01 ]; then
  chown root:root /etc/home-center/pki/web-ca/ca.key
  chmod 0600 /etc/home-center/pki/web-ca/ca.key
fi
chmod 0644 /etc/home-center/helper-policy.json
chown root:root /etc/home-center/helper-policy.json
for web_directory in /etc/home-center/pki/web /etc/home-center/pki/web/candidate; do
  if [ ! -e "$web_directory" ] && [ ! -L "$web_directory" ]; then
    install -d -m 0750 -o root -g home-center "$web_directory"
  fi
  [ ! -L "$web_directory" ] && [ "$(stat -c '%F:%u:%a' "$web_directory")" = directory:0:750 ] \
    || { echo "WEB_DIRECTORY_METADATA_REJECTED=$web_directory" >&2; false; }
done

install -m 0644 -o root -g root "$RELEASE/deploy/home-center.service" /etc/systemd/system/home-center.service
install -m 0644 -o root -g root "$RELEASE/deploy/home-center-backup.service" /etc/systemd/system/home-center-backup.service
install -m 0644 -o root -g root "$RELEASE/deploy/home-center-backup.timer" /etc/systemd/system/home-center-backup.timer
install -m 0644 -o root -g root "$RELEASE/deploy/home-center-helper.service" /etc/systemd/system/home-center-helper.service
install -m 0644 -o root -g root "$RELEASE/deploy/home-center-tls-maintenance.service" /etc/systemd/system/home-center-tls-maintenance.service
install -m 0644 -o root -g root "$RELEASE/deploy/home-center-tls-maintenance.timer" /etc/systemd/system/home-center-tls-maintenance.timer
ln -sfn "$RELEASE" /opt/home-center/.current.new
mv -Tf /opt/home-center/.current.new /opt/home-center/current
sync -f /opt/home-center
systemctl daemon-reload
systemctl enable home-center-helper.service home-center.service home-center-backup.timer home-center-tls-maintenance.timer >/dev/null
systemctl restart home-center-helper.service
systemctl restart home-center.service
systemctl start home-center-backup.timer
systemctl start home-center-tls-maintenance.timer

WEB_HEALTH_CA=/etc/home-center/pki/ca.crt
WEB_IDENTITY_COUNT=0
for web_identity_file in /etc/home-center/pki/web/current/tls.crt /etc/home-center/pki/web/current/tls.key; do
  [ ! -s "$web_identity_file" ] || WEB_IDENTITY_COUNT=$((WEB_IDENTITY_COUNT + 1))
done
if [ "$WEB_IDENTITY_COUNT" -eq 2 ]; then
  [ -s /etc/home-center/pki/web-ca/ca.crt ] || { echo MISSING_WEB_TRUST_ANCHOR >&2; false; }
  if openssl verify -x509_strict -CAfile /etc/home-center/pki/web-ca/ca.crt /etc/home-center/pki/web/current/tls.crt >/dev/null 2>&1; then
    WEB_HEALTH_CA=/etc/home-center/pki/web-ca/ca.crt
  elif openssl verify -x509_strict -CAfile /etc/home-center/pki/ca.crt /etc/home-center/pki/web/current/tls.crt >/dev/null 2>&1; then
    WEB_HEALTH_CA=/etc/home-center/pki/ca.crt
  else
    echo WEB_IDENTITY_CHAIN_REJECTED >&2
    false
  fi
elif [ "$WEB_IDENTITY_COUNT" -ne 0 ]; then
  echo PARTIAL_WEB_IDENTITY_REJECTED >&2
  false
fi

for _ in $(seq 1 20); do
  code=$(curl --silent --show-error --cacert "$WEB_HEALTH_CA" --output /run/home-center-health.json --write-out '%{http_code}' --max-time 3 "https://$EXPECTED_IP:8443/readyz" 2>/dev/null || true)
  [ "$code" = 200 ] && break
  sleep 1
done
[ "${code:-}" = 200 ]
/usr/bin/python3 -I - /run/home-center-health.json "$NODE" "$VERSION" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
if not (
    d.get('schema') == 'home-center.readiness.v1'
    and d.get('status') == 'ready'
    and d.get('version') == sys.argv[3]
    and d.get('node_id') == 'hm-dm-' + sys.argv[2]
):
    raise SystemExit('readiness_contract_rejected')
PY
rm -f /run/home-center-health.json

[ -x /usr/sbin/runuser ] || { echo RUNUSER_REQUIRED_FOR_HELPER_PROBE >&2; false; }
/usr/sbin/runuser -u home-center -- /usr/bin/python3 -I - "$STAMP" <<'PY'
import json,secrets,socket,sys
request={
  'schema':'home-center.helper.request.v1',
  'request_id':'install-probe-'+sys.argv[1],
  'action':'helper.probe.v1',
  'params':{},
  'nonce':secrets.token_hex(16),
}
with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
    s.settimeout(5)
    s.connect('/run/home-center-helper/helper.sock')
    s.sendall(json.dumps(request,separators=(',',':')).encode()+b'\n')
    data=b''
    while b'\n' not in data and len(data) < 65536:
        chunk=s.recv(4096)
        if not chunk: break
        data+=chunk
result=json.loads(data.split(b'\n',1)[0])
if not (
    result.get('schema') == 'home-center.helper.result.v1'
    and result.get('request_id') == request['request_id']
    and result.get('action') == 'helper.probe.v1'
    and result.get('status') == 'succeeded'
    and result.get('exit_code') == 0
):
    raise SystemExit('helper_probe_contract_rejected')
print('HOME_CENTER_HELPER_PROBE=PASS')
PY

systemctl start home-center-backup.service
systemctl is-active --quiet home-center-helper.service
systemctl is-active --quiet home-center.service
systemctl is-active --quiet home-center-backup.timer
systemctl is-active --quiet home-center-tls-maintenance.timer
systemctl is-enabled --quiet home-center-helper.service
systemctl is-enabled --quiet home-center.service
systemctl is-enabled --quiet home-center-backup.timer
systemctl is-enabled --quiet home-center-tls-maintenance.timer
publish_transaction succeeded
trap - ERR INT TERM HUP
echo "HOME_CENTER_NODE_DEPLOY=PASS"
echo "NODE=$NODE"
echo "VERSION=$VERSION"
echo "REVISION=$REVISION"
echo "ARTIFACT_SHA256=$SHA256"
echo "RELEASE=$RELEASE"
echo "ROLLBACK_POINT=$BACKUP"
