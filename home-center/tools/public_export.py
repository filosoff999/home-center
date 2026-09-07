#!/usr/bin/env python3
"""Generate an infrastructure-neutral public Home Center source tree.

The exporter is fail-closed and allowlist-only. It is intentionally kept in the
private source repository because its private-marker denylist contains deployment
identifiers that must never be copied to the public repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path


RUNTIME_MODULES = (
    "__init__.py",
    "__main__.py",
    "action_registry.v1.json",
    "actions.py",
    "ad_auth.py",
    "api.py",
    "api_v2.py",
    "auth.py",
    "backup.py",
    "config.py",
    "external_access.py",
    "inventory.py",
    "local_admin_auth.py",
    "local_admin_provision.py",
    "reconcile.py",
    "release_identity.py",
    "runtime.py",
    "server.py",
    "store.py",
    "util.py",
)
RUNTIME_FILES = (
    "deploy/runtime/run.py",
    "deploy/runtime/backup-run.py",
    "deploy/runtime/provision-local-admin.py",
)
SYSTEMD_FILES = (
    "home-center.service",
    "home-center-backup.service",
    "home-center-backup.timer",
)

# Private-source gate. These values intentionally exist only in the private repo.
FORBIDDEN_PRIVATE = (
    ("private-node", re.compile(r"(?i)dc0[12]")),
    ("private-domain", re.compile(r"(?i)hm[.-]dm")),
    ("private-address", re.compile(r"192\.168\.10\.(?:253|254)(?![0-9])")),
    ("private-sid", re.compile(r"S-1-5-21-483832520-828804035-215000592")),
    ("private-operator", re.compile(r"(?i)chatgpt-ops")),
    ("private-evidence-repo", re.compile(r"(?i)serverops-control")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github-token", re.compile(r"(?:github_pat_|ghp_)[A-Za-z0-9_]{20,}")),
    ("openai-key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
)


def write_text(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def copy_text(source_root: Path, out: Path, relative: str, *, mode: int = 0o644) -> None:
    source = source_root / relative
    if not source.is_file():
        raise FileNotFoundError(source)
    write_text(out / relative, source.read_text(encoding="utf-8"), mode)


def sanitize_runtime(source_root: Path, out: Path) -> None:
    base = source_root / "product/control-plane/src/home_center"
    target = out / "product/control-plane/src/home_center"
    target.mkdir(parents=True, exist_ok=True)
    for name in RUNTIME_MODULES:
        source = base / name
        if not source.is_file():
            raise FileNotFoundError(source)
        text = source.read_text(encoding="utf-8")
        if name == "ad_auth.py":
            text = text.replace('realm="HM.DM"', 'realm="EXAMPLE.INTERNAL"')
        elif name == "runtime.py":
            text = text.replace("HM.DM profile must contain exactly two nodes", "deployment profile must contain exactly two nodes")
        elif name == "api_v2.py":
            text = text.replace('filename="home-center-hm-dm-web-ca.crt"', 'filename="home-center-web-ca.crt"')
        write_text(target / name, text)
    write_text(target / "tls_status.py", PUBLIC_TLS_STATUS)


def sanitize_web(source_root: Path, out: Path) -> None:
    source = source_root / "product/web/static"
    target = out / "product/web/static"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("app.css", "hc-web-001.css", "release.js"):
        copy_text(source_root, out, f"product/web/static/{name}")

    html = (source / "index.html").read_text(encoding="utf-8")
    replacements = {
        '<div><strong>Home Center</strong><span>HM.DM</span></div>': '<div><strong>Home Center</strong><span>Infrastructure</span></div>',
        '<span>Связь dc01 ↔ dc02</span>': '<span>Связь между управляемыми узлами</span>',
        '<p>HM.DM production</p>': '<p>Two-node infrastructure</p>',
        'id="topologyDc01"><span class="role-label">LEADER</span><strong>dc01</strong><small>192.168.10.254</small>': 'id="topologyNodeA"><span class="role-label">LEADER</span><strong>Узел A</strong><small>из фактического состояния</small>',
        'id="topologyDc02"><span class="role-label standby">STANDBY</span><strong>dc02</strong><small>192.168.10.253</small>': 'id="topologyNodeB"><span class="role-label standby">STANDBY</span><strong>Узел B</strong><small>из фактического состояния</small>',
        '>Доменная учётная запись HM.DM<': '>Доменная учётная запись AD<',
        'TLS 1.2+ · HM.DM': 'TLS 1.2+',
    }
    for old, new in replacements.items():
        if old not in html:
            raise RuntimeError(f"public Web sanitizer source marker missing: {old[:40]}")
        html = html.replace(old, new)
    write_text(target / "index.html", html)

    script = (source / "app.js").read_text(encoding="utf-8")
    old_topology = '''function renderTopology(nodes) {\n  for (const name of ["dc01","dc02"]) {\n    const found = nodes.find((item) => item.name === name);\n    $(`#topology${name[0].toUpperCase()}${name.slice(1)}`).classList.toggle("ready", found?.status === "ready");\n  }\n}'''
    new_topology = '''function renderTopology(nodes) {\n  const slots = [$("#topologyNodeA"), $("#topologyNodeB")];\n  slots.forEach((slot, index) => {\n    if (!slot) return;\n    const found = nodes[index];\n    slot.classList.toggle("ready", found?.status === "ready");\n    if (!found) return;\n    const role = slot.querySelector(".role-label");\n    const title = slot.querySelector("strong");\n    const detail = slot.querySelector("small");\n    if (role) { role.textContent = String(found.role || "node").toUpperCase(); role.classList.toggle("standby", found.role === "standby"); }\n    if (title) title.textContent = found.name || found.node_id || `Узел ${index + 1}`;\n    if (detail) detail.textContent = found.address || "—";\n  });\n}'''
    if old_topology not in script:
        raise RuntimeError("public Web topology source marker missing")
    script = script.replace(old_topology, new_topology)
    write_text(target / "app.js", script)


def sanitize_contracts(source_root: Path, out: Path) -> None:
    source = source_root / "contracts"
    target = out / "contracts"
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        rel_text = relative.as_posix()
        if rel_text in {
            "releases/release-candidate-acceptance.v1.schema.json",
            "releases/release-candidate-verification.v1.schema.json",
        }:
            continue
        text = path.read_text(encoding="utf-8")
        text = text.replace("dc01", "node-a").replace("dc02", "node-b")
        text = text.replace("HM.DM", "EXAMPLE.INTERNAL").replace("hm.dm", "example.internal")
        text = text.replace("hm-dm", "home-center-example")
        text = text.replace("192.168.10.254", "192.0.2.10").replace("192.168.10.253", "192.0.2.11")
        text = text.replace("S-1-5-21-483832520-828804035-215000592", "replace-with-domain-sid")
        write_text(target / relative, text)


def sanitize_systemd(source_root: Path, out: Path) -> None:
    target = out / "deploy/systemd"
    for name in SYSTEMD_FILES:
        source = source_root / "deploy/systemd" / name
        if not source.is_file():
            raise FileNotFoundError(source)
        lines: list[str] = []
        for line in source.read_text(encoding="utf-8").splitlines():
            if line.startswith("Documentation="):
                lines.append("Documentation=https://github.com/ControlCenterSoft/home-center-free")
                continue
            if line.startswith("IPAddressDeny=") or line.startswith("IPAddressAllow="):
                continue
            lines.append(line)
        write_text(target / name, "\n".join(lines) + "\n")


def generic_config(node: str) -> dict[str, object]:
    first = node == "node-a"
    peer = "node-b" if first else "node-a"
    address = "192.0.2.10" if first else "192.0.2.11"
    peer_address = "192.0.2.11" if first else "192.0.2.10"
    return {
        "schema": "home-center.config.v4",
        "cluster_id": "home-center-example",
        "node_id": node,
        "node_name": node,
        "role": "leader" if first else "standby",
        "management_address": address,
        "web_port": 8443,
        "peer_port": 9443,
        "state_db": "/var/lib/home-center/state.sqlite3",
        "backup_dir": "/var/backups/home-center",
        "web_root": "/opt/home-center/current/web",
        "local_admin_credentials_file": "/etc/home-center/secrets/local-admin.json",
        "ad_auth": {
            "enabled": False,
            "realm": "EXAMPLE.INTERNAL",
            "kdc_hosts": ["auth-a.example.internal", "auth-b.example.internal"],
            "allowed_admin_groups": ["home-center-admins@example.internal"],
            "timeout_seconds": 5,
            "cache_root": "/var/lib/home-center/ad-auth",
        },
        "external_access": {"enabled": False, "mode": "trusted-reverse-proxy", "public_hostname": None, "trusted_proxy_addresses": []},
        "session_key_file": "/etc/home-center/secrets/session.key",
        "audit_key_file": "/etc/home-center/secrets/audit.key",
        "tls_certificate": "/etc/home-center/pki/node.crt",
        "tls_private_key": "/etc/home-center/pki/node.key",
        "cluster_ca": "/etc/home-center/pki/ca.crt",
        "web_ca": "/etc/home-center/pki/web-ca/ca.crt",
        "deployment_profile": "/opt/home-center/current/deployment-profile.json",
        "reconcile_interval_seconds": 15,
        "peer_timeout_seconds": 3,
        "peer": {"node_id": peer, "name": peer, "address": peer_address, "url": f"https://{peer_address}:9443", "certificate_name": f"home-center-{peer}"},
    }


def generic_profile() -> dict[str, object]:
    capabilities = ["inventory.v1", "health.v1", "audit.v1", "backup.sqlite.v1", "cluster.peer-mtls.v1"]
    return {
        "schema": "home-center.deployment-profile.v1",
        "metadata": {"name": "home-center-two-node-example", "version": "1.0.0", "environment": "development", "description": "Infrastructure-neutral two-node example; replace all placeholders before deployment."},
        "spec": {
            "cluster_id": "home-center-example",
            "domain": {"dns_name": "example.internal", "sid": "replace-with-domain-sid", "preserve_existing": True, "provision_new_domain": False},
            "nodes": [
                {"id": "node-a", "name": "node-a", "management_address": "192.0.2.10", "roles": ["control-plane.leader", "node-agent"], "required_capabilities": capabilities},
                {"id": "node-b", "name": "node-b", "management_address": "192.0.2.11", "roles": ["control-plane.standby", "node-agent"], "required_capabilities": capabilities},
            ],
            "network": {"web_port": 8443, "peer_port": 9443, "management_cidr": "192.0.2.0/24", "peer_transport": "mutual-tls-1.3"},
            "placement": {"writer_count": 1, "leader": "node-a", "standby": "node-b", "automatic_failover": False, "failover_requires": ["explicit-typed-job", "peer-unreachable", "external-service-health", "fencing-or-operator-proof"], "split_brain_policy": "single-writer-manual-failover"},
            "health": {"reconcile_interval_seconds": 15, "peer_timeout_seconds": 3, "ready_nodes": 2, "degraded_ready_nodes": 1},
            "backup": {"schedule": "daily", "retention_count": 14, "verify_after_create": True, "cluster_copy": "operator-defined"},
            "maintenance": {"minimum_ready_nodes": 1, "block_leader_removal_without_promotion": True, "block_external_ad_role_change": True, "destructive_wipe_separate": True},
        },
    }


PUBLIC_TLS_STATUS = r'''"""Infrastructure-neutral TLS status for the public distribution."""
from __future__ import annotations
import hashlib
import ssl
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .config import Config

OPENSSL = "/usr/bin/openssl"
RENEWAL_DAYS = 30

def _chain_valid(cert: Path, ca: Path) -> bool:
    try:
        result = subprocess.run([OPENSSL, "verify", "-x509_strict", "-CAfile", str(ca), str(cert)], check=False, capture_output=True, text=False, timeout=5, cwd="/", env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C"})
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0

def _certificate_profile(path: Path, *, scope: str) -> dict[str, Any]:
    try:
        result = subprocess.run([OPENSSL, "x509", "-in", str(path), "-noout", "-text"], check=False, capture_output=True, text=True, timeout=5, cwd="/", env={"PATH":"/usr/bin:/bin","LANG":"C","LC_ALL":"C"})
    except (OSError, subprocess.SubprocessError):
        result = None
    text = result.stdout if result is not None and result.returncode == 0 else ""
    if "Public Key Algorithm: id-ecPublicKey" in text:
        algorithm = "ECDSA"
    elif "Public Key Algorithm: ED25519" in text:
        algorithm = "Ed25519"
    elif "Public Key Algorithm: rsaEncryption" in text:
        algorithm = "RSA"
    else:
        algorithm = "unknown"
    curve = "P-256" if "ASN1 OID: prime256v1" in text else None
    signature = "unknown"
    for line in text.splitlines():
        if "Signature Algorithm:" in line:
            signature = line.split("Signature Algorithm:", 1)[1].strip(); break
    if scope == "browser-web-ca":
        valid = algorithm == "ECDSA" and curve == "P-256" and signature == "ecdsa-with-SHA256" and "CA:TRUE" in text
        profile = "ecdsa-p256-sha256-ca" if valid else "unsupported-for-browser-web-ca"
    elif scope == "peer-mtls":
        valid = algorithm == "Ed25519" and signature == "ED25519" and "CA:FALSE" in text
        profile = "ed25519-peer-mtls" if valid else "unexpected-peer-profile"
    else:
        valid = algorithm == "ECDSA" and curve == "P-256" and signature == "ecdsa-with-SHA256" and "CA:FALSE" in text
        profile = "ecdsa-p256-sha256" if valid else "unsupported-for-browser-web"
    return {"profile": profile, "profile_valid": valid, "public_key_algorithm": algorithm, "public_key_curve": curve, "signature_algorithm": signature}

def _fingerprint(path: Path) -> str:
    der = ssl.PEM_cert_to_DER_cert(path.read_text(encoding="ascii"))
    return hashlib.sha256(der).hexdigest()

def _certificate(path: Path, ca: Path, expected_hostname: str, *, scope: str, required_ip: str | None = None) -> dict[str, Any]:
    decoded = ssl._ssl._test_decode_cert(str(path))  # type: ignore[attr-defined]
    sans = decoded.get("subjectAltName", ())
    dns = sorted(v for k,v in sans if k == "DNS")
    ips = sorted(v for k,v in sans if k in {"IP Address","IP"})
    not_after = decoded.get("notAfter")
    days = None
    if not_after:
        days = int((datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), timezone.utc) - datetime.now(timezone.utc)).total_seconds() // 86400)
    hostname_match = expected_hostname in dns or (required_ip is not None and required_ip in ips)
    result = {"mode":"configured-identity", "fingerprint_sha256":_fingerprint(path), "not_after":not_after, "days_remaining":days, "san_dns":dns, "san_ip":ips, "expected_hostname":expected_hostname, "hostname_match":hostname_match, "chain_valid":_chain_valid(path, ca), "required_san_dns":[expected_hostname], "required_san_ip":[required_ip] if required_ip else [], "san_policy_valid":hostname_match}
    result.update(_certificate_profile(path, scope=scope))
    return result

def status(config: Config) -> dict[str, Any]:
    web_hostname = config.external_access.public_hostname or config.node_name
    web = _certificate(config.tls_certificate, config.cluster_ca, web_hostname, scope="browser-web", required_ip=config.management_address)
    peer = _certificate(config.tls_certificate, config.cluster_ca, f"home-center-{config.node_name}", scope="peer-mtls", required_ip=config.management_address)
    web_ca_profile = _certificate_profile(config.web_ca, scope="browser-web-ca")
    try:
        web_ca_fp = _fingerprint(config.web_ca)
    except Exception:
        web_ca_fp = None
    days = web.get("days_remaining")
    renewal_due = days is None or int(days) <= RENEWAL_DAYS or not bool(web.get("chain_valid")) or not bool(web.get("hostname_match"))
    return {"schema":"home-center.tls-status.v1", "node_id":config.node_id, "node_name":config.node_name, "web_hostname":web_hostname, "canonical_web_hostname":web_hostname, "web_url":f"https://{web_hostname}:{config.web_port}", "web":web, "peer":peer, "web_ca":{"fingerprint_sha256":web_ca_fp, **web_ca_profile}, "trust_anchor":{"fingerprint_sha256":_fingerprint(config.cluster_ca)}, "candidate":{"certificate_present":False,"private_key_present":False,"owner_marker_present":False,"complete":False,"partial":False,"invalid":False}, "operational":{"state":"ready","healthy":True,"recovery_required":False,"reason":None}, "renewal":{"threshold_days":RENEWAL_DAYS,"web_ca_threshold_days":None,"due":renewal_due,"last_maintenance":None}}
'''

PUBLIC_BUILDER = r'''#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
OUT=${1:-"$ROOT/dist"}
VERSION=$(python3 - "$ROOT/product/control-plane/src/home_center/__init__.py" <<'PY'
import re,sys
m=re.search(r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$',open(sys.argv[1],encoding='utf-8').read(),re.M)
if not m: raise SystemExit('VERSION_NOT_FOUND')
print(m.group(1))
PY
)
REVISION=$(python3 - "$ROOT/PUBLIC_SOURCE.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1],encoding='utf-8'))['source_revision'])
PY
)
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo INVALID_SOURCE_REVISION >&2; exit 66; }
SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-1767225600}
STAGE=$(mktemp -d); trap 'rm -rf -- "$STAGE"' EXIT
mkdir -p "$OUT" "$STAGE/home_center" "$STAGE/web" "$STAGE/contracts" "$STAGE/deploy"
cp -a "$ROOT/product/control-plane/src/home_center/." "$STAGE/home_center/"
cp -a "$ROOT/product/web/static/." "$STAGE/web/"
printf '\n' >>"$STAGE/web/app.css"; cat "$ROOT/product/web/static/hc-web-001.css" >>"$STAGE/web/app.css"
cat >"$STAGE/web/release.js" <<EOF
"use strict";
window.HOME_CENTER_RELEASE = Object.freeze({version:"$VERSION",revision:"$REVISION"});
document.addEventListener("DOMContentLoaded",()=>{const brand=document.querySelector(".brand > div:last-child");if(!brand)return;const current=document.querySelector("#homeCenterVersion");const label=current||document.createElement("small");label.id="homeCenterVersion";label.textContent="v"+window.HOME_CENTER_RELEASE.version+" · "+window.HOME_CENTER_RELEASE.revision.slice(0,12);if(!current)brand.append(label);});
EOF
cp -a "$ROOT/contracts/." "$STAGE/contracts/"
cp "$ROOT/deploy/profiles/two-node.example.v1.json" "$STAGE/deployment-profile.json"
cp "$ROOT/deploy/config/"*.example.json "$STAGE/deploy/"
cp "$ROOT/deploy/runtime/run.py" "$ROOT/deploy/runtime/backup-run.py" "$ROOT/deploy/runtime/provision-local-admin.py" "$STAGE/"
cp "$ROOT/deploy/systemd/home-center.service" "$ROOT/deploy/systemd/home-center-backup.service" "$ROOT/deploy/systemd/home-center-backup.timer" "$STAGE/deploy/"
printf '%s\n' "$VERSION" >"$STAGE/VERSION"; printf '%s\n' "$REVISION" >"$STAGE/REVISION"; cp "$ROOT/PUBLIC_SOURCE.json" "$STAGE/PUBLIC_SOURCE.json"
find "$STAGE" -type f -exec chmod 0644 {} +
find "$STAGE" -type f ! -name MANIFEST.sha256 -print0 | sort -z | xargs -0 sha256sum | sed "s#  $STAGE/#  #" >"$STAGE/MANIFEST.sha256"
ARCHIVE="$OUT/home-center-free-${VERSION}-linux-amd64.tar.gz"
tar --sort=name --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 --numeric-owner -C "$STAGE" -cf - . | gzip -n -9 >"$ARCHIVE"
(cd "$OUT" && sha256sum "$(basename "$ARCHIVE")" >"$(basename "$ARCHIVE").sha256")
printf 'ARTIFACT=%s\nSHA256=%s\n' "$ARCHIVE" "$(sha256sum "$ARCHIVE"|awk '{print $1}')"
'''

PUBLIC_INSTALLER = r'''#!/usr/bin/env bash
set -Eeuo pipefail
usage(){ echo "usage: sudo $0 --artifact PATH --sha256 HEX --config PATH --tls-cert PATH --tls-key PATH --cluster-ca PATH --web-ca PATH" >&2; exit 64; }
[ "$(id -u)" -eq 0 ] || { echo ROOT_REQUIRED >&2; exit 77; }
ARTIFACT= SHA256= CONFIG= TLS_CERT= TLS_KEY= CLUSTER_CA= WEB_CA=
while [ "$#" -gt 0 ]; do case "$1" in --artifact) ARTIFACT=${2:-};shift 2;; --sha256) SHA256=${2:-};shift 2;; --config) CONFIG=${2:-};shift 2;; --tls-cert) TLS_CERT=${2:-};shift 2;; --tls-key) TLS_KEY=${2:-};shift 2;; --cluster-ca) CLUSTER_CA=${2:-};shift 2;; --web-ca) WEB_CA=${2:-};shift 2;; *) usage;; esac; done
for p in "$ARTIFACT" "$CONFIG" "$TLS_CERT" "$TLS_KEY" "$CLUSTER_CA" "$WEB_CA"; do [ -f "$p" ] || usage; done
[[ "$SHA256" =~ ^[0-9a-f]{64}$ ]] || usage
[ "$(sha256sum "$ARTIFACT"|awk '{print $1}')" = "$SHA256" ] || { echo ARTIFACT_CHECKSUM_MISMATCH >&2; exit 66; }
if grep -Eqi '192\.0\.2\.|example\.internal|home-center-example|replace-with-domain-sid' "$CONFIG"; then echo EXAMPLE_CONFIG_MUST_BE_CUSTOMIZED >&2; exit 66; fi
python3 - "$CONFIG" <<'PY'
import ipaddress,json,sys
v=json.load(open(sys.argv[1],encoding='utf-8'))
if v.get('schema')!='home-center.config.v4': raise SystemExit('CONFIG_SCHEMA_REJECTED')
ipaddress.ip_address(v['management_address']);ipaddress.ip_address(v['peer']['address'])
PY
VERSION=$(tar -xOf "$ARTIFACT" ./VERSION|tr -d '\r\n'); REVISION=$(tar -xOf "$ARTIFACT" ./REVISION|tr -d '\r\n')
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && "$REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo INVALID_RELEASE_IDENTITY >&2; exit 66; }
getent group home-center >/dev/null || groupadd --system home-center
id home-center >/dev/null 2>&1 || useradd --system --gid home-center --home-dir /var/lib/home-center --shell /usr/sbin/nologin home-center
install -d -m 0755 -o root -g root /opt/home-center/releases
install -d -m 0750 -o home-center -g home-center /var/lib/home-center /var/backups/home-center
install -d -m 0750 -o root -g home-center /etc/home-center /etc/home-center/secrets /etc/home-center/pki /etc/home-center/pki/web-ca
TMP=$(mktemp -d /opt/home-center/releases/.install.XXXXXX); trap 'rm -rf -- "$TMP"' EXIT
tar -xzf "$ARTIFACT" -C "$TMP"; (cd "$TMP" && sha256sum -c MANIFEST.sha256)
RELEASE="/opt/home-center/releases/${VERSION}-${REVISION:0:12}-${SHA256:0:12}"; [ ! -e "$RELEASE" ] || { echo RELEASE_ALREADY_EXISTS >&2; exit 73; }
mv -T "$TMP" "$RELEASE"; trap - EXIT; chown -R root:root "$RELEASE"; find "$RELEASE" -type d -exec chmod 0755 {} +; find "$RELEASE" -type f -exec chmod 0644 {} +
ln -sfn "$RELEASE" /opt/home-center/current
install -m 0640 -o root -g home-center "$CONFIG" /etc/home-center/config.json
install -m 0644 -o root -g root "$TLS_CERT" /etc/home-center/pki/node.crt
install -m 0640 -o root -g home-center "$TLS_KEY" /etc/home-center/pki/node.key
install -m 0644 -o root -g root "$CLUSTER_CA" /etc/home-center/pki/ca.crt
install -m 0644 -o root -g root "$WEB_CA" /etc/home-center/pki/web-ca/ca.crt
[ -e /etc/home-center/secrets/session.key ] || { umask 077; openssl rand -hex 32 >/etc/home-center/secrets/session.key; }
[ -e /etc/home-center/secrets/audit.key ] || { umask 077; openssl rand -hex 32 >/etc/home-center/secrets/audit.key; }
chown root:home-center /etc/home-center/secrets/session.key /etc/home-center/secrets/audit.key; chmod 0640 /etc/home-center/secrets/session.key /etc/home-center/secrets/audit.key
for unit in "$RELEASE"/deploy/*.service "$RELEASE"/deploy/*.timer; do [ -e "$unit" ] || continue; install -m 0644 -o root -g root "$unit" /etc/systemd/system/; done
systemctl daemon-reload
printf 'Home Center %s installed. Provision admin: sudo /usr/bin/python3 -I /opt/home-center/current/provision-local-admin.py\nThen start: sudo systemctl enable --now home-center.service home-center-backup.timer\n' "$VERSION"
'''

PUBLIC_SCAN = r'''#!/usr/bin/env python3
from __future__ import annotations
import pathlib,re,sys
patterns=(
 ("private-key",re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
 ("github-token",re.compile(r"(?:github_pat_|ghp_)[A-Za-z0-9_]{20,}")),
 ("openai-key",re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
 ("slack-token",re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
 ("rfc1918-address",re.compile(r"(?<![0-9])(?:10(?:\.[0-9]{1,3}){3}|192\.168(?:\.[0-9]{1,3}){2}|172\.(?:1[6-9]|2[0-9]|3[01])(?:\.[0-9]{1,3}){2})(?![0-9])")),
 ("legacy-two-node-name",re.compile(r"(?i)dc0[12]")),
)
root=pathlib.Path(sys.argv[1] if len(sys.argv)>1 else '.')
failed=[]
for path in sorted(root.rglob('*')):
 if not path.is_file() or '.git' in path.parts: continue
 try:text=path.read_text(encoding='utf-8')
 except UnicodeDecodeError:continue
 for name,pattern in patterns:
  if pattern.search(text):failed.append(f"{path.relative_to(root)}: {name}")
if failed:
 print('PUBLIC_PRIVACY_SCAN_FAILED',file=sys.stderr);print('\n'.join(failed),file=sys.stderr);raise SystemExit(1)
print('PUBLIC_PRIVACY_SCAN=PASS')
'''

PUBLIC_CI = r'''name: Public CI
on:
  push:
  pull_request:
permissions:
  contents: read
jobs:
  verify:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with: {persist-credentials: false}
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065
        with: {python-version: "3.12", cache: ""}
      - run: python3 tools/privacy_scan.py .
      - run: python3 -m compileall -q product/control-plane/src/home_center deploy/runtime
      - name: Import runtime
        run: PYTHONPATH=product/control-plane/src python3 -c 'import home_center.server, home_center.api_v2, home_center.runtime'
      - name: Validate JSON
        run: python3 - <<'PY'
          import json,pathlib
          for p in pathlib.Path('.').rglob('*.json'):json.load(p.open(encoding='utf-8'))
          PY
      - name: Reproducible artifact
        run: |
          bash deploy/scripts/build-public-artifact.sh "$RUNNER_TEMP/a"
          bash deploy/scripts/build-public-artifact.sh "$RUNNER_TEMP/b"
          cmp "$RUNNER_TEMP/a/"*.tar.gz "$RUNNER_TEMP/b/"*.tar.gz
          (cd "$RUNNER_TEMP/a" && sha256sum -c *.sha256)
'''


def readme(version: str, revision: str) -> str:
    return f"""# Home Center Free\n\nPublic, infrastructure-neutral distribution of Home Center.\n\nCurrent synchronized release: **{version}**  \nPrivate source revision: `{revision}`\n\n## Privacy and publication boundary\n\nThis repository is generated from an explicit runtime allowlist. Private deployment configuration, operator evidence, production runbooks, infrastructure-specific release acceptance code, credentials, keys, host identities and private network addresses are not exported. Public CI includes a fail-closed privacy scan.\n\n## Build\n\n```bash\ngit clone https://github.com/ControlCenterSoft/home-center-free.git\ncd home-center-free\nbash deploy/scripts/build-public-artifact.sh\n```\n\n## Install\n\nCopy one of `deploy/config/config.node-*.example.json`, replace every example value with your own infrastructure values, prepare your node certificate/key plus peer and Web CAs, then run `deploy/scripts/install-public.sh`. The installer rejects unmodified documentation values.\n\nHome Center does not implicitly mutate AD, DNS, DHCP, GPO, router/NAT, DDNS or firewall configuration. Automatic failover remains disabled in the two-node profile unless a future release introduces an explicit safe witness/fencing design.\n\n## Public-source differences\n\nThe runtime/API/Web UI, local administrator authentication, optional AD authentication, inventory, health, audit, backup, two-node peer model and external-access boundary are exported. Private production rollout/evidence verifiers and infrastructure-specific automatic TLS rotation are deliberately excluded from the public variant; operators provide TLS identity explicitly during installation.\n\n## Русский\n\n`home-center-free` — публичный вариант Home Center для самостоятельной установки. Он синхронизируется с опубликованным приватным релизом, но не содержит сведения о конкретной инфраструктуре, серверах, домене, ключах, учётных данных или production evidence. Примеры используют только зарезервированные документационные значения.\n"""


def security_text() -> str:
    return """# Security policy\n\nNever commit passwords, tokens, private keys, real deployment identities, private network addresses, production evidence or operator-specific configuration.\n\nReport suspected vulnerabilities privately to the repository owner; do not place sensitive details in a public issue. Every change must pass `tools/privacy_scan.py` and code review.\n"""


def scan_private(root: Path) -> None:
    failures=[]
    for path in sorted(root.rglob("*")):
        if not path.is_file(): continue
        try: text=path.read_text(encoding="utf-8")
        except UnicodeDecodeError: continue
        for name,pattern in FORBIDDEN_PRIVATE:
            if pattern.search(text): failures.append(f"{path.relative_to(root)}: {name}")
    if failures: raise SystemExit("PUBLIC_PRIVACY_SCAN_FAILED\n"+"\n".join(failures))


def manifest(root: Path) -> None:
    rows=[]
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name!="PUBLIC_EXPORT_MANIFEST.sha256": rows.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root).as_posix()}")
    write_text(root/"PUBLIC_EXPORT_MANIFEST.sha256","\n".join(rows)+"\n")


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--source-root",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--version",required=True)
    parser.add_argument("--source-revision",required=True)
    parser.add_argument("--source-tag",required=True)
    args=parser.parse_args()
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+",args.version) is None: raise SystemExit("INVALID_VERSION")
    if re.fullmatch(r"[0-9a-f]{40}",args.source_revision) is None: raise SystemExit("INVALID_SOURCE_REVISION")
    if args.source_tag!="v"+args.version: raise SystemExit("SOURCE_TAG_VERSION_MISMATCH")
    source=args.source_root.resolve(); out=args.output.resolve()
    if out.exists(): shutil.rmtree(out)
    out.mkdir(parents=True)
    sanitize_runtime(source,out); sanitize_web(source,out); sanitize_contracts(source,out); sanitize_systemd(source,out)
    copy_text(source,out,"pyproject.toml")
    for relative in RUNTIME_FILES: copy_text(source,out,relative,mode=0o755)
    write_text(out/"deploy/config/config.node-a.example.json",json.dumps(generic_config("node-a"),indent=2,sort_keys=True)+"\n")
    write_text(out/"deploy/config/config.node-b.example.json",json.dumps(generic_config("node-b"),indent=2,sort_keys=True)+"\n")
    write_text(out/"deploy/profiles/two-node.example.v1.json",json.dumps(generic_profile(),indent=2,sort_keys=True)+"\n")
    write_text(out/"deploy/scripts/build-public-artifact.sh",PUBLIC_BUILDER,0o755)
    write_text(out/"deploy/scripts/install-public.sh",PUBLIC_INSTALLER,0o755)
    write_text(out/"tools/privacy_scan.py",PUBLIC_SCAN,0o755)
    write_text(out/".github/workflows/ci.yml",PUBLIC_CI)
    write_text(out/"README.md",readme(args.version,args.source_revision)); write_text(out/"SECURITY.md",security_text())
    write_text(out/".gitignore","__pycache__/\n*.py[cod]\ndist/\n*.sqlite3\n.env\n")
    write_text(out/"PUBLIC_SOURCE.json",json.dumps({"schema":"home-center.public-source.v1","version":args.version,"source_tag":args.source_tag,"source_revision":args.source_revision,"variant":"free-public"},indent=2,sort_keys=True)+"\n")
    write_text(out/"PUBLIC_EXPORT_POLICY.md","# Public export policy\n\nAllowlist-only runtime export. Private deployment/evidence/runbook paths are never copied. Example network identities use documentation ranges.\n")
    scan_private(out); manifest(out)
    print(f"PUBLIC_EXPORT=PASS version={args.version} revision={args.source_revision} files={sum(1 for p in out.rglob('*') if p.is_file())}")

if __name__=="__main__": main()
