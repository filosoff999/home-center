#!/usr/bin/env python3
"""Apply deterministic public-only overlays to an exact release worktree.

The release commit itself is never modified. Known infrastructure-bound legacy
literals are generalized before the allowlist exporter reads the worktree. If a
future release already contains the generic form, the overlay is a verified
no-op. Any unknown shape fails closed.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def replace_or_verify(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == 1 and new_count == 0:
        path.write_text(text.replace(old, new), encoding="utf-8")
        return
    if old_count == 0 and new_count == 1:
        return
    raise SystemExit(
        f"OVERLAY_SOURCE_MARKER_STATE_REJECTED path={path} old_count={old_count} new_count={new_count}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.source_root.resolve()

    actions = root / "product/control-plane/src/home_center/actions.py"
    replace_or_verify(
        actions,
        're.fullmatch(r"hm-dm-dc0[12]", target_node_id)',
        're.fullmatch(r"[a-z][a-z0-9._-]{2,63}", target_node_id)',
    )

    print("PUBLIC_SOURCE_OVERLAY=PASS")


if __name__ == "__main__":
    main()
