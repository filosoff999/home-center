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
    "домашней инфраструктур",
    "переносимые профили",
    "детский контроль",
    "медиатек",
    "Lampa",
    "ZigBee",
    "VPN",
    "белые и чёрные списки",
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

if "home.control-center.pro" not in combined:
    fail("public hostname is absent")
if "0.14.0" not in homepage:
    fail("latest release identity is absent from homepage")
if "Plan-only" not in homepage:
    fail("0.14.0 safety boundary is absent from homepage")

for marker in POSITIONING_MARKERS:
    if marker.casefold() not in homepage.casefold():
        fail(f"home infrastructure positioning marker is absent: {marker}")

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
