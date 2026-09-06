from __future__ import annotations

import fcntl
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
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

OPENSSL = "/usr/bin/openssl"
SYSTEMCTL = "/usr/bin/systemctl"
CONFIG = Path("/etc/home-center/config.json")
PKI = Path("/etc/home-center/pki")
PEER_CA_CERT = PKI / "ca.crt"
WEB_CA_CERT = PKI / "web-ca" / "ca.crt"
WEB_ROOT = PKI / "web"
WEB_RELEASES = WEB_ROOT / "releases"
WEB_CURRENT = WEB_ROOT / "current"
CANDIDATE = WEB_ROOT / "candidate"
CANDIDATE_CERT = CANDIDATE / "tls.crt"
CANDIDATE_KEY = CANDIDATE / "tls.key"
CANDIDATE_OWNER = CANDIDATE / ".owner.json"
CANDIDATE_OWNER_SCHEMA = "home-center.web-candidate-owner.v1"
MUTATION_LOCK_DIR = Path("/run/home-center-locks")
MUTATION_LOCK = MUTATION_LOCK_DIR / "node-mutation.lock"
ROOT_UID = 0
SAFE_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "HOME": "/nonexistent"}
NODE_SPECS = {
    "dc01": {"node_id": "hm-dm-dc01", "role": "leader", "ip": "192.168.10.254", "fqdn": "dc01.hm.dm"},
    "dc02": {"node_id": "hm-dm-dc02", "role": "standby", "ip": "192.168.10.253", "fqdn": "dc02.hm.dm"},
}
FUTURE_VIP_HOSTNAME = "home-center.hm.dm"
MIN_VALIDITY_SECONDS = 30 * 86400
WEB_CA_MIN_VALIDITY_SECONDS = (397 + 30) * 86400
RESTART_TIMEOUT_SECONDS = 30
PRESENTED_HEALTH_TIMEOUT_SECONDS = 25
OPENSSL_TIMEOUT_SECONDS = 5
ACTIVATION_TRANSACTION_SECONDS = 330
ACTIVATION_ROLLBACK_RESERVE_SECONDS = 130
ACTIVATION_HELPER_TIMEOUT_SECONDS = 360


class ActivationError(RuntimeError):
    pass


@contextmanager
def _mutation_lock() -> Iterator[None]:
    """Serialize all node-local software and Web-certificate mutations."""
    try:
        MUTATION_LOCK_DIR.mkdir(mode=0o700)
    except FileExistsError:
        pass
    directory = MUTATION_LOCK_DIR.lstat()
    if not stat.S_ISDIR(directory.st_mode) or stat.S_ISLNK(directory.st_mode):
        raise ActivationError("mutation_lock_directory_rejected")
    if directory.st_uid != ROOT_UID or stat.S_IMODE(directory.st_mode) != 0o700:
        raise ActivationError("mutation_lock_directory_rejected")
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(MUTATION_LOCK, flags, 0o600)
    except OSError as exc:
        raise ActivationError("mutation_lock_open_failed") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != ROOT_UID or stat.S_IMODE(info.st_mode) != 0o600:
            raise ActivationError("mutation_lock_file_rejected")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ActivationError("mutation_in_progress") from exc
        yield
    finally:
        os.close(descriptor)


def _run(
    argv: list[str],
    *,
    input_bytes: bytes | None = None,
    timeout: int = OPENSSL_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[bytes]:
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
    if raw.get("cluster_ca") != str(PEER_CA_CERT) or raw.get("web_ca") != str(WEB_CA_CERT):
        raise ActivationError("trust_anchor_path_rejected")
    if PEER_CA_CERT == WEB_CA_CERT:
        raise ActivationError("trust_anchors_must_differ")
    return spec


def _regular_secure(path: Path, *, private: bool) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ActivationError("candidate_type_rejected")
    if info.st_uid != ROOT_UID:
        raise ActivationError("candidate_owner_rejected")
    forbidden = 0o027 if private else 0o022
    if info.st_mode & forbidden:
        raise ActivationError("candidate_permissions_rejected")
    if info.st_size < 64 or info.st_size > 64 * 1024:
        raise ActivationError("candidate_size_rejected")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(64 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _candidate_directory_secure() -> None:
    info = CANDIDATE.lstat()
    account = pwd.getpwnam("home-center")
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ActivationError("candidate_directory_rejected")
    if info.st_uid != ROOT_UID or info.st_gid != account.pw_gid or stat.S_IMODE(info.st_mode) != 0o750:
        raise ActivationError("candidate_directory_rejected")


def _candidate_owner(*, allow_incomplete: bool = False) -> dict[str, str]:
    """Validate the durable ownership proof written before candidate staging.

    The marker is fsynced before either credential file is created.  Therefore a
    later process may remove a matching partial bundle without guessing whether
    the files belong to an interrupted Home Center operation.
    """
    _candidate_directory_secure()
    info = CANDIDATE_OWNER.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != ROOT_UID
        or info.st_gid != 0
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size < 128
        or info.st_size > 2048
    ):
        raise ActivationError("candidate_owner_marker_rejected")
    try:
        value = json.loads(CANDIDATE_OWNER.read_text(encoding="ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActivationError("candidate_owner_marker_rejected") from exc
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "operation_id",
        "certificate_file_sha256",
        "private_key_file_sha256",
    }:
        raise ActivationError("candidate_owner_marker_rejected")
    if value.get("schema") != CANDIDATE_OWNER_SCHEMA:
        raise ActivationError("candidate_owner_marker_rejected")
    operation_id = value.get("operation_id")
    certificate_sha256 = value.get("certificate_file_sha256")
    private_key_sha256 = value.get("private_key_file_sha256")
    if not isinstance(operation_id, str) or re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
        raise ActivationError("candidate_owner_marker_rejected")
    if not isinstance(certificate_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", certificate_sha256) is None:
        raise ActivationError("candidate_owner_marker_rejected")
    if not isinstance(private_key_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", private_key_sha256) is None:
        raise ActivationError("candidate_owner_marker_rejected")
    paths = (
        (CANDIDATE_CERT, certificate_sha256, False),
        (CANDIDATE_KEY, private_key_sha256, True),
    )
    pending_paths = _candidate_pending_paths(
        {
            "operation_id": operation_id,
            "certificate_file_sha256": certificate_sha256,
            "private_key_file_sha256": private_key_sha256,
            "schema": CANDIDATE_OWNER_SCHEMA,
        }
    )
    allowed_entries = {CANDIDATE_OWNER.name, CANDIDATE_CERT.name, CANDIDATE_KEY.name}
    allowed_entries.update(path.name for path in pending_paths)
    if any(entry.name not in allowed_entries for entry in CANDIDATE.iterdir()):
        raise ActivationError("candidate_contents_rejected")
    for path, expected, private in paths:
        try:
            _regular_secure(path, private=private)
        except FileNotFoundError:
            if allow_incomplete:
                continue
            raise ActivationError("candidate_bundle_incomplete")
        if _sha256_file(path) != expected:
            raise ActivationError("candidate_owner_digest_mismatch")
    owner = {
        "schema": CANDIDATE_OWNER_SCHEMA,
        "operation_id": operation_id,
        "certificate_file_sha256": certificate_sha256,
        "private_key_file_sha256": private_key_sha256,
    }
    if not allow_incomplete:
        for pending in pending_paths:
            try:
                pending.lstat()
            except FileNotFoundError:
                continue
            raise ActivationError("candidate_pending_file_rejected")
    return owner


def _candidate_pending_paths(owner: dict[str, str]) -> tuple[Path, Path]:
    operation_id = owner["operation_id"]
    return (
        CANDIDATE / f".tls.crt.{operation_id}.pending",
        CANDIDATE / f".tls.key.{operation_id}.pending",
    )


def _recover_pending_candidate_owner(expected_owner: dict[str, str] | None = None) -> bool:
    """Remove an atomically staged owner marker only after exact validation."""
    _candidate_directory_secure()
    pending = [
        entry
        for entry in CANDIDATE.iterdir()
        if re.fullmatch(r"\.owner\.[0-9a-f]{32}\.pending", entry.name) is not None
    ]
    if not pending:
        return False
    if len(pending) != 1 or {entry.name for entry in CANDIDATE.iterdir()} != {pending[0].name}:
        raise ActivationError("candidate_owner_pending_state_rejected")
    path = pending[0]
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != ROOT_UID
        or info.st_gid != 0
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size < 128
        or info.st_size > 2048
    ):
        raise ActivationError("candidate_owner_pending_rejected")
    try:
        owner = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActivationError("candidate_owner_pending_rejected") from exc
    if not isinstance(owner, dict) or set(owner) != {
        "schema",
        "operation_id",
        "certificate_file_sha256",
        "private_key_file_sha256",
    }:
        raise ActivationError("candidate_owner_pending_rejected")
    if (
        owner.get("schema") != CANDIDATE_OWNER_SCHEMA
        or not isinstance(owner.get("operation_id"), str)
        or re.fullmatch(r"[0-9a-f]{32}", owner["operation_id"]) is None
        or path.name != f".owner.{owner['operation_id']}.pending"
        or not isinstance(owner.get("certificate_file_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", owner["certificate_file_sha256"]) is None
        or not isinstance(owner.get("private_key_file_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", owner["private_key_file_sha256"]) is None
        or (expected_owner is not None and owner != expected_owner)
    ):
        raise ActivationError("candidate_owner_pending_rejected")
    path.unlink()
    _fsync_directory(CANDIDATE)
    return True


def _recover_owned_candidate() -> bool:
    """Clear only a marker-proven interrupted candidate bundle.

    Missing credential files are expected after a crash during staging or
    cleanup.  Any present file must still match the precommitted digest.
    """
    try:
        owner = _candidate_owner(allow_incomplete=True)
    except FileNotFoundError:
        return False
    account = pwd.getpwnam("home-center")
    for path, expected_mode, expected_gid in zip(
        _candidate_pending_paths(owner),
        (0o644, 0o600),
        (account.pw_gid, 0),
        strict=True,
    ):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != ROOT_UID
            or info.st_gid != expected_gid
            or stat.S_IMODE(info.st_mode) != expected_mode
        ):
            raise ActivationError("candidate_pending_file_rejected")
        path.unlink()
    for path in (CANDIDATE_KEY, CANDIDATE_CERT):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    _fsync_directory(CANDIDATE)
    CANDIDATE_OWNER.unlink()
    _fsync_directory(CANDIDATE)
    return True


def _fingerprint(cert: Path) -> str:
    pem = cert.read_text(encoding="ascii")
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def _key_public(key: Path) -> bytes:
    return _run([OPENSSL, "pkey", "-in", str(key), "-pubout", "-outform", "DER"]).stdout


def _cert_public(cert: Path) -> bytes:
    pem = _run([OPENSSL, "x509", "-in", str(cert), "-pubkey", "-noout"]).stdout
    return _run([OPENSSL, "pkey", "-pubin", "-outform", "DER"], input_bytes=pem).stdout


def _require_browser_compatible_certificate(cert: Path, *, authority: bool) -> None:
    """Require an ECDSA P-256/SHA-256 Web chain compatible with managed browsers.

    Peer mTLS deliberately remains on the independent Ed25519 cluster PKI.  The Web
    listener cannot use that identity because some Android/Chrome ClientHello paths
    do not advertise Ed25519.  Both the Web leaf and its trust anchor are therefore
    constrained here, rather than relying only on the issuance script.
    """
    text = _run([OPENSSL, "x509", "-in", str(cert), "-noout", "-text"]).stdout.decode(
        "utf-8", errors="replace"
    )
    if "Public Key Algorithm: id-ecPublicKey" not in text or "ASN1 OID: prime256v1" not in text:
        raise ActivationError("web_ca_algorithm_rejected" if authority else "web_algorithm_rejected")
    if "Signature Algorithm: ecdsa-with-SHA256" not in text:
        raise ActivationError("web_ca_signature_rejected" if authority else "web_signature_rejected")
    if authority:
        if "CA:TRUE" not in text or "Certificate Sign" not in text:
            raise ActivationError("web_ca_profile_rejected")
    else:
        if "CA:FALSE" not in text or "TLS Web Server Authentication" not in text:
            raise ActivationError("web_profile_rejected")
        if "TLS Web Client Authentication" in text:
            raise ActivationError("client_identity_rejected")


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
    expected_dns = frozenset({spec["fqdn"].rstrip(".").lower(), FUTURE_VIP_HOSTNAME.rstrip(".").lower()})
    if dns_names != expected_dns:
        raise ActivationError("unexpected_dns_san")
    if ip_values != frozenset({expected_ip}):
        raise ActivationError("unexpected_ip_san")


def validate_candidate(spec: dict[str, str], cert: Path = CANDIDATE_CERT, key: Path = CANDIDATE_KEY) -> str:
    _regular_secure(WEB_CA_CERT, private=False)
    _regular_secure(cert, private=False)
    _regular_secure(key, private=True)
    _run([OPENSSL, "verify", "-x509_strict", "-CAfile", str(WEB_CA_CERT), str(WEB_CA_CERT)])
    _run([OPENSSL, "x509", "-in", str(WEB_CA_CERT), "-noout", "-checkend", str(WEB_CA_MIN_VALIDITY_SECONDS)])
    _require_browser_compatible_certificate(WEB_CA_CERT, authority=True)
    _run([OPENSSL, "verify", "-x509_strict", "-CAfile", str(WEB_CA_CERT), str(cert)])
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
    _require_browser_compatible_certificate(cert, authority=False)
    if _key_public(key) != _cert_public(cert):
        raise ActivationError("key_mismatch")
    return _fingerprint(cert)


def _group(path: Path, mode: int) -> None:
    account = pwd.getpwnam("home-center")
    os.chown(path, 0, account.pw_gid)
    os.chmod(path, mode)


def _write_release_owner(path: Path, owner: dict[str, str]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        payload = json.dumps(owner, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _release_owner(stage: Path) -> dict[str, str]:
    marker = stage / ".owner.json"
    marker_info = marker.lstat()
    account = pwd.getpwnam("home-center")
    if (
        not stat.S_ISREG(marker_info.st_mode)
        or stat.S_ISLNK(marker_info.st_mode)
        or marker_info.st_uid != ROOT_UID
        # The root helper deliberately runs with Group=home-center and no
        # capabilities.  Its O_EXCL marker therefore inherits that gid.  Mode
        # 0600 keeps the ownership proof root-only despite the service group.
        or marker_info.st_gid != account.pw_gid
        or stat.S_IMODE(marker_info.st_mode) != 0o600
        or marker_info.st_size < 128
        or marker_info.st_size > 2048
    ):
        raise ActivationError("release_stage_owner_rejected")
    try:
        owner = json.loads(marker.read_text(encoding="ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActivationError("release_stage_owner_rejected") from exc
    if not isinstance(owner, dict) or set(owner) != {
        "schema",
        "operation_id",
        "certificate_file_sha256",
        "private_key_file_sha256",
    }:
        raise ActivationError("release_stage_owner_rejected")
    if owner.get("schema") != CANDIDATE_OWNER_SCHEMA:
        raise ActivationError("release_stage_owner_rejected")
    if not isinstance(owner.get("operation_id"), str) or re.fullmatch(r"[0-9a-f]{32}", owner["operation_id"]) is None:
        raise ActivationError("release_stage_owner_rejected")
    for field in ("certificate_file_sha256", "private_key_file_sha256"):
        if not isinstance(owner.get(field), str) or re.fullmatch(r"[0-9a-f]{64}", owner[field]) is None:
            raise ActivationError("release_stage_owner_rejected")
    return owner


def _release_directory_secure(path: Path) -> None:
    account = pwd.getpwnam("home-center")
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != ROOT_UID
        or info.st_gid != account.pw_gid
        or stat.S_IMODE(info.st_mode) != 0o750
    ):
        raise ActivationError("release_stage_rejected")


def _release_file_secure(path: Path, *, mode: int, gid: int) -> None:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != ROOT_UID
        or info.st_gid != gid
        or stat.S_IMODE(info.st_mode) != mode
        or info.st_size < 64
        or info.st_size > 64 * 1024
    ):
        raise ActivationError("release_stage_contents_rejected")


def _discard_owned_release_stage(stage: Path, owner: dict[str, str] | None = None) -> None:
    _release_directory_secure(stage)
    staged_owner = _release_owner(stage)
    if owner is not None and staged_owner != owner:
        raise ActivationError("release_stage_owner_rejected")
    allowed = {".owner.json", "tls.crt", "tls.key"}
    entries = list(stage.iterdir())
    if any(entry.name not in allowed for entry in entries):
        raise ActivationError("release_stage_contents_rejected")
    account = pwd.getpwnam("home-center")
    for name, mode, gid, digest_field in (
        ("tls.crt", 0o644, account.pw_gid, "certificate_file_sha256"),
        ("tls.key", 0o640, account.pw_gid, "private_key_file_sha256"),
    ):
        path = stage / name
        try:
            _release_file_secure(path, mode=mode, gid=gid)
        except FileNotFoundError:
            continue
        if _sha256_file(path) != staged_owner[digest_field]:
            raise ActivationError("release_stage_digest_mismatch")
    for name in ("tls.key", "tls.crt"):
        try:
            (stage / name).unlink()
        except FileNotFoundError:
            pass
    _fsync_directory(stage)
    (stage / ".owner.json").unlink()
    _fsync_directory(stage)
    stage.rmdir()
    _fsync_directory(WEB_RELEASES)


def _ensure_release_root() -> None:
    account = pwd.getpwnam("home-center")
    try:
        WEB_RELEASES.mkdir(mode=0o750)
    except FileExistsError:
        pass
    else:
        os.chown(WEB_RELEASES, 0, account.pw_gid)
        os.chmod(WEB_RELEASES, 0o750)
        _fsync_directory(WEB_ROOT)
    info = WEB_RELEASES.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != ROOT_UID
        or info.st_gid != account.pw_gid
        or stat.S_IMODE(info.st_mode) != 0o750
    ):
        raise ActivationError("release_root_rejected")


def _recover_release_stages() -> bool:
    recovered = False
    for entry in WEB_RELEASES.iterdir():
        if re.fullmatch(r"\.[0-9a-f]{24}(?:\.[0-9a-f]{32})?\.staging", entry.name) is not None:
            _discard_owned_release_stage(entry)
            recovered = True
            continue
        if re.fullmatch(r"[0-9a-f]{24}", entry.name) is None:
            raise ActivationError("release_root_contents_rejected")
        _release_directory_secure(entry)
    return recovered


def _validate_release(release: Path, fingerprint: str, spec: dict[str, str]) -> None:
    _release_directory_secure(release)
    if {entry.name for entry in release.iterdir()} != {".owner.json", "tls.crt", "tls.key"}:
        raise ActivationError("release_contents_rejected")
    owner = _release_owner(release)
    account = pwd.getpwnam("home-center")
    _release_file_secure(release / "tls.crt", mode=0o644, gid=account.pw_gid)
    _release_file_secure(release / "tls.key", mode=0o640, gid=account.pw_gid)
    if _sha256_file(release / "tls.crt") != owner["certificate_file_sha256"]:
        raise ActivationError("release_certificate_digest_mismatch")
    if _sha256_file(release / "tls.key") != owner["private_key_file_sha256"]:
        raise ActivationError("release_private_key_digest_mismatch")
    if validate_candidate(spec, release / "tls.crt", release / "tls.key") != fingerprint:
        raise ActivationError("release_fingerprint_mismatch")


def _prepare_release(fingerprint: str, spec: dict[str, str]) -> Path:
    _ensure_release_root()
    _recover_release_stages()
    owner = _candidate_owner()
    release = WEB_RELEASES / fingerprint[:24]
    if release.exists() or release.is_symlink():
        try:
            _validate_release(release, fingerprint, spec)
        except FileNotFoundError as exc:
            raise ActivationError("release_path_rejected")
    else:
        stage = WEB_RELEASES / f".{release.name}.{owner['operation_id']}.staging"
        if stage.exists() or stage.is_symlink():
            _discard_owned_release_stage(stage, owner)
        stage.mkdir(mode=0o750)
        _group(stage, 0o750)
        _write_release_owner(stage / ".owner.json", owner)
        _fsync_directory(stage)
        for source, destination, mode in (
            (CANDIDATE_CERT, stage / "tls.crt", 0o644),
            (CANDIDATE_KEY, stage / "tls.key", 0o640),
        ):
            shutil.copyfile(source, destination)
            _group(destination, mode)
            with destination.open("rb") as handle:
                os.fsync(handle.fileno())
            _fsync_directory(stage)
        staged_fingerprint = validate_candidate(spec, stage / "tls.crt", stage / "tls.key")
        if staged_fingerprint != fingerprint:
            raise ActivationError("release_stage_fingerprint_mismatch")
        os.replace(stage, release)
        _fsync_directory(WEB_RELEASES)
        _validate_release(release, fingerprint, spec)
    return release


def _clear_candidate() -> None:
    _candidate_owner()
    for path in (CANDIDATE_KEY, CANDIDATE_CERT):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    _fsync_directory(CANDIDATE)
    CANDIDATE_OWNER.unlink()
    _fsync_directory(CANDIDATE)


def _current_release() -> Path | None:
    if not (WEB_CURRENT.exists() or WEB_CURRENT.is_symlink()):
        return None
    if not WEB_CURRENT.is_symlink():
        raise ActivationError("web_current_not_symlink")
    current = WEB_CURRENT.resolve(strict=True)
    if WEB_RELEASES not in current.parents:
        raise ActivationError("web_current_outside_release_root")
    if current.parent != WEB_RELEASES or re.fullmatch(r"[0-9a-f]{24}", current.name) is None:
        raise ActivationError("web_current_release_identity_rejected")
    info = current.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != ROOT_UID:
        raise ActivationError("web_current_release_metadata_rejected")
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
        _fsync_directory(WEB_ROOT)
        return
    os.symlink(str(release), pending)
    os.replace(pending, WEB_CURRENT)
    _fsync_directory(WEB_ROOT)


def _recover_current_pending_links() -> bool:
    recovered = False
    for suffix in ("new", "rollback"):
        pending = WEB_ROOT / f".current.{suffix}"
        try:
            info = pending.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISLNK(info.st_mode):
            raise ActivationError("web_current_pending_rejected")
        try:
            target = pending.resolve(strict=True)
        except OSError as exc:
            raise ActivationError("web_current_pending_rejected") from exc
        if target.parent != WEB_RELEASES or re.fullmatch(r"[0-9a-f]{24}", target.name) is None:
            raise ActivationError("web_current_pending_rejected")
        _release_directory_secure(target)
        pending.unlink()
        recovered = True
    if recovered:
        _fsync_directory(WEB_ROOT)
    return recovered


def _restart() -> None:
    _run([SYSTEMCTL, "restart", "home-center.service"], timeout=RESTART_TIMEOUT_SECONDS)


def _presented_fingerprint(
    spec: dict[str, str],
    expected: str,
    trust_anchor: Path,
    *,
    timeout_seconds: int = PRESENTED_HEALTH_TIMEOUT_SECONDS,
) -> None:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(trust_anchor))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    deadline = time.monotonic() + timeout_seconds
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


def _rollback_identity(
    spec: dict[str, str],
    previous: Path | None,
    *,
    verify_live: bool = True,
) -> tuple[str, Path]:
    certificate = previous / "tls.crt" if previous is not None else Path(spec["legacy_tls_certificate"])
    _regular_secure(certificate, private=False)
    if previous is not None:
        private_key = previous / "tls.key"
        _regular_secure(private_key, private=True)
        if _cert_public(certificate) != _key_public(private_key):
            raise ActivationError("previous_key_mismatch")
    anchors = (WEB_CA_CERT, PEER_CA_CERT) if previous is not None else (PEER_CA_CERT,)
    for anchor in anchors:
        try:
            _run([OPENSSL, "verify", "-x509_strict", "-CAfile", str(anchor), str(certificate)])
        except ActivationError:
            continue
        fingerprint = _fingerprint(certificate)
        if previous is None and verify_live:
            # The helper sandbox deliberately cannot read the legacy node key.
            # A verified live handshake proves that the old service can use the
            # matching private key before any current-link mutation occurs.
            _presented_fingerprint(spec, fingerprint, anchor)
        return fingerprint, anchor
    raise ActivationError("previous_chain_rejected")


def _activate_locked(deadline: float) -> dict[str, Any]:
    spec = _spec()
    _candidate_owner()
    fingerprint = validate_candidate(spec)
    previous = _current_release()
    rollback_fingerprint, rollback_anchor = _rollback_identity(spec, previous)
    release = _prepare_release(fingerprint, spec)
    if time.monotonic() + ACTIVATION_ROLLBACK_RESERVE_SECONDS > deadline:
        raise ActivationError("activation_budget_exhausted_before_switch")
    _clear_candidate()
    try:
        _point_current(release, "new")
    except Exception as switch_error:
        # os.replace is atomic, but an interrupted/error return does not prove
        # whether the new identity became current. Force explicit recovery.
        raise ActivationError("activation_switch_outcome_unknown") from switch_error
    try:
        _restart()
        _presented_fingerprint(spec, fingerprint, WEB_CA_CERT)
    except Exception as activation_error:
        try:
            _point_current(previous, "rollback")
            _restart()
            _presented_fingerprint(
                spec,
                rollback_fingerprint,
                rollback_anchor,
            )
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


def activate() -> dict[str, Any]:
    with _mutation_lock():
        return _activate_locked(time.monotonic() + ACTIVATION_TRANSACTION_SECONDS)


def main() -> int:
    try:
        result = activate()
    except ActivationError as exc:
        code = str(exc)
        if code == "activation_rolled_back":
            result = {
                "schema": "home-center.tls-activation-result.v1",
                "status": "rolled_back",
                "reason": "activation_rolled_back",
            }
        elif code == "rollback_failed":
            result = {
                "schema": "home-center.tls-activation-result.v1",
                "status": "unknown",
                "reason": "rollback_failed_recovery_required",
            }
        elif code == "activation_switch_outcome_unknown":
            result = {
                "schema": "home-center.tls-activation-result.v1",
                "status": "unknown",
                "reason": "activation_switch_outcome_unknown_recovery_required",
            }
        else:
            result = {
                "schema": "home-center.tls-activation-result.v1",
                "status": "failed",
                "reason": "activation_preflight_failed",
            }
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        return 1
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError, ValueError, UnicodeDecodeError):
        print('{"reason":"activation_preflight_failed","schema":"home-center.tls-activation-result.v1","status":"failed"}')
        return 1
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
