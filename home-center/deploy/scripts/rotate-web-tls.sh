#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

[ "$(id -u)" -eq 0 ] || { echo ROOT_REQUIRED >&2; exit 65; }
[ "$(hostname -s)" = dc01 ] || { echo ORCHESTRATOR_MUST_RUN_ON_DC01 >&2; exit 65; }
ip -4 -o addr show | awk '{print $4}' | grep -qx '192.168.10.254/24' || { echo DC01_IP_IDENTITY_MISMATCH >&2; exit 65; }
LOCK_DIR=/run/home-center-locks
LOCK_FILE=$LOCK_DIR/cluster-rollout.lock
if [ ! -e "$LOCK_DIR" ] && [ ! -L "$LOCK_DIR" ]; then
  install -d -m 0700 -o root -g root "$LOCK_DIR"
fi
[ ! -L "$LOCK_DIR" ] && [ "$(stat -c '%F:%u:%g:%a' "$LOCK_DIR" 2>/dev/null)" = directory:0:0:700 ] || { echo HOME_CENTER_LOCK_DIRECTORY_REJECTED >&2; exit 66; }
if [ ! -e "$LOCK_FILE" ] && [ ! -L "$LOCK_FILE" ]; then
  install -m 0600 -o root -g root /dev/null "$LOCK_FILE"
fi
[ ! -L "$LOCK_FILE" ] && [ "$(stat -c '%F:%u:%g:%a' "$LOCK_FILE" 2>/dev/null)" = 'regular file:0:0:600' ] || { echo HOME_CENTER_LOCK_FILE_REJECTED >&2; exit 66; }
exec 9<>"$LOCK_FILE"
flock -n 9 || { echo HOME_CENTER_CLUSTER_ROLLOUT_ALREADY_RUNNING >&2; exit 75; }

PEER_CA_CERT=/etc/home-center/pki/ca.crt
WEB_CA_DIR=/etc/home-center/pki/web-ca
WEB_CA_CERT=$WEB_CA_DIR/ca.crt
WEB_CA_KEY=$WEB_CA_DIR/ca.key
WEB_ROOT=/etc/home-center/pki/web
WEB_CANDIDATE=$WEB_ROOT/candidate
WEB_CANDIDATE_OWNER=$WEB_CANDIDATE/.owner.json
WEB_CA_MIN_VALIDITY_SECONDS=$((427 * 86400))
[ -s "$PEER_CA_CERT" ] || { echo HOME_CENTER_PEER_CA_INCOMPLETE >&2; exit 66; }

TMP=$(mktemp -d /run/home-center-web-tls.XXXXXX)
LOCAL_CANDIDATE_OWNED=0
LOCAL_CANDIDATE_TOKEN=
LOCAL_CANDIDATE_CERT_SHA=
LOCAL_CANDIDATE_KEY_SHA=
REMOTE_CANDIDATE_TOKEN=
REMOTE_CANDIDATE_CERT_SHA=
REMOTE_CANDIDATE_KEY_SHA=
MAINTENANCE_STOPPED=0
DC02_ACTIVATED=0
DC01_ACTIVATED=0
DC02_MAY_HAVE_ACTIVATED=0
DC01_MAY_HAVE_ACTIVATED=0
DC02_PREVIOUS_WEB_CURRENT=
DC01_PREVIOUS_WEB_CURRENT=
REMOTE_WEB_TEMP_DIR=
REMOTE_WEB_TEMP_TOKEN=
ROTATION_ROLLBACK_RUNNING=0
ROTATION_MAIN_BASHPID=$BASHPID
chmod 0700 "$TMP"
cleanup() {
  set +e
  rm -rf -- "$TMP"
  if [ "$LOCAL_CANDIDATE_OWNED" -eq 1 ] && [ "$DC01_MAY_HAVE_ACTIVATED" -eq 0 ]; then
    candidate_cas_local cleanup-owned "$LOCAL_CANDIDATE_TOKEN" \
      "$LOCAL_CANDIDATE_CERT_SHA" "$LOCAL_CANDIDATE_KEY_SHA" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# Web PKI is deliberately independent from the Ed25519 peer/mTLS PKI. Android
# Chrome paths observed in production advertise ECDSA P-256/P-384 and RSA but
# not Ed25519, so both the Web trust anchor and Web leaves are fixed to P-256.
if [ ! -e "$WEB_CA_CERT" ] && [ ! -e "$WEB_CA_KEY" ]; then
  echo HOME_CENTER_WEB_CA_NOT_PROVISIONED >&2
  exit 66
elif [ ! -s "$WEB_CA_CERT" ] || [ ! -s "$WEB_CA_KEY" ]; then
  echo HOME_CENTER_WEB_CA_PARTIAL_STATE_REJECTED >&2
  exit 66
fi

[ ! -L "$WEB_CA_CERT" ] && [ ! -L "$WEB_CA_KEY" ] || { echo HOME_CENTER_WEB_CA_SYMLINK_REJECTED >&2; exit 66; }
[ "$(stat -c '%F:%u:%a' "$WEB_CA_DIR" 2>/dev/null)" = directory:0:750 ] || { echo HOME_CENTER_WEB_CA_DIRECTORY_METADATA_REJECTED >&2; exit 66; }
[ "$(stat -c '%F:%u:%a' "$WEB_CA_CERT" 2>/dev/null)" = 'regular file:0:644' ] || { echo HOME_CENTER_WEB_CA_CERTIFICATE_METADATA_REJECTED >&2; exit 66; }
[ "$(stat -c '%F:%u:%a' "$WEB_CA_KEY" 2>/dev/null)" = 'regular file:0:600' ] || { echo HOME_CENTER_WEB_CA_KEY_METADATA_REJECTED >&2; exit 66; }
openssl verify -x509_strict -CAfile "$WEB_CA_CERT" "$WEB_CA_CERT" >/dev/null
openssl x509 -in "$WEB_CA_CERT" -noout -checkend "$WEB_CA_MIN_VALIDITY_SECONDS" >/dev/null || { echo HOME_CENTER_WEB_CA_RENEWAL_REQUIRED >&2; exit 66; }
WEB_CA_TEXT=$(openssl x509 -in "$WEB_CA_CERT" -noout -text)
grep -q 'Public Key Algorithm: id-ecPublicKey' <<<"$WEB_CA_TEXT"
grep -q 'ASN1 OID: prime256v1' <<<"$WEB_CA_TEXT"
grep -q 'Signature Algorithm: ecdsa-with-SHA256' <<<"$WEB_CA_TEXT"
grep -q 'CA:TRUE' <<<"$WEB_CA_TEXT"
WEB_CA_KEY_PUB=$(openssl pkey -in "$WEB_CA_KEY" -pubout -outform DER | sha256sum | awk '{print $1}')
WEB_CA_CERT_PUB=$(openssl x509 -in "$WEB_CA_CERT" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | awk '{print $1}')
[ "$WEB_CA_KEY_PUB" = "$WEB_CA_CERT_PUB" ] || { echo HOME_CENTER_WEB_CA_KEY_MISMATCH >&2; exit 66; }

SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes dc02)
SCP=(scp -q -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes)
[ "$("${SSH[@]}" hostname -s)" = dc02 ] || { echo DC02_IDENTITY_MISMATCH >&2; exit 65; }
"${SSH[@]}" ip -4 -o addr show | awk '{print $4}' | grep -qx '192.168.10.253/24'
"${SSH[@]}" sudo -n true

REMOTE_WEB_CA_DIR=$("${SSH[@]}" 'umask 077; mktemp -d /tmp/home-center-web-ca.XXXXXX')
case "$REMOTE_WEB_CA_DIR" in /tmp/home-center-web-ca.??????) ;; *) echo DC02_WEB_CA_TEMP_PATH_REJECTED >&2; exit 66 ;; esac
REMOTE_WEB_CA=$REMOTE_WEB_CA_DIR/ca.crt
if ! "${SCP[@]}" "$WEB_CA_CERT" "dc02:$REMOTE_WEB_CA"; then
  "${SSH[@]}" "rm -rf -- '$REMOTE_WEB_CA_DIR'" || true
  exit 69
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

validate_reconcile_result() {
  local payload=$1 expected_node=$2
  /usr/bin/python3 -I - "$payload" "$expected_node" <<'PY'
import json
import sys

payload, expected_node = sys.argv[1:]
try:
    value = json.loads(payload)
except json.JSONDecodeError:
    raise SystemExit("tls_reconcile_result_not_json")
if set(value) != {
    "schema", "checked_at", "node_id", "status", "reason", "action",
    "certificate_sha256", "release", "previous_release",
}:
    raise SystemExit("tls_reconcile_result_shape_rejected")
if not (
    value.get("schema") == "home-center.tls-maintenance.v1"
    and value.get("node_id") == expected_node
    and value.get("status") == "healthy"
    and value.get("reason") is None
    and value.get("action") == "reconcile"
):
    raise SystemExit("tls_reconcile_result_rejected")
PY
}

candidate_cas_local() {
  local mode=$1 token=$2 certificate_sha256=$3 private_key_sha256=$4
  /usr/bin/python3 -I - "$mode" "$token" "$certificate_sha256" "$private_key_sha256" <<'PY'
import re
import sys

sys.path.insert(0, "/opt/home-center/current")
from home_center import tls_activate

mode, operation_id, certificate_sha256, private_key_sha256 = sys.argv[1:]
if mode not in {"assert-absent", "cleanup-complete", "cleanup-owned"}:
    raise SystemExit("candidate_cas_mode_rejected")
if re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
    raise SystemExit("candidate_cas_operation_rejected")
if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in (certificate_sha256, private_key_sha256)):
    raise SystemExit("candidate_cas_digest_rejected")
expected_owner = {
    "schema": tls_activate.CANDIDATE_OWNER_SCHEMA,
    "operation_id": operation_id,
    "certificate_file_sha256": certificate_sha256,
    "private_key_file_sha256": private_key_sha256,
}

with tls_activate._mutation_lock():
    tls_activate._candidate_directory_secure()
    tls_activate._recover_pending_candidate_owner(expected_owner)
    entries = {entry.name for entry in tls_activate.CANDIDATE.iterdir()}
    expected_entries = {
        ".owner.json",
        "tls.crt",
        "tls.key",
        f".tls.crt.{operation_id}.pending",
        f".tls.key.{operation_id}.pending",
    }
    if not entries:
        print("absent")
        raise SystemExit(0)
    if mode == "assert-absent":
        raise SystemExit("candidate_not_absent")
    if not entries <= expected_entries or ".owner.json" not in entries:
        raise SystemExit("candidate_cas_contents_rejected")
    owner = tls_activate._candidate_owner(allow_incomplete=mode == "cleanup-owned")
    if owner != expected_owner:
        raise SystemExit("candidate_cas_owner_rejected")
    if mode == "cleanup-complete":
        tls_activate._candidate_owner()
    if not tls_activate._recover_owned_candidate():
        raise SystemExit("candidate_cas_cleanup_rejected")
    if any(tls_activate.CANDIDATE.iterdir()):
        raise SystemExit("candidate_cas_cleanup_incomplete")
    print("cleared")
PY
}

candidate_cas_remote() {
  local mode=$1 token=$2 certificate_sha256=$3 private_key_sha256=$4
  "${SSH[@]}" "sudo -n /usr/bin/python3 -I - '$mode' '$token' '$certificate_sha256' '$private_key_sha256'" <<'PY'
import re
import sys

sys.path.insert(0, "/opt/home-center/current")
from home_center import tls_activate

mode, operation_id, certificate_sha256, private_key_sha256 = sys.argv[1:]
if mode not in {"assert-absent", "cleanup-complete", "cleanup-owned"}:
    raise SystemExit("candidate_cas_mode_rejected")
if re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
    raise SystemExit("candidate_cas_operation_rejected")
if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in (certificate_sha256, private_key_sha256)):
    raise SystemExit("candidate_cas_digest_rejected")
expected_owner = {
    "schema": tls_activate.CANDIDATE_OWNER_SCHEMA,
    "operation_id": operation_id,
    "certificate_file_sha256": certificate_sha256,
    "private_key_file_sha256": private_key_sha256,
}

with tls_activate._mutation_lock():
    tls_activate._candidate_directory_secure()
    tls_activate._recover_pending_candidate_owner(expected_owner)
    entries = {entry.name for entry in tls_activate.CANDIDATE.iterdir()}
    expected_entries = {
        ".owner.json",
        "tls.crt",
        "tls.key",
        f".tls.crt.{operation_id}.pending",
        f".tls.key.{operation_id}.pending",
    }
    if not entries:
        print("absent")
        raise SystemExit(0)
    if mode == "assert-absent":
        raise SystemExit("candidate_not_absent")
    if not entries <= expected_entries or ".owner.json" not in entries:
        raise SystemExit("candidate_cas_contents_rejected")
    owner = tls_activate._candidate_owner(allow_incomplete=mode == "cleanup-owned")
    if owner != expected_owner:
        raise SystemExit("candidate_cas_owner_rejected")
    if mode == "cleanup-complete":
        tls_activate._candidate_owner()
    if not tls_activate._recover_owned_candidate():
        raise SystemExit("candidate_cas_cleanup_rejected")
    if any(tls_activate.CANDIDATE.iterdir()):
        raise SystemExit("candidate_cas_cleanup_incomplete")
    print("cleared")
PY
}

# A prior power loss may have left a marker-owned candidate or a helper
# recovery latch.  Reconcile both nodes before issuing a new pair, so retries
# are autonomous but never delete an unowned credential.
if ! DC01_RECONCILIATION=$(
  /usr/sbin/runuser -u home-center -- /usr/bin/python3 -I \
    /opt/home-center/current/tls-maintenance-run.py --reconcile
); then
  echo DC01_TLS_RECONCILIATION_FAILED >&2
  exit 70
fi
validate_reconcile_result "$DC01_RECONCILIATION" hm-dm-dc01
if ! DC02_RECONCILIATION=$("${SSH[@]}" \
  "sudo -n /usr/sbin/runuser -u home-center -- /usr/bin/python3 -I /opt/home-center/current/tls-maintenance-run.py --reconcile"
); then
  echo DC02_TLS_RECONCILIATION_FAILED >&2
  exit 70
fi
validate_reconcile_result "$DC02_RECONCILIATION" hm-dm-dc02

issue() {
  local name=$1 ip=$2 fqdn=$3 out=$4
  install -d -m 0700 "$out"
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:prime256v1 -out "$out/tls.key"
  openssl req -new -key "$out/tls.key" -out "$out/tls.csr" -subj "/CN=$fqdn/O=Home Center"
  cat >"$out/extensions.cnf" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth
subjectAltName=DNS:$fqdn,DNS:home-center.hm.dm,IP:$ip
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF
  openssl x509 -req -sha256 -in "$out/tls.csr" -CA "$WEB_CA_CERT" -CAkey "$WEB_CA_KEY" -CAcreateserial \
    -days 397 -extfile "$out/extensions.cnf" -out "$out/tls.crt"
  chmod 0600 "$out/tls.key"
  chmod 0644 "$out/tls.crt"
  openssl verify -x509_strict -CAfile "$WEB_CA_CERT" "$out/tls.crt" >/dev/null
  openssl x509 -in "$out/tls.crt" -noout -checkend 2592000 >/dev/null
  # Exact SAN membership is authoritative; x509 -checkhost/-checkip alone is not a fail-closed mismatch gate.
  local san_text
  san_text=$(openssl x509 -in "$out/tls.crt" -noout -ext subjectAltName)
  grep -Fq "DNS:$fqdn" <<<"$san_text"
  grep -Fq 'DNS:home-center.hm.dm' <<<"$san_text"
  grep -Fq "IP Address:$ip" <<<"$san_text"
  openssl x509 -in "$out/tls.crt" -noout -checkhost "$fqdn" >/dev/null
  openssl x509 -in "$out/tls.crt" -noout -checkhost home-center.hm.dm >/dev/null
  openssl x509 -in "$out/tls.crt" -noout -checkip "$ip" >/dev/null
  openssl x509 -in "$out/tls.crt" -noout -text | grep -q 'TLS Web Server Authentication'
  if openssl x509 -in "$out/tls.crt" -noout -text | grep -q 'TLS Web Client Authentication'; then
    echo "${name^^}_WEB_CERT_CLIENT_AUTH_REJECTED" >&2
    return 1
  fi
  local certificate_text
  certificate_text=$(openssl x509 -in "$out/tls.crt" -noout -text)
  grep -q 'Public Key Algorithm: id-ecPublicKey' <<<"$certificate_text"
  grep -q 'ASN1 OID: prime256v1' <<<"$certificate_text"
  grep -q 'Signature Algorithm: ecdsa-with-SHA256' <<<"$certificate_text"
  local key_pub cert_pub
  key_pub=$(openssl pkey -in "$out/tls.key" -pubout -outform DER | sha256sum | awk '{print $1}')
  cert_pub=$(openssl x509 -in "$out/tls.crt" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | awk '{print $1}')
  [ "$key_pub" = "$cert_pub" ] || { echo "${name^^}_WEB_CERT_KEY_MISMATCH" >&2; return 1; }
  openssl x509 -in "$out/tls.crt" -outform DER | sha256sum | awk '{print $1}' >"$out/fingerprint"
}

presented_fingerprint() {
  local ip=$1 fqdn=$2 ca=${3:-$WEB_CA_CERT}
  /usr/bin/python3 -I - "$ip" "$fqdn" "$ca" <<'PY'
import hashlib, socket, ssl, sys
ip, host, ca = sys.argv[1:]
ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca)
ctx.minimum_version = ssl.TLSVersion.TLSv1_2
with socket.create_connection((ip, 8443), timeout=5) as raw:
    with ctx.wrap_socket(raw, server_hostname=host) as tls:
        print(hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest())
PY
}

current_web_release_local() {
  if [ ! -e /etc/home-center/pki/web/current ] && [ ! -L /etc/home-center/pki/web/current ]; then
    return 0
  fi
  [ -L /etc/home-center/pki/web/current ] || return 1
  local release
  release=$(readlink -f /etc/home-center/pki/web/current)
  [[ "$release" =~ ^/etc/home-center/pki/web/releases/[0-9a-f]{24}$ ]] || return 1
  [ -d "$release" ] && [ ! -L "$release" ] || return 1
  printf '%s\n' "$release"
}

rollback_web_local() {
  local previous=$1 expected_current=$2 certificate anchor expected presented current
  exec 7<>"$LOCK_DIR/node-mutation.lock"
  flock -w 360 7
  current=$(current_web_release_local)
  [ "$current" = "$expected_current" ] || { echo DC01_WEB_ROLLBACK_CAS_REJECTED >&2; return 1; }
  if [ -n "$previous" ]; then
    certificate=$previous/tls.crt
  else
    certificate=/etc/home-center/pki/node.crt
  fi
  if openssl verify -x509_strict -CAfile "$WEB_CA_CERT" "$certificate" >/dev/null 2>&1; then
    anchor=$WEB_CA_CERT
  else
    openssl verify -x509_strict -CAfile "$PEER_CA_CERT" "$certificate" >/dev/null
    anchor=$PEER_CA_CERT
  fi
  expected=$(openssl x509 -in "$certificate" -outform DER | sha256sum | awk '{print $1}')
  if [ -n "$previous" ]; then
    rm -f -- /etc/home-center/pki/web/.current.rotation-rollback
    ln -s "$previous" /etc/home-center/pki/web/.current.rotation-rollback
    mv -Tf /etc/home-center/pki/web/.current.rotation-rollback /etc/home-center/pki/web/current
  else
    [ ! -L /etc/home-center/pki/web/current ] || unlink /etc/home-center/pki/web/current
  fi
  sync -f /etc/home-center/pki/web
  systemctl restart home-center.service
  presented=$(presented_fingerprint 192.168.10.254 dc01.hm.dm "$anchor")
  [ "$presented" = "$expected" ]
  curl --fail --silent --show-error --cacert "$anchor" --max-time 5 https://192.168.10.254:8443/readyz |
    /usr/bin/python3 -I -c "import json,sys; d=json.load(sys.stdin); ok=d.get('schema')=='home-center.readiness.v1' and d.get('status')=='ready' and d.get('node_id')=='hm-dm-dc01'; sys.exit('dc01_web_rollback_readiness_rejected') if not ok else None"
  exec 7>&-
}

rollback_web_remote() {
  local previous=$1 expected_current=$2
  "${SSH[@]}" "sudo -n bash -s -- '$previous' '$expected_current'" <<'REMOTE_ROLLBACK'
set -Eeuo pipefail
export LC_ALL=C
previous=$1
expected_current=$2
lock_dir=/run/home-center-locks
lock_file=$lock_dir/node-mutation.lock
[ ! -L "$lock_dir" ] && [ "$(stat -c '%F:%u:%g:%a' "$lock_dir")" = directory:0:0:700 ] \
  || { echo DC02_WEB_ROLLBACK_LOCK_DIRECTORY_REJECTED >&2; exit 66; }
[ ! -L "$lock_file" ] && [ "$(stat -c '%F:%u:%g:%a' "$lock_file")" = 'regular file:0:0:600' ] \
  || { echo DC02_WEB_ROLLBACK_LOCK_FILE_REJECTED >&2; exit 66; }
exec 7<>"$lock_file"
flock -w 360 7
current=$(readlink -f /etc/home-center/pki/web/current)
[[ "$current" =~ ^/etc/home-center/pki/web/releases/[0-9a-f]{24}$ ]]
[ "$current" = "$expected_current" ] || { echo DC02_WEB_ROLLBACK_CAS_REJECTED >&2; exit 1; }
if [ -n "$previous" ]; then
  [[ "$previous" =~ ^/etc/home-center/pki/web/releases/[0-9a-f]{24}$ ]]
  [ -d "$previous" ] && [ ! -L "$previous" ]
  certificate=$previous/tls.crt
else
  certificate=/etc/home-center/pki/node.crt
fi
if openssl verify -x509_strict -CAfile /etc/home-center/pki/web-ca/ca.crt "$certificate" >/dev/null 2>&1; then
  anchor=/etc/home-center/pki/web-ca/ca.crt
else
  openssl verify -x509_strict -CAfile /etc/home-center/pki/ca.crt "$certificate" >/dev/null
  anchor=/etc/home-center/pki/ca.crt
fi
expected=$(openssl x509 -in "$certificate" -outform DER | sha256sum | awk '{print $1}')
if [ -n "$previous" ]; then
  rm -f -- /etc/home-center/pki/web/.current.rotation-rollback
  ln -s "$previous" /etc/home-center/pki/web/.current.rotation-rollback
  mv -Tf /etc/home-center/pki/web/.current.rotation-rollback /etc/home-center/pki/web/current
else
  [ ! -L /etc/home-center/pki/web/current ] || unlink /etc/home-center/pki/web/current
fi
sync -f /etc/home-center/pki/web
systemctl restart home-center.service
served=$(openssl s_client -connect 192.168.10.253:8443 -servername dc02.hm.dm -CAfile "$anchor" -verify_return_error -verify_hostname dc02.hm.dm </dev/null 2>/dev/null | openssl x509 -outform DER | sha256sum | awk '{print $1}')
[ "$served" = "$expected" ]
curl --fail --silent --show-error --cacert "$anchor" --max-time 5 https://192.168.10.253:8443/readyz |
  /usr/bin/python3 -I -c "import json,sys; d=json.load(sys.stdin); ok=d.get('schema')=='home-center.readiness.v1' and d.get('status')=='ready' and d.get('node_id')=='hm-dm-dc02'; sys.exit('dc02_web_rollback_readiness_rejected') if not ok else None"
REMOTE_ROLLBACK
}

restore_maintenance_timers_checked() {
  systemctl start home-center-tls-maintenance.timer
  "${SSH[@]}" sudo -n systemctl start home-center-tls-maintenance.timer
  systemctl is-active --quiet home-center-tls-maintenance.timer
  systemctl is-enabled --quiet home-center-tls-maintenance.timer
  "${SSH[@]}" 'set -Eeuo pipefail; systemctl is-active --quiet home-center-tls-maintenance.timer; systemctl is-enabled --quiet home-center-tls-maintenance.timer'
}

cleanup_candidates_checked() {
  if [ "$DC01_MAY_HAVE_ACTIVATED" -eq 0 ]; then
    if [ "$LOCAL_CANDIDATE_OWNED" -eq 1 ]; then
      candidate_cas_local cleanup-owned "$LOCAL_CANDIDATE_TOKEN" \
        "$LOCAL_CANDIDATE_CERT_SHA" "$LOCAL_CANDIDATE_KEY_SHA" >/dev/null || return 1
      LOCAL_CANDIDATE_OWNED=0
      LOCAL_CANDIDATE_TOKEN=
      LOCAL_CANDIDATE_CERT_SHA=
      LOCAL_CANDIDATE_KEY_SHA=
    fi
    [ -z "$(find "$WEB_CANDIDATE" -mindepth 1 -maxdepth 1 -print -quit)" ] || return 1
  else
    return 1
  fi
  [ "$DC02_MAY_HAVE_ACTIVATED" -eq 0 ] || return 1
  "${SSH[@]}" "sudo -n test -z \"\$(sudo -n find '$WEB_CANDIDATE' -mindepth 1 -maxdepth 1 -print -quit)\""
}

cleanup_remote_temp_checked() {
  [ -n "$REMOTE_WEB_TEMP_DIR" ] || return 0
  case "$REMOTE_WEB_TEMP_DIR" in /tmp/home-center-web.??????) ;; *) return 1 ;; esac
  if "${SSH[@]}" "test ! -e '$REMOTE_WEB_TEMP_DIR' && test ! -L '$REMOTE_WEB_TEMP_DIR'"; then
    REMOTE_WEB_TEMP_DIR=
    REMOTE_WEB_TEMP_TOKEN=
    return 0
  fi
  [[ "$REMOTE_WEB_TEMP_TOKEN" =~ ^[0-9a-f]{32}$ ]] || return 1
  "${SSH[@]}" "set -Eeuo pipefail; test ! -L '$REMOTE_WEB_TEMP_DIR/.owner'; test \"\$(stat -c '%F:%a' '$REMOTE_WEB_TEMP_DIR/.owner')\" = 'regular file:600'; test \"\$(stat -c %u '$REMOTE_WEB_TEMP_DIR/.owner')\" = \"\$(id -u)\"; test \"\$(cat '$REMOTE_WEB_TEMP_DIR/.owner')\" = '$REMOTE_WEB_TEMP_TOKEN'; rm -rf -- '$REMOTE_WEB_TEMP_DIR'; test ! -e '$REMOTE_WEB_TEMP_DIR' && test ! -L '$REMOTE_WEB_TEMP_DIR'" || return 1
  REMOTE_WEB_TEMP_DIR=
  REMOTE_WEB_TEMP_TOKEN=
}

rotation_failure() {
  local rc=${1:-$?} rollback_ok=1 step_rc
  if [ "$BASHPID" -ne "$ROTATION_MAIN_BASHPID" ]; then
    return "$rc"
  fi
  if [ "$ROTATION_ROLLBACK_RUNNING" -eq 1 ]; then
    echo HOME_CENTER_WEB_TLS_ROTATION=REENTRANT_ROLLBACK_FAILURE >&2
    exit 70
  fi
  ROTATION_ROLLBACK_RUNNING=1
  trap - ERR EXIT INT TERM HUP
  set +e
  if [ "$DC01_MAY_HAVE_ACTIVATED" -eq 1 ] && [ "$DC01_ACTIVATED" -eq 0 ]; then
    echo DC01_WEB_ACTIVATION_OUTCOME_UNKNOWN_RECOVERY_REQUIRED >&2
    rollback_ok=0
  fi
  if [ "$DC02_MAY_HAVE_ACTIVATED" -eq 1 ] && [ "$DC02_ACTIVATED" -eq 0 ]; then
    echo DC02_WEB_ACTIVATION_OUTCOME_UNKNOWN_RECOVERY_REQUIRED >&2
    rollback_ok=0
  fi
  if [ "$DC01_ACTIVATED" -eq 1 ]; then
    (set -Eeuo pipefail; rollback_web_local "$DC01_PREVIOUS_WEB_CURRENT" "/etc/home-center/pki/web/releases/${DC01_EXPECTED:0:24}")
    step_rc=$?
    [ "$step_rc" -eq 0 ] || rollback_ok=0
  fi
  if [ "$DC02_ACTIVATED" -eq 1 ]; then
    (set -Eeuo pipefail; rollback_web_remote "$DC02_PREVIOUS_WEB_CURRENT" "/etc/home-center/pki/web/releases/${DC02_EXPECTED:0:24}")
    step_rc=$?
    [ "$step_rc" -eq 0 ] || rollback_ok=0
  fi
  if [ "$rollback_ok" -eq 1 ]; then
    (set -Eeuo pipefail; verify_peer_invariants_checked)
    step_rc=$?
    [ "$step_rc" -eq 0 ] || rollback_ok=0
  fi
  if [ "$rollback_ok" -eq 1 ]; then
    (set -Eeuo pipefail; cleanup_remote_temp_checked)
    step_rc=$?
    [ "$step_rc" -eq 0 ] || rollback_ok=0
  fi
  if [ "$rollback_ok" -eq 1 ]; then
    (set -Eeuo pipefail; cleanup_candidates_checked)
    step_rc=$?
    [ "$step_rc" -eq 0 ] || rollback_ok=0
  fi
  if [ "$rollback_ok" -eq 1 ] && [ "$MAINTENANCE_STOPPED" -eq 1 ]; then
    (set -Eeuo pipefail; restore_maintenance_timers_checked)
    step_rc=$?
    if [ "$step_rc" -eq 0 ]; then
      MAINTENANCE_STOPPED=0
    else
      rollback_ok=0
    fi
  fi
  cleanup
  if [ "$rollback_ok" -eq 1 ]; then
    echo "HOME_CENTER_WEB_TLS_ROTATION=ROLLED_BACK rc=$rc" >&2
    exit "$rc"
  fi
  echo "HOME_CENTER_WEB_TLS_ROTATION=ROLLBACK_FAILED original_rc=$rc" >&2
  exit 70
}

parse_activation_result() {
  local payload=$1 expected=$2 command_rc=$3 node=$4
  /usr/bin/python3 -I - "$payload" "$expected" "$command_rc" "$node" <<'PY'
import json
import re
import sys

payload, expected, command_rc, node = sys.argv[1:]
try:
    value = json.loads(payload)
except json.JSONDecodeError:
    raise SystemExit(f"{node}_activation_result_not_json")
if value.get("schema") != "home-center.tls-maintenance.v1":
    raise SystemExit(f"{node}_activation_schema_rejected")
if set(value) != {
    "schema", "checked_at", "node_id", "status", "reason", "action",
    "certificate_sha256", "release", "previous_release",
}:
    raise SystemExit(f"{node}_activation_shape_rejected")
if value.get("node_id") != f"hm-dm-{node}" or value.get("action") != "activate":
    raise SystemExit(f"{node}_activation_identity_rejected")
status = value.get("status")
if status == "rolled_back" and value.get("reason") == "activation_rolled_back" and command_rc != "0":
    print("ROLLED_BACK\t-")
    raise SystemExit(0)
if (
    status == "failed"
    and value.get("reason") == "activation_preflight_failed"
    and command_rc != "0"
    and value.get("certificate_sha256") is None
    and value.get("release") is None
    and value.get("previous_release") is None
):
    print("SAFE_NO_MUTATION\t-")
    raise SystemExit(0)
previous = value.get("previous_release")
if not (
    status == "rotated"
    and command_rc == "0"
    and re.fullmatch(r"[0-9a-f]{64}", expected)
    and value.get("certificate_sha256") == expected
    and value.get("release") == expected[:24]
    and (previous is None or isinstance(previous, str) and re.fullmatch(r"[0-9a-f]{24}", previous))
):
    raise SystemExit(f"{node}_activation_contract_rejected")
print("ROTATED\t" + (previous if previous is not None else "-"))
PY
}

stage_local_candidate() {
  local source_dir=$1 token owner_pending certificate_pending key_pending certificate_file_sha256 private_key_file_sha256
  token=$(openssl rand -hex 16)
  certificate_file_sha256=$(sha256sum "$source_dir/tls.crt" | awk '{print $1}')
  private_key_file_sha256=$(sha256sum "$source_dir/tls.key" | awk '{print $1}')
  owner_pending=$WEB_CANDIDATE/.owner.$token.pending
  certificate_pending=$WEB_CANDIDATE/.tls.crt.$token.pending
  key_pending=$WEB_CANDIDATE/.tls.key.$token.pending
  case "$owner_pending" in "$WEB_CANDIDATE"/.owner.????????????????????????????????.pending) ;; *) return 1 ;; esac
  install -d -m 0750 -o root -g home-center "$WEB_CANDIDATE"
  [ "$(stat -c '%F:%u:%a' "$WEB_CANDIDATE")" = directory:0:750 ]
  [ -z "$(find "$WEB_CANDIDATE" -mindepth 1 -maxdepth 1 -print -quit)" ]
  [ ! -e "$owner_pending" ] && [ ! -L "$owner_pending" ]
  LOCAL_CANDIDATE_TOKEN=$token
  LOCAL_CANDIDATE_CERT_SHA=$certificate_file_sha256
  LOCAL_CANDIDATE_KEY_SHA=$private_key_file_sha256
  LOCAL_CANDIDATE_OWNED=1
  (umask 077; printf '%s\n' \
    "{\"certificate_file_sha256\":\"$certificate_file_sha256\",\"operation_id\":\"$token\",\"private_key_file_sha256\":\"$private_key_file_sha256\",\"schema\":\"home-center.web-candidate-owner.v1\"}" \
    >"$owner_pending")
  chown root:root "$owner_pending"
  chmod 0600 "$owner_pending"
  sync -f "$owner_pending"
  mv -T "$owner_pending" "$WEB_CANDIDATE_OWNER"
  sync -f "$WEB_CANDIDATE"
  install -m 0644 -o root -g home-center "$source_dir/tls.crt" "$certificate_pending"
  sync -f "$certificate_pending"
  [ "$(sha256sum "$certificate_pending" | awk '{print $1}')" = "$certificate_file_sha256" ]
  mv -T "$certificate_pending" "$WEB_CANDIDATE/tls.crt"
  sync -f "$WEB_CANDIDATE/tls.crt"
  sync -f "$WEB_CANDIDATE"
  install -m 0600 -o root -g root "$source_dir/tls.key" "$key_pending"
  sync -f "$key_pending"
  [ "$(sha256sum "$key_pending" | awk '{print $1}')" = "$private_key_file_sha256" ]
  mv -T "$key_pending" "$WEB_CANDIDATE/tls.key"
  sync -f "$WEB_CANDIDATE/tls.key"
  sync -f "$WEB_CANDIDATE"
}

activate_remote() {
  local dir=$1 expected=$2
  local remote_dir result action_rc parsed outcome previous_name certificate_file_sha256 private_key_file_sha256
  REMOTE_WEB_TEMP_DIR=/tmp/home-center-web.$(openssl rand -hex 3)
  REMOTE_WEB_TEMP_TOKEN=$(openssl rand -hex 16)
  remote_dir=$REMOTE_WEB_TEMP_DIR
  case "$remote_dir" in /tmp/home-center-web.??????) ;; *) echo DC02_WEB_TEMP_PATH_REJECTED >&2; return 1 ;; esac
  "${SSH[@]}" "set -Eeuo pipefail; umask 077; mkdir '$remote_dir'; chmod 0700 '$remote_dir'; printf '%s\\n' '$REMOTE_WEB_TEMP_TOKEN' >'$remote_dir/.owner'; chmod 0600 '$remote_dir/.owner'"
  if ! "${SCP[@]}" "$dir/tls.crt" "$dir/tls.key" "dc02:$remote_dir/"; then
    cleanup_remote_temp_checked || true
    return 1
  fi
  certificate_file_sha256=$(sha256sum "$dir/tls.crt" | awk '{print $1}')
  private_key_file_sha256=$(sha256sum "$dir/tls.key" | awk '{print $1}')
  REMOTE_CANDIDATE_TOKEN=$REMOTE_WEB_TEMP_TOKEN
  REMOTE_CANDIDATE_CERT_SHA=$certificate_file_sha256
  REMOTE_CANDIDATE_KEY_SHA=$private_key_file_sha256
  DC02_MAY_HAVE_ACTIVATED=1
  if result=$("${SSH[@]}" "sudo -n bash -s -- '$remote_dir' '$WEB_ROOT' '$WEB_CANDIDATE' '$REMOTE_WEB_TEMP_TOKEN' '$certificate_file_sha256' '$private_key_file_sha256'" <<'REMOTE_ACTIVATE'
set -Eeuo pipefail
export LC_ALL=C
remote_dir=$1
web_root=$2
candidate=$3
token=$4
certificate_file_sha256=$5
private_key_file_sha256=$6
owner=$candidate/.owner.json
owner_pending=$candidate/.owner.$token.pending
certificate_pending=$candidate/.tls.crt.$token.pending
key_pending=$candidate/.tls.key.$token.pending
cleanup_remote() {
  rm -rf -- "$remote_dir"
}
trap cleanup_remote EXIT
case "$owner_pending" in "$candidate"/.owner.????????????????????????????????.pending) ;; *) exit 66 ;; esac
chmod 0644 "$remote_dir/tls.crt"
chmod 0600 "$remote_dir/tls.key"
install -d -m 0750 -o root -g home-center "$candidate"
[ "$(stat -c '%F:%U:%G:%a' "$candidate")" = 'directory:root:home-center:750' ]
[ -z "$(find "$candidate" -mindepth 1 -maxdepth 1 -print -quit)" ]
[ ! -e "$owner_pending" ] && [ ! -L "$owner_pending" ]
(umask 077; printf '{"certificate_file_sha256":"%s","operation_id":"%s","private_key_file_sha256":"%s","schema":"home-center.web-candidate-owner.v1"}\n' \
  "$certificate_file_sha256" "$token" "$private_key_file_sha256" >"$owner_pending")
chown root:root "$owner_pending"
chmod 0600 "$owner_pending"
sync -f "$owner_pending"
mv -T "$owner_pending" "$owner"
sync -f "$candidate"
install -m 0644 -o root -g home-center "$remote_dir/tls.crt" "$certificate_pending"
sync -f "$certificate_pending"
[ "$(sha256sum "$certificate_pending" | awk '{print $1}')" = "$certificate_file_sha256" ]
mv -T "$certificate_pending" "$candidate/tls.crt"
sync -f "$candidate/tls.crt"
sync -f "$candidate"
install -m 0600 -o root -g root "$remote_dir/tls.key" "$key_pending"
sync -f "$key_pending"
[ "$(sha256sum "$key_pending" | awk '{print $1}')" = "$private_key_file_sha256" ]
mv -T "$key_pending" "$candidate/tls.key"
sync -f "$candidate/tls.key"
sync -f "$candidate"
set +e
activation=$(/usr/sbin/runuser -u home-center -- /usr/bin/python3 -I /opt/home-center/current/tls-maintenance-run.py --activate-staged)
activation_rc=$?
set -e
printf '%s\n' "$activation"
exit "$activation_rc"
REMOTE_ACTIVATE
  ); then
    action_rc=0
  else
    action_rc=$?
  fi
  if ! cleanup_remote_temp_checked; then
    return 1
  fi
  if ! parsed=$(parse_activation_result "$result" "$expected" "$action_rc" dc02); then
    return 1
  fi
  IFS=$'\t' read -r outcome previous_name <<<"$parsed"
  if [ "$outcome" = ROLLED_BACK ]; then
    candidate_cas_remote assert-absent "$REMOTE_CANDIDATE_TOKEN" \
      "$REMOTE_CANDIDATE_CERT_SHA" "$REMOTE_CANDIDATE_KEY_SHA" >/dev/null || return 1
    DC02_MAY_HAVE_ACTIVATED=0
    REMOTE_CANDIDATE_TOKEN=
    REMOTE_CANDIDATE_CERT_SHA=
    REMOTE_CANDIDATE_KEY_SHA=
    echo DC02_WEB_ACTIVATION=SAFELY_ROLLED_BACK >&2
    return 1
  fi
  if [ "$outcome" = SAFE_NO_MUTATION ]; then
    # A generic preflight result is safe only while the exact marker and both
    # precommitted digests are still present.  This CAS proof rules out a
    # concurrent activation between staging and result parsing.
    candidate_cas_remote cleanup-owned "$REMOTE_CANDIDATE_TOKEN" \
      "$REMOTE_CANDIDATE_CERT_SHA" "$REMOTE_CANDIDATE_KEY_SHA" >/dev/null || return 1
    DC02_MAY_HAVE_ACTIVATED=0
    REMOTE_CANDIDATE_TOKEN=
    REMOTE_CANDIDATE_CERT_SHA=
    REMOTE_CANDIDATE_KEY_SHA=
    echo DC02_WEB_ACTIVATION=SAFE_PREFLIGHT_FAILURE >&2
    return 1
  fi
  [ "$outcome" = ROTATED ] || return 1
  candidate_cas_remote assert-absent "$REMOTE_CANDIDATE_TOKEN" \
    "$REMOTE_CANDIDATE_CERT_SHA" "$REMOTE_CANDIDATE_KEY_SHA" >/dev/null || return 1
  if [ "$previous_name" = - ]; then
    DC02_PREVIOUS_WEB_CURRENT=
  else
    DC02_PREVIOUS_WEB_CURRENT=/etc/home-center/pki/web/releases/$previous_name
  fi
  DC02_ACTIVATED=1
  DC02_MAY_HAVE_ACTIVATED=0
  REMOTE_CANDIDATE_TOKEN=
  REMOTE_CANDIDATE_CERT_SHA=
  REMOTE_CANDIDATE_KEY_SHA=
  local presented
  presented=$(presented_fingerprint 192.168.10.253 dc02.hm.dm)
  [ "$presented" = "$expected" ] || { echo DC02_PRESENTED_CERTIFICATE_MISMATCH >&2; return 1; }
  echo "DC02_WEB_TLS_ROTATION=PASS"
  echo "DC02_WEB_CERT_SHA256=$expected"
}

activate_local() {
  local dir=$1 expected=$2
  stage_local_candidate "$dir"
  local result action_rc parsed outcome previous_name
  DC01_MAY_HAVE_ACTIVATED=1
  if result=$(/usr/sbin/runuser -u home-center -- /usr/bin/python3 -I /opt/home-center/current/tls-maintenance-run.py --activate-staged); then
    action_rc=0
  else
    action_rc=$?
  fi
  if ! parsed=$(parse_activation_result "$result" "$expected" "$action_rc" dc01); then
    return 1
  fi
  IFS=$'\t' read -r outcome previous_name <<<"$parsed"
  if [ "$outcome" = ROLLED_BACK ]; then
    candidate_cas_local assert-absent "$LOCAL_CANDIDATE_TOKEN" \
      "$LOCAL_CANDIDATE_CERT_SHA" "$LOCAL_CANDIDATE_KEY_SHA" >/dev/null || return 1
    LOCAL_CANDIDATE_OWNED=0
    DC01_MAY_HAVE_ACTIVATED=0
    LOCAL_CANDIDATE_TOKEN=
    LOCAL_CANDIDATE_CERT_SHA=
    LOCAL_CANDIDATE_KEY_SHA=
    echo DC01_WEB_ACTIVATION=SAFELY_ROLLED_BACK >&2
    return 1
  fi
  if [ "$outcome" = SAFE_NO_MUTATION ]; then
    candidate_cas_local cleanup-owned "$LOCAL_CANDIDATE_TOKEN" \
      "$LOCAL_CANDIDATE_CERT_SHA" "$LOCAL_CANDIDATE_KEY_SHA" >/dev/null || return 1
    LOCAL_CANDIDATE_OWNED=0
    DC01_MAY_HAVE_ACTIVATED=0
    LOCAL_CANDIDATE_TOKEN=
    LOCAL_CANDIDATE_CERT_SHA=
    LOCAL_CANDIDATE_KEY_SHA=
    echo DC01_WEB_ACTIVATION=SAFE_PREFLIGHT_FAILURE >&2
    return 1
  fi
  [ "$outcome" = ROTATED ] || return 1
  candidate_cas_local assert-absent "$LOCAL_CANDIDATE_TOKEN" \
    "$LOCAL_CANDIDATE_CERT_SHA" "$LOCAL_CANDIDATE_KEY_SHA" >/dev/null || return 1
  if [ "$previous_name" = - ]; then
    DC01_PREVIOUS_WEB_CURRENT=
  else
    DC01_PREVIOUS_WEB_CURRENT=/etc/home-center/pki/web/releases/$previous_name
  fi
  DC01_ACTIVATED=1
  LOCAL_CANDIDATE_OWNED=0
  DC01_MAY_HAVE_ACTIVATED=0
  LOCAL_CANDIDATE_TOKEN=
  LOCAL_CANDIDATE_CERT_SHA=
  LOCAL_CANDIDATE_KEY_SHA=
  local presented
  presented=$(presented_fingerprint 192.168.10.254 dc01.hm.dm)
  [ "$presented" = "$expected" ] || { echo DC01_PRESENTED_CERTIFICATE_MISMATCH >&2; return 1; }
  echo "DC01_WEB_TLS_ROTATION=PASS"
  echo "DC01_WEB_CERT_SHA256=$expected"
}

restricted_browser_handshake() {
  local ip=$1 fqdn=$2 protocol=$3
  local output
  if [ "$protocol" = -tls1_2 ]; then
    output=$(openssl s_client -connect "$ip:8443" -servername "$fqdn" -CAfile "$WEB_CA_CERT" \
      -verify_return_error -verify_hostname "$fqdn" -sigalgs ecdsa_secp256r1_sha256 \
      -cipher ECDHE-ECDSA-AES128-GCM-SHA256 -tls1_2 </dev/null 2>&1)
    grep -q 'Cipher is ECDHE-ECDSA-AES128-GCM-SHA256' <<<"$output"
  else
    output=$(openssl s_client -connect "$ip:8443" -servername "$fqdn" -CAfile "$WEB_CA_CERT" \
      -verify_return_error -verify_hostname "$fqdn" -sigalgs ecdsa_secp256r1_sha256 \
      -ciphersuites TLS_AES_128_GCM_SHA256 -tls1_3 </dev/null 2>&1)
    grep -q 'Cipher is TLS_AES_128_GCM_SHA256' <<<"$output"
  fi
  grep -q 'Verify return code: 0 (ok)' <<<"$output"
}

peer_public_state_local() {
  local ca_sha certificate_sha key_public_sha certificate_public_sha
  ca_sha=$(openssl x509 -in /etc/home-center/pki/ca.crt -outform DER | sha256sum | awk '{print $1}') || return 1
  certificate_sha=$(openssl x509 -in /etc/home-center/pki/node.crt -outform DER | sha256sum | awk '{print $1}') || return 1
  key_public_sha=$(openssl pkey -in /etc/home-center/pki/node.key -pubout -outform DER | sha256sum | awk '{print $1}') || return 1
  certificate_public_sha=$(openssl x509 -in /etc/home-center/pki/node.crt -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | awk '{print $1}') || return 1
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

verify_peer_invariants_checked() {
  local dc02_peer dc01_peer
  [ "$(peer_public_state_local)" = "$DC01_PEER_STATE_BEFORE" ] || return 1
  [ "$(peer_public_state_remote)" = "$DC02_PEER_STATE_BEFORE" ] || return 1
  dc02_peer=$(curl --fail --silent --show-error \
    --cert /etc/home-center/pki/node.crt --key /etc/home-center/pki/node.key \
    --cacert "$PEER_CA_CERT" --max-time 5 \
    https://192.168.10.253:9443/internal/v1/node) || return 1
  dc01_peer=$("${SSH[@]}" 'sudo -n curl --fail --silent --show-error --cert /etc/home-center/pki/node.crt --key /etc/home-center/pki/node.key --cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.254:9443/internal/v1/node') || return 1
  /usr/bin/python3 -I - "$dc02_peer" "$dc01_peer" <<'PY'
import json
import sys

for node, raw in zip(("dc02", "dc01"), sys.argv[1:]):
    value = json.loads(raw)
    if not (
        value.get("cluster_id") == "hm-dm-production"
        and value.get("capability", {}).get("node", {}).get("id") == "hm-dm-" + node
    ):
        raise SystemExit("rotation_peer_identity_rejected")
PY
}

issue dc02 192.168.10.253 dc02.hm.dm "$TMP/dc02"
issue dc01 192.168.10.254 dc01.hm.dm "$TMP/dc01"
DC02_EXPECTED=$(cat "$TMP/dc02/fingerprint")
DC01_EXPECTED=$(cat "$TMP/dc01/fingerprint")
DC01_PEER_STATE_BEFORE=$(peer_public_state_local)
DC02_PEER_STATE_BEFORE=$(peer_public_state_remote)
trap 'rotation_failure $?' ERR
trap 'rotation_failure 130' INT
trap 'rotation_failure 143' TERM
trap 'rotation_failure 129' HUP
MAINTENANCE_STOPPED=1
systemctl stop home-center-tls-maintenance.timer home-center-tls-maintenance.service
"${SSH[@]}" sudo -n systemctl stop home-center-tls-maintenance.timer home-center-tls-maintenance.service
[ ! -L "$LOCK_DIR/node-mutation.lock" ] \
  && [ "$(stat -c '%F:%u:%g:%a' "$LOCK_DIR/node-mutation.lock")" = 'regular file:0:0:600' ] \
  || { echo DC01_NODE_MUTATION_LOCK_REJECTED >&2; false; }
flock -w 360 "$LOCK_DIR/node-mutation.lock" true
"${SSH[@]}" "set -Eeuo pipefail; sudo -n test ! -L '$LOCK_DIR/node-mutation.lock'; test \"\$(sudo -n stat -c '%F:%u:%g:%a' '$LOCK_DIR/node-mutation.lock')\" = 'regular file:0:0:600'; sudo -n flock -w 360 '$LOCK_DIR/node-mutation.lock' true"

# Canary is mandatory. dc01 is never touched unless dc02 activation and exact presented-cert postflight pass.
activate_remote "$TMP/dc02" "$DC02_EXPECTED"
restricted_browser_handshake 192.168.10.253 dc02.hm.dm -tls1_2
restricted_browser_handshake 192.168.10.253 dc02.hm.dm -tls1_3
echo DC02_RESTRICTED_BROWSER_HANDSHAKES=PASS
curl --fail --silent --show-error --cacert "$WEB_CA_CERT" --max-time 5 https://192.168.10.253:8443/readyz |
  /usr/bin/python3 -I -c "import json,sys; d=json.load(sys.stdin); ok=d.get('schema')=='home-center.readiness.v1' and d.get('status')=='ready' and d.get('node_id')=='hm-dm-dc02'; sys.exit('dc02_readiness_rejected') if not ok else print('DC02_WEB_READINESS_CANARY=PASS')"
curl --fail --silent --show-error --cert /etc/home-center/pki/node.crt --key /etc/home-center/pki/node.key \
  --cacert "$PEER_CA_CERT" --max-time 5 https://192.168.10.253:9443/internal/v1/node |
  /usr/bin/python3 -I -c "import json,sys; d=json.load(sys.stdin); ok=d.get('cluster_id')=='hm-dm-production' and d.get('capability',{}).get('node',{}).get('id')=='hm-dm-dc02'; sys.exit('dc02_peer_identity_rejected') if not ok else print('DC02_PEER_MTLS_CANARY=PASS')"
sleep 30
restricted_browser_handshake 192.168.10.253 dc02.hm.dm -tls1_2
restricted_browser_handshake 192.168.10.253 dc02.hm.dm -tls1_3
curl --fail --silent --show-error --cacert "$WEB_CA_CERT" --max-time 5 https://192.168.10.253:8443/readyz |
  /usr/bin/python3 -I -c "import json,sys; d=json.load(sys.stdin); ok=d.get('schema')=='home-center.readiness.v1' and d.get('status')=='ready' and d.get('node_id')=='hm-dm-dc02'; sys.exit('dc02_soak_readiness_rejected') if not ok else None"
curl --fail --silent --show-error --cert /etc/home-center/pki/node.crt --key /etc/home-center/pki/node.key \
  --cacert "$PEER_CA_CERT" --max-time 5 https://192.168.10.253:9443/internal/v1/node |
  /usr/bin/python3 -I -c "import json,sys; d=json.load(sys.stdin); ok=d.get('cluster_id')=='hm-dm-production' and d.get('capability',{}).get('node',{}).get('id')=='hm-dm-dc02'; sys.exit('dc02_soak_peer_identity_rejected') if not ok else None"
echo DC02_WEB_TLS_CANARY_30S=PASS
activate_local "$TMP/dc01" "$DC01_EXPECTED"
restricted_browser_handshake 192.168.10.254 dc01.hm.dm -tls1_2
restricted_browser_handshake 192.168.10.254 dc01.hm.dm -tls1_3
echo DC01_RESTRICTED_BROWSER_HANDSHAKES=PASS
curl --fail --silent --show-error --cacert "$WEB_CA_CERT" --max-time 5 https://192.168.10.254:8443/readyz |
  /usr/bin/python3 -I -c "import json,sys; d=json.load(sys.stdin); ok=d.get('schema')=='home-center.readiness.v1' and d.get('status')=='ready' and d.get('node_id')=='hm-dm-dc01'; sys.exit('dc01_readiness_rejected') if not ok else print('DC01_WEB_READINESS=PASS')"

curl --fail --silent --show-error --cert /etc/home-center/pki/node.crt --key /etc/home-center/pki/node.key \
  --cacert "$PEER_CA_CERT" --max-time 5 https://192.168.10.253:9443/internal/v1/node |
  /usr/bin/python3 -I -c "import json,sys; d=json.load(sys.stdin); ok=d.get('cluster_id')=='hm-dm-production' and d.get('capability',{}).get('node',{}).get('id')=='hm-dm-dc02'; sys.exit('dc02_peer_identity_rejected') if not ok else print('DC02_PEER_MTLS_AFTER_WEB_ROTATION=PASS')"
"${SSH[@]}" "sudo -n curl --fail --silent --show-error --cert /etc/home-center/pki/node.crt --key /etc/home-center/pki/node.key --cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.254:9443/internal/v1/node" |
  /usr/bin/python3 -I -c "import json,sys; d=json.load(sys.stdin); ok=d.get('cluster_id')=='hm-dm-production' and d.get('capability',{}).get('node',{}).get('id')=='hm-dm-dc01'; sys.exit('dc01_peer_identity_rejected') if not ok else print('DC01_PEER_MTLS_AFTER_WEB_ROTATION=PASS')"

openssl verify -x509_strict -CAfile "$WEB_CA_CERT" "$TMP/dc01/tls.crt" "$TMP/dc02/tls.crt" >/dev/null
[ "$(peer_public_state_local)" = "$DC01_PEER_STATE_BEFORE" ] || { echo DC01_PEER_IDENTITY_CHANGED >&2; false; }
[ "$(peer_public_state_remote)" = "$DC02_PEER_STATE_BEFORE" ] || { echo DC02_PEER_IDENTITY_CHANGED >&2; false; }
echo PEER_PUBLIC_IDENTITIES_UNCHANGED=PASS

cleanup_candidates_checked
echo TLS_ROTATION_CANDIDATES_CLEARED=PASS
restore_maintenance_timers_checked
MAINTENANCE_STOPPED=0
echo TLS_MAINTENANCE_TIMERS_RESTORED=PASS

trap - ERR INT TERM HUP
echo HOME_CENTER_WEB_TLS_STAGED_ROTATION=PASS
echo WEB_PKI_ALGORITHM=ECDSA_P256_SHA256
echo PEER_MTLS_PKI=UNCHANGED
echo CANONICAL_WEB_ENDPOINT=https://dc01.hm.dm:8443
echo FUTURE_VIP_IDENTITY=home-center.hm.dm
echo AUTOMATIC_FAILOVER=DISABLED
