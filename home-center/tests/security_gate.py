from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []

patterns = {
    r"shell\s*=\s*True": "subprocess shell=True is forbidden",
    r"\bos\.system\s*\(": "os.system is forbidden",
    r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY": "private key material is forbidden",
    r"ghp_[A-Za-z0-9]{20,}": "GitHub token-shaped value is forbidden",
    r"sk-[A-Za-z0-9_-]{20,}": "provider token-shaped value is forbidden",
}
for path in sorted((ROOT / "product/control-plane/src").rglob("*.py")):
    text = path.read_text(encoding="utf-8")
    for pattern, message in patterns.items():
        if re.search(pattern, text):
            errors.append(f"{path.relative_to(ROOT)}: {message}")

unit = (ROOT / "deploy/systemd/home-center.service").read_text(encoding="utf-8")
for required in (
    "NoNewPrivileges=yes",
    "ProtectSystem=strict",
    "CapabilityBoundingSet=",
    "MemoryDenyWriteExecute=yes",
    "User=home-center",
    "IPAddressDeny=any",
    "RestrictNamespaces=false",
):
    if required not in unit:
        errors.append(f"systemd hardening missing: {required}")

helper_unit = (ROOT / "deploy/systemd/home-center-helper.service").read_text(encoding="utf-8")
for required in (
    "User=root",
    "Group=home-center",
    "ExecStart=/usr/bin/python3 -I /opt/home-center/current/home_center/privileged_helper_v2.py --serve",
    "NoNewPrivileges=yes",
    "ProtectSystem=strict",
    "ReadWritePaths=/etc/home-center/pki/web -/run/home-center-locks",
    "InaccessiblePaths=-/etc/home-center/pki/ca.key -/etc/home-center/pki/web-ca/ca.key -/etc/home-center/pki/node.key",
    "ProtectHome=yes",
    "PrivateDevices=yes",
    "MemoryDenyWriteExecute=yes",
    "RestrictAddressFamilies=AF_UNIX AF_INET",
    "IPAddressDeny=any",
    "IPAddressAllow=192.168.10.253/32",
    "IPAddressAllow=192.168.10.254/32",
    "CapabilityBoundingSet=",
    "AmbientCapabilities=",
    "RuntimeDirectory=home-center-helper",
    "StateDirectory=home-center-helper",
):
    if required not in helper_unit:
        errors.append(f"helper systemd hardening missing: {required}")
if "AF_INET6" in helper_unit:
    errors.append("privileged helper must not have IPv6 address families")
if re.search(r"^ReadWritePaths=(?!/etc/home-center/pki/web -/run/home-center-locks$)", helper_unit, re.MULTILINE):
    errors.append("privileged helper writable filesystem surface must stay limited to isolated Web PKI")
if re.search(r"^ReadWritePaths=.*?/etc/home-center/pki/node", helper_unit, re.MULTILINE):
    errors.append("privileged helper must not receive a writable peer/CA identity path")

config_source = (ROOT / "product/control-plane/src/home_center/config.py").read_text(encoding="utf-8")
for required in (
    "web_ca: Path",
    'web_ca=_path(raw, "web_ca")',
    "cfg.tls_certificate, cfg.cluster_ca, cfg.web_ca, cfg.deployment_profile",
):
    if required not in config_source:
        errors.append(f"separate Web CA config gate missing: {required}")

maintenance_unit = (ROOT / "deploy/systemd/home-center-tls-maintenance.service").read_text(encoding="utf-8")
for required in (
    "Type=oneshot",
    "User=home-center",
    "Group=home-center",
    "ExecStart=/usr/bin/python3 -I /opt/home-center/current/tls-maintenance-run.py",
    "TimeoutStartSec=600s",
    "Requires=home-center-helper.service",
    "NoNewPrivileges=yes",
    "ProtectSystem=strict",
    "ReadWritePaths=/var/lib/home-center",
    "ProtectHome=yes",
    "PrivateDevices=yes",
    "MemoryDenyWriteExecute=yes",
    "RestrictAddressFamilies=AF_UNIX",
    "IPAddressDeny=any",
    "CapabilityBoundingSet=",
    "AmbientCapabilities=",
):
    if required not in maintenance_unit:
        errors.append(f"TLS maintenance systemd hardening missing: {required}")
if "AF_INET" in maintenance_unit or "AF_INET6" in maintenance_unit:
    errors.append("unprivileged TLS maintenance must not have IP networking")

maintenance_timer = (ROOT / "deploy/systemd/home-center-tls-maintenance.timer").read_text(encoding="utf-8")
for required in (
    "OnBootSec=10min",
    "OnUnitActiveSec=12h",
    "RandomizedDelaySec=30min",
    "Persistent=true",
    "Unit=home-center-tls-maintenance.service",
):
    if required not in maintenance_timer:
        errors.append(f"TLS maintenance timer contract missing: {required}")

api = (ROOT / "product/control-plane/src/home_center/api.py").read_text(encoding="utf-8")
if "typed_action_not_available" not in api:
    errors.append("fail-closed mutation gate missing")
if "generic shell" in api.lower():
    errors.append("generic shell exposed in API")

api_v2 = (ROOT / "product/control-plane/src/home_center/api_v2.py").read_text(encoding="utf-8")
for required in (
    'path == "/api/v1/tls/ca.crt"',
    'data = _validated_web_ca(self.runtime.config)',
    'if web_der == peer_der:',
    '_certificate_profile(web_ca, scope="browser-web-ca")',
    'path == "/api/v1/tls"',
    '"tls_status_unavailable"',
):
    if required not in api_v2:
        errors.append(f"P2.3 TLS API/status surface missing: {required}")

actions = (ROOT / "product/control-plane/src/home_center/actions.py").read_text(encoding="utf-8")
for required in (
    'SYSTEMCTL = "/usr/bin/systemctl"',
    '"bootstrap-admin": frozenset({"service.read"})',
    'set(value) != required',
    'service not in allowed',
    'request_sha256',
    'result_sha256',
):
    if required not in actions:
        errors.append(f"typed action guard missing: {required}")
if "shell=True" in actions or "shell = True" in actions:
    errors.append("typed actions must never invoke a shell")

# P2.2 remains frozen in the base helper: exactly one synthetic action.
helper = (ROOT / "product/control-plane/src/home_center/privileged_helper.py").read_text(encoding="utf-8")
for required in (
    '"helper.probe.v1": Action(',
    'permission="helper.probe"',
    'executable="/usr/bin/true"',
    "SO_PEERCRED",
    'if request.get("params") != {}',
    'reason="request_id_conflict"',
    '"interrupted_execution_requires_operator_recovery"',
    "capture_output=True",
    "timeout=action.timeout_seconds",
    "env=SAFE_ENV",
    "cwd=\"/\"",
    "require_root_controlled_policy=True",
    "MAX_REQUEST_BYTES = 16 * 1024",
    "MAX_OUTPUT_BYTES = 16 * 1024",
    '"mutation_recovery_required"',
    '"recovery_required"',
    '"rollback_failed_recovery_required"',
):
    if required not in helper:
        errors.append(f"privileged helper guard missing: {required}")
for forbidden in (
    "shell=True",
    "shell = True",
    "os.system(",
    "subprocess.Popen",
    'executable="/bin/sh"',
    'executable="/usr/bin/bash"',
):
    if forbidden in helper:
        errors.append(f"privileged helper forbidden primitive present: {forbidden}")
if helper.count("Action(") != 1:
    errors.append("P2.2 base helper action table must remain exactly one synthetic action")

# P2.3 extends the frozen helper only through bounded activation and
# fail-closed reconciliation actions.
helper_v2 = (ROOT / "product/control-plane/src/home_center/privileged_helper_v2.py").read_text(encoding="utf-8")
for required in (
    'base.ACTIONS["tls.web.activate.v1"] = base.Action(',
    'permission="tls.web.rotate"',
    'executable="/usr/bin/python3"',
    'argv=("/opt/home-center/current/home_center/tls_activate.py",)',
    'timeout_seconds=ACTIVATION_HELPER_TIMEOUT_SECONDS',
    'timeout_requires_recovery=True',
    'base.ACTIONS["tls.web.reconcile.v1"] = base.Action(',
    'permission="tls.web.reconcile"',
    'argv=("/opt/home-center/current/home_center/tls_reconcile.py",)',
    'timeout_seconds=60',
    'base.PERMISSIONS = frozenset(action.permission for action in base.ACTIONS.values())',
):
    if required not in helper_v2:
        errors.append(f"P2.3 helper extension guard missing: {required}")
if helper_v2.count("base.Action(") != 2:
    errors.append("P2.3 helper extension must admit exactly activation plus reconciliation")
for forbidden in ("shell=True", "shell = True", "os.system(", "subprocess.Popen"):
    if forbidden in helper_v2:
        errors.append(f"P2.3 helper extension forbidden primitive present: {forbidden}")

helper_policy = json.loads((ROOT / "deploy/helper-policy.v1.json").read_text(encoding="utf-8"))
if helper_policy != {
    "schema": "home-center.helper.policy.v1",
    "callers": {"home-center": ["helper.probe", "tls.web.rotate", "tls.web.reconcile"]},
    "enabled_actions": ["helper.probe.v1", "tls.web.activate.v1", "tls.web.reconcile.v1"],
}:
    errors.append("P2.3 helper policy must expose only probe plus bounded Web TLS activation/reconciliation")

registry = json.loads((ROOT / "product/control-plane/src/home_center/action_registry.v1.json").read_text(encoding="utf-8"))
registered = {item.get("id"): item for item in registry.get("actions", [])}
if set(registered) != {"service.state.read.v1"}:
    errors.append("certified action registry must exactly match the implemented P2 action set")
else:
    action = registered["service.state.read.v1"]
    if action.get("risk") != "read-only":
        errors.append("first P2 action must remain read-only")
    if action.get("recovery") != {"strategy": "none-read-only"}:
        errors.append("read-only action recovery contract mismatch")
    services = action.get("input", {}).get("properties", {}).get("service", {}).get("enum")
    if services != ["home-center.service", "home-center-backup.timer"]:
        errors.append("service action allowlist mismatch")

version_source = (ROOT / "product/control-plane/src/home_center/__init__.py").read_text(encoding="utf-8")
project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
builder = (ROOT / "deploy/scripts/build-artifact.sh").read_text(encoding="utf-8")
if '__version__ = "0.4.1"' not in version_source or 'version = "0.4.1"' not in project:
    errors.append("runtime/package version mismatch")
if "HOME_CENTER_VERSION:-$SOURCE_VERSION" not in builder or '[ "$VERSION" = 0.4.1 ]' not in builder:
    errors.append("artifact version mismatch")
if 'git -C "$ROOT" rev-parse HEAD' not in builder or 'git -C "$ROOT/../.."' in builder:
    errors.append("artifact revision must resolve from the independent repository root")
if '[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]]' not in builder:
    errors.append("artifact revision must be an immutable Git commit")
for required in (
    'cp "$ROOT/deploy/helper-policy.v1.json" "$STAGE/deploy/"',
    '"$ROOT/deploy/scripts/rotate-web-tls.sh"',
    '"$ROOT/deploy/systemd/home-center-helper.service"',
    '"$ROOT/deploy/systemd/home-center-tls-maintenance.service"',
    '"$ROOT/deploy/systemd/home-center-tls-maintenance.timer"',
):
    if required not in builder:
        errors.append(f"P2.3 artifact packaging missing: {required}")

server = (ROOT / "product/control-plane/src/home_center/server.py").read_text(encoding="utf-8")
for required in (
    "WEB_MINIMUM_TLS_VERSION = ssl.TLSVersion.TLSv1_2",
    "PEER_MINIMUM_TLS_VERSION = ssl.TLSVersion.TLSv1_3",
    "context.minimum_version = WEB_MINIMUM_TLS_VERSION",
    "context.minimum_version = PEER_MINIMUM_TLS_VERSION",
    "context.verify_mode = ssl.CERT_REQUIRED",
    'WEB_CURRENT = WEB_ROOT / "current"',
    'WEB_CERTIFICATE = WEB_CURRENT / "tls.crt"',
    'WEB_PRIVATE_KEY = WEB_CURRENT / "tls.key"',
    'raise RuntimeError("web_identity_incomplete")',
):
    if required not in server:
        errors.append(f"TLS policy missing: {required}")
if "ssl.TLSVersion.TLSv1_1" in server or re.search(r"ssl\.TLSVersion\.TLSv1(?![_0-9])", server):
    errors.append("TLS versions below 1.2 are forbidden")

tls_activate = (ROOT / "product/control-plane/src/home_center/tls_activate.py").read_text(encoding="utf-8")
for required in (
    'OPENSSL = "/usr/bin/openssl"',
    'SYSTEMCTL = "/usr/bin/systemctl"',
    'WEB_ROOT = PKI / "web"',
    'PEER_CA_CERT = PKI / "ca.crt"',
    'WEB_CA_CERT = PKI / "web-ca" / "ca.crt"',
    'WEB_RELEASES = WEB_ROOT / "releases"',
    'WEB_CURRENT = WEB_ROOT / "current"',
    'CANDIDATE = WEB_ROOT / "candidate"',
    'FUTURE_VIP_HOSTNAME = "home-center.hm.dm"',
    'MIN_VALIDITY_SECONDS = 30 * 86400',
    '"-x509_strict"',
    '"-ext", "subjectAltName"',
    'raise ActivationError("node_hostname_mismatch")',
    'raise ActivationError("vip_hostname_mismatch")',
    'raise ActivationError("node_ip_mismatch")',
    '"-checkhost", spec["fqdn"]',
    '"-checkhost", FUTURE_VIP_HOSTNAME',
    '"-checkip", spec["ip"]',
    'if _key_public(key) != _cert_public(cert):',
    'raise ActivationError("key_mismatch")',
    'raise ActivationError("web_ca_algorithm_rejected" if authority else "web_algorithm_rejected")',
    'raise ActivationError("web_ca_signature_rejected" if authority else "web_signature_rejected")',
    '"Public Key Algorithm: id-ecPublicKey"',
    '"ASN1 OID: prime256v1"',
    '"Signature Algorithm: ecdsa-with-SHA256"',
    'if WEB_RELEASES not in current.parents:',
    'pending = WEB_ROOT / f".current.{suffix}"',
    'os.replace(pending, WEB_CURRENT)',
    '_point_current(previous, "rollback")',
    '_presented_fingerprint(spec, fingerprint, WEB_CA_CERT)',
    'rollback_fingerprint, rollback_anchor = _rollback_identity(spec, previous)',
    'raise ActivationError("previous_chain_rejected")',
    'raise ActivationError("rollback_failed")',
    '_clear_candidate()',
    'MUTATION_LOCK_DIR = Path("/run/home-center-locks")',
    'ROOT_UID = 0',
    'fcntl.LOCK_EX | fcntl.LOCK_NB',
    'ACTIVATION_ROLLBACK_RESERVE_SECONDS = 130',
    'ACTIVATION_HELPER_TIMEOUT_SECONDS = 360',
    'raise ActivationError("activation_budget_exhausted_before_switch")',
):
    if required not in tls_activate:
        errors.append(f"TLS activation guard missing: {required}")
for forbidden in ("shell=True", "shell = True", "os.system(", "subprocess.Popen"):
    if forbidden in tls_activate:
        errors.append(f"TLS activation forbidden primitive present: {forbidden}")

tls_maintenance = (ROOT / "product/control-plane/src/home_center/tls_maintenance.py").read_text(encoding="utf-8")
for required in (
    'call_helper("tls.web.activate.v1", request_prefix="tls-maintenance")',
    'candidate.get("partial")',
    'call_helper("tls.web.reconcile.v1", request_prefix="tls-reconcile")',
    '"candidate_not_staged"',
    '"post_activation_validation_failed_recovery_required"',
    'mode.add_argument("--activate-staged", action="store_true")',
    'mode.add_argument("--reconcile", action="store_true")',
    'or not web.get("profile_valid")',
    'helper_status == "unknown"',
    '"mutation_recovery_required"',
    '"rolled_back"',
):
    if required not in tls_maintenance:
        errors.append(f"TLS maintenance guard missing: {required}")
for forbidden in ("subprocess.", "socket.", "tls.key", "ca.key"):
    if forbidden in tls_maintenance:
        errors.append(f"unprivileged TLS maintenance forbidden direct primitive present: {forbidden}")

tls_status = (ROOT / "product/control-plane/src/home_center/tls_status.py").read_text(encoding="utf-8")
for required in (
    'WEB_CURRENT = WEB_ROOT / "current"',
    'WEB_CERTIFICATE = WEB_CURRENT / "tls.crt"',
    'WEB_PRIVATE_KEY = WEB_CURRENT / "tls.key"',
    'CANDIDATE_CERTIFICATE = Path("/etc/home-center/pki/web/candidate/tls.crt")',
    'CANDIDATE_PRIVATE_KEY = Path("/etc/home-center/pki/web/candidate/tls.key")',
    'ROOT_UID = 0',
    'if _chain_valid(WEB_CERTIFICATE, config.web_ca):',
    'if _chain_valid(WEB_CERTIFICATE, config.cluster_ca):',
    '"quarantined-peer-web-identity"',
    'candidate = _candidate_state()',
    '"candidate": candidate',
    '"operational": {',
    '"recovery_required": operational_state == "recovery_required"',
    'not bool(web.get("chain_valid"))',
    'not bool(web.get("hostname_match"))',
    'not bool(web.get("profile_valid"))',
    'profile = "ecdsa-p256-sha256" if browser_profile_valid else "unsupported-for-browser-web"',
    'or not bool(web.get("san_policy_valid"))',
    'int(web_ca_days_remaining) <= WEB_CA_RENEWAL_DAYS',
    'int(days_remaining) <= RENEWAL_DAYS',
):
    if required not in tls_status:
        errors.append(f"TLS status/renewal gate missing: {required}")

workflow = ROOT / ".github/workflows/ci.yml"
if not workflow.is_file():
    errors.append("independent GitHub workflow missing")
else:
    text = workflow.read_text(encoding="utf-8")
    if "runs-on: ubuntu-latest" not in text:
        errors.append("GitHub-hosted runner missing")
    if "self-hosted" in text:
        errors.append("self-hosted runner is forbidden for Home Center")
    if "working-directory: home-center" in text or '"home-center/**"' in text:
        errors.append("monorepo path assumption is forbidden")
    if "steps.version.outputs.value" not in text or "Resolve artifact version" not in text:
        errors.append("artifact workflow must derive its version from runtime source")
    if "home-center-0.1.0-linux" in text:
        errors.append("artifact workflow contains a stale hard-coded version")

scripts = sorted((ROOT / "deploy/scripts").glob("*.sh"))
syntax = subprocess.run(["bash", "-n", *map(str, scripts)], check=False, capture_output=True, text=True)
if syntax.returncode != 0:
    errors.append(f"deployment shell syntax failed: {syntax.stderr.strip()}")

installer = (ROOT / "deploy/scripts/install-node.sh").read_text(encoding="utf-8")
release_dir = installer.find("for release_directory in /opt/home-center /opt/home-center/releases")
release_stage = installer.find("STAGE=$(mktemp -d /opt/home-center/releases/.stage.XXXXXX)")
if release_dir < 0 or release_stage < 0 or release_dir > release_stage:
    errors.append("release directory must exist before atomic staging")
if "if [ -e /opt/home-center/current ] || [ -L /opt/home-center/current ]; then" not in installer:
    errors.append("previous release discovery must require an existing symlink")
if "chown root:home-center /etc/home-center /etc/home-center/secrets /etc/home-center/pki" not in installer:
    errors.append("runtime identity must be able to traverse root-owned configuration directories")
for required in (
    'for web_directory in /etc/home-center/pki/web /etc/home-center/pki/web/candidate',
    'install -m 0644 -o root -g root "$RELEASE/deploy/helper-policy.v1.json" /etc/home-center/helper-policy.json',
    'install -m 0644 -o root -g root "$RELEASE/deploy/home-center-helper.service" /etc/systemd/system/home-center-helper.service',
    'install -m 0644 -o root -g root "$RELEASE/deploy/home-center-tls-maintenance.service" /etc/systemd/system/home-center-tls-maintenance.service',
    'install -m 0644 -o root -g root "$RELEASE/deploy/home-center-tls-maintenance.timer" /etc/systemd/system/home-center-tls-maintenance.timer',
    "systemctl restart home-center-helper.service",
    "systemctl start home-center-tls-maintenance.timer",
    "/usr/sbin/runuser -u home-center -- /usr/bin/python3 -I",
    "HOME_CENTER_HELPER_PROBE=PASS",
    "BACKUP_READY=1",
    "restore_optional_files",
    "home-center-tls-maintenance.timer",
    'WEB_HEALTH_CA=/etc/home-center/pki/ca.crt',
    'WEB_HEALTH_CA=/etc/home-center/pki/web-ca/ca.crt',
    "PARTIAL_WEB_IDENTITY_REJECTED",
    "MISSING_OR_UNSAFE_DC01_WEB_CA_PRIVATE_KEY",
    "DC02_WEB_CA_PRIVATE_KEY_FORBIDDEN",
    '[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]]',
    "LOCK_FILE=$LOCK_DIR/node-mutation.lock",
    "TRANSACTION_FILE=$TRANSACTION_DIR/$TRANSACTION_ID-$NODE.json",
    '"schema":"home-center.deploy-transaction.v1"',
):
    if required not in installer:
        errors.append(f"P2.3 installer gate missing: {required}")

rollback = (ROOT / "deploy/scripts/rollback-node.sh").read_text(encoding="utf-8")
for required in (
    "for unit in home-center-tls-maintenance.timer home-center-tls-maintenance.service home-center-helper.service",
    'stop_unit_checked "$unit"',
    "helper-policy.json",
    "home-center-helper.service",
    "home-center-tls-maintenance.service",
    "home-center-tls-maintenance.timer",
    'validate_slot "$slot"',
    "restore_slot helper-policy.json",
    "restore_slot home-center-helper.service",
    "restore_slot home-center-tls-maintenance.service",
    "restore_slot home-center-tls-maintenance.timer",
    "PREVIOUS_RELEASE_PATH_IDENTITY_MISMATCH",
    "rollback_readiness_rejected",
    "rollback_helper_probe_rejected",
    "HOME_CENTER_NODE_ROLLBACK=FAILED",
):
    if required not in rollback:
        errors.append(f"P2.3 rollback gate missing: {required}")

rotate = (ROOT / "deploy/scripts/rotate-web-tls.sh").read_text(encoding="utf-8")
for required in (
    '[ "$(id -u)" -eq 0 ]',
    '[ "$(hostname -s)" = dc01 ]',
    "PEER_CA_CERT=/etc/home-center/pki/ca.crt",
    "WEB_CA_CERT=$WEB_CA_DIR/ca.crt",
    "WEB_CA_KEY=$WEB_CA_DIR/ca.key",
    'WEB_CANDIDATE=$WEB_ROOT/candidate',
    'WEB_CANDIDATE_OWNER=$WEB_CANDIDATE/.owner.json',
    "flock -n 9",
    "ec_paramgen_curve:prime256v1",
    "Signature Algorithm: ecdsa-with-SHA256",
    "HOME_CENTER_WEB_CA_PARTIAL_STATE_REJECTED",
    "sudo -n test ! -e '$WEB_CA_KEY'",
    "extendedKeyUsage=serverAuth",
    "subjectAltName=DNS:$fqdn,DNS:home-center.hm.dm,IP:$ip",
    "openssl verify -x509_strict",
    'grep -Fq "DNS:$fqdn"',
    "grep -Fq 'DNS:home-center.hm.dm'",
    'grep -Fq "IP Address:$ip"',
    "-checkhost home-center.hm.dm",
    "-checkip \"$ip\"",
    "TLS Web Client Authentication",
    '"$WEB_CANDIDATE/tls.crt"',
    '"$WEB_CANDIDATE/tls.key"',
    "tls-maintenance-run.py --activate-staged",
    "candidate_cas_local cleanup-owned",
    "candidate_cas_remote cleanup-owned",
    'activate_remote "$TMP/dc02" "$DC02_EXPECTED"',
    "DC02_RESTRICTED_BROWSER_HANDSHAKES=PASS",
    "DC02_WEB_TLS_CANARY_30S=PASS",
    'activate_local "$TMP/dc01" "$DC01_EXPECTED"',
    "DC02_PEER_MTLS_AFTER_WEB_ROTATION=PASS",
    "DC01_PEER_MTLS_AFTER_WEB_ROTATION=PASS",
    "AUTOMATIC_FAILOVER=DISABLED",
    "WEB_PKI_ALGORITHM=ECDSA_P256_SHA256",
    "PEER_MTLS_PKI=UNCHANGED",
    "rollback_web_local",
    "rollback_web_remote",
    "HOME_CENTER_WEB_TLS_ROTATION=ROLLBACK_FAILED",
    "systemctl stop home-center-tls-maintenance.timer home-center-tls-maintenance.service",
):
    if required not in rotate:
        errors.append(f"staged Web TLS rotation gate missing: {required}")
if rotate.find('activate_remote "$TMP/dc02"') > rotate.find('activate_local "$TMP/dc01"'):
    errors.append("dc02 Web TLS canary must precede dc01 promotion")
if rotate.find("DC02_RESTRICTED_BROWSER_HANDSHAKES=PASS") > rotate.find('activate_local "$TMP/dc01"'):
    errors.append("dc02 restricted browser handshake gate must precede dc01 promotion")
if '"$WEB_CA_KEY"' in re.sub(r"openssl (x509|verify|req|pkey)[^\n]*", "", rotate):
    for line in rotate.splitlines():
        uses_web_ca_key = re.search(r"\$WEB_CA_KEY(?![A-Za-z0-9_])|\$\{WEB_CA_KEY\}", line)
        if uses_web_ca_key and not ("-CAkey" in line or "openssl pkey -in" in line or "stat -c" in line or "-L" in line or "-s" in line or "-e" in line or "test ! -e" in line):
            errors.append("CA private key use is outside the bounded signing/validation path")
            break
if re.search(r"(?:scp|tar|cp|install).*ca\.key", rotate, re.IGNORECASE):
    errors.append("CA private key must never be copied or staged")
if "genpkey -algorithm ED25519" in rotate:
    errors.append("Web PKI must not use the browser-incompatible Ed25519 profile")

bootstrap = (ROOT / "deploy/scripts/bootstrap-hm-dm.sh").read_text(encoding="utf-8")
if "/etc/home-center/pki/*" in bootstrap or "/etc/home-center/secrets/*" in bootstrap:
    errors.append("remote root-only directories must not rely on caller-side glob expansion")
if "trap - ERR EXIT" not in bootstrap:
    errors.append("cluster rollback handler must disable recursive traps")
if "IFS= read -r ADMIN_TOKEN" not in bootstrap or "unset ADMIN_TOKEN" not in bootstrap:
    errors.append("bootstrap token must be read without newline leakage and promptly unset")
if "cat /etc/home-center/secrets/admin.token" in bootstrap:
    errors.append("bootstrap token must not be copied into a malformed curl header")
for required in (
    "basicConstraints=critical,CA:TRUE,pathlen:0",
    "keyUsage=critical,keyCertSign,cRLSign",
    "subjectKeyIdentifier=hash",
    "authorityKeyIdentifier=keyid:always",
    "openssl verify -x509_strict",
    "WEB_CA_PROVISIONING=PASS",
    "HOME_CENTER_WEB_CA_PARTIAL_STATE_REJECTED",
    "sudo -n test ! -e '$WEB_CA_KEY'",
    "TRANSACTION_ID=$(date -u +%Y%m%dT%H%M%SZ)-$(openssl rand -hex 6)",
    "REMOTE_TRANSACTION_FILE=/var/lib/home-center-deploy/transactions/$TRANSACTION_ID-dc02.json",
    "DC02_SOFTWARE_CANARY_30S=PASS",
    '"peer_identity"',
    "cluster_source_overview_rejected",
):
    if required not in bootstrap:
        errors.append(f"strict X.509 bootstrap profile missing: {required}")
if 'sudo -n find /var/backups/home-center' not in bootstrap:
    errors.append("remote backup evidence must use bounded privilege for the root-inaccessible directory")

if errors:
    print("HOME_CENTER_SECURITY_GATE=FAIL")
    for error in errors:
        print(error)
    raise SystemExit(1)
print("HOME_CENTER_SECURITY_GATE=PASS")
