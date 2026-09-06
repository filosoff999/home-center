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
    "ExecStart=/usr/bin/python3 /opt/home-center/current/home_center/privileged_helper.py --serve",
    "NoNewPrivileges=yes",
    "ProtectSystem=strict",
    "ProtectHome=yes",
    "PrivateDevices=yes",
    "MemoryDenyWriteExecute=yes",
    "RestrictAddressFamilies=AF_UNIX",
    "IPAddressDeny=any",
    "CapabilityBoundingSet=",
    "AmbientCapabilities=",
    "RuntimeDirectory=home-center-helper",
    "StateDirectory=home-center-helper",
):
    if required not in helper_unit:
        errors.append(f"helper systemd hardening missing: {required}")
if "AF_INET" in helper_unit or "AF_INET6" in helper_unit:
    errors.append("privileged helper must not have IP address families")

api = (ROOT / "product/control-plane/src/home_center/api.py").read_text(encoding="utf-8")
if "typed_action_not_available" not in api:
    errors.append("fail-closed mutation gate missing")
if "generic shell" in api.lower():
    errors.append("generic shell exposed in API")

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
    errors.append("P2.2 helper action table must contain exactly one admitted action")

helper_policy = json.loads((ROOT / "deploy/helper-policy.v1.json").read_text(encoding="utf-8"))
if helper_policy != {
    "schema": "home-center.helper.policy.v1",
    "callers": {"home-center": ["helper.probe"]},
    "enabled_actions": ["helper.probe.v1"],
}:
    errors.append("P2.2 helper policy must expose only the synthetic probe")

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
if '__version__ = "0.3.0"' not in version_source or 'version = "0.3.0"' not in project:
    errors.append("runtime/package version mismatch")
if "HOME_CENTER_VERSION:-0.3.0" not in builder:
    errors.append("artifact version mismatch")
if 'git -C "$ROOT" rev-parse HEAD' not in builder or 'git -C "$ROOT/../.."' in builder:
    errors.append("artifact revision must resolve from the independent repository root")
for required in (
    'cp "$ROOT/deploy/helper-policy.v1.json" "$STAGE/deploy/"',
    '"$ROOT/deploy/systemd/home-center-helper.service"',
):
    if required not in builder:
        errors.append(f"helper artifact packaging missing: {required}")

server = (ROOT / "product/control-plane/src/home_center/server.py").read_text(encoding="utf-8")
for required in (
    "WEB_MINIMUM_TLS_VERSION = ssl.TLSVersion.TLSv1_2",
    "PEER_MINIMUM_TLS_VERSION = ssl.TLSVersion.TLSv1_3",
    "context.minimum_version = WEB_MINIMUM_TLS_VERSION",
    "context.minimum_version = PEER_MINIMUM_TLS_VERSION",
    "context.verify_mode = ssl.CERT_REQUIRED",
):
    if required not in server:
        errors.append(f"TLS policy missing: {required}")
if "ssl.TLSVersion.TLSv1_1" in server or re.search(r"ssl\.TLSVersion\.TLSv1(?![_0-9])", server):
    errors.append("TLS versions below 1.2 are forbidden")

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
release_dir = installer.find("install -d -m 0755 -o root -g root /opt/home-center /opt/home-center/releases")
release_stage = installer.find("STAGE=$(mktemp -d /opt/home-center/releases/.stage.XXXXXX)")
if release_dir < 0 or release_stage < 0 or release_dir > release_stage:
    errors.append("release directory must exist before atomic staging")
if "if [ -L /opt/home-center/current ]; then" not in installer:
    errors.append("previous release discovery must require an existing symlink")
if "chown root:home-center /etc/home-center /etc/home-center/secrets /etc/home-center/pki" not in installer:
    errors.append("runtime identity must be able to traverse root-owned configuration directories")
for required in (
    'install -m 0644 -o root -g root "$RELEASE/deploy/helper-policy.v1.json" /etc/home-center/helper-policy.json',
    'install -m 0644 -o root -g root "$RELEASE/deploy/home-center-helper.service" /etc/systemd/system/home-center-helper.service',
    "systemctl restart home-center-helper.service",
    "/usr/sbin/runuser -u home-center -- python3",
    "HOME_CENTER_HELPER_PROBE=PASS",
    "BACKUP_READY=1",
    "restore_helper_files",
):
    if required not in installer:
        errors.append(f"helper installer gate missing: {required}")

rollback = (ROOT / "deploy/scripts/rollback-node.sh").read_text(encoding="utf-8")
for required in (
    "systemctl stop home-center-helper.service home-center.service",
    "helper-policy.json.existed",
    "home-center-helper.service.existed",
    'rm -f /etc/home-center/helper-policy.json',
    'rm -f /etc/systemd/system/home-center-helper.service',
):
    if required not in rollback:
        errors.append(f"helper rollback gate missing: {required}")

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
    "authorityKeyIdentifier=keyid,issuer",
    "openssl verify -x509_strict",
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
