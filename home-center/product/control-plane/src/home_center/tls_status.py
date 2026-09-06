from __future__ import annotations

import hashlib
import json
import ssl
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config

STATUS_FILE = Path("/var/lib/home-center/tls-maintenance-status.json")
WEB_CERTIFICATE = Path("/etc/home-center/pki/web/current/tls.crt")
WEB_PRIVATE_KEY = Path("/etc/home-center/pki/web/current/tls.key")
CANDIDATE_CERTIFICATE = Path("/etc/home-center/pki/web/candidate/tls.crt")
CANDIDATE_PRIVATE_KEY = Path("/etc/home-center/pki/web/candidate/tls.key")
OPENSSL = "/usr/bin/openssl"
RENEWAL_DAYS = 30
CANONICAL_WEB_HOSTNAME = "dc01.hm.dm"


def _decode(path: Path) -> dict[str, Any]:
    return ssl._ssl._test_decode_cert(str(path))  # type: ignore[attr-defined]


def _fingerprint(path: Path) -> str:
    pem = path.read_text(encoding="ascii")
    der = ssl.PEM_cert_to_DER_cert(pem)
    return hashlib.sha256(der).hexdigest()


def _name(parts: Any) -> str:
    values: list[str] = []
    for rdn in parts or ():
        for key, value in rdn:
            values.append(f"{key}={value}")
    return ",".join(values)


def _parse_time(value: str | None) -> tuple[str | None, int | None]:
    if not value:
        return None, None
    epoch = ssl.cert_time_to_seconds(value)
    when = datetime.fromtimestamp(epoch, timezone.utc)
    remaining = int((when - datetime.now(timezone.utc)).total_seconds() // 86400)
    return when.isoformat(timespec="seconds").replace("+00:00", "Z"), remaining


def _chain_valid(cert: Path, ca: Path) -> bool:
    result = subprocess.run(
        [OPENSSL, "verify", "-x509_strict", "-CAfile", str(ca), str(cert)],
        check=False,
        capture_output=True,
        text=False,
        timeout=5,
        cwd="/",
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
    )
    return result.returncode == 0


def _certificate(path: Path, ca: Path, expected_hostname: str, *, mode: str) -> dict[str, Any]:
    decoded = _decode(path)
    sans = decoded.get("subjectAltName", ())
    dns = sorted(value for kind, value in sans if kind == "DNS")
    ips = sorted(value for kind, value in sans if kind in {"IP Address", "IP"})
    not_before, _ = _parse_time(decoded.get("notBefore"))
    not_after, remaining = _parse_time(decoded.get("notAfter"))
    return {
        "mode": mode,
        "fingerprint_sha256": _fingerprint(path),
        "subject": _name(decoded.get("subject")),
        "issuer": _name(decoded.get("issuer")),
        "serial_number": decoded.get("serialNumber"),
        "not_before": not_before,
        "not_after": not_after,
        "days_remaining": remaining,
        "san_dns": dns,
        "san_ip": ips,
        "expected_hostname": expected_hostname,
        "hostname_match": expected_hostname in dns,
        "chain_valid": _chain_valid(path, ca),
    }


def _maintenance_status() -> dict[str, Any] | None:
    try:
        value = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _candidate_state() -> dict[str, bool]:
    certificate_present = CANDIDATE_CERTIFICATE.is_file()
    private_key_present = CANDIDATE_PRIVATE_KEY.is_file()
    return {
        "certificate_present": certificate_present,
        "private_key_present": private_key_present,
        "complete": certificate_present and private_key_present,
        "partial": certificate_present != private_key_present,
    }


def status(config: Config) -> dict[str, Any]:
    web_hostname = f"{config.node_name}.hm.dm"
    separate = WEB_CERTIFICATE.is_file() and WEB_PRIVATE_KEY.is_file()
    web_cert = WEB_CERTIFICATE if separate else config.tls_certificate
    ca = _certificate(config.cluster_ca, config.cluster_ca, CANONICAL_WEB_HOSTNAME, mode="trust-anchor")
    web = _certificate(
        web_cert,
        config.cluster_ca,
        web_hostname,
        mode="separate-web-identity" if separate else "legacy-peer-fallback",
    )
    peer = _certificate(
        config.tls_certificate,
        config.cluster_ca,
        f"home-center-{config.node_name}",
        mode="peer-mtls-identity",
    )
    days_remaining = web.get("days_remaining")
    renewal_due = (
        not separate
        or not bool(web.get("chain_valid"))
        or not bool(web.get("hostname_match"))
        or days_remaining is None
        or int(days_remaining) <= RENEWAL_DAYS
    )
    return {
        "schema": "home-center.tls-status.v1",
        "node_id": config.node_id,
        "node_name": config.node_name,
        "web_hostname": web_hostname,
        "canonical_web_hostname": CANONICAL_WEB_HOSTNAME,
        "web_url": f"https://{web_hostname}:{config.web_port}",
        "web": web,
        "peer": peer,
        "trust_anchor": {
            "fingerprint_sha256": ca["fingerprint_sha256"],
            "subject": ca["subject"],
            "not_after": ca["not_after"],
        },
        "candidate": _candidate_state(),
        "renewal": {
            "threshold_days": RENEWAL_DAYS,
            "due": renewal_due,
            "last_maintenance": _maintenance_status(),
        },
    }
