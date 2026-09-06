from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import ssl
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config

STATUS_FILE = Path("/var/lib/home-center/tls-maintenance-status.json")
WEB_ROOT = Path("/etc/home-center/pki/web")
WEB_RELEASES = WEB_ROOT / "releases"
WEB_CURRENT = WEB_ROOT / "current"
WEB_CERTIFICATE = WEB_CURRENT / "tls.crt"
WEB_PRIVATE_KEY = WEB_CURRENT / "tls.key"
CANDIDATE_CERTIFICATE = Path("/etc/home-center/pki/web/candidate/tls.crt")
CANDIDATE_PRIVATE_KEY = Path("/etc/home-center/pki/web/candidate/tls.key")
CANDIDATE_DIRECTORY = Path("/etc/home-center/pki/web/candidate")
CANDIDATE_OWNER = CANDIDATE_DIRECTORY / ".owner.json"
OPENSSL = "/usr/bin/openssl"
RENEWAL_DAYS = 30
WEB_LEAF_LIFETIME_DAYS = 397
WEB_CA_RENEWAL_DAYS = WEB_LEAF_LIFETIME_DAYS + RENEWAL_DAYS
CANONICAL_WEB_HOSTNAME = "dc01.hm.dm"
FUTURE_VIP_HOSTNAME = "home-center.hm.dm"
ROOT_UID = 0


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


def _certificate_profile(path: Path, *, scope: str) -> dict[str, Any]:
    result = subprocess.run(
        [OPENSSL, "x509", "-in", str(path), "-noout", "-text"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
        cwd="/",
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
    )
    text = result.stdout if result.returncode == 0 else ""
    if "Public Key Algorithm: id-ecPublicKey" in text:
        public_key_algorithm = "ECDSA"
    elif "Public Key Algorithm: ED25519" in text:
        public_key_algorithm = "Ed25519"
    elif "Public Key Algorithm: rsaEncryption" in text:
        public_key_algorithm = "RSA"
    else:
        public_key_algorithm = "unknown"
    if "ASN1 OID: prime256v1" in text:
        public_key_curve: str | None = "P-256"
    elif "ASN1 OID: secp384r1" in text:
        public_key_curve = "P-384"
    else:
        public_key_curve = None
    signature_algorithm = "unknown"
    for line in text.splitlines():
        if "Signature Algorithm:" in line:
            signature_algorithm = line.split("Signature Algorithm:", 1)[1].strip()
            break
    browser_algorithm_valid = (
        public_key_algorithm == "ECDSA"
        and public_key_curve == "P-256"
        and signature_algorithm == "ecdsa-with-SHA256"
    )
    browser_profile_valid = (
        browser_algorithm_valid
        and "CA:FALSE" in text
        and "TLS Web Server Authentication" in text
        and "TLS Web Client Authentication" not in text
    )
    browser_ca_profile_valid = browser_algorithm_valid and "CA:TRUE" in text and "Certificate Sign" in text
    peer_profile_valid = (
        public_key_algorithm == "Ed25519"
        and signature_algorithm == "ED25519"
        and "CA:FALSE" in text
        and "TLS Web Server Authentication" in text
        and "TLS Web Client Authentication" in text
    )
    if scope == "browser-web":
        profile = "ecdsa-p256-sha256" if browser_profile_valid else "unsupported-for-browser-web"
        profile_valid = browser_profile_valid
    elif scope == "browser-web-ca":
        profile = "ecdsa-p256-sha256-ca" if browser_ca_profile_valid else "unsupported-for-browser-web-ca"
        profile_valid = browser_ca_profile_valid
    else:
        profile = "ed25519-peer-mtls" if peer_profile_valid else "unexpected-peer-profile"
        profile_valid = peer_profile_valid
    return {
        "profile": profile,
        "profile_valid": profile_valid,
        "public_key_algorithm": public_key_algorithm,
        "public_key_curve": public_key_curve,
        "signature_algorithm": signature_algorithm,
    }


def _certificate(
    path: Path,
    ca: Path,
    expected_hostname: str,
    *,
    mode: str,
    profile_scope: str = "browser-web",
    required_dns: tuple[str, ...] = (),
    required_ips: tuple[str, ...] = (),
    exact_sans: bool = False,
) -> dict[str, Any]:
    decoded = _decode(path)
    sans = decoded.get("subjectAltName", ())
    dns = sorted(value for kind, value in sans if kind == "DNS")
    ips = sorted(value for kind, value in sans if kind in {"IP Address", "IP"})
    not_before, _ = _parse_time(decoded.get("notBefore"))
    not_after, remaining = _parse_time(decoded.get("notAfter"))
    if exact_sans:
        san_policy_valid = set(dns) == set(required_dns) and set(ips) == set(required_ips)
    else:
        san_policy_valid = all(name in dns for name in required_dns) and all(address in ips for address in required_ips)
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
        "required_san_dns": list(required_dns),
        "required_san_ip": list(required_ips),
        "san_policy_valid": san_policy_valid,
        **_certificate_profile(path, scope=profile_scope),
    }


def _maintenance_status() -> dict[str, Any] | None:
    try:
        value = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _candidate_entry(path: Path, *, mode: int, gid: int) -> tuple[bool, bool]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False, True
    valid = (
        stat.S_ISREG(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and info.st_uid == ROOT_UID
        and info.st_gid == gid
        and stat.S_IMODE(info.st_mode) == mode
    )
    return True, valid


def _candidate_state() -> dict[str, bool]:
    try:
        home_center_gid = pwd.getpwnam("home-center").pw_gid
    except KeyError:
        # Unit tests and source-only tooling may run before the package creates
        # the service account. A deployed service cannot start without it.
        home_center_gid = os.getgid()
    try:
        directory = CANDIDATE_DIRECTORY.lstat()
    except FileNotFoundError:
        directory_present = False
        directory_valid = True
    else:
        directory_present = True
        directory_valid = (
            stat.S_ISDIR(directory.st_mode)
            and not stat.S_ISLNK(directory.st_mode)
            and directory.st_uid == ROOT_UID
            and directory.st_gid == home_center_gid
            and stat.S_IMODE(directory.st_mode) == 0o750
        )
    entry_names: set[str] = set()
    directory_readable = True
    if directory_present and directory_valid:
        try:
            entry_names = {entry.name for entry in CANDIDATE_DIRECTORY.iterdir()}
        except OSError:
            directory_readable = False
    pending_present = any(
        re.fullmatch(r"\.(?:tls\.(?:crt|key)|owner)\.[0-9a-f]{32}\.pending", name) is not None
        for name in entry_names
    )
    allowed_names = {CANDIDATE_CERTIFICATE.name, CANDIDATE_PRIVATE_KEY.name, CANDIDATE_OWNER.name}
    unexpected_entry = any(
        name not in allowed_names
        and re.fullmatch(r"\.(?:tls\.(?:crt|key)|owner)\.[0-9a-f]{32}\.pending", name) is None
        for name in entry_names
    )
    certificate_present, certificate_valid = _candidate_entry(
        CANDIDATE_CERTIFICATE, mode=0o644, gid=home_center_gid
    )
    private_key_present, private_key_valid = _candidate_entry(CANDIDATE_PRIVATE_KEY, mode=0o600, gid=0)
    owner_present, owner_valid = _candidate_entry(CANDIDATE_OWNER, mode=0o600, gid=0)
    invalid = (
        not directory_valid
        or not directory_readable
        or not certificate_valid
        or not private_key_valid
        or not owner_valid
        or unexpected_entry
    )
    complete = certificate_present and private_key_present and owner_present and not invalid and not pending_present
    anything_present = certificate_present or private_key_present or owner_present or bool(entry_names)
    return {
        "certificate_present": certificate_present,
        "private_key_present": private_key_present,
        "owner_marker_present": owner_present,
        "complete": complete,
        "partial": invalid or (anything_present and not complete),
        "invalid": invalid,
    }


def _separate_web_identity_present() -> bool:
    if not (WEB_CURRENT.exists() or WEB_CURRENT.is_symlink()):
        return False
    if not WEB_CURRENT.is_symlink():
        raise RuntimeError("web_current_must_be_symlink")
    try:
        release = WEB_CURRENT.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("web_current_dangling") from exc
    if release.parent != WEB_RELEASES or len(release.name) != 24 or any(character not in "0123456789abcdef" for character in release.name):
        raise RuntimeError("web_current_outside_release_root")
    release_info = release.lstat()
    try:
        home_center_gid = pwd.getpwnam("home-center").pw_gid
    except KeyError:
        home_center_gid = os.getgid()
    if (
        not stat.S_ISDIR(release_info.st_mode)
        or stat.S_ISLNK(release_info.st_mode)
        or release_info.st_uid != ROOT_UID
        or release_info.st_gid != home_center_gid
        or stat.S_IMODE(release_info.st_mode) != 0o750
    ):
        raise RuntimeError("web_current_release_metadata_rejected")
    for path, mode in ((WEB_CERTIFICATE, 0o644), (WEB_PRIVATE_KEY, 0o640)):
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            raise RuntimeError("web_identity_incomplete") from exc
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != ROOT_UID
            or info.st_gid != home_center_gid
            or stat.S_IMODE(info.st_mode) != mode
        ):
            raise RuntimeError("web_identity_metadata_rejected")
    try:
        fingerprint = _fingerprint(WEB_CERTIFICATE)
    except (OSError, UnicodeDecodeError, ValueError, ssl.SSLError) as exc:
        raise RuntimeError("web_certificate_rejected") from exc
    if release.name != fingerprint[:24]:
        raise RuntimeError("web_release_identity_mismatch")
    return True


def web_trust_anchor(config: Config) -> Path:
    """Return the CA for the certificate the Web listener is actually serving."""
    separate = _separate_web_identity_present()
    if not separate:
        return config.cluster_ca
    if _chain_valid(WEB_CERTIFICATE, config.web_ca):
        return config.web_ca
    if _chain_valid(WEB_CERTIFICATE, config.cluster_ca):
        return config.cluster_ca
    # Preserve fail-closed behavior for an unknown chain: status will report
    # chain_valid=false against the managed Web anchor.
    return config.web_ca


def status(config: Config) -> dict[str, Any]:
    web_hostname = f"{config.node_name}.hm.dm"
    separate = _separate_web_identity_present()
    web_cert = WEB_CERTIFICATE if separate else config.tls_certificate
    web_ca = web_trust_anchor(config)
    web_mode = (
        "legacy-peer-fallback"
        if not separate
        else "separate-web-identity"
        if web_ca == config.web_ca
        else "quarantined-peer-web-identity"
    )
    ca = _certificate(web_ca, web_ca, CANONICAL_WEB_HOSTNAME, mode="served-web-trust-anchor")
    managed_web_ca = _certificate(
        config.web_ca,
        config.web_ca,
        CANONICAL_WEB_HOSTNAME,
        mode="managed-web-ca",
        profile_scope="browser-web-ca",
    )
    web = _certificate(
        web_cert,
        web_ca,
        web_hostname,
        mode=web_mode,
        profile_scope="browser-web",
        required_dns=(web_hostname, FUTURE_VIP_HOSTNAME),
        required_ips=(config.management_address,),
        exact_sans=True,
    )
    peer = _certificate(
        config.tls_certificate,
        config.cluster_ca,
        f"home-center-{config.node_name}",
        mode="peer-mtls-identity",
        profile_scope="peer-mtls",
        required_dns=(f"home-center-{config.node_name}",),
        required_ips=(config.management_address,),
    )
    days_remaining = web.get("days_remaining")
    web_ca_days_remaining = managed_web_ca.get("days_remaining")
    candidate = _candidate_state()
    maintenance = _maintenance_status()
    maintenance_status = maintenance.get("status") if isinstance(maintenance, dict) else None
    maintenance_reason = maintenance.get("reason") if isinstance(maintenance, dict) else None
    if candidate["partial"] or candidate["invalid"] or maintenance_status in {"unknown", "blocked"}:
        operational_state = "recovery_required"
        operational_reason = (
            "candidate_state_invalid"
            if candidate["invalid"]
            else "candidate_bundle_incomplete"
            if candidate["partial"]
            else str(maintenance_reason or "maintenance_recovery_required")
        )
    elif maintenance_status in {"failed", "rolled_back"}:
        operational_state = "degraded"
        operational_reason = str(maintenance_reason or "maintenance_action_failed")
    else:
        operational_state = "ready"
        operational_reason = None
    renewal_due = (
        web_mode != "separate-web-identity"
        or not bool(web.get("chain_valid"))
        or not bool(web.get("hostname_match"))
        or not bool(web.get("profile_valid"))
        or not bool(web.get("san_policy_valid"))
        or days_remaining is None
        or int(days_remaining) <= RENEWAL_DAYS
        or not bool(managed_web_ca.get("chain_valid"))
        or not bool(managed_web_ca.get("profile_valid"))
        or web_ca_days_remaining is None
        or int(web_ca_days_remaining) <= WEB_CA_RENEWAL_DAYS
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
        "web_ca": {
            "fingerprint_sha256": managed_web_ca["fingerprint_sha256"],
            "subject": managed_web_ca["subject"],
            "not_after": managed_web_ca["not_after"],
            "days_remaining": managed_web_ca["days_remaining"],
            "profile": managed_web_ca["profile"],
            "profile_valid": managed_web_ca["profile_valid"],
        },
        "trust_anchor": {
            "fingerprint_sha256": ca["fingerprint_sha256"],
            "subject": ca["subject"],
            "not_after": ca["not_after"],
        },
        "candidate": candidate,
        "operational": {
            "state": operational_state,
            "healthy": operational_state == "ready",
            "recovery_required": operational_state == "recovery_required",
            "reason": operational_reason,
        },
        "renewal": {
            "threshold_days": RENEWAL_DAYS,
            "web_ca_threshold_days": WEB_CA_RENEWAL_DAYS,
            "due": renewal_due,
            "last_maintenance": maintenance,
        },
    }
