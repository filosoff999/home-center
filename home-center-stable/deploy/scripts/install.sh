#!/usr/bin/env bash
set -Eeuo pipefail

source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
config_source=""
activate="false"
release_root="/opt/home-center/releases"

while (($#)); do
  case "$1" in
    --source) source_root="$2"; shift 2 ;;
    --config) config_source="$2"; shift 2 ;;
    --activate) activate="true"; shift ;;
    --release-root) release_root="$2"; shift 2 ;;
    *) echo "UNKNOWN_ARGUMENT" >&2; exit 64 ;;
  esac
done

if [[ "$EUID" -ne 0 ]]; then echo "ROOT_REQUIRED" >&2; exit 77; fi
if [[ -z "$config_source" || ! -f "$config_source" || -L "$config_source" ]]; then
  echo "CONFIG_FILE_REQUIRED" >&2; exit 64
fi
if [[ "$release_root" != /* || "$release_root" == "/" || -L "$release_root" ]]; then
  echo "RELEASE_ROOT_REJECTED" >&2; exit 64
fi

source_root="$(cd "$source_root" && pwd -P)"
bash "$source_root/deploy/scripts/verify-artifact.sh" "$source_root"
python3 - "$config_source" <<'PY'
import json, re, stat, sys
from pathlib import Path
path = Path(sys.argv[1])
info = path.lstat()
if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > 1024 * 1024:
    raise SystemExit("CONFIG_FILE_REJECTED")
value = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(value, dict) or value.get("schema") not in {"home-center.config.v4", "home-center.config.v5"}:
    raise SystemExit("CONFIG_SCHEMA_REJECTED")
node_id = value.get("node_id")
if not isinstance(node_id, str) or re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,126}[a-z0-9])?", node_id) is None:
    raise SystemExit("CONFIG_NODE_REJECTED")
PY

revision="$(tr -d '\n' < "$source_root/REVISION")"
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { echo "REVISION_REJECTED" >&2; exit 65; }
version="$(tr -d '\n' < "$source_root/VERSION")"
[[ "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || { echo "VERSION_REJECTED" >&2; exit 65; }
target="$release_root/$version-${revision:0:12}"
install -d -m 0755 "$release_root"

if [[ -e "$target" || -L "$target" ]]; then
  [[ -d "$target" && ! -L "$target" ]] || { echo "RELEASE_TARGET_REJECTED" >&2; exit 65; }
  bash "$target/deploy/scripts/verify-artifact.sh" "$target"
  [[ "$(tr -d '\n' < "$target/REVISION")" == "$revision" ]] || { echo "RELEASE_TARGET_CONFLICT" >&2; exit 65; }
else
  stage="$(mktemp -d "$release_root/.home-center-$version.XXXXXXXX")"
  trap '[[ -n "${stage:-}" && -d "$stage" ]] && rm -rf -- "$stage"' EXIT
  cp -a -- "$source_root/." "$stage/"
  bash "$stage/deploy/scripts/verify-artifact.sh" "$stage"
  chmod -R u=rwX,go=rX "$stage"
  mv -- "$stage" "$target"
  stage=""
  trap - EXIT
fi

getent group home-center >/dev/null || groupadd --system home-center
getent passwd home-center >/dev/null || useradd --system --gid home-center --home-dir /var/lib/home-center --shell /usr/sbin/nologin home-center
install -d -m 0750 -o home-center -g home-center /var/lib/home-center /var/backups/home-center
install -d -m 0750 -o root -g home-center /etc/home-center /etc/home-center/secrets
if [[ -e /etc/home-center/config.json || -L /etc/home-center/config.json ]]; then
  [[ -f /etc/home-center/config.json && ! -L /etc/home-center/config.json ]] || { echo "INSTALLED_CONFIG_REJECTED" >&2; exit 65; }
  cmp -s -- "$config_source" /etc/home-center/config.json || { echo "INSTALLED_CONFIG_DIFFERS" >&2; exit 65; }
else
  install -m 0640 -o root -g home-center "$config_source" /etc/home-center/config.json
fi
if [[ ! -e /etc/home-center/helper-policy.json ]]; then
  install -m 0640 -o root -g home-center "$target/deploy/config/helper-policy.example.json" /etc/home-center/helper-policy.json
fi

if [[ "$activate" != "true" ]]; then
  echo "HOME_CENTER_INSTALL=STAGED target=$target"
  exit 0
fi

for unit in home-center.service home-center-helper.service home-center-backup.service home-center-backup.timer; do
  install -m 0644 "$target/deploy/systemd/$unit" "/etc/systemd/system/$unit"
done
current="/opt/home-center/current"
[[ ! -e "$current" || -L "$current" ]] || { echo "CURRENT_POINTER_REJECTED" >&2; exit 65; }
previous=""
if [[ -L "$current" ]]; then previous="$(readlink "$current")"; fi
temporary_link="/opt/home-center/.current.$$"
ln -s -- "$target" "$temporary_link"
mv -Tf -- "$temporary_link" "$current"
systemctl daemon-reload
if ! systemctl restart home-center-helper.service home-center.service || ! systemctl is-active --quiet home-center.service; then
  if [[ -n "$previous" ]]; then
    ln -s -- "$previous" "$temporary_link"
    mv -Tf -- "$temporary_link" "$current"
    systemctl restart home-center-helper.service home-center.service || true
  else
    rm -f -- "$current"
  fi
  echo "ACTIVATION_ROLLED_BACK" >&2
  exit 70
fi
systemctl enable home-center.service home-center-helper.service home-center-backup.timer
echo "HOME_CENTER_INSTALL=ACTIVE target=$target"
