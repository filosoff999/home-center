from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from home_center import privileged_helper as base  # noqa: E402
from home_center.tls_activate import ACTIVATION_HELPER_TIMEOUT_SECONDS  # noqa: E402


base.ACTIONS["tls.web.activate.v1"] = base.Action(
    permission="tls.web.rotate",
    executable="/usr/bin/python3",
    argv=("/opt/home-center/current/home_center/tls_activate.py",),
    timeout_seconds=ACTIVATION_HELPER_TIMEOUT_SECONDS,
    timeout_requires_recovery=True,
)
base.ACTIONS["tls.web.reconcile.v1"] = base.Action(
    permission="tls.web.reconcile",
    executable="/usr/bin/python3",
    argv=("/opt/home-center/current/home_center/tls_reconcile.py",),
    timeout_seconds=60,
)
base.SECRET_ACTIONS["local-admin.password.rotate.v1"] = "local-admin.password.rotate"
base.SECRET_ACTIONS["local-admin.password.validate.v1"] = "local-admin.password.validate"
base.PERMISSIONS = frozenset(
    [*(action.permission for action in base.ACTIONS.values()), *base.SECRET_ACTIONS.values()]
)


if __name__ == "__main__":
    raise SystemExit(base.main())
