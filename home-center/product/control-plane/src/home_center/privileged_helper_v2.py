from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from home_center import privileged_helper as base  # noqa: E402


base.ACTIONS["tls.web.activate.v1"] = base.Action(
    permission="tls.web.rotate",
    executable="/usr/bin/python3",
    argv=("/opt/home-center/current/home_center/tls_activate.py",),
    timeout_seconds=60,
)
base.PERMISSIONS = frozenset(action.permission for action in base.ACTIONS.values())


if __name__ == "__main__":
    raise SystemExit(base.main())
