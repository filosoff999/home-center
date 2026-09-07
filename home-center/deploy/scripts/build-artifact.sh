#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
OUT=${1:-"$ROOT/dist"}
SOURCE_VERSION=$(awk -F'"' '/^__version__ = / {print $2}' "$ROOT/product/control-plane/src/home_center/__init__.py")
VERSION=${HOME_CENTER_VERSION:-$SOURCE_VERSION}
[ "$VERSION" = "$SOURCE_VERSION" ] || { echo VERSION_OVERRIDE_MISMATCH >&2; exit 66; }
# Historical accepted production artifact gate: [ "$VERSION" = 0.5.0 ]
# Accepted predecessor artifact gate: [ "$VERSION" = 0.6.0 ]
# Immediate predecessor artifact gate: [ "$VERSION" = 0.7.0 ]
# Published predecessor artifact gate: [ "$VERSION" = 0.8.0 ]
# Immediate predecessor artifact gate: [ "$VERSION" = 0.9.0 ]
# Published predecessor artifact gate: [ "$VERSION" = 0.9.2 ]
# Published 0.10 source artifact gate: [ "$VERSION" = 0.10.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED
# Immediate development predecessor artifact gate: [ "$VERSION" = 0.11.0 ]
[ "$VERSION" = 0.12.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED >&2; exit 66; }
REVISION=${HOME_CENTER_REVISION:-$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || printf 'working-tree')}
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo REVISION_NOT_IMMUTABLE >&2; exit 66; }
SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-1767225600}
[[ "$SOURCE_DATE_EPOCH" =~ ^[1-9][0-9]{8,11}$ ]] || { echo SOURCE_DATE_EPOCH_REJECTED >&2; exit 66; }
if [ "${HOME_CENTER_RELEASE_BUILD:-0}" = 1 ]; then
  grep -qx 'CONFIG_SCHEMA = "home-center.config.v4"' "$ROOT/product/control-plane/src/home_center/config.py" \
    || { echo HOME_CENTER_0100_CONFIG_SCHEMA_NOT_ADMITTED >&2; exit 66; }
  [ "$(git -C "$ROOT" rev-parse HEAD)" = "$REVISION" ] || { echo RELEASE_REVISION_NOT_HEAD >&2; exit 66; }
  [ -z "$(git -C "$ROOT" status --porcelain --untracked-files=all)" ] || { echo RELEASE_WORKTREE_NOT_CLEAN >&2; exit 66; }
elif [ "${HOME_CENTER_RELEASE_BUILD:-0}" != 0 ]; then
  echo RELEASE_BUILD_MODE_REJECTED >&2
  exit 66
fi
STAGE=$(mktemp -d)
trap 'rm -rf -- "$STAGE"' EXIT

mkdir -p "$OUT" "$STAGE/home_center" "$STAGE/web" "$STAGE/contracts" "$STAGE/deploy"
cp -a "$ROOT/product/control-plane/src/home_center/." "$STAGE/home_center/"
find "$STAGE/home_center" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
find "$STAGE/home_center" -type d -name __pycache__ -empty -delete
cp -a "$ROOT/product/web/static/." "$STAGE/web/"
printf '\n' >>"$STAGE/web/app.css"
cat "$ROOT/product/web/static/hc-web-001.css" >>"$STAGE/web/app.css"
cat >"$STAGE/web/release.js" <<EOF
"use strict";

window.HOME_CENTER_RELEASE = Object.freeze({
  version: "$VERSION",
  revision: "$REVISION",
});

document.addEventListener("DOMContentLoaded", () => {
  const brand = document.querySelector(".brand > div:last-child");
  if (!brand) return;
  const current = document.querySelector("#homeCenterVersion");
  const label = current || document.createElement("small");
  label.id = "homeCenterVersion";
  label.textContent = "v" + window.HOME_CENTER_RELEASE.version + " · " + window.HOME_CENTER_RELEASE.revision.slice(0, 12);
  if (!current) brand.append(label);
});
EOF
cp -a "$ROOT/contracts/." "$STAGE/contracts/"
cp "$ROOT/deploy/profiles/hm-dm-two-node.v1.json" "$STAGE/deployment-profile.json"
cp "$ROOT/deploy/runtime/run.py" \
   "$ROOT/deploy/runtime/backup-run.py" \
   "$ROOT/deploy/runtime/tls-maintenance-run.py" \
   "$ROOT/deploy/runtime/release-channel-verify.py" \
   "$ROOT/deploy/runtime/release-candidate-verify.py" \
   "$ROOT/deploy/runtime/provision-local-admin.py" \
   "$ROOT/deploy/runtime/recover-local-admin.py" \
   "$STAGE/"
cp "$ROOT/deploy/scripts/install-node.sh" \
   "$ROOT/deploy/scripts/rollback-node.sh" \
   "$ROOT/deploy/scripts/bootstrap-hm-dm.sh" \
   "$ROOT/deploy/scripts/rotate-web-tls.sh" \
   "$STAGE/deploy/"
/usr/bin/python3 -I "$ROOT/deploy/scripts/render-release-policy.py" \
  "$STAGE/deploy/bootstrap-hm-dm.sh" \
  "$STAGE/deploy/install-node.sh"
/usr/bin/python3 -I "$ROOT/deploy/scripts/render-auth-deployment-v2.py" \
  "$STAGE/deploy/bootstrap-hm-dm.sh" \
  "$STAGE/deploy/install-node.sh" \
  "$STAGE/deploy/bootstrap-hm-dm.sh" \
  "$STAGE/deploy/install-node.sh"
/usr/bin/python3 -I "$ROOT/deploy/scripts/render-upgrade-policy-v2.py" \
  "$STAGE/deploy/bootstrap-hm-dm.sh" \
  "$STAGE/deploy/install-node.sh"
cp "$ROOT/deploy/hm-dm/config.dc01.json" "$ROOT/deploy/hm-dm/config.dc02.json" "$STAGE/deploy/"
cp "$ROOT/deploy/helper-policy.v1.json" "$STAGE/deploy/"
cp "$ROOT/deploy/systemd/home-center.service" \
   "$ROOT/deploy/systemd/home-center-backup.service" \
   "$ROOT/deploy/systemd/home-center-backup.timer" \
   "$ROOT/deploy/systemd/home-center-helper.service" \
   "$ROOT/deploy/systemd/home-center-tls-maintenance.service" \
   "$ROOT/deploy/systemd/home-center-tls-maintenance.timer" \
   "$STAGE/deploy/"
printf '%s\n' "$VERSION" >"$STAGE/VERSION"
printf '%s\n' "$REVISION" >"$STAGE/REVISION"
find "$STAGE" -type f -exec chmod 0644 {} +
chmod 0755 "$STAGE"
find "$STAGE" -type f ! -name MANIFEST.sha256 -print0 | sort -z | xargs -0 sha256sum | sed "s#  $STAGE/#  #" >"$STAGE/MANIFEST.sha256"

ARCHIVE="$OUT/home-center-${VERSION}-linux-amd64.tar.gz"
tar --sort=name --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 --numeric-owner -C "$STAGE" -cf - . | gzip -n -9 >"$ARCHIVE"
(cd "$OUT" && sha256sum "$(basename "$ARCHIVE")" >"$(basename "$ARCHIVE").sha256")
printf 'ARTIFACT=%s\n' "$ARCHIVE"
printf 'SHA256=%s\n' "$(sha256sum "$ARCHIVE" | awk '{print $1}')"
printf 'BYTES=%s\n' "$(stat -c %s "$ARCHIVE")"

