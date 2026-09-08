from __future__ import annotations

import sys
from pathlib import Path

ENTRYPOINT = Path(__file__)
HERE = ENTRYPOINT.resolve().parent
for candidate in (HERE, HERE.parents[1] / "product/control-plane/src"):
    package = candidate / "home_center"
    initialization = package / "__init__.py"
    if (
        package.is_dir()
        and not package.is_symlink()
        and initialization.is_file()
        and not initialization.is_symlink()
    ):
        sys.path.insert(0, str(candidate))
        break
else:
    raise SystemExit("HOME_CENTER_RUNTIME_LAYOUT_REJECTED")

from home_center.backup import main  # noqa: E402


if __name__ == "__main__":
    main()
