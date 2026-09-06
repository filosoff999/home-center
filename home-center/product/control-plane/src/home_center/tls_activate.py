from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import pwd
import re
import shutil
import socket
import ssl
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

OPENSSL = "/usr/bin/openssl"
SYSTEMCTL = "/usr/bin/systemctl"
CONFIG = Path("/etc/home-center/config.json")
PKI = Path("/etc/home-center/pki")
CA_CERT = PKI / "ca.crt"
WEB_ROOT = PKI / "web"
WEB_RELEASES = WEB_ROOT / "releases"
WEB_CURRENT = WEB_ROOT / "current"
CANDIDATE = WEB_ROOT / "candidate"
CANDIDATE_CERT = CANDIDATE / "tls.crt"
CANDIDATE_KEY = CANDIDATE / "tls.key"
SAFE_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "HOME": "/nonexistent"}
NODE_SPECS = {
    "dc01": {"node_id": "hm-dm-dc01", "role": "leader", "ip": "192.168.10.254", "fqdn": "dc01.hm.dm"},
    "dc02": {"node_id": "hm-dm-dc02", "role": "standby", "ip": "192.168.10.253", "fqdn": "dc02.hm.dm"},
}
FUTURE_VIP_HOSTNAME = "home-center.hm.dm"
MIN_VALIDITY_SECONDS = 30 * 86400


class ActivationError(RuntimeError):
    pass


def _run(argv: list[str], *, input_bytes: bytes | None = None, timeout: int = 15) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        argv,
        input=input_bytes,
        check=False,
        capture_output=True,
        text=False,
        timeout=timeout,
        env=SAFE_ENV,
        cwd="/",
    )
    if completed.returncode != 0:
        raise ActivationError("validation_command_failed")
    return completed


def _spec() -> dict[str, str]:
    raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != "home-center.config.v1":
        raise ActivationError("invalid_config")
    name = raw.get("node_name")
    if name not in NODE_SPECS:
        raise ActivationError("unsupported_node")
    spec = dict(NODE_SPECS[str(name)])
    if raw.get("node_id") != spec["node_id"] or raw.get("role") != spec["role"] or raw.get("management_address") != spec["ip"]:
        raise ActivationError("node_identity_mismatch")
    legacy_certificate = raw.get("tls_certificate")
    if not isinstance(legacy_certificate, str) or not Path(legacy_certificate).is_absolute():
        raise ActivationError("legacy_certificate_path_rejected")
    spec["legacy_tls_certificate"] = legacy_certificate
    return spec


def _regular_secure(path: Path, *, private: bool) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ActivationError("candidate_type_rejected")
    if info.st_uid != 0:
        raise ActivationError("candidate_owner_rejected")
    forbidden = 0o027 if private else 0o022
    if info.st_mode & forbidden:
        raise ActivationError("candidate_permissions_rejected")
    if info.st_size < 64 or info.st_size > 64 * 1024:
        raise ActivationError("candidate_size_rejected")


def _fingerprint(cert: Path) -> str:
    pem = cert.read_text(encoding="ascii")
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def _key_public(key: Path) -> bytes:
    return _run([OPENSSL, "pkey", "-in", str(key), "-pubout", "-outform", "DER"]).stdout


def _cert_public(cert: Path) -> bytes:
    pem = _run([OPENSSL, "x509", "-in", str(cert), "-pubkey", "-noout"]).stdout
    return _run([OPENSSL, "pkey", "-pubin", "-outform", "DER"], input_bytes=pem).stdout


def _san_identities(cert: Path) -> tuple[frozenset[str], frozenset[str]]:
    """Return exact DNS/IP SAN identities without trusting x509 -check* exit codes."""
    output = _run([OPENSSL, "x509", "-in", str(cert), "-noout", "-ext", "subjectAltName"]).stdout.decode(
        "utf-8", errors="strict"
    )
    dns_names = frozenset(value.rstrip(".").lower() for value in re.findall(r"\bDNS:([^,\s]+)", output))
    ip_values: set[str] = set()
    for value in re.findall(r"\bIP Address:([^,\s]+)", output):
        try:
            ip_values.add(str(ipaddress.ip_address(value)))
        except ValueError as exc:
            raise ActivationError("invalid_ip_san") from exc
    if not dns_names and not ip_values:
        raise ActivationError("subject_alt_name_missing")
    return dns_names, frozenset(ip_values)


def _require_expected_identities(spec: dict[str, str], cert: Path) -> None:
    dns_names, ip_values = _san_identities(cert)
    if spec["fqdn"].rstrip(".").lower() not in dns_names:
        raise ActivationError("node_hostname_mismatch")
    if FUTURE_VIP_HOSTNAME.rstrip(".").lower() not in dns_names:
        raise ActivationError("vip_hostname_mismatch")
    try:
        expected_ip = str(ipaddress.ip_address(spec["ip"]))
    except ValueError as exc:
        raise ActivationError("invalid_expected_ip") from exc
    if expected_ip not in ip_values:
        raise ActivationError("node_ip_mismatch")


def validate_candidate(spec: dict[str, str], cert: Path = CANDIDATE_CERT, key: Path = CANDIDATE_KEY) -> str:
    _regular_secure(cert, private=False)
    _regular_secure(key, private=True)
    _run([OPENSSL, "verify", "-x509_strict", "-CAfile", str(CA_CERT), str(cert)])
    _run([OPENSSL, "x509", "-in", str(cert), "-noout", "-checkend", str(MIN_VALIDITY_SECONDS)])
    _require_expected_identities(spec, cert)
    _run([OPENSSL, "x509", "-in", str(cert), "-noout", "-checkhost", spec["fqdn"]])
    _run([OPENSSL, "x509", "-in", str(cert), "-noout", "-checkhost", FUTURE_VIP_HOSTNAME])
    _run([OPENSSL, "x509", "-in", str(cert), "-noout", "-checkip", spec["ip"]])
    text = _run([OPENSSL, "x509", "-in", str(cert), "-noout", "-text"]).stdout.decode("utf-8", errors="replace")
    if "CA:FALSE" not in text or "TLS Web Server Authentication" not in text:
        raise ActivationError("web_profile_rejected")
    if "TLS Web Client Authentication" in text:
        raise ActivationError("client_identity_rejected")
    if _key_public(key) != _cert_public(cert):
        raise ActivationError("key_mismatch")
    return _fingerprint(cert)


def _group(path: Path, mode: int) -> None:
    account = pwd.getpwnam("home-center")
    os.chown(path, 0, account.pw_gid)
    os.chmod(path, mode)


def _prepare_release(fingerprint: str, spec: dict[str, str]) -> Path:
    WEB_RELEASES.mkdir(parents=True, exist_ok=True)
    _group(WEB_RELEASES, 0o750)
    release = WEB_RELEASES / fingerprint[:24]
    if release.exists():
        if not release.is_dir():
            raise ActivationError("release_path_rejected")
    else:
        release.mkdir(mode=0o750)
        _group(release, 0o750)
        shutil.copyfile(CANDIDATE_CERT, release / "tls.crt")
        shutil.copyfile(CANDIDATE_KEY, release / "tls.key")
        _group(release / "tls.crt", 0o644)
        _group(release / "tls.key", 0o640)
    release_fingerprint = validate_candidate(spec, release / "tls.crt", release / "tls.key")
    if release_fingerprint != fingerprint:
        raise ActivationError("release_fingerprint_mismatch")
    return release


def _clear_candidate() -> None:
    for path in (CANDIDATE_KEY, CANDIDATE_CERT):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _current_release() -> Path | None:
    if not (WEB_CURRENT.exists() or WEB_CURRENT.is_symlink()):
        return None
    if not WEB_CURRENT.is_symlink():
        raise ActivationError("web_current_not_symlink")
    current = WEB_CURRENT.resolve(strict=True)
    if WEB_RELEASES not in current.parents:
        raise ActivationError("web_current_outside_release_root")
    return current


def _point_current(release: Path | None, suffix: str) -> None:
    pending = WEB_ROOT / f".current.{suffix}"
    try:
        pending.unlink()
    except FileNotFoundError:
        pass
    if release is None:
        try:
            WEB_CURRENT.unlink()
        except FileNotFoundError:
            pass
        return
    os.symlink(str(release), pending)
    os.replace(pending, WEB_CURRENT)


def _restart() -> None:
    _run([SYSTEMCTL, "restart", "home-center.service"], timeout=30)


def _presented_fingerprint(spec: dict[str, str], expected: str) -> None:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(CA_CERT))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((spec["ip"], 8443), timeout=3) as raw:
                with context.wrap_socket(raw, server_hostname=spec["fqdn"]) as tls:
                    if hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest() != expected:
                        raise ActivationError("presented_certificate_mismatch")
                    return
        except (OSError, ssl.SSLError, ActivationError):
            time.sleep(1)
    raise ActivationError("https_health_failed")


def _rollback_fingerprint(spec: dict[str, str], previous: Path | None) -> str:
    certificate = previous / "tls.crt" if previous is not None else Path(spec["legacy_tls_certificate"])
    return _fingerprint(certificate)


def activate() -> dict[str, Any]:
    spec = _spec()
    fingerprint = validate_candidate(spec)
    previous = _current_release()
    release = _prepare_release(fingerprint, spec)
    _clear_candidate()
    _point_current(release, "new")
    try:
        _restart()
        _presented_fingerprint(spec, fingerprint)
    except Exception as activation_error:
        try:
            _point_current(previous, "rollback")
            _restart()
            _presented_fingerprint(spec, _rollback_fingerprint(spec, previous))
        except Exception as rollback_error:
            raise ActivationError("rollback_failed") from rollback_error
        raise ActivationError("activation_rolled_back") from activation_error
    return {
        "schema": "home-center.tls-activation-result.v1",
        "status": "activated",
        "node_id": spec["node_id"],
        "web_hostname": spec["fqdn"],
        "future_vip_hostname": FUTURE_VIP_HOSTNAME,
        "certificate_sha256": fingerprint,
        "release": release.name,
        "previous_release": previous.name if previous else None,
    }


def main() -> int:
    try:
        result = activate()
    except (ActivationError, OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError, ValueError, UnicodeDecodeError):
        print('{"schema":"home-center.tls-activation-result.v1","status":"failed"}')
        return 1
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
