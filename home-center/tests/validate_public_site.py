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
if "0.15 stable" not in homepage:
    fail("legacy static stable placeholder is absent from homepage")
if "Состав конкретной установки зависит" not in homepage:
    fail("release/module availability boundary is absent from homepage")

for marker in POSITIONING_MARKERS:
    if marker.casefold() not in homepage.casefold():
        fail(f"home product positioning marker is absent: {marker}")

if "Home Center 0.15.0" not in releases:
    fail("legacy static release placeholder is absent from releases page")
if "Возможность продукта и stable-пакет" not in releases:
    fail("product-vs-stable availability explanation is absent")
if "ControlCenterSoft/home-center-stable/releases" not in docs:
    fail("public stable installation channel is absent from installation page")
if "ControlCenterSoft/home-center-free" in docs:
    fail("retired public installer channel is still present in installation page")
if "ControlCenterSoft/home-center-stable/releases" not in app_js:
    fail("legacy release CTA compatibility routing is absent")
if "const stableVersion = '0.56.0';" not in app_js:
    fail("rendered public Stable identity is not pinned to 0.56.0")
if "home-center-0.56.0-source.tar.gz" not in app_js:
    fail("0.56.0 clean-install source artifact mapping is absent")
if "UPGRADE.md" not in app_js:
    fail("release-specific upgrade instruction mapping is absent")

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
