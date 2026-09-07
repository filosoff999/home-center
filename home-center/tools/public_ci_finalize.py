#!/usr/bin/env python3
"""Install the canonical self-verifying CI workflow into a public export tree."""

from __future__ import annotations

import argparse
from pathlib import Path


PUBLIC_CI = '''name: Public CI
on:
  push:
  pull_request:
permissions:
  contents: read
jobs:
  verify:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with: {persist-credentials: false}
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065
        with: {python-version: "3.12", cache: ""}
      - run: python3 tools/privacy_scan.py .
      - run: sha256sum -c PUBLIC_EXPORT_MANIFEST.sha256
      - run: python3 -m compileall -q product/control-plane/src/home_center deploy/runtime
      - name: Import runtime
        run: PYTHONPATH=product/control-plane/src python3 -c 'import home_center.server, home_center.api_v2, home_center.runtime'
      - name: Validate JSON
        run: |
          python3 -c 'import json,pathlib; [json.load(p.open(encoding="utf-8")) for p in pathlib.Path(".").rglob("*.json")]'
      - name: Reproducible artifact
        run: |
          bash deploy/scripts/build-public-artifact.sh "$RUNNER_TEMP/a"
          bash deploy/scripts/build-public-artifact.sh "$RUNNER_TEMP/b"
          cmp "$RUNNER_TEMP/a/"*.tar.gz "$RUNNER_TEMP/b/"*.tar.gz
          (cd "$RUNNER_TEMP/a" && sha256sum -c *.sha256)
'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    target = args.output.resolve() / ".github/workflows/ci.yml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(PUBLIC_CI, encoding="utf-8")
    target.chmod(0o644)
    print("PUBLIC_CI_FINALIZE=PASS")


if __name__ == "__main__":
    main()
