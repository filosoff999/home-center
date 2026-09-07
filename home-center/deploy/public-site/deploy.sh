#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="${1:-}"
BASE="/var/www/home-center-public"
RELEASES="$BASE/releases"
CURRENT="$BASE/current"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="$RELEASES/$STAMP"

if [[ -z "$SOURCE_DIR" || ! -f "$SOURCE_DIR/index.html" || ! -f "$SOURCE_DIR/assets/styles.css" ]]; then
  echo "usage: deploy.sh /path/to/validated/website" >&2
  exit 64
fi

install -d -m 0755 "$RELEASES"
install -d -m 0755 "$TARGET"
cp -a "$SOURCE_DIR"/. "$TARGET"/
find "$TARGET" -type d -exec chmod 0755 {} +
find "$TARGET" -type f -exec chmod 0644 {} +
ln -sfn "$TARGET" "$CURRENT.next"
mv -Tf "$CURRENT.next" "$CURRENT"

echo "$TARGET"
