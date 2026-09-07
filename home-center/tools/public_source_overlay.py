#!/usr/bin/env python3
"""Apply deterministic public-only overlays to an exact release worktree.

The release commit itself is never modified. This script rewrites only known
infrastructure-bound validation literals before the allowlist exporter reads the
worktree, then verifies the expected old literal is gone.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def replace_exact(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"OVERLAY_SOURCE_MARKER_COUNT_REJECTED path={path} count={count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.source_root.resolve()

    actions = root / "product/control-plane/src/home_center/actions.py"
    replace_exact(
        actions,
        're.fullmatch(r"hm-dm-dc0[12]", target_node_id)',
        're.fullmatch(r"[a-z][a-z0-9._-]{2,63}", target_node_id)',
    )

    print("PUBLIC_SOURCE_OVERLAY=PASS")


if __name__ == "__main__":
    main()
