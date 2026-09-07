#!/usr/bin/env python3
"""Portable privacy/secret scan embedded into the public repository.

Private deployment literals are checked by the stricter source-side exporter in
the private repository. The public copy must not embed those private literals in
its scanner, so this portable gate focuses on credential material, token shapes,
and legacy environment-bound node identifiers while allowing standard RFC1918
network constants required by generic reverse-proxy validation.
"""

from __future__ import annotations

import pathlib
import re
import sys

PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github-token", re.compile(r"(?:github_pat_|ghp_)[A-Za-z0-9_]{20,}")),
    ("openai-key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("legacy-environment-node", re.compile(r"(?i)dc0[12]")),
)


def main() -> None:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    failed: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name, pattern in PATTERNS:
            if pattern.search(text):
                failed.append(f"{path.relative_to(root)}: {name}")
    if failed:
        print("PUBLIC_PRIVACY_SCAN_FAILED", file=sys.stderr)
        print("\n".join(failed), file=sys.stderr)
        raise SystemExit(1)
    print("PUBLIC_PRIVACY_SCAN=PASS")


if __name__ == "__main__":
    main()
