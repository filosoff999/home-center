#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
OUT=${1:-"$ROOT/dist"}
VERSION=$(python3 - "$ROOT/product/control-plane/src/home_center/__init__.py" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$', text, re.M)
if not match:
    raise SystemExit("VERSION_NOT_FOUND")
print(match.group(1))
PY
)
REVISION=$(python3 - "$ROOT/PUBLIC_SOURCE.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["source_revision"])
PY
)
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo INVALID_SOURCE_REVISION >&2; exit 66; }
SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-1767225600}
[[ "$SOURCE_DATE_EPOCH" =~ ^[1-9][0-9]{8,11}$ ]] || { echo SOURCE_DATE_EPOCH_REJECTED >&2; exit 66; }

STAGE=$(mktemp -d)
trap 'rm -rf -- "$STAGE"' EXIT
mkdir -p "$OUT" "$STAGE/home_center" "$STAGE/web" "$STAGE/contracts" "$STAGE/deploy"
cp -a "$ROOT/product/control-plane/src/home_center/." "$STAGE/home_center/"
cp -a "$ROOT/product/web/static/." "$STAGE/web/"
cp -a "$ROOT/contracts/." "$STAGE/contracts/"
find "$STAGE" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
find "$STAGE" -type d -name __pycache__ -prune -exec rm -rf {} +

printf '\n' >> "$STAGE/web/app.css"
cat "$ROOT/product/web/static/hc-web-001.css" >> "$STAGE/web/app.css"
cat > "$STAGE/web/release.js" <<EOF
"use strict";
window.HOME_CENTER_RELEASE = Object.freeze({version:"$VERSION",revision:"$REVISION"});
document.addEventListener("DOMContentLoaded",()=>{const brand=document.querySelector(".brand > div:last-child");if(!brand)return;const current=document.querySelector("#homeCenterVersion");const label=current||document.createElement("small");label.id="homeCenterVersion";label.textContent="v"+window.HOME_CENTER_RELEASE.version+" · "+window.HOME_CENTER_RELEASE.revision.slice(0,12);if(!current)brand.append(label);});
EOF

cp "$ROOT/deploy/profiles/two-node.example.v1.json" "$STAGE/deployment-profile.json"
cp "$ROOT/deploy/config/"*.example.json "$STAGE/deploy/"
cp "$ROOT/deploy/runtime/run.py" \
   "$ROOT/deploy/runtime/backup-run.py" \
   "$ROOT/deploy/runtime/provision-local-admin.py" \
   "$STAGE/"
cp "$ROOT/deploy/systemd/home-center.service" \
   "$ROOT/deploy/systemd/home-center-backup.service" \
   "$ROOT/deploy/systemd/home-center-backup.timer" \
   "$STAGE/deploy/"
printf '%s\n' "$VERSION" > "$STAGE/VERSION"
printf '%s\n' "$REVISION" > "$STAGE/REVISION"
cp "$ROOT/PUBLIC_SOURCE.json" "$STAGE/PUBLIC_SOURCE.json"

find "$STAGE" -type f -exec chmod 0644 {} +
find "$STAGE" -type f ! -name MANIFEST.sha256 -print0 | sort -z | xargs -0 sha256sum \
  | sed "s#  $STAGE/#  #" > "$STAGE/MANIFEST.sha256"

ARCHIVE="$OUT/home-center-free-${VERSION}-linux-amd64.tar.gz"
tar --sort=name --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 --numeric-owner \
  -C "$STAGE" -cf - . | gzip -n -9 > "$ARCHIVE"
(cd "$OUT" && sha256sum "$(basename "$ARCHIVE")" > "$(basename "$ARCHIVE").sha256")
printf 'ARTIFACT=%s\nSHA256=%s\n' "$ARCHIVE" "$(sha256sum "$ARCHIVE" | awk '{print $1}')"
