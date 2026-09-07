#!/usr/bin/env python3
"""Finalize the generated public tree with public-only PKI/install assets."""

from __future__ import annotations

import argparse
from pathlib import Path

import public_export


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
WEB_CURRENT = Path("/etc/home-center/pki/web/current")
WEB_CERTIFICATE = WEB_CURRENT / "tls.crt"


def _chain_valid(cert: Path, ca: Path) -> bool:
    try:
        result = subprocess.run(
            [OPENSSL, "verify", "-x509_strict", "-CAfile", str(ca), str(cert)],
            check=False,
            capture_output=True,
            text=False,
            timeout=5,
            cwd="/",
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _certificate_profile(path: Path, *, scope: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [OPENSSL, "x509", "-in", str(path), "-noout", "-text"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            cwd="/",
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
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
            signature = line.split("Signature Algorithm:", 1)[1].strip()
            break
    if scope == "browser-web-ca":
        valid = algorithm == "ECDSA" and curve == "P-256" and signature == "ecdsa-with-SHA256" and "CA:TRUE" in text
        profile = "ecdsa-p256-sha256-ca" if valid else "unsupported-for-browser-web-ca"
    elif scope == "peer-mtls":
        valid = algorithm == "Ed25519" and signature == "ED25519" and "CA:FALSE" in text
        profile = "ed25519-peer-mtls" if valid else "unexpected-peer-profile"
    else:
        valid = algorithm == "ECDSA" and curve == "P-256" and signature == "ecdsa-with-SHA256" and "CA:FALSE" in text
        profile = "ecdsa-p256-sha256" if valid else "unsupported-for-browser-web"
    return {
        "profile": profile,
        "profile_valid": valid,
        "public_key_algorithm": algorithm,
        "public_key_curve": curve,
        "signature_algorithm": signature,
    }


def _fingerprint(path: Path) -> str:
    der = ssl.PEM_cert_to_DER_cert(path.read_text(encoding="ascii"))
    return hashlib.sha256(der).hexdigest()


def _certificate(
    path: Path,
    ca: Path,
    expected_hostname: str,
    *,
    scope: str,
    required_ip: str | None = None,
) -> dict[str, Any]:
    decoded = ssl._ssl._test_decode_cert(str(path))  # type: ignore[attr-defined]
    sans = decoded.get("subjectAltName", ())
    dns = sorted(value for kind, value in sans if kind == "DNS")
    ips = sorted(value for kind, value in sans if kind in {"IP Address", "IP"})
    not_after = decoded.get("notAfter")
    days = None
    if not_after:
        expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), timezone.utc)
        days = int((expires - datetime.now(timezone.utc)).total_seconds() // 86400)
    hostname_match = expected_hostname in dns or (required_ip is not None and required_ip in ips)
    result = {
        "mode": "separate-web-identity" if scope == "browser-web" else "peer-mtls-identity",
        "fingerprint_sha256": _fingerprint(path),
        "not_after": not_after,
        "days_remaining": days,
        "san_dns": dns,
        "san_ip": ips,
        "expected_hostname": expected_hostname,
        "hostname_match": hostname_match,
        "chain_valid": _chain_valid(path, ca),
        "required_san_dns": [expected_hostname],
        "required_san_ip": [required_ip] if required_ip else [],
        "san_policy_valid": hostname_match,
    }
    result.update(_certificate_profile(path, scope=scope))
    return result


def status(config: Config) -> dict[str, Any]:
    web_hostname = config.external_access.public_hostname or config.node_name
    web_path = WEB_CERTIFICATE if WEB_CERTIFICATE.is_file() else config.tls_certificate
    web_ca = config.web_ca if WEB_CERTIFICATE.is_file() else config.cluster_ca
    web = _certificate(
        web_path,
        web_ca,
        web_hostname,
        scope="browser-web",
        required_ip=config.management_address,
    )
    peer = _certificate(
        config.tls_certificate,
        config.cluster_ca,
        f"home-center-{config.node_name}",
        scope="peer-mtls",
        required_ip=config.management_address,
    )
    web_ca_profile = _certificate_profile(config.web_ca, scope="browser-web-ca")
    try:
        web_ca_fingerprint = _fingerprint(config.web_ca)
    except Exception:
        web_ca_fingerprint = None
    days = web.get("days_remaining")
    renewal_due = (
        days is None
        or int(days) <= RENEWAL_DAYS
        or not bool(web.get("chain_valid"))
        or not bool(web.get("hostname_match"))
        or not bool(web.get("profile_valid"))
    )
    return {
        "schema": "home-center.tls-status.v1",
        "node_id": config.node_id,
        "node_name": config.node_name,
        "web_hostname": web_hostname,
        "canonical_web_hostname": web_hostname,
        "web_url": f"https://{web_hostname}:{config.web_port}",
        "web": web,
        "peer": peer,
        "web_ca": {"fingerprint_sha256": web_ca_fingerprint, **web_ca_profile},
        "trust_anchor": {"fingerprint_sha256": _fingerprint(config.cluster_ca)},
        "candidate": {
            "certificate_present": False,
            "private_key_present": False,
            "owner_marker_present": False,
            "complete": False,
            "partial": False,
            "invalid": False,
        },
        "operational": {"state": "ready", "healthy": True, "recovery_required": False, "reason": None},
        "renewal": {
            "threshold_days": RENEWAL_DAYS,
            "web_ca_threshold_days": None,
            "due": renewal_due,
            "last_maintenance": None,
        },
    }
'''


PUBLIC_INSTALLER = r'''#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo "usage: sudo $0 --artifact PATH --sha256 HEX --config PATH --peer-cert PATH --peer-key PATH --cluster-ca PATH --web-cert PATH --web-key PATH --web-ca PATH" >&2
  exit 64
}

[ "$(id -u)" -eq 0 ] || { echo ROOT_REQUIRED >&2; exit 77; }
ARTIFACT= SHA256= CONFIG= PEER_CERT= PEER_KEY= CLUSTER_CA= WEB_CERT= WEB_KEY= WEB_CA=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --artifact) ARTIFACT=${2:-}; shift 2 ;;
    --sha256) SHA256=${2:-}; shift 2 ;;
    --config) CONFIG=${2:-}; shift 2 ;;
    --peer-cert) PEER_CERT=${2:-}; shift 2 ;;
    --peer-key) PEER_KEY=${2:-}; shift 2 ;;
    --cluster-ca) CLUSTER_CA=${2:-}; shift 2 ;;
    --web-cert) WEB_CERT=${2:-}; shift 2 ;;
    --web-key) WEB_KEY=${2:-}; shift 2 ;;
    --web-ca) WEB_CA=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done
for path in "$ARTIFACT" "$CONFIG" "$PEER_CERT" "$PEER_KEY" "$CLUSTER_CA" "$WEB_CERT" "$WEB_KEY" "$WEB_CA"; do
  [ -f "$path" ] || usage
done
[[ "$SHA256" =~ ^[0-9a-f]{64}$ ]] || usage
[ "$(sha256sum "$ARTIFACT" | awk '{print $1}')" = "$SHA256" ] || { echo ARTIFACT_CHECKSUM_MISMATCH >&2; exit 66; }

if grep -Eqi '192\.0\.2\.|example\.internal|home-center-example|replace-with-domain-sid' "$CONFIG"; then
  echo EXAMPLE_CONFIG_MUST_BE_CUSTOMIZED >&2
  exit 66
fi

python3 - "$CONFIG" "$PEER_CERT" "$WEB_CERT" <<'PY'
import ipaddress, json, ssl, sys
config = json.load(open(sys.argv[1], encoding="utf-8"))
if config.get("schema") != "home-center.config.v4":
    raise SystemExit("CONFIG_SCHEMA_REJECTED")
management = str(ipaddress.ip_address(config["management_address"]))
ipaddress.ip_address(config["peer"]["address"])
peer = ssl._ssl._test_decode_cert(sys.argv[2])
web = ssl._ssl._test_decode_cert(sys.argv[3])
def sans(cert, kind):
    return {value for item_kind, value in cert.get("subjectAltName", ()) if item_kind == kind}
peer_dns = sans(peer, "DNS")
peer_ip = sans(peer, "IP Address") | sans(peer, "IP")
expected_peer = "home-center-" + config["node_name"]
if expected_peer not in peer_dns or management not in peer_ip:
    raise SystemExit("PEER_CERTIFICATE_SAN_REJECTED")
web_dns = sans(web, "DNS")
web_ip = sans(web, "IP Address") | sans(web, "IP")
external = config.get("external_access") or {}
expected_web = external.get("public_hostname") if external.get("enabled") else config["node_name"]
if expected_web not in web_dns and management not in web_ip:
    raise SystemExit("WEB_CERTIFICATE_SAN_REJECTED")
PY

verify_pair() {
  local certificate=$1 private_key=$2 label=$3 cert_pub key_pub
  cert_pub=$(openssl x509 -in "$certificate" -pubkey -noout | openssl pkey -pubin -outform DER 2>/dev/null | sha256sum | awk '{print $1}')
  key_pub=$(openssl pkey -in "$private_key" -pubout -outform DER 2>/dev/null | sha256sum | awk '{print $1}')
  [ -n "$cert_pub" ] && [ "$cert_pub" = "$key_pub" ] || { echo "${label}_KEY_MISMATCH" >&2; exit 66; }
}
verify_pair "$PEER_CERT" "$PEER_KEY" PEER
verify_pair "$WEB_CERT" "$WEB_KEY" WEB
openssl verify -x509_strict -CAfile "$CLUSTER_CA" "$PEER_CERT" >/dev/null || { echo PEER_CERTIFICATE_CHAIN_REJECTED >&2; exit 66; }
openssl verify -x509_strict -CAfile "$WEB_CA" "$WEB_CERT" >/dev/null || { echo WEB_CERTIFICATE_CHAIN_REJECTED >&2; exit 66; }
PEER_DER_SHA=$(openssl x509 -in "$PEER_CERT" -outform DER | sha256sum | awk '{print $1}')
WEB_DER_SHA=$(openssl x509 -in "$WEB_CERT" -outform DER | sha256sum | awk '{print $1}')
CLUSTER_CA_DER_SHA=$(openssl x509 -in "$CLUSTER_CA" -outform DER | sha256sum | awk '{print $1}')
WEB_CA_DER_SHA=$(openssl x509 -in "$WEB_CA" -outform DER | sha256sum | awk '{print $1}')
[ "$PEER_DER_SHA" != "$WEB_DER_SHA" ] || { echo WEB_AND_PEER_CERTIFICATES_MUST_DIFFER >&2; exit 66; }
[ "$CLUSTER_CA_DER_SHA" != "$WEB_CA_DER_SHA" ] || { echo WEB_AND_CLUSTER_CA_MUST_DIFFER >&2; exit 66; }

VERSION=$(tar -xOf "$ARTIFACT" ./VERSION | tr -d '\r\n')
REVISION=$(tar -xOf "$ARTIFACT" ./REVISION | tr -d '\r\n')
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && "$REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo INVALID_RELEASE_IDENTITY >&2; exit 66; }

getent group home-center >/dev/null || groupadd --system home-center
id home-center >/dev/null 2>&1 || useradd --system --gid home-center --home-dir /var/lib/home-center --shell /usr/sbin/nologin home-center
HOME_CENTER_GID=$(getent group home-center | cut -d: -f3)
install -d -m 0755 -o root -g root /opt/home-center/releases
install -d -m 0750 -o home-center -g home-center /var/lib/home-center /var/backups/home-center
install -d -m 0750 -o root -g home-center /etc/home-center /etc/home-center/secrets /etc/home-center/pki /etc/home-center/pki/web-ca
install -d -m 0750 -o root -g home-center /etc/home-center/pki/web /etc/home-center/pki/web/releases

TMP=$(mktemp -d /opt/home-center/releases/.install.XXXXXX)
trap 'rm -rf -- "$TMP"' EXIT
tar -xzf "$ARTIFACT" -C "$TMP"
(cd "$TMP" && sha256sum -c MANIFEST.sha256)
RELEASE="/opt/home-center/releases/${VERSION}-${REVISION:0:12}-${SHA256:0:12}"
[ ! -e "$RELEASE" ] || { echo RELEASE_ALREADY_EXISTS >&2; exit 73; }
mv -T "$TMP" "$RELEASE"
trap - EXIT
chown -R root:root "$RELEASE"
find "$RELEASE" -type d -exec chmod 0755 {} +
find "$RELEASE" -type f -exec chmod 0644 {} +
ln -sfn "$RELEASE" /opt/home-center/current

install -m 0640 -o root -g home-center "$CONFIG" /etc/home-center/config.json
install -m 0644 -o root -g home-center "$PEER_CERT" /etc/home-center/pki/node.crt
install -m 0640 -o root -g home-center "$PEER_KEY" /etc/home-center/pki/node.key
install -m 0644 -o root -g root "$CLUSTER_CA" /etc/home-center/pki/ca.crt
install -m 0644 -o root -g root "$WEB_CA" /etc/home-center/pki/web-ca/ca.crt

WEB_ID=${WEB_DER_SHA:0:24}
WEB_RELEASE="/etc/home-center/pki/web/releases/$WEB_ID"
[ ! -e "$WEB_RELEASE" ] || { echo WEB_IDENTITY_ALREADY_EXISTS >&2; exit 73; }
install -d -m 0750 -o root -g home-center "$WEB_RELEASE"
install -m 0644 -o root -g home-center "$WEB_CERT" "$WEB_RELEASE/tls.crt"
install -m 0640 -o root -g home-center "$WEB_KEY" "$WEB_RELEASE/tls.key"
WEB_LINK_TMP="/etc/home-center/pki/web/.current.$$.tmp"
ln -s "releases/$WEB_ID" "$WEB_LINK_TMP"
mv -Tf "$WEB_LINK_TMP" /etc/home-center/pki/web/current

[ -e /etc/home-center/secrets/session.key ] || { umask 077; openssl rand -hex 32 > /etc/home-center/secrets/session.key; }
[ -e /etc/home-center/secrets/audit.key ] || { umask 077; openssl rand -hex 32 > /etc/home-center/secrets/audit.key; }
chown root:home-center /etc/home-center/secrets/session.key /etc/home-center/secrets/audit.key
chmod 0640 /etc/home-center/secrets/session.key /etc/home-center/secrets/audit.key

for unit in "$RELEASE"/deploy/*.service "$RELEASE"/deploy/*.timer; do
  [ -e "$unit" ] || continue
  install -m 0644 -o root -g root "$unit" /etc/systemd/system/
done
systemctl daemon-reload
printf 'Home Center %s installed with separate Web and peer PKI.\n' "$VERSION"
printf 'Provision admin: sudo /usr/bin/python3 -I /opt/home-center/current/provision-local-admin.py\n'
printf 'Then start: sudo systemctl enable --now home-center.service home-center-backup.timer\n'
'''


CERTIFICATES = """# Certificate requirements\n\nHome Center Free keeps browser Web TLS and peer mTLS as independent identities. Do not reuse one private key for both roles.\n\n## Peer mTLS\n\nProvide `--peer-cert`, `--peer-key` and `--cluster-ca`. The peer certificate must contain DNS SAN `home-center-<node_name>` and the configured management IP SAN. The installer verifies the key pair and chain.\n\n## Browser Web TLS\n\nProvide `--web-cert`, `--web-key` and an independent `--web-ca`. The Web certificate must contain either the configured public hostname (when external access is enabled), the node name, or the management IP. The installer verifies the key pair and chain and installs the leaf into the immutable Web identity store used by Home Center.\n\nThe Web certificate and peer certificate must differ. The Web CA and cluster CA must also differ. Private keys are installed locally and are never part of the repository or public artifact.\n"""


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"FINALIZE_SOURCE_MARKER_REJECTED path={path}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()

    public_export.write_text(
        root / "product/control-plane/src/home_center/tls_status.py",
        PUBLIC_TLS_STATUS,
    )
    public_export.write_text(
        root / "deploy/scripts/install-public.sh",
        PUBLIC_INSTALLER,
        0o755,
    )
    public_export.write_text(root / "CERTIFICATES.md", CERTIFICATES)

    replace_once(
        root / "README.md",
        "prepare your node certificate/key plus peer and Web CAs, then run `deploy/scripts/install-public.sh`.",
        "prepare independent peer mTLS and browser Web TLS identities as described in `CERTIFICATES.md`, then run `deploy/scripts/install-public.sh`.",
    )

    public_export.scan_private(root)
    public_export.manifest(root)
    print("PUBLIC_EXPORT_FINALIZE=PASS separate_web_peer_pki=1")


if __name__ == "__main__":
    main()
