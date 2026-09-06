#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

usage() { echo "usage: $0 --artifact PATH --sha256 HEX --node dc01|dc02" >&2; exit 64; }
ARTIFACT= SHA256= NODE=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --artifact) ARTIFACT=${2:-}; shift 2 ;;
    --sha256) SHA256=${2:-}; shift 2 ;;
    --node) NODE=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done
[ -f "$ARTIFACT" ] && [[ "$SHA256" =~ ^[0-9a-f]{64}$ ]] && [[ "$NODE" =~ ^dc0[12]$ ]] || usage
[ "$(hostname -s)" = "$NODE" ] || { echo HOST_IDENTITY_MISMATCH >&2; exit 65; }
EXPECTED_IP=192.168.10.254; [ "$NODE" = dc02 ] && EXPECTED_IP=192.168.10.253
ip -4 -o addr show | awk '{print $4}' | grep -qx "$EXPECTED_IP/24" || { echo IP_IDENTITY_MISMATCH >&2; exit 65; }
[ "$(sha256sum "$ARTIFACT" | awk '{print $1}')" = "$SHA256" ] || { echo ARTIFACT_CHECKSUM_MISMATCH >&2; exit 66; }
tar -tzf "$ARTIFACT" | grep -Eq '(^/|(^|/)\.\.(/|$))' && { echo UNSAFE_ARCHIVE_PATH >&2; exit 66; } || true

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
VERSION=$(tar -xOf "$ARTIFACT" ./VERSION | tr -d '\r\n')
REVISION=$(tar -xOf "$ARTIFACT" ./REVISION | tr -d '\r\n')
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo INVALID_VERSION >&2; exit 66; }
[[ "$REVISION" =~ ^[0-9a-f]{40}$|^working-tree$ ]] || { echo INVALID_REVISION >&2; exit 66; }
RELEASE="/opt/home-center/releases/${VERSION}-${REVISION:0:12}-${SHA256:0:12}"
BACKUP="/var/backups/home-center-deploy/$STAMP-$NODE"
STAGE=
PREVIOUS=
if [ -L /opt/home-center/current ]; then
  PREVIOUS=$(readlink -f /opt/home-center/current)
  case "$PREVIOUS" in /opt/home-center/releases/*) ;; *) echo INVALID_CURRENT_RELEASE >&2; exit 66 ;; esac
  [ -d "$PREVIOUS" ] || { echo CURRENT_RELEASE_MISSING >&2; exit 66; }
fi
rollback() {
  rc=$?
  set +e
  systemctl stop home-center.service
  if [ -n "$PREVIOUS" ] && [ -d "$PREVIOUS" ]; then
    ln -sfn "$PREVIOUS" /opt/home-center/.current.rollback
    mv -Tf /opt/home-center/.current.rollback /opt/home-center/current
    systemctl restart home-center.service
  else
    systemctl disable --now home-center.service home-center-backup.timer >/dev/null 2>&1 || true
    [ ! -L /opt/home-center/current ] || unlink /opt/home-center/current
  fi
  [ -z "$STAGE" ] || rm -rf -- "$STAGE"
  echo "DEPLOY_ROLLBACK=COMPLETE rc=$rc" >&2
  exit "$rc"
}
trap rollback ERR

getent group home-center >/dev/null || groupadd --system home-center
id home-center >/dev/null 2>&1 || useradd --system --gid home-center --home-dir /var/lib/home-center --shell /usr/sbin/nologin home-center
install -d -m 0755 -o root -g root /opt/home-center /opt/home-center/releases
STAGE=$(mktemp -d /opt/home-center/releases/.stage.XXXXXX)
install -d -m 0750 -o home-center -g home-center /var/lib/home-center /var/backups/home-center
install -d -m 0700 -o root -g root "$BACKUP"
for path in /etc/home-center/config.json /etc/systemd/system/home-center.service /etc/systemd/system/home-center-backup.service /etc/systemd/system/home-center-backup.timer; do
  [ ! -e "$path" ] || cp -a "$path" "$BACKUP/$(basename "$path")"
done
printf '%s\n' "$PREVIOUS" >"$BACKUP/previous-release"

tar -xzf "$ARTIFACT" -C "$STAGE" --no-same-owner --no-same-permissions
(cd "$STAGE" && sha256sum -c MANIFEST.sha256)
if find "$STAGE" \( -type l -o -type b -o -type c -o -type p -o -type s \) -print -quit | grep -q .; then
  echo UNSAFE_ARTIFACT_TYPE >&2
  false
fi
find "$STAGE" -type d -exec chmod 0755 {} +
find "$STAGE" -type f -exec chmod 0644 {} +
chown -R root:root "$STAGE"
if [ -d "$RELEASE" ]; then
  diff -qr "$STAGE" "$RELEASE" >/dev/null || { echo EXISTING_RELEASE_CONTENT_MISMATCH >&2; false; }
  rm -rf -- "$STAGE"
else
  mv "$STAGE" "$RELEASE"
fi

install -d -m 0750 -o root -g home-center /etc/home-center
install -m 0640 -o root -g home-center "$RELEASE/deploy/config.$NODE.json" /etc/home-center/config.json
for secret in /etc/home-center/secrets/admin.token /etc/home-center/secrets/session.key /etc/home-center/secrets/audit.key /etc/home-center/pki/node.key /etc/home-center/pki/node.crt /etc/home-center/pki/ca.crt; do
  [ -s "$secret" ] || { echo "MISSING_REQUIRED_FILE=$secret" >&2; false; }
done
chmod 0750 /etc/home-center /etc/home-center/secrets /etc/home-center/pki
chown root:home-center /etc/home-center /etc/home-center/secrets /etc/home-center/pki
chown root:home-center /etc/home-center/config.json /etc/home-center/secrets/* /etc/home-center/pki/*
chmod 0640 /etc/home-center/config.json /etc/home-center/secrets/* /etc/home-center/pki/node.key
chmod 0644 /etc/home-center/pki/node.crt /etc/home-center/pki/ca.crt

install -m 0644 -o root -g root "$RELEASE/deploy/home-center.service" /etc/systemd/system/home-center.service
install -m 0644 -o root -g root "$RELEASE/deploy/home-center-backup.service" /etc/systemd/system/home-center-backup.service
install -m 0644 -o root -g root "$RELEASE/deploy/home-center-backup.timer" /etc/systemd/system/home-center-backup.timer
ln -sfn "$RELEASE" /opt/home-center/.current.new
mv -Tf /opt/home-center/.current.new /opt/home-center/current
systemctl daemon-reload
systemctl enable home-center.service home-center-backup.timer >/dev/null
systemctl restart home-center.service

for _ in $(seq 1 20); do
  code=$(curl --silent --show-error --cacert /etc/home-center/pki/ca.crt --output /run/home-center-health.json --write-out '%{http_code}' --max-time 3 "https://$EXPECTED_IP:8443/readyz" 2>/dev/null || true)
  [ "$code" = 200 ] && break
  sleep 1
done
[ "${code:-}" = 200 ]
python3 - /run/home-center-health.json "$NODE" "$VERSION" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
assert d['schema']=='home-center.readiness.v1'
assert d['status']=='ready'
assert d['version']==sys.argv[3]
assert d['node_id']=='hm-dm-'+sys.argv[2]
PY
rm -f /run/home-center-health.json
systemctl start home-center-backup.service
systemctl is-active --quiet home-center.service
systemctl is-enabled --quiet home-center.service
systemctl is-enabled --quiet home-center-backup.timer
trap - ERR
echo "HOME_CENTER_NODE_DEPLOY=PASS"
echo "NODE=$NODE"
echo "VERSION=$VERSION"
echo "REVISION=$REVISION"
echo "ARTIFACT_SHA256=$SHA256"
echo "RELEASE=$RELEASE"
echo "ROLLBACK_POINT=$BACKUP"
