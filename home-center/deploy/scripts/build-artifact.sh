#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
OUT=${1:-"$ROOT/dist"}
SOURCE_VERSION=$(awk -F'"' '/^__version__ = / {print $2}' "$ROOT/product/control-plane/src/home_center/__init__.py")
VERSION=${HOME_CENTER_VERSION:-$SOURCE_VERSION}
[ "$VERSION" = "$SOURCE_VERSION" ] || { echo VERSION_OVERRIDE_MISMATCH >&2; exit 66; }
[ "$VERSION" = 0.4.2 ] || { echo RELEASE_VERSION_NOT_ADMITTED >&2; exit 66; }
REVISION=${HOME_CENTER_REVISION:-$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || printf 'working-tree')}
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo REVISION_NOT_IMMUTABLE >&2; exit 66; }
STAGE=$(mktemp -d)
trap 'rm -rf -- "$STAGE"' EXIT

mkdir -p "$OUT" "$STAGE/home_center" "$STAGE/web" "$STAGE/contracts" "$STAGE/deploy"
cp -a "$ROOT/product/control-plane/src/home_center/." "$STAGE/home_center/"
find "$STAGE/home_center" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
find "$STAGE/home_center" -type d -name __pycache__ -empty -delete
cp -a "$ROOT/product/web/static/." "$STAGE/web/"
cp -a "$ROOT/contracts/." "$STAGE/contracts/"
cp "$ROOT/deploy/profiles/hm-dm-two-node.v1.json" "$STAGE/deployment-profile.json"
cp "$ROOT/deploy/runtime/run.py" "$ROOT/deploy/runtime/backup-run.py" "$ROOT/deploy/runtime/tls-maintenance-run.py" "$STAGE/"
cp "$ROOT/deploy/scripts/install-node.sh" \
   "$ROOT/deploy/scripts/rollback-node.sh" \
   "$ROOT/deploy/scripts/bootstrap-hm-dm.sh" \
   "$ROOT/deploy/scripts/rotate-web-tls.sh" \
   "$STAGE/deploy/"
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
find "$STAGE" -type f ! -name MANIFEST.sha256 -print0 | sort -z | xargs -0 sha256sum | sed "s#  $STAGE/#  #" >"$STAGE/MANIFEST.sha256"

ARCHIVE="$OUT/home-center-${VERSION}-linux-amd64.tar.gz"
tar --sort=name --mtime='UTC 2026-01-01' --owner=0 --group=0 --numeric-owner -C "$STAGE" -cf - . | gzip -n -9 >"$ARCHIVE"
(cd "$OUT" && sha256sum "$(basename "$ARCHIVE")" >"$(basename "$ARCHIVE").sha256")
printf 'ARTIFACT=%s\n' "$ARCHIVE"
printf 'SHA256=%s\n' "$(sha256sum "$ARCHIVE" | awk '{print $1}')"
printf 'BYTES=%s\n' "$(stat -c %s "$ARCHIVE")"
