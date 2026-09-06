from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_config
from .helper_client import HelperClientError, call_helper
from .tls_status import STATUS_FILE, status as tls_status

STATUS_SCHEMA = "home-center.tls-maintenance.v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_status(value: dict[str, Any]) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{STATUS_FILE.name}.", dir=str(STATUS_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, STATUS_FILE)
        directory_fd = os.open(STATUS_FILE.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _result(node_id: str, status: str, *, reason: str | None, action: str, certificate_sha256: str | None = None) -> dict[str, Any]:
    return {
        "schema": STATUS_SCHEMA,
        "checked_at": _utc_now(),
        "node_id": node_id,
        "status": status,
        "reason": reason,
        "action": action,
        "certificate_sha256": certificate_sha256,
    }


def maintain(*, activate_staged: bool = False) -> dict[str, Any]:
    config = load_config()
    before = tls_status(config)
    node_id = config.node_id
    candidate = before.get("candidate", {})
    if candidate.get("partial"):
        result = _result(node_id, "blocked", reason="partial_candidate", action="none")
        _atomic_status(result)
        return result

    renewal_due = bool(before.get("renewal", {}).get("due"))
    if not activate_staged and not renewal_due:
        result = _result(
            node_id,
            "healthy",
            reason=None,
            action="none",
            certificate_sha256=before.get("web", {}).get("fingerprint_sha256"),
        )
        _atomic_status(result)
        return result

    if not candidate.get("complete"):
        result = _result(node_id, "renewal_required", reason="candidate_not_staged", action="none")
        _atomic_status(result)
        return result

    try:
        helper = call_helper("tls.web.activate.v1", request_prefix="tls-maintenance")
    except (HelperClientError, OSError, ValueError, json.JSONDecodeError):
        result = _result(node_id, "failed", reason="helper_unavailable", action="activate")
        _atomic_status(result)
        return result

    if helper.get("status") != "succeeded" or helper.get("exit_code") != 0:
        result = _result(
            node_id,
            "failed",
            reason=str(helper.get("reason") or "activation_failed"),
            action="activate",
        )
        _atomic_status(result)
        return result

    try:
        activation = json.loads(str(helper.get("stdout") or ""))
    except json.JSONDecodeError:
        activation = None
    if not isinstance(activation, dict) or activation.get("schema") != "home-center.tls-activation-result.v1" or activation.get("status") != "activated":
        result = _result(node_id, "failed", reason="invalid_activation_result", action="activate")
        _atomic_status(result)
        return result

    expected = activation.get("certificate_sha256")
    try:
        after = tls_status(config)
    except Exception:
        result = _result(node_id, "failed", reason="post_activation_status_unavailable", action="activate")
        _atomic_status(result)
        return result
    web = after.get("web", {})
    if (
        not isinstance(expected, str)
        or len(expected) != 64
        or web.get("mode") != "separate-web-identity"
        or web.get("fingerprint_sha256") != expected
        or not web.get("chain_valid")
        or not web.get("hostname_match")
    ):
        result = _result(node_id, "failed", reason="post_activation_validation_failed", action="activate")
        _atomic_status(result)
        return result

    result = _result(node_id, "rotated", reason=None, action="activate", certificate_sha256=expected)
    _atomic_status(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--activate-staged", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = maintain(activate_staged=args.activate_staged)
    except Exception:
        result = _result("unknown", "failed", reason="maintenance_internal_error", action="none")
        try:
            _atomic_status(result)
        except OSError:
            pass
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 1 if result["status"] in {"failed", "blocked"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
