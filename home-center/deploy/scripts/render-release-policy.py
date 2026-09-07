#!/usr/bin/env python3
# Render the exact 0.10.0 release cut into staged deployment scripts.
#
# This first renderer retains immediate-predecessor provenance. The final
# staged source admission is generalized by render-upgrade-policy-v2.py.

from __future__ import annotations

import sys
from pathlib import Path

TARGET_VERSION = "0.10.0"
SOURCE_VERSION = "0.9.2"
SOURCE_REVISION = "689d90995a4f365e2f19640486b136b525f29c6d"
SOURCE_ARTIFACT_SHA256 = "90d5cb0b657abfdd13f077b4a2347c5066fbaddcd4f4bfa6b25944e73a8abe87"
SOURCE_RELEASE = "/opt/home-center/releases/0.9.2-689d90995a4f-90d5cb0b657a"

BOOTSTRAP_REPLACEMENTS = {
    '[ "$TARGET_VERSION" = 0.5.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED >&2; exit 66; }':
        '[ "$TARGET_VERSION" = 0.10.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED >&2; exit 66; }',
    "ADMITTED_SOURCE_V043_VERSION=0.4.3": "ADMITTED_SOURCE_V092_VERSION=0.9.2",
    "ADMITTED_SOURCE_V043_REVISION=64f798ceae0b669cbac01b452c3cf4fd96070136":
        f"ADMITTED_SOURCE_V092_REVISION={SOURCE_REVISION}",
    "ADMITTED_SOURCE_V043_RELEASE=/opt/home-center/releases/0.4.3-64f798ceae0b-b2dde6a51ec9":
        f"ADMITTED_SOURCE_V092_RELEASE={SOURCE_RELEASE}",
    '  [ "$version" = "$ADMITTED_SOURCE_V043_VERSION" ] \\':
        '  [ "$version" = "$ADMITTED_SOURCE_V092_VERSION" ] \\',
    '    && [ "$revision" = "$ADMITTED_SOURCE_V043_REVISION" ] \\':
        '    && [ "$revision" = "$ADMITTED_SOURCE_V092_REVISION" ] \\',
    '    && [ "$release" = "$ADMITTED_SOURCE_V043_RELEASE" ]':
        '    && [ "$release" = "$ADMITTED_SOURCE_V092_RELEASE" ]',
}

INSTALL_REPLACEMENTS = {
    '[ "$VERSION" = 0.6.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED >&2; exit 66; }':
        '[ "$VERSION" = 0.10.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED >&2; exit 66; }',
}


def _replace_exact(source: str, replacements: dict[str, str], kind: str) -> str:
    rendered = source
    for old, new in replacements.items():
        count = rendered.count(old)
        if count != 1:
            raise SystemExit(f"release_policy_{kind}_shape_rejected:{count}:{old[:48]}")
        rendered = rendered.replace(old, new, 1)
    return rendered


def _write_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".rendered")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def render(bootstrap_path: Path, install_path: Path) -> None:
    try:
        bootstrap = bootstrap_path.read_text(encoding="utf-8")
        installer = install_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit("release_policy_source_unavailable") from exc

    bootstrap = _replace_exact(bootstrap, BOOTSTRAP_REPLACEMENTS, "bootstrap")
    installer = _replace_exact(installer, INSTALL_REPLACEMENTS, "installer")
    required = (
        '[ "$TARGET_VERSION" = 0.10.0 ]',
        "ADMITTED_SOURCE_V092_VERSION=0.9.2",
        f"ADMITTED_SOURCE_V092_REVISION={SOURCE_REVISION}",
        f"ADMITTED_SOURCE_V092_RELEASE={SOURCE_RELEASE}",
    )
    if any(value not in bootstrap for value in required):
        raise SystemExit("release_policy_bootstrap_render_rejected")
    if '[ "$VERSION" = 0.10.0 ]' not in installer:
        raise SystemExit("release_policy_installer_render_rejected")
    _write_atomic(bootstrap_path, bootstrap)
    _write_atomic(install_path, installer)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: render-release-policy.py BOOTSTRAP INSTALLER")
    render(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()

