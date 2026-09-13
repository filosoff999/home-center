from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "website"
REQUIRED = [
    "index.html",
    "releases.html",
    "docs.html",
    "assets/styles.css",
    "assets/app.js",
    "assets/logo.svg",
    "site.webmanifest",
    "robots.txt",
    "sitemap.xml",
    ".well-known/security.txt",
]

FORBIDDEN = [
    r"\bdc0[12]\b",
    r"\bhm\.dm\b",
    r"\b192\.168\.",
    r"\b10\.(?:\d{1,3}\.){2}\d{1,3}\b",
    r"AI Development",
    r"CI Fabric",
    r"ChatGPT",
    r"Codex",
    r"model provider",
    r"bastion",
    r"private key",
    r"BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY",
]

POSITIONING_MARKERS = [
    "Уютный",
    "переносимые профили",
    "детский контроль",
    "белые и чёрные списки",
    "VPN",
    "ZigBee",
    "Yandex Smart Home",
    "TorrServer",
    "Lampa",
    "Minecraft Server",
    "Android MDM",
    "PXE/iPXE",
    "Multi-node",
    "Резервные копии",
    "восстановление",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


for rel in REQUIRED:
    path = SITE / rel
    if not path.is_file() or path.stat().st_size == 0:
        fail(f"missing required public asset: {rel}")

text_files = [
    p for p in SITE.rglob("*")
    if p.is_file() and p.suffix in {".html", ".css", ".js", ".svg", ".xml", ".txt", ".webmanifest"}
]
combined = "\n".join(p.read_text(encoding="utf-8") for p in text_files)
homepage = (SITE / "index.html").read_text(encoding="utf-8")
docs = (SITE / "docs.html").read_text(encoding="utf-8")
releases = (SITE / "releases.html").read_text(encoding="utf-8")
app_js = (SITE / "assets/app.js").read_text(encoding="utf-8")

if "home.control-center.pro" not in combined:
    fail("public hostname is absent")
# The large homepage still carries a legacy static placeholder; app.js must
# rewrite it to the authoritative Stable identity until the next full page refresh.
if "0.15 stable" not in homepage:
    fail("legacy static stable placeholder is absent from homepage")
if "Состав конкретной установки зависит" not in homepage:
    fail("release/module availability boundary is absent from homepage")

for marker in POSITIONING_MARKERS:
    if marker.casefold() not in homepage.casefold():
        fail(f"home product positioning marker is absent: {marker}")

if "Home Center 0.62.1" not in releases:
    fail("current Stable identity is absent from releases page")
if "Возможность продукта и stable-пакет" not in releases:
    fail("product-vs-stable availability explanation is absent")
if "0.61.2 заменяет 0.61.0" not in releases:
    fail("0.61.2 split-routing security correction notice is absent from releases page")
if "Исправление 0.61.2 входит в текущую линию 0.62.1" not in docs:
    fail("0.61.x split-routing security correction inheritance is absent from install documentation")
if "ControlCenterSoft/home-center-stable/releases" not in docs:
    fail("public stable installation channel is absent from installation page")
if "ControlCenterSoft/home-center-free" in docs:
    fail("retired public installer channel is still present in installation page")
if "home-center-0.62.1-source.tar.gz" not in docs:
    fail("0.62.1 clean-install source artifact is absent from installation page")
if "deploy/scripts/install.sh" not in docs:
    fail("authoritative 0.62.1 install command is absent")
if "0.61.2" not in docs or "0.62.1" not in docs:
    fail("supported 0.61.2 to 0.62.1 upgrade boundary is absent")
if "UPGRADE.md" not in docs:
    fail("release-specific upgrade instruction is absent")
if "const stableVersion = '0.62.1';" not in app_js:
    fail("rendered public Stable identity is not pinned to 0.62.1")
if "home-center-0.62.1-source.tar.gz" not in app_js:
    fail("legacy homepage mapping does not target the 0.62.1 clean-install source artifact")

for pattern in FORBIDDEN:
    if re.search(pattern, combined, flags=re.IGNORECASE):
        fail(f"forbidden public marker matched: {pattern}")

for html in SITE.glob("*.html"):
    content = html.read_text(encoding="utf-8")
    if "<meta name=\"viewport\"" not in content:
        fail(f"missing responsive viewport: {html.name}")
    if "<title>" not in content:
        fail(f"missing title: {html.name}")

print(f"PASS: validated {len(text_files)} public text assets")
