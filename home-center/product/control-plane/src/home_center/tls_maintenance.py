from __future__ import annotations

import argparse
import json
import os
import re
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


def _result(
    node_id: str,
    status: str,
    *,
    reason: str | None,
    action: str,
    certificate_sha256: str | None = None,
    release: str | None = None,
    previous_release: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": STATUS_SCHEMA,
        "checked_at": _utc_now(),
        "node_id": node_id,
        "status": status,
        "reason": reason,
        "action": action,
        "certificate_sha256": certificate_sha256,
        "release": release,
        "previous_release": previous_release,
    }


def _reconcile(node_id: str) -> bool:
    try:
        reconciliation = call_helper("tls.web.reconcile.v1", request_prefix="tls-reconcile")
        evidence = json.loads(str(reconciliation.get("stdout") or ""))
    except (HelperClientError, OSError, ValueError, json.JSONDecodeError):
        return False
    if not (
        reconciliation.get("status") == "succeeded"
        and reconciliation.get("exit_code") == 0
        and isinstance(evidence, dict)
        and set(evidence) == {"schema", "status", "node_id", "mode", "release", "certificate_sha256"}
        and evidence.get("schema") == "home-center.tls-reconcile-result.v1"
        and evidence.get("status") == "reconciled"
        and evidence.get("node_id") == node_id
        and evidence.get("mode")
        in {"legacy-peer-fallback", "separate-web-identity", "quarantined-peer-web-identity"}
        and isinstance(evidence.get("certificate_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", evidence["certificate_sha256"]) is not None
    ):
        return False
    release = evidence.get("release")
    return bool(
        (evidence["mode"] == "legacy-peer-fallback" and release == "legacy")
        or (
            evidence["mode"] in {"separate-web-identity", "quarantined-peer-web-identity"}
            and isinstance(release, str)
            and re.fullmatch(r"[0-9a-f]{24}", release) is not None
            and release == evidence["certificate_sha256"][:24]
        )
    )


def maintain(*, activate_staged: bool = False) -> dict[str, Any]:
    config = load_config()
    before = tls_status(config)
    node_id = config.node_id
    candidate = before.get("candidate", {})
    last_maintenance = before.get("renewal", {}).get("last_maintenance")
    if (
        isinstance(last_maintenance, dict)
        and last_maintenance.get("action") == "activate"
        and last_maintenance.get("status") in {"unknown", "blocked"}
        and str(last_maintenance.get("reason") or "").endswith("recovery_required")
    ):
        if not _reconcile(node_id):
            result = _result(node_id, "blocked", reason="maintenance_recovery_required", action="activate")
            _atomic_status(result)
            return result
        try:
            before = tls_status(config)
            candidate = before.get("candidate", {})
        except Exception:
            result = _result(node_id, "blocked", reason="maintenance_recovery_required", action="activate")
            _atomic_status(result)
            return result
    if candidate.get("partial"):
        if _reconcile(node_id):
            try:
                before = tls_status(config)
                candidate = before.get("candidate", {})
            except Exception:
                candidate = {"partial": True}
        if candidate.get("partial"):
            result = _result(node_id, "blocked", reason="partial_candidate_recovery_required", action="reconcile")
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
        result = _result(node_id, "unknown", reason="helper_result_unavailable_recovery_required", action="activate")
        _atomic_status(result)
        return result

    helper_status = helper.get("status")
    helper_reason = str(helper.get("reason") or "activation_failed")
    if helper_status == "unknown":
        reason = (
            helper_reason
            if helper_reason in {
                "action_timeout_recovery_required",
                "interrupted_execution_requires_operator_recovery",
                "rollback_failed_recovery_required",
            }
            else "activation_result_unknown_recovery_required"
        )
        result = _result(node_id, "unknown", reason=reason, action="activate")
        _atomic_status(result)
        return result
    if helper_status == "rejected" and helper_reason == "mutation_recovery_required":
        result = _result(node_id, "blocked", reason="mutation_recovery_required", action="activate")
        _atomic_status(result)
        return result
    if helper_status != "succeeded" or helper.get("exit_code") != 0:
        if helper_reason == "activation_rolled_back":
            result = _result(node_id, "rolled_back", reason=helper_reason, action="activate")
            _atomic_status(result)
            return result
        result = _result(
            node_id,
            "failed",
            reason=helper_reason,
            action="activate",
        )
        _atomic_status(result)
        return result

    try:
        activation = json.loads(str(helper.get("stdout") or ""))
    except json.JSONDecodeError:
        activation = None
    if not isinstance(activation, dict) or activation.get("schema") != "home-center.tls-activation-result.v1" or activation.get("status") != "activated":
        result = _result(node_id, "unknown", reason="invalid_activation_result_recovery_required", action="activate")
        _atomic_status(result)
        return result

    expected = activation.get("certificate_sha256")
    release = activation.get("release")
    previous_release = activation.get("previous_release")
    if (
        not isinstance(expected, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected) is None
        or release != expected[:24]
        or not isinstance(release, str)
        or (previous_release is not None and (not isinstance(previous_release, str) or re.fullmatch(r"[0-9a-f]{24}", previous_release) is None))
    ):
        result = _result(node_id, "unknown", reason="invalid_activation_identity_recovery_required", action="activate")
        _atomic_status(result)
        return result
    try:
        after = tls_status(config)
    except Exception:
        result = _result(node_id, "unknown", reason="post_activation_status_unavailable_recovery_required", action="activate")
        _atomic_status(result)
        return result
    web = after.get("web", {})
    if (
        web.get("mode") != "separate-web-identity"
        or web.get("fingerprint_sha256") != expected
        or not web.get("chain_valid")
        or not web.get("hostname_match")
        or not web.get("profile_valid")
        or not web.get("san_policy_valid")
        or bool(after.get("renewal", {}).get("due"))
    ):
        result = _result(
            node_id,
            "unknown",
            reason="post_activation_validation_failed_recovery_required",
            action="activate",
            certificate_sha256=expected,
            release=release,
            previous_release=previous_release,
        )
        _atomic_status(result)
        return result

    result = _result(
        node_id,
        "rotated",
        reason=None,
        action="activate",
        certificate_sha256=expected,
        release=release,
        previous_release=previous_release,
    )
    _atomic_status(result)
    return result


def reconcile_state() -> dict[str, Any]:
    config = load_config()
    if _reconcile(config.node_id):
        result = _result(config.node_id, "healthy", reason=None, action="reconcile")
    else:
        result = _result(
            config.node_id,
            "blocked",
            reason="maintenance_recovery_required",
            action="reconcile",
        )
    _atomic_status(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--activate-staged", action="store_true")
    mode.add_argument("--reconcile", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = reconcile_state() if args.reconcile else maintain(activate_staged=args.activate_staged)
    except Exception:
        result = _result("unknown", "failed", reason="maintenance_internal_error", action="none")
        try:
            _atomic_status(result)
        except OSError:
            pass
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 1 if result["status"] in {"failed", "blocked", "unknown", "rolled_back"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
