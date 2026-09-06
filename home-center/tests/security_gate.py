from __future__ import annotations

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
        if re.search(pattern, text): errors.append(f"{path.relative_to(ROOT)}: {message}")

unit = (ROOT / "deploy/systemd/home-center.service").read_text(encoding="utf-8")
for required in ("NoNewPrivileges=yes", "ProtectSystem=strict", "CapabilityBoundingSet=", "MemoryDenyWriteExecute=yes", "User=home-center", "IPAddressDeny=any", "RestrictNamespaces=false"):
    if required not in unit: errors.append(f"systemd hardening missing: {required}")

api = (ROOT / "product/control-plane/src/home_center/api.py").read_text(encoding="utf-8")
if "typed_action_not_available" not in api: errors.append("fail-closed mutation gate missing")
if "generic shell" in api.lower(): errors.append("generic shell exposed in API")

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
    if "runs-on: ubuntu-latest" not in text: errors.append("GitHub-hosted runner missing")
    if "self-hosted" in text: errors.append("self-hosted runner is forbidden for Home Center")
    if "working-directory: home-center" in text or '"home-center/**"' in text:
        errors.append("monorepo path assumption is forbidden")

scripts = sorted((ROOT / "deploy/scripts").glob("*.sh"))
syntax = subprocess.run(["bash", "-n", *map(str, scripts)], check=False, capture_output=True, text=True)
if syntax.returncode != 0: errors.append(f"deployment shell syntax failed: {syntax.stderr.strip()}")

installer = (ROOT / "deploy/scripts/install-node.sh").read_text(encoding="utf-8")
release_dir = installer.find("install -d -m 0755 -o root -g root /opt/home-center /opt/home-center/releases")
release_stage = installer.find("STAGE=$(mktemp -d /opt/home-center/releases/.stage.XXXXXX)")
if release_dir < 0 or release_stage < 0 or release_dir > release_stage:
    errors.append("release directory must exist before atomic staging")
if "if [ -L /opt/home-center/current ]; then" not in installer:
    errors.append("previous release discovery must require an existing symlink")
if "chown root:home-center /etc/home-center /etc/home-center/secrets /etc/home-center/pki" not in installer:
    errors.append("runtime identity must be able to traverse root-owned configuration directories")

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
    for error in errors: print(error)
    raise SystemExit(1)
print("HOME_CENTER_SECURITY_GATE=PASS")
