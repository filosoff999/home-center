#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

[ "$(id -u)" -eq 0 ] || { echo ROOT_REQUIRED >&2; exit 65; }
[ "$(hostname -s)" = dc01 ] || { echo ORCHESTRATOR_MUST_RUN_ON_DC01 >&2; exit 65; }
ip -4 -o addr show | awk '{print $4}' | grep -qx '192.168.10.254/24' || { echo DC01_IP_IDENTITY_MISMATCH >&2; exit 65; }

CA_CERT=/etc/home-center/pki/ca.crt
CA_KEY=/etc/home-center/pki/ca.key
WEB_CANDIDATE=/etc/home-center/pki/web/candidate
[ -s "$CA_CERT" ] && [ -s "$CA_KEY" ] || { echo HOME_CENTER_CA_INCOMPLETE >&2; exit 66; }
[ ! -L "$CA_CERT" ] && [ ! -L "$CA_KEY" ] || { echo HOME_CENTER_CA_SYMLINK_REJECTED >&2; exit 66; }
[ "$(stat -c %u "$CA_CERT")" -eq 0 ] && [ "$(stat -c %u "$CA_KEY")" -eq 0 ] || { echo HOME_CENTER_CA_OWNER_REJECTED >&2; exit 66; }
[ $(( 8#$(stat -c %a "$CA_KEY") & 077 )) -eq 0 ] || { echo HOME_CENTER_CA_KEY_PERMISSIONS_REJECTED >&2; exit 66; }
openssl verify -x509_strict -CAfile "$CA_CERT" "$CA_CERT" >/dev/null

SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes dc02)
SCP=(scp -q -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes)
[ "$("${SSH[@]}" hostname -s)" = dc02 ] || { echo DC02_IDENTITY_MISMATCH >&2; exit 65; }
"${SSH[@]}" ip -4 -o addr show | awk '{print $4}' | grep -qx '192.168.10.253/24'
"${SSH[@]}" sudo -n true

TMP=$(mktemp -d /run/home-center-web-tls.XXXXXX)
chmod 0700 "$TMP"
cleanup() {
  rm -rf -- "$TMP"
}
trap cleanup EXIT

issue() {
  local name=$1 ip=$2 fqdn=$3 out=$4
  install -d -m 0700 "$out"
  openssl genpkey -algorithm ED25519 -out "$out/tls.key"
  openssl req -new -key "$out/tls.key" -out "$out/tls.csr" -subj "/CN=$fqdn/O=Home Center"
  cat >"$out/extensions.cnf" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth
subjectAltName=DNS:$fqdn,DNS:home-center.hm.dm,IP:$ip
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF
  openssl x509 -req -in "$out/tls.csr" -CA "$CA_CERT" -CAkey "$CA_KEY" -CAcreateserial \
    -days 397 -extfile "$out/extensions.cnf" -out "$out/tls.crt"
  chmod 0600 "$out/tls.key"
  chmod 0644 "$out/tls.crt"
  openssl verify -x509_strict -CAfile "$CA_CERT" "$out/tls.crt" >/dev/null
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
  local key_pub cert_pub
  key_pub=$(openssl pkey -in "$out/tls.key" -pubout -outform DER | sha256sum | awk '{print $1}')
  cert_pub=$(openssl x509 -in "$out/tls.crt" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | awk '{print $1}')
  [ "$key_pub" = "$cert_pub" ] || { echo "${name^^}_WEB_CERT_KEY_MISMATCH" >&2; return 1; }
  openssl x509 -in "$out/tls.crt" -outform DER | sha256sum | awk '{print $1}' >"$out/fingerprint"
}

presented_fingerprint() {
  local ip=$1 fqdn=$2
  python3 - "$ip" "$fqdn" "$CA_CERT" <<'PY'
import hashlib, socket, ssl, sys
ip, host, ca = sys.argv[1:]
ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca)
ctx.minimum_version = ssl.TLSVersion.TLSv1_2
with socket.create_connection((ip, 8443), timeout=5) as raw:
    with ctx.wrap_socket(raw, server_hostname=host) as tls:
        print(hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest())
PY
}

activate_remote() {
  local dir=$1 expected=$2
  local suffix=${expected:0:16}
  local remote_cert="/tmp/home-center-web-${suffix}.crt"
  local remote_key="/tmp/home-center-web-${suffix}.key"
  "${SCP[@]}" "$dir/tls.crt" "dc02:$remote_cert"
  "${SCP[@]}" "$dir/tls.key" "dc02:$remote_key"
  "${SSH[@]}" "set -Eeuo pipefail; \
    sudo -n install -d -m 0750 -o root -g home-center '$WEB_CANDIDATE'; \
    sudo -n install -m 0644 -o root -g home-center '$remote_cert' '$WEB_CANDIDATE/tls.crt'; \
    sudo -n install -m 0600 -o root -g root '$remote_key' '$WEB_CANDIDATE/tls.key'; \
    rm -f '$remote_cert' '$remote_key'"
  local result
  result=$("${SSH[@]}" "sudo -n /usr/sbin/runuser -u home-center -- /usr/bin/python3 /opt/home-center/current/home_center/tls_maintenance.py --activate-staged")
  python3 - "$result" "$expected" <<'PY'
import json, sys
value=json.loads(sys.argv[1])
assert value['schema']=='home-center.tls-maintenance.v1'
assert value['status']=='rotated'
assert value['certificate_sha256']==sys.argv[2]
PY
  local presented
  presented=$(presented_fingerprint 192.168.10.253 dc02.hm.dm)
  [ "$presented" = "$expected" ] || { echo DC02_PRESENTED_CERTIFICATE_MISMATCH >&2; return 1; }
  echo "DC02_WEB_TLS_ROTATION=PASS"
  echo "DC02_WEB_CERT_SHA256=$expected"
}

activate_local() {
  local dir=$1 expected=$2
  install -d -m 0750 -o root -g home-center "$WEB_CANDIDATE"
  install -m 0644 -o root -g home-center "$dir/tls.crt" "$WEB_CANDIDATE/tls.crt"
  install -m 0600 -o root -g root "$dir/tls.key" "$WEB_CANDIDATE/tls.key"
  local result
  result=$(/usr/sbin/runuser -u home-center -- /usr/bin/python3 /opt/home-center/current/home_center/tls_maintenance.py --activate-staged)
  python3 - "$result" "$expected" <<'PY'
import json, sys
value=json.loads(sys.argv[1])
assert value['schema']=='home-center.tls-maintenance.v1'
assert value['status']=='rotated'
assert value['certificate_sha256']==sys.argv[2]
PY
  local presented
  presented=$(presented_fingerprint 192.168.10.254 dc01.hm.dm)
  [ "$presented" = "$expected" ] || { echo DC01_PRESENTED_CERTIFICATE_MISMATCH >&2; return 1; }
  echo "DC01_WEB_TLS_ROTATION=PASS"
  echo "DC01_WEB_CERT_SHA256=$expected"
}

issue dc02 192.168.10.253 dc02.hm.dm "$TMP/dc02"
issue dc01 192.168.10.254 dc01.hm.dm "$TMP/dc01"
DC02_EXPECTED=$(cat "$TMP/dc02/fingerprint")
DC01_EXPECTED=$(cat "$TMP/dc01/fingerprint")

# Canary is mandatory. dc01 is never touched unless dc02 activation and exact presented-cert postflight pass.
activate_remote "$TMP/dc02" "$DC02_EXPECTED"
activate_local "$TMP/dc01" "$DC01_EXPECTED"

curl --silent --show-error --cert /etc/home-center/pki/node.crt --key /etc/home-center/pki/node.key \
  --cacert "$CA_CERT" --max-time 5 https://192.168.10.253:9443/internal/v1/node |
  python3 -c "import json,sys; d=json.load(sys.stdin); assert d['cluster_id']=='hm-dm-production'; assert d['capability']['node']['id']=='hm-dm-dc02'; print('DC02_PEER_MTLS_AFTER_WEB_ROTATION=PASS')"
"${SSH[@]}" "sudo -n curl --silent --show-error --cert /etc/home-center/pki/node.crt --key /etc/home-center/pki/node.key --cacert /etc/home-center/pki/ca.crt --max-time 5 https://192.168.10.254:9443/internal/v1/node" |
  python3 -c "import json,sys; d=json.load(sys.stdin); assert d['cluster_id']=='hm-dm-production'; assert d['capability']['node']['id']=='hm-dm-dc01'; print('DC01_PEER_MTLS_AFTER_WEB_ROTATION=PASS')"

openssl verify -x509_strict -CAfile "$CA_CERT" "$TMP/dc01/tls.crt" "$TMP/dc02/tls.crt" >/dev/null

echo HOME_CENTER_WEB_TLS_STAGED_ROTATION=PASS
echo CANONICAL_WEB_ENDPOINT=https://dc01.hm.dm:8443
echo FUTURE_VIP_IDENTITY=home-center.hm.dm
echo AUTOMATIC_FAILOVER=DISABLED
