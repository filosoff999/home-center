#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="${1:-}"
BASE="/var/www/home-center-public"
RELEASES="$BASE/releases"
CURRENT="$BASE/current"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="$RELEASES/$STAMP"
ASSET_VERSION="$STAMP"

if [[ -z "$SOURCE_DIR" || ! -f "$SOURCE_DIR/index.html" || ! -f "$SOURCE_DIR/assets/styles.css" ]]; then
  echo "usage: deploy.sh /path/to/validated/website" >&2
  exit 64
fi

install -d -m 0755 "$RELEASES"
install -d -m 0755 "$TARGET"
cp -a "$SOURCE_DIR"/. "$TARGET"/

# Static assets are intentionally cached by nginx. Stamp CSS/JS references in
# every deployed HTML document so a new atomic release cannot reuse an older
# cached stylesheet or script with newer markup.
for html in "$TARGET"/*.html; do
  [[ -f "$html" ]] || continue
  sed -i \
    -e "s|href=\"/assets/styles.css\"|href=\"/assets/styles.css?v=${ASSET_VERSION}\"|g" \
    -e "s|src=\"/assets/app.js\"|src=\"/assets/app.js?v=${ASSET_VERSION}\"|g" \
    "$html"
done

if grep -R -n -E 'href="/assets/styles\.css"|src="/assets/app\.js"' "$TARGET"/*.html >/dev/null 2>&1; then
  echo "asset cache-busting failed" >&2
  exit 65
fi

find "$TARGET" -type d -exec chmod 0755 {} +
find "$TARGET" -type f -exec chmod 0644 {} +
ln -sfn "$TARGET" "$CURRENT.next"
mv -Tf "$CURRENT.next" "$CURRENT"

echo "$TARGET"
