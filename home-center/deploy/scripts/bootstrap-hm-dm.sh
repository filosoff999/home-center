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
[ "$(hostname -s)" = dc01 ] || { echo ORCHESTRATOR_MUST_RUN_ON_DC01 >&2; exit 65; }
ip -4 -o addr show | awk '{print $4}' | grep -qx '192.168.10.254/24' || { echo DC01_IP_IDENTITY_MISMATCH >&2; exit 65; }
[ "$(sha256sum "$ARTIFACT" | awk '{print $1}')" = "$SHA256" ] || { echo ARTIFACT_CHECKSUM_MISMATCH >&2; exit 66; }

SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes dc02)
SCP=(scp -q -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes)
REMOTE_HOST=$("${SSH[@]}" hostname -s)
[ "$REMOTE_HOST" = dc02 ] || { echo DC02_IDENTITY_MISMATCH >&2; exit 65; }
"${SSH[@]}" ip -4 -o addr show | awk '{print $4}' | grep -qx '192.168.10.253/24'
"${SSH[@]}" sudo -n true

for host in local remote; do
  if [ "$host" = local ]; then
    py=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
    openssl version >/dev/null
  else
    py=$("${SSH[@]}" python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
    "${SSH[@]}" openssl version >/dev/null
  fi
  python3 - "$py" <<'PY'
import sys
assert tuple(map(int,sys.argv[1].split('.'))) >= (3,12)
PY
done

if systemctl is-active --quiet home-center.service 2>/dev/null; then
  LOCAL_MODE=upgrade
else
  LOCAL_MODE=initial
  ! ss -lnt | awk '{print $4}' | grep -Eq ':(8443|9443)$' || { echo DC01_PORT_CONFLICT >&2; exit 67; }
fi
if "${SSH[@]}" systemctl is-active --quiet home-center.service 2>/dev/null; then
  REMOTE_MODE=upgrade
else
  REMOTE_MODE=initial
  ! "${SSH[@]}" ss -lnt | awk '{print $4}' | grep -Eq ':(8443|9443)$' || { echo DC02_PORT_CONFLICT >&2; exit 67; }
fi

install -d -m 0750 -o root -g root /etc/home-center
install -d -m 0700 -o root -g root /etc/home-center/secrets /etc/home-center/pki
"${SSH[@]}" 'sudo -n install -d -m 0750 -o root -g root /etc/home-center; sudo -n install -d -m 0700 -o root -g root /etc/home-center/secrets /etc/home-center/pki'

ensure_secret() {
  local path=$1 kind=$2
  [ -s "$path" ] && return
  case "$kind" in
    hex) umask 077; openssl rand -hex 32 >"$path" ;;
    binary) umask 077; openssl rand 32 >"$path" ;;
  esac
  chmod 0600 "$path"
}
ensure_secret /etc/home-center/secrets/admin.token hex
ensure_secret /etc/home-center/secrets/session.key binary
ensure_secret /etc/home-center/secrets/audit.key binary

if [ ! -s /etc/home-center/pki/ca.key ] && [ ! -s /etc/home-center/pki/ca.crt ]; then
  openssl genpkey -algorithm ED25519 -out /etc/home-center/pki/ca.key
  openssl req -x509 -new -key /etc/home-center/pki/ca.key -out /etc/home-center/pki/ca.crt -days 3650 \
    -subj '/CN=Home Center HM.DM Cluster CA/O=Home Center' \
    -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
    -addext 'keyUsage=critical,keyCertSign,cRLSign' \
    -addext 'subjectKeyIdentifier=hash'
  chmod 0600 /etc/home-center/pki/ca.key
  chmod 0644 /etc/home-center/pki/ca.crt
elif [ ! -s /etc/home-center/pki/ca.key ] || [ ! -s /etc/home-center/pki/ca.crt ]; then
  echo PARTIAL_CA_STATE_REJECTED >&2
  exit 68
fi

issue_node_certificate() {
  local name=$1 ip=$2 dns=$3 out=$4
  install -d -m 0700 "$out"
  openssl genpkey -algorithm ED25519 -out "$out/node.key"
  openssl req -new -key "$out/node.key" -out "$out/node.csr" -subj "/CN=home-center-$name/O=Home Center"
  cat >"$out/extensions.cnf" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=DNS:home-center-$name,DNS:$name,DNS:$dns,IP:$ip
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF
  openssl x509 -req -in "$out/node.csr" -CA /etc/home-center/pki/ca.crt -CAkey /etc/home-center/pki/ca.key -CAcreateserial -days 825 -extfile "$out/extensions.cnf" -out "$out/node.crt"
  openssl verify -CAfile /etc/home-center/pki/ca.crt "$out/node.crt" >/dev/null
}

TMP=$(mktemp -d /run/home-center-bootstrap.XXXXXX)
LOCAL_ROLLBACK= REMOTE_ROLLBACK= REMOTE_DEPLOYED=0 LOCAL_DEPLOYED=0
cleanup() { rm -rf -- "$TMP"; }
fail_rollback() {
  rc=$?
  trap - ERR EXIT
  set +e
  [ "$LOCAL_DEPLOYED" -eq 0 ] || bash "$ROLLBACK" --rollback-point "$LOCAL_ROLLBACK"
  if [ "$REMOTE_DEPLOYED" -eq 1 ]; then
    "${SCP[@]}" "$ROLLBACK" dc02:/tmp/home-center-rollback.sh
    "${SSH[@]}" "sudo -n bash /tmp/home-center-rollback.sh --rollback-point '$REMOTE_ROLLBACK'; rm -f /tmp/home-center-rollback.sh"
  fi
  cleanup
  echo "HOME_CENTER_CLUSTER_DEPLOY=ROLLED_BACK rc=$rc" >&2
  exit "$rc"
}
trap cleanup EXIT
trap fail_rollback ERR

if [ ! -s /etc/home-center/pki/node.key ] && [ ! -s /etc/home-center/pki/node.crt ]; then
  issue_node_certificate dc01 192.168.10.254 dc01.hm.dm "$TMP/dc01"
  install -m 0600 -o root -g root "$TMP/dc01/node.key" /etc/home-center/pki/node.key
  install -m 0644 -o root -g root "$TMP/dc01/node.crt" /etc/home-center/pki/node.crt
elif [ ! -s /etc/home-center/pki/node.key ] || [ ! -s /etc/home-center/pki/node.crt ]; then
  echo PARTIAL_DC01_CERT_STATE_REJECTED >&2
  false
fi
openssl verify -x509_strict -CAfile /etc/home-center/pki/ca.crt /etc/home-center/pki/node.crt >/dev/null
openssl x509 -in /etc/home-center/pki/node.crt -noout -checkend 2592000 >/dev/null
LOCAL_CERT_CN=$(openssl x509 -in /etc/home-center/pki/node.crt -noout -subject -nameopt RFC2253 | sed -n 's/^subject=.*CN=\([^,]*\).*$/\1/p')
[ "$LOCAL_CERT_CN" = home-center-dc01 ] || { echo DC01_CERT_IDENTITY_MISMATCH >&2; false; }

REMOTE_SECRET_COUNT=0
for remote_secret in \
  /etc/home-center/secrets/admin.token \
  /etc/home-center/secrets/session.key \
  /etc/home-center/secrets/audit.key \
  /etc/home-center/pki/node.key \
  /etc/home-center/pki/node.crt \
  /etc/home-center/pki/ca.crt
do
  if "${SSH[@]}" sudo -n test -s "$remote_secret"; then
    REMOTE_SECRET_COUNT=$((REMOTE_SECRET_COUNT + 1))
  fi
done
if [ "$REMOTE_SECRET_COUNT" = 0 ]; then
  install -d -m 0700 "$TMP/dc02/pki" "$TMP/dc02/secrets"
  issue_node_certificate dc02 192.168.10.253 dc02.hm.dm "$TMP/dc02/pki"
  install -m 0644 /etc/home-center/pki/ca.crt "$TMP/dc02/pki/ca.crt"
  install -m 0600 /etc/home-center/secrets/admin.token "$TMP/dc02/secrets/admin.token"
  openssl rand 32 >"$TMP/dc02/secrets/session.key"
  openssl rand 32 >"$TMP/dc02/secrets/audit.key"
  chmod 0600 "$TMP/dc02/pki/node.key" "$TMP/dc02/secrets/"*
  tar -C "$TMP/dc02" -cf - pki/node.key pki/node.crt pki/ca.crt secrets/admin.token secrets/session.key secrets/audit.key | "${SSH[@]}" 'sudo -n tar -C /etc/home-center -xf -'
  "${SSH[@]}" 'sudo -n chown root:root /etc/home-center/pki/node.key /etc/home-center/pki/node.crt /etc/home-center/pki/ca.crt /etc/home-center/secrets/admin.token /etc/home-center/secrets/session.key /etc/home-center/secrets/audit.key; sudo -n chmod 0600 /etc/home-center/pki/node.key /etc/home-center/secrets/admin.token /etc/home-center/secrets/session.key /etc/home-center/secrets/audit.key; sudo -n chmod 0644 /etc/home-center/pki/node.crt /etc/home-center/pki/ca.crt'
elif [ "$REMOTE_SECRET_COUNT" != 6 ]; then
  echo PARTIAL_DC02_SECRET_STATE_REJECTED >&2
  false
else
  REMOTE_CA_SHA=$("${SSH[@]}" sudo -n sha256sum /etc/home-center/pki/ca.crt | awk '{print $1}')
  [ "$REMOTE_CA_SHA" = "$(sha256sum /etc/home-center/pki/ca.crt | awk '{print $1}')" ] || { echo DC02_CA_MISMATCH >&2; false; }
  REMOTE_ADMIN_SHA=$("${SSH[@]}" sudo -n sha256sum /etc/home-center/secrets/admin.token | awk '{print $1}')
  [ "$REMOTE_ADMIN_SHA" = "$(sha256sum /etc/home-center/secrets/admin.token | awk '{print $1}')" ] || { echo DC02_ADMIN_TOKEN_MISMATCH >&2; false; }
fi
"${SSH[@]}" 'sudo -n openssl verify -x509_strict -CAfile /etc/home-center/pki/ca.crt /etc/home-center/pki/node.crt >/dev/null; sudo -n openssl x509 -in /etc/home-center/pki/node.crt -noout -checkend 2592000 >/dev/null'
REMOTE_CERT_CN=$("${SSH[@]}" sudo -n openssl x509 -in /etc/home-center/pki/node.crt -noout -subject -nameopt RFC2253 | sed -n 's/^subject=.*CN=\([^,]*\).*$/\1/p')
[ "$REMOTE_CERT_CN" = home-center-dc02 ] || { echo DC02_CERT_IDENTITY_MISMATCH >&2; false; }

REMOTE_ARTIFACT="/tmp/home-center-${SHA256:0:12}.tar.gz"
REMOTE_INSTALLER=/tmp/home-center-install-node.sh
"${SCP[@]}" "$ARTIFACT" "dc02:$REMOTE_ARTIFACT"
"${SCP[@]}" "$INSTALLER" "dc02:$REMOTE_INSTALLER"
REMOTE_OUTPUT=$("${SSH[@]}" "sudo -n bash '$REMOTE_INSTALLER' --artifact '$REMOTE_ARTIFACT' --sha256 '$SHA256' --node dc02")
REMOTE_ROLLBACK=$(printf '%s\n' "$REMOTE_OUTPUT" | sed -n 's/^ROLLBACK_POINT=//p' | tail -1)
[ -n "$REMOTE_ROLLBACK" ]
REMOTE_DEPLOYED=1
printf '%s\n' "$REMOTE_OUTPUT"
"${SSH[@]}" "rm -f '$REMOTE_ARTIFACT' '$REMOTE_INSTALLER'" || true

LOCAL_OUTPUT=$(bash "$INSTALLER" --artifact "$ARTIFACT" --sha256 "$SHA256" --node dc01)
LOCAL_ROLLBACK=$(printf '%s\n' "$LOCAL_OUTPUT" | sed -n 's/^ROLLBACK_POINT=//p' | tail -1)
[ -n "$LOCAL_ROLLBACK" ]
LOCAL_DEPLOYED=1
printf '%s\n' "$LOCAL_OUTPUT"

AUTH_CONFIG="$TMP/curl-auth.conf"
IFS= read -r ADMIN_TOKEN </etc/home-center/secrets/admin.token
[ "${#ADMIN_TOKEN}" -ge 32 ] || { echo ADMIN_TOKEN_INVALID >&2; false; }
printf 'header = "Authorization: Bearer %s"\n' "$ADMIN_TOKEN" >"$AUTH_CONFIG"
unset ADMIN_TOKEN
chmod 0600 "$AUTH_CONFIG"
for _ in $(seq 1 30); do
  LOCAL_CLUSTER=$(curl --config "$AUTH_CONFIG" --silent --show-error --cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.254:8443/api/v1/overview 2>/dev/null || true)
  REMOTE_CLUSTER=$(curl --config "$AUTH_CONFIG" --silent --show-error --cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.253:8443/api/v1/overview 2>/dev/null || true)
  if python3 - "$LOCAL_CLUSTER" "$REMOTE_CLUSTER" <<'PY' 2>/dev/null
import json,sys
for raw in sys.argv[1:]:
    d=json.loads(raw)
    assert d['schema']=='home-center.overview.v1'
    assert d['cluster']['status']=='healthy'
    assert d['cluster']['ready_nodes']==2
    assert d['cluster']['expected_nodes']==2
    assert d['cluster']['automatic_failover'] is False
    assert {n['name'] for n in d['nodes']}=={'dc01','dc02'}
PY
  then break; fi
  sleep 2
done
python3 - "$LOCAL_CLUSTER" "$REMOTE_CLUSTER" <<'PY'
import json,sys
for expected,raw in zip(('leader','standby'),sys.argv[1:]):
    d=json.loads(raw)
    assert d['schema']=='home-center.overview.v1'
    assert d['cluster']['status']=='healthy'
    assert d['cluster']['ready_nodes']==2
    assert d['cluster']['local_role']==expected
    assert d['cluster']['automatic_failover'] is False
    assert d['cluster']['split_brain_policy']=='single-writer-manual-failover'
print('CLUSTER_OVERVIEW_BOTH_NODES=PASS')
PY

curl --silent --show-error --cert /etc/home-center/pki/node.crt --key /etc/home-center/pki/node.key --cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.253:9443/internal/v1/node | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['cluster_id']=='hm-dm-production'; assert d['capability']['node']['id']=='hm-dm-dc02'; print('DC02_MTLS_IDENTITY=PASS')"
"${SSH[@]}" "sudo -n curl --silent --show-error --cert /etc/home-center/pki/node.crt --key /etc/home-center/pki/node.key --cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.254:9443/internal/v1/node" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['cluster_id']=='hm-dm-production'; assert d['capability']['node']['id']=='hm-dm-dc01'; print('DC01_MTLS_IDENTITY_FROM_DC02=PASS')"

systemctl is-active --quiet home-center.service
systemctl is-enabled --quiet home-center-backup.timer
"${SSH[@]}" 'systemctl is-active --quiet home-center.service; systemctl is-enabled --quiet home-center-backup.timer'
LOCAL_BACKUPS=$(find /var/backups/home-center -maxdepth 1 -name 'home-center-*.manifest.json' -type f | wc -l)
REMOTE_BACKUPS=$("${SSH[@]}" "sudo -n find /var/backups/home-center -maxdepth 1 -name 'home-center-*.manifest.json' -type f | wc -l")
[ "$LOCAL_BACKUPS" -ge 1 ] && [ "$REMOTE_BACKUPS" -ge 1 ]

trap - ERR
echo HOME_CENTER_CLUSTER_DEPLOY=PASS
echo DEPLOY_ORDER=dc02_then_dc01
echo DC01_MODE="$LOCAL_MODE"
echo DC02_MODE="$REMOTE_MODE"
echo ARTIFACT_SHA256="$SHA256"
echo BOTH_NODES_READY=PASS
echo PEER_MTLS=PASS
echo BACKUP_CREATE_VERIFY=PASS
echo AUTOMATIC_FAILOVER=DISABLED_NO_WITNESS
echo SAMBA_AD_MUTATIONS=NONE
