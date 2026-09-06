#!/usr/bin/env python3
"""Render the exact release transition policy into the staged bootstrap.

The cluster bootstrap is deliberately large and security-reviewed. Release
cuts therefore change only the target/predecessor admission literals and fail
closed if the reviewed source no longer matches the expected predecessor
shape. The rendered bootstrap is what ships in the immutable artifact and is
covered by MANIFEST.sha256 and reproducible-build checks.
"""

from __future__ import annotations

import sys
from pathlib import Path


TARGET_VERSION = "0.6.0"
SOURCE_VERSION = "0.5.0"
SOURCE_REVISION = "1d1ff0be759667c40361bbd04b9da273a778b9c8"
SOURCE_RELEASE = "/opt/home-center/releases/0.5.0-1d1ff0be7596-3898daba8711"

REPLACEMENTS = {
    '[ "$TARGET_VERSION" = 0.5.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED >&2; exit 66; }':
        '[ "$TARGET_VERSION" = 0.6.0 ] || { echo RELEASE_VERSION_NOT_ADMITTED >&2; exit 66; }',
    "ADMITTED_SOURCE_V043_VERSION=0.4.3": "ADMITTED_SOURCE_V050_VERSION=0.5.0",
    "ADMITTED_SOURCE_V043_REVISION=64f798ceae0b669cbac01b452c3cf4fd96070136":
        f"ADMITTED_SOURCE_V050_REVISION={SOURCE_REVISION}",
    "ADMITTED_SOURCE_V043_RELEASE=/opt/home-center/releases/0.4.3-64f798ceae0b-b2dde6a51ec9":
        f"ADMITTED_SOURCE_V050_RELEASE={SOURCE_RELEASE}",
    '  [ "$version" = "$ADMITTED_SOURCE_V043_VERSION" ] \\':
        '  [ "$version" = "$ADMITTED_SOURCE_V050_VERSION" ] \\',
    '    && [ "$revision" = "$ADMITTED_SOURCE_V043_REVISION" ] \\':
        '    && [ "$revision" = "$ADMITTED_SOURCE_V050_REVISION" ] \\',
    '    && [ "$release" = "$ADMITTED_SOURCE_V043_RELEASE" ]':
        '    && [ "$release" = "$ADMITTED_SOURCE_V050_RELEASE" ]',
}


def render(path: Path) -> None:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit("bootstrap_policy_source_unavailable") from exc

    rendered = source
    for old, new in REPLACEMENTS.items():
        count = rendered.count(old)
        if count != 1:
            raise SystemExit(f"bootstrap_policy_source_shape_rejected:{count}:{old[:48]}")
        rendered = rendered.replace(old, new, 1)

    forbidden = (
        "ADMITTED_SOURCE_V043_",
        '[ "$TARGET_VERSION" = 0.5.0 ]',
    )
    if any(value in rendered for value in forbidden):
        raise SystemExit("bootstrap_policy_stale_admission_rejected")
    required = (
        '[ "$TARGET_VERSION" = 0.6.0 ]',
        "ADMITTED_SOURCE_V050_VERSION=0.5.0",
        f"ADMITTED_SOURCE_V050_REVISION={SOURCE_REVISION}",
        f"ADMITTED_SOURCE_V050_RELEASE={SOURCE_RELEASE}",
    )
    if any(value not in rendered for value in required):
        raise SystemExit("bootstrap_policy_render_rejected")

    temporary = path.with_name(path.name + ".rendered")
    temporary.write_text(rendered, encoding="utf-8", newline="\n")
    temporary.replace(path)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: render-bootstrap-policy.py PATH")
    render(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
