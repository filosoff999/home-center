#!/usr/bin/env python3
"""Recover the local administrator only from a physical Linux console."""

from __future__ import annotations

import argparse
import getpass
import grp
import os
import re
import secrets
import stat
import sys
from pathlib import Path


HERE = Path(__file__).resolve()
for candidate in (HERE.parent, HERE.parents[2] / "product/control-plane/src"):
    if (candidate / "home_center").is_dir():
        sys.path.insert(0, str(candidate))
        break

from home_center.config import load_config  # noqa: E402
from home_center.local_admin_recovery import LocalAdminRecoveryError, RecoveryEvidenceLog  # noqa: E402
from home_center.local_admin_rotation import LocalAdminCredentialRotator, LocalAdminRotationError  # noqa: E402


CONFIG_PATH = Path("/etc/home-center/config.json")
CREDENTIAL_PATH = Path("/etc/home-center/secrets/local-admin.json")
EVIDENCE_PATH = Path("/var/lib/home-center-recovery/events.jsonl")
SERVICE_GROUP = "home-center"
LOCAL_CONSOLE = re.compile(r"^/dev/tty(?:[1-9]|[1-5][0-9]|6[0-3])$")
POLICY_REJECTIONS = {
    "password_too_short",
    "password_letter_required",
    "password_digit_required",
    "password_rejected",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recover the Home Center local administrator from a physical console"
    )
    return parser.parse_args()


def _root_controlled_config() -> None:
    try:
        info = CONFIG_PATH.lstat()
    except OSError as exc:
        raise LocalAdminRecoveryError("recovery_config_unavailable") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != 0
        or info.st_mode & 0o022
    ):
        raise LocalAdminRecoveryError("recovery_config_metadata_rejected")


def _local_console_tty() -> str:
    streams = (sys.stdin, sys.stdout, sys.stderr)
    if any(not stream.isatty() for stream in streams):
        raise LocalAdminRecoveryError("physical_console_required")
    try:
        names = tuple(os.ttyname(stream.fileno()) for stream in streams)
    except (AttributeError, OSError, ValueError) as exc:
        raise LocalAdminRecoveryError("physical_console_required") from exc
    if len(set(names)) != 1 or LOCAL_CONSOLE.fullmatch(names[0]) is None:
        raise LocalAdminRecoveryError("physical_console_required")
    return names[0]


def _record(
    evidence: RecoveryEvidenceLog,
    *,
    actor_uid: int,
    target: str,
    tty: str,
    outcome: str,
    reason: str,
) -> str | None:
    try:
        return evidence.append(
            actor_uid=actor_uid,
            target=target,
            tty=tty,
            outcome=outcome,
            reason=reason,
        )
    except LocalAdminRecoveryError:
        return None


def main() -> int:
    parse_args()
    if os.geteuid() != 0:
        print("LOCAL_ADMIN_RECOVERY_REJECTED=ROOT_REQUIRED", file=sys.stderr)
        return 77
    try:
        tty = _local_console_tty()
        _root_controlled_config()
        config = load_config(CONFIG_PATH)
        if config.local_admin_credentials_file != CREDENTIAL_PATH:
            raise LocalAdminRecoveryError("recovery_credential_path_rejected")
        service_gid = grp.getgrnam(SERVICE_GROUP).gr_gid
        evidence = RecoveryEvidenceLog(EVIDENCE_PATH)
        # Verify the complete evidence chain before asking for a password.
        evidence.append(
            actor_uid=os.getuid(),
            target=config.node_id,
            tty=tty,
            outcome="requested",
            reason="recovery_console_verified",
        )
    except (KeyError, LocalAdminRecoveryError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, LocalAdminRecoveryError) else "recovery_preflight_failed"
        print(f"LOCAL_ADMIN_RECOVERY_REJECTED={code}", file=sys.stderr)
        return 78

    actor_uid = os.getuid()
    challenge = secrets.token_hex(3)
    phrase = f"RESET admin {challenge}"
    print("Home Center local administrator recovery")
    print(f"Type exactly: {phrase}")
    try:
        if input("Confirmation: ") != phrase:
            _record(
                evidence,
                actor_uid=actor_uid,
                target=config.node_id,
                tty=tty,
                outcome="denied",
                reason="operator_confirmation_failed",
            )
            print("LOCAL_ADMIN_RECOVERY_REJECTED=OPERATOR_CONFIRMATION_FAILED", file=sys.stderr)
            return 65
        password = getpass.getpass("New local administrator password: ")
        confirmation = getpass.getpass("Confirm new password: ")
    except (EOFError, KeyboardInterrupt):
        _record(
            evidence,
            actor_uid=actor_uid,
            target=config.node_id,
            tty=tty,
            outcome="denied",
            reason="operator_cancelled",
        )
        print("\nLOCAL_ADMIN_RECOVERY_REJECTED=OPERATOR_CANCELLED", file=sys.stderr)
        return 65

    if password != confirmation:
        password = ""
        confirmation = ""
        _record(
            evidence,
            actor_uid=actor_uid,
            target=config.node_id,
            tty=tty,
            outcome="denied",
            reason="password_confirmation_mismatch",
        )
        print("LOCAL_ADMIN_RECOVERY_REJECTED=PASSWORD_CONFIRMATION_MISMATCH", file=sys.stderr)
        return 65

    accepted_event_id: str | None = None

    def commit_evidence() -> None:
        nonlocal accepted_event_id
        accepted_event_id = evidence.append(
            actor_uid=actor_uid,
            target=config.node_id,
            tty=tty,
            outcome="accepted",
            reason="credential_reset_committed",
        )

    try:
        rotator = LocalAdminCredentialRotator(
            CREDENTIAL_PATH,
            expected_uid=0,
            expected_gid=service_gid,
            expected_mode=0o640,
            expected_directory_uid=0,
            expected_directory_gid=service_gid,
            expected_directory_mode=0o750,
        )
        committed = rotator.reset(password, commit_hook=commit_evidence)
    except LocalAdminRotationError as exc:
        outcome = "denied" if exc.code in POLICY_REJECTIONS else "failed"
        evidence_id = _record(
            evidence,
            actor_uid=actor_uid,
            target=config.node_id,
            tty=tty,
            outcome=outcome,
            reason=exc.code,
        )
        password = ""
        confirmation = ""
        if evidence_id is None:
            print("LOCAL_ADMIN_RECOVERY_FAILED=EVIDENCE_UNAVAILABLE", file=sys.stderr)
            return 75
        print(f"LOCAL_ADMIN_RECOVERY_REJECTED={exc.code}", file=sys.stderr)
        return 66
    finally:
        password = ""
        confirmation = ""

    if committed.username != "admin" or accepted_event_id is None:
        print("LOCAL_ADMIN_RECOVERY_FAILED=POSTCOMMIT_VALIDATION_FAILED", file=sys.stderr)
        return 75
    print(f"LOCAL_ADMIN_RECOVERY=PASS event_id={accepted_event_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
