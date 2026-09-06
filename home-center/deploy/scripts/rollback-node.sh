#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

[ "${1:-}" = "--rollback-point" ] && [ -n "${2:-}" ] || { echo "usage: $0 --rollback-point PATH" >&2; exit 64; }
POINT=$(readlink -f "$2")
case "$POINT" in /var/backups/home-center-deploy/*) ;; *) echo INVALID_ROLLBACK_POINT >&2; exit 65 ;; esac
[ -d "$POINT" ] && [ -f "$POINT/previous-release" ] || { echo ROLLBACK_POINT_INCOMPLETE >&2; exit 66; }

PREVIOUS=$(cat "$POINT/previous-release")
systemctl stop home-center.service 2>/dev/null || true
for unit in home-center.service home-center-backup.service home-center-backup.timer; do
  [ ! -f "$POINT/$unit" ] || install -m 0644 -o root -g root "$POINT/$unit" "/etc/systemd/system/$unit"
done
[ ! -f "$POINT/config.json" ] || install -m 0640 -o root -g home-center "$POINT/config.json" /etc/home-center/config.json

if [ -n "$PREVIOUS" ]; then
  case "$PREVIOUS" in /opt/home-center/releases/*) ;; *) echo INVALID_PREVIOUS_RELEASE >&2; exit 66 ;; esac
  [ -d "$PREVIOUS" ] || { echo PREVIOUS_RELEASE_MISSING >&2; exit 66; }
  ln -sfn "$PREVIOUS" /opt/home-center/.current.rollback
  mv -Tf /opt/home-center/.current.rollback /opt/home-center/current
  systemctl daemon-reload
  systemctl enable --now home-center.service home-center-backup.timer >/dev/null
  systemctl is-active --quiet home-center.service
  echo ROLLBACK_MODE=PREVIOUS_RELEASE
  echo PREVIOUS_RELEASE="$PREVIOUS"
else
  systemctl disable --now home-center.service home-center-backup.timer >/dev/null 2>&1 || true
  [ ! -L /opt/home-center/current ] || unlink /opt/home-center/current
  systemctl daemon-reload
  echo ROLLBACK_MODE=INITIAL_DEPLOY_DISABLED
fi
echo HOME_CENTER_NODE_ROLLBACK=PASS
