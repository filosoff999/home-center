"""Strict, versioned runtime configuration."""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ad_auth import AdAuthConfig
from .util import secure_file


CONFIG_SCHEMA = "home-center.config.v3"


@dataclass(frozen=True, slots=True)
class Peer:
    node_id: str
    name: str
    address: str
    url: str
    certificate_name: str


@dataclass(frozen=True, slots=True)
class Config:
    cluster_id: str
    node_id: str
    node_name: str
    role: str
    management_address: str
    web_port: int
    peer_port: int
    state_db: Path
    backup_dir: Path
    web_root: Path
    local_admin_credentials_file: Path
    session_key_file: Path
    audit_key_file: Path
    tls_certificate: Path
    tls_private_key: Path
    cluster_ca: Path
    web_ca: Path
    deployment_profile: Path
    peer: Peer
    reconcile_interval_seconds: int
    peer_timeout_seconds: int
    ad_auth: AdAuthConfig = field(default_factory=AdAuthConfig.disabled)

    @property
    def web_bind(self) -> tuple[str, int]:
        return self.management_address, self.web_port

    @property
    def peer_bind(self) -> tuple[str, int]:
        return self.management_address, self.peer_port


def _required(raw: dict[str, Any], name: str, expected: type) -> Any:
    value = raw.get(name)
    if not isinstance(value, expected) or (expected is str and not value.strip()):
        raise ValueError(f"invalid or missing config field: {name}")
    return value


def _path(raw: dict[str, Any], name: str) -> Path:
    value = Path(_required(raw, name, str))
    if not value.is_absolute():
        raise ValueError(f"config path must be absolute: {name}")
    return value



def _ad_auth(raw: dict[str, Any]) -> AdAuthConfig:
    value = _required(raw, "ad_auth", dict)
    required = {"enabled", "realm", "kdc_hosts", "allowed_admin_groups", "timeout_seconds", "cache_root"}
    if set(value) != required:
        raise ValueError("ad_auth config shape rejected")
    enabled = value.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("ad_auth enabled must be boolean")
    realm = _required(value, "realm", str).upper()
    if re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{2,254}", realm) is None:
        raise ValueError("ad_auth realm rejected")
    kdc_hosts = value.get("kdc_hosts")
    if (
        not isinstance(kdc_hosts, list)
        or not 1 <= len(kdc_hosts) <= 4
        or any(not isinstance(item, str) for item in kdc_hosts)
    ):
        raise ValueError("ad_auth kdc_hosts rejected")
    normalized_hosts = tuple(item.lower() for item in kdc_hosts)
    realm_suffix = "." + realm.lower()
    if any(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", item) is None
        or not item.endswith(realm_suffix)
        for item in normalized_hosts
    ):
        raise ValueError("ad_auth kdc_hosts rejected")
    groups = value.get("allowed_admin_groups")
    if (
        not isinstance(groups, list)
        or not 1 <= len(groups) <= 16
        or any(
            not isinstance(item, str)
            or item != item.strip()
            or not 1 <= len(item) <= 128
            or any(ord(character) < 32 for character in item)
            for item in groups
        )
    ):
        raise ValueError("ad_auth allowed_admin_groups rejected")
    timeout = value.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 10:
        raise ValueError("ad_auth timeout rejected")
    cache_root = Path(_required(value, "cache_root", str))
    if not cache_root.is_absolute():
        raise ValueError("ad_auth cache_root must be absolute")
    return AdAuthConfig(
        enabled=enabled,
        realm=realm,
        kdc_hosts=normalized_hosts,
        allowed_admin_groups=tuple(groups),
        timeout_seconds=timeout,
        cache_root=cache_root,
    )

def load_config(path: str | Path | None = None) -> Config:
    config_path = Path(path or os.environ.get("HOME_CENTER_CONFIG", "/etc/home-center/config.json"))
    with config_path.open("r", encoding="utf-8") as stream:
        raw = json.load(stream)
    if not isinstance(raw, dict) or raw.get("schema") != CONFIG_SCHEMA:
        raise ValueError(f"unsupported config schema in {config_path}")
    if config_path.stat().st_mode & 0o002:
        raise PermissionError(f"world-writable config rejected: {config_path}")

    role = _required(raw, "role", str)
    if role not in {"leader", "standby"}:
        raise ValueError("role must be leader or standby")
    address = _required(raw, "management_address", str)
    parsed_address = ipaddress.ip_address(address)
    if parsed_address.is_unspecified or parsed_address.is_loopback or parsed_address.is_multicast:
        raise ValueError("management_address must be a concrete LAN address")

    peer_raw = _required(raw, "peer", dict)
    peer_address = _required(peer_raw, "address", str)
    ipaddress.ip_address(peer_address)
    peer_url = _required(peer_raw, "url", str)
    if peer_url != f"https://{peer_address}:{int(raw.get('peer_port', 9443))}":
        raise ValueError("peer URL must match peer address and configured peer port")

    ports = (int(raw.get("web_port", 8443)), int(raw.get("peer_port", 9443)))
    if any(port < 1024 or port > 65535 for port in ports) or ports[0] == ports[1]:
        raise ValueError("web/peer ports must be distinct unprivileged ports")

    cfg = Config(
        cluster_id=_required(raw, "cluster_id", str),
        node_id=_required(raw, "node_id", str),
        node_name=_required(raw, "node_name", str),
        role=role,
        management_address=address,
        web_port=ports[0],
        peer_port=ports[1],
        state_db=_path(raw, "state_db"),
        backup_dir=_path(raw, "backup_dir"),
        web_root=_path(raw, "web_root"),
        local_admin_credentials_file=_path(raw, "local_admin_credentials_file"),
        session_key_file=_path(raw, "session_key_file"),
        audit_key_file=_path(raw, "audit_key_file"),
        tls_certificate=_path(raw, "tls_certificate"),
        tls_private_key=_path(raw, "tls_private_key"),
        cluster_ca=_path(raw, "cluster_ca"),
        web_ca=_path(raw, "web_ca"),
        deployment_profile=_path(raw, "deployment_profile"),
        peer=Peer(
            node_id=_required(peer_raw, "node_id", str),
            name=_required(peer_raw, "name", str),
            address=peer_address,
            url=peer_url,
            certificate_name=_required(peer_raw, "certificate_name", str),
        ),
        reconcile_interval_seconds=max(5, min(int(raw.get("reconcile_interval_seconds", 15)), 300)),
        peer_timeout_seconds=max(1, min(int(raw.get("peer_timeout_seconds", 3)), 15)),
        ad_auth=_ad_auth(raw),
    )
    if cfg.peer.node_id == cfg.node_id or cfg.peer.name == cfg.node_name:
        raise ValueError("peer identity must differ from local node identity")
    if cfg.web_ca.resolve() == cfg.cluster_ca.resolve():
        raise ValueError("web_ca must be independent from cluster_ca")
    for secret in (
        cfg.local_admin_credentials_file,
        cfg.session_key_file,
        cfg.audit_key_file,
        cfg.tls_private_key,
    ):
        secure_file(secret, allow_group_read=True)
    for public_file in (cfg.tls_certificate, cfg.cluster_ca, cfg.web_ca, cfg.deployment_profile):
        if not public_file.is_file():
            raise FileNotFoundError(public_file)
    if not cfg.web_root.is_dir():
        raise FileNotFoundError(cfg.web_root)
    return cfg
