"""Persisted, fail-closed two-node update reconcile primitives.

P2.5 consumes the P2.4 :class:`VerifiedRelease` decision. This module is
intentionally incapable of downloading artifacts, running commands, changing
services, or enabling production deployment. It provides durable state, exact
identity checks, dc02-first transitions, anti-replay state, single-writer
locking, and bounded non-secret monitoring for a later explicitly activated
updater.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from .release_channel import VerifiedRelease


PRODUCTION_ACTIVATION_ENABLED = False
CHECKPOINT_SCHEMA = "home-center.update-reconcile-checkpoint.v1"
MAX_CHECKPOINT_BYTES = 64 * 1024

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
TRANSACTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
UTC_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
REASON = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class UpdateReconcileError(ValueError):
    """Stable, bounded rejection reason suitable for audit/status output."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ReconcilePhase(StrEnum):
    DISCOVER = "discover"
    ACQUIRE = "acquire"
    VERIFY = "verify"
    ADMIT = "admit"
    BACKUP_DC02 = "backup-dc02"
    UPDATE_DC02 = "update-dc02"
    CANARY_SOAK = "canary-soak"
    BACKUP_DC01 = "backup-dc01"
    UPDATE_DC01 = "update-dc01"
    CLUSTER_ACCEPT = "cluster-accept"
    CHECKPOINT = "checkpoint"
    SUCCEEDED = "succeeded"
    RECOVERY_REQUIRED = "recovery_required"


class ClusterUpdateStatus(StrEnum):
    CURRENT = "current"
    DRIFTED = "drifted"
    BLOCKED = "blocked"
    QUARANTINED = "quarantined"
    RECOVERY_REQUIRED = "recovery_required"


_FORWARD: dict[ReconcilePhase, ReconcilePhase] = {
    ReconcilePhase.DISCOVER: ReconcilePhase.ACQUIRE,
    ReconcilePhase.ACQUIRE: ReconcilePhase.VERIFY,
    ReconcilePhase.VERIFY: ReconcilePhase.ADMIT,
    ReconcilePhase.ADMIT: ReconcilePhase.BACKUP_DC02,
    ReconcilePhase.BACKUP_DC02: ReconcilePhase.UPDATE_DC02,
    ReconcilePhase.UPDATE_DC02: ReconcilePhase.CANARY_SOAK,
    ReconcilePhase.CANARY_SOAK: ReconcilePhase.BACKUP_DC01,
    ReconcilePhase.BACKUP_DC01: ReconcilePhase.UPDATE_DC01,
    ReconcilePhase.UPDATE_DC01: ReconcilePhase.CLUSTER_ACCEPT,
    ReconcilePhase.CLUSTER_ACCEPT: ReconcilePhase.CHECKPOINT,
    ReconcilePhase.CHECKPOINT: ReconcilePhase.SUCCEEDED,
}

_PHASE_RANK = {phase: index for index, phase in enumerate((*_FORWARD.keys(), ReconcilePhase.SUCCEEDED))}
_TERMINAL = {ReconcilePhase.SUCCEEDED, ReconcilePhase.RECOVERY_REQUIRED}


def _reject(code: str) -> None:
    raise UpdateReconcileError(code)


def _string(value: Any, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _reject(code)
    return value


def _parse_timestamp(value: Any, code: str) -> datetime:
    raw = _string(value, UTC_TIMESTAMP, code)
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise UpdateReconcileError(code) from exc


def _utc(now: datetime) -> str:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        _reject("timestamp_timezone_required")
    return now.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_clock_not_rolled_back(checkpoint: "UpdateCheckpoint", now: datetime) -> str:
    raw = _utc(now)
    if _parse_timestamp(raw, "timestamp_rejected") < _parse_timestamp(
        checkpoint.updated_at, "checkpoint_updated_at_rejected"
    ):
        _reject("clock_rollback_rejected")
    return raw


def _validate_release(release: VerifiedRelease) -> None:
    if not isinstance(release, VerifiedRelease):
        _reject("target_release_type_rejected")
    _string(release.version, SEMVER, "target_version_rejected")
    _string(release.revision, HEX40, "target_revision_rejected")
    _string(release.artifact_sha256, HEX64, "target_artifact_rejected")
    if isinstance(release.artifact_bytes, bool) or not isinstance(release.artifact_bytes, int) or release.artifact_bytes <= 0:
        _reject("target_artifact_rejected")
    _string(release.manifest_sha256, HEX64, "target_provenance_rejected")
    _string(release.record_sha256, HEX64, "target_provenance_rejected")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _reject("checkpoint_duplicate_json_key")
        result[key] = value
    return result


def _strict_json(data: bytes) -> Any:
    if not isinstance(data, bytes) or not data or len(data) > MAX_CHECKPOINT_BYTES:
        _reject("checkpoint_json_size_rejected")
    if data.startswith(b"\xef\xbb\xbf"):
        _reject("checkpoint_json_bom_rejected")
    try:
        return json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_float=lambda _: _reject("checkpoint_json_float_rejected"),
            parse_constant=lambda _: _reject("checkpoint_json_constant_rejected"),
        )
    except UpdateReconcileError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise UpdateReconcileError("checkpoint_json_rejected") from exc


@dataclass(frozen=True)
class NodeReleaseIdentity:
    """Exact non-secret release/health identity observed on one node."""

    node_id: str
    version: str
    revision: str
    artifact_sha256: str
    ready: bool
    web_ca_sha256: str
    web_leaf_sha256: str
    web_public_key_sha256: str
    peer_ca_sha256: str
    peer_certificate_sha256: str
    peer_public_key_sha256: str

    def validate(self) -> None:
        if self.node_id not in {"dc01", "dc02"}:
            _reject("node_id_rejected")
        _string(self.version, SEMVER, "node_version_rejected")
        _string(self.revision, HEX40, "node_revision_rejected")
        for value in (
            self.artifact_sha256,
            self.web_ca_sha256,
            self.web_leaf_sha256,
            self.web_public_key_sha256,
            self.peer_ca_sha256,
            self.peer_certificate_sha256,
            self.peer_public_key_sha256,
        ):
            _string(value, HEX64, "node_digest_rejected")
        if not isinstance(self.ready, bool):
            _reject("node_ready_rejected")

    def matches(self, release: VerifiedRelease) -> bool:
        self.validate()
        _validate_release(release)
        return (
            self.version == release.version
            and self.revision == release.revision
            and self.artifact_sha256 == release.artifact_sha256
        )

    def cryptographic_identity(self) -> tuple[str, ...]:
        self.validate()
        return (
            self.web_ca_sha256,
            self.web_leaf_sha256,
            self.web_public_key_sha256,
            self.peer_ca_sha256,
            self.peer_certificate_sha256,
            self.peer_public_key_sha256,
        )


@dataclass(frozen=True)
class UpdateCheckpoint:
    schema: str
    transaction_id: str
    phase: ReconcilePhase
    ledger_sequence: int
    target_version: str
    target_revision: str
    target_artifact_sha256: str
    stable_record_sha256: str
    started_at: str
    updated_at: str
    dc02_complete: bool
    dc01_complete: bool
    quarantined_artifact_sha256: str | None = None
    recovery_reason: str | None = None

    def validate(self) -> None:
        if self.schema != CHECKPOINT_SCHEMA:
            _reject("checkpoint_schema_rejected")
        _string(self.transaction_id, TRANSACTION_ID, "checkpoint_transaction_rejected")
        if not isinstance(self.phase, ReconcilePhase):
            _reject("checkpoint_phase_rejected")
        if isinstance(self.ledger_sequence, bool) or not isinstance(self.ledger_sequence, int) or self.ledger_sequence < 0:
            _reject("checkpoint_sequence_rejected")
        _string(self.target_version, SEMVER, "checkpoint_target_rejected")
        _string(self.target_revision, HEX40, "checkpoint_target_rejected")
        _string(self.target_artifact_sha256, HEX64, "checkpoint_digest_rejected")
        _string(self.stable_record_sha256, HEX64, "checkpoint_digest_rejected")
        started = _parse_timestamp(self.started_at, "checkpoint_started_at_rejected")
        updated = _parse_timestamp(self.updated_at, "checkpoint_updated_at_rejected")
        if updated < started:
            _reject("checkpoint_clock_rejected")
        if not isinstance(self.dc02_complete, bool) or not isinstance(self.dc01_complete, bool):
            _reject("checkpoint_completion_rejected")
        if self.dc01_complete and not self.dc02_complete:
            _reject("checkpoint_dc01_before_dc02_rejected")
        if self.quarantined_artifact_sha256 is not None:
            _string(self.quarantined_artifact_sha256, HEX64, "checkpoint_quarantine_rejected")
        if self.recovery_reason is not None:
            _string(self.recovery_reason, REASON, "checkpoint_recovery_reason_rejected")
        if self.phase is ReconcilePhase.RECOVERY_REQUIRED and self.recovery_reason is None:
            _reject("checkpoint_recovery_reason_required")
        if self.phase is not ReconcilePhase.RECOVERY_REQUIRED and self.recovery_reason is not None:
            _reject("checkpoint_recovery_reason_unexpected")
        if self.phase is ReconcilePhase.SUCCEEDED and not (self.dc02_complete and self.dc01_complete):
            _reject("checkpoint_success_incomplete")

    def target_matches(self, release: VerifiedRelease) -> bool:
        self.validate()
        _validate_release(release)
        return (
            self.target_version == release.version
            and self.target_revision == release.revision
            and self.target_artifact_sha256 == release.artifact_sha256
            and self.stable_record_sha256 == release.record_sha256
        )

    def to_document(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": self.schema,
            "transaction_id": self.transaction_id,
            "phase": self.phase.value,
            "ledger_sequence": self.ledger_sequence,
            "target_version": self.target_version,
            "target_revision": self.target_revision,
            "target_artifact_sha256": self.target_artifact_sha256,
            "stable_record_sha256": self.stable_record_sha256,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "dc02_complete": self.dc02_complete,
            "dc01_complete": self.dc01_complete,
            "quarantined_artifact_sha256": self.quarantined_artifact_sha256,
            "recovery_reason": self.recovery_reason,
        }

    @classmethod
    def from_document(cls, document: Any) -> "UpdateCheckpoint":
        keys = {
            "schema",
            "transaction_id",
            "phase",
            "ledger_sequence",
            "target_version",
            "target_revision",
            "target_artifact_sha256",
            "stable_record_sha256",
            "started_at",
            "updated_at",
            "dc02_complete",
            "dc01_complete",
            "quarantined_artifact_sha256",
            "recovery_reason",
        }
        if not isinstance(document, dict) or set(document) != keys:
            _reject("checkpoint_shape_rejected")
        try:
            phase = ReconcilePhase(document["phase"])
        except (TypeError, ValueError) as exc:
            raise UpdateReconcileError("checkpoint_phase_rejected") from exc
        checkpoint = cls(
            schema=document["schema"],
            transaction_id=document["transaction_id"],
            phase=phase,
            ledger_sequence=document["ledger_sequence"],
            target_version=document["target_version"],
            target_revision=document["target_revision"],
            target_artifact_sha256=document["target_artifact_sha256"],
            stable_record_sha256=document["stable_record_sha256"],
            started_at=document["started_at"],
            updated_at=document["updated_at"],
            dc02_complete=document["dc02_complete"],
            dc01_complete=document["dc01_complete"],
            quarantined_artifact_sha256=document["quarantined_artifact_sha256"],
            recovery_reason=document["recovery_reason"],
        )
        checkpoint.validate()
        return checkpoint


def new_checkpoint(
    transaction_id: str,
    release: VerifiedRelease,
    *,
    ledger_sequence: int,
    now: datetime,
) -> UpdateCheckpoint:
    _validate_release(release)
    if isinstance(ledger_sequence, bool) or not isinstance(ledger_sequence, int) or ledger_sequence < 0:
        _reject("stable_sequence_rejected")
    timestamp = _utc(now)
    checkpoint = UpdateCheckpoint(
        schema=CHECKPOINT_SCHEMA,
        transaction_id=transaction_id,
        phase=ReconcilePhase.DISCOVER,
        ledger_sequence=ledger_sequence,
        target_version=release.version,
        target_revision=release.revision,
        target_artifact_sha256=release.artifact_sha256,
        stable_record_sha256=release.record_sha256,
        started_at=timestamp,
        updated_at=timestamp,
        dc02_complete=False,
        dc01_complete=False,
    )
    checkpoint.validate()
    return checkpoint


def ensure_monotonic_stable(
    release: VerifiedRelease,
    *,
    ledger_sequence: int,
    previous: UpdateCheckpoint | None,
) -> None:
    """Reject replay, sequence rollback, or same-sequence equivocation."""

    _validate_release(release)
    if isinstance(ledger_sequence, bool) or not isinstance(ledger_sequence, int) or ledger_sequence < 0:
        _reject("stable_sequence_rejected")
    if previous is None:
        return
    previous.validate()
    if ledger_sequence < previous.ledger_sequence:
        _reject("stable_sequence_rollback")
    if ledger_sequence == previous.ledger_sequence and not previous.target_matches(release):
        _reject("stable_sequence_equivocation")


def advance(checkpoint: UpdateCheckpoint, *, now: datetime) -> UpdateCheckpoint:
    """Advance exactly one proved step; skipping or resuming ambiguity is impossible."""

    checkpoint.validate()
    if checkpoint.phase in _TERMINAL:
        _reject("checkpoint_terminal")
    target = _FORWARD.get(checkpoint.phase)
    if target is None:
        _reject("checkpoint_transition_rejected")
    dc02_complete = checkpoint.dc02_complete or target is ReconcilePhase.CANARY_SOAK
    dc01_complete = checkpoint.dc01_complete or target is ReconcilePhase.CLUSTER_ACCEPT
    result = replace(
        checkpoint,
        phase=target,
        updated_at=_ensure_clock_not_rolled_back(checkpoint, now),
        dc02_complete=dc02_complete,
        dc01_complete=dc01_complete,
    )
    result.validate()
    return result


def require_recovery(checkpoint: UpdateCheckpoint, reason: str, *, now: datetime) -> UpdateCheckpoint:
    checkpoint.validate()
    _string(reason, REASON, "recovery_reason_rejected")
    result = replace(
        checkpoint,
        phase=ReconcilePhase.RECOVERY_REQUIRED,
        updated_at=_ensure_clock_not_rolled_back(checkpoint, now),
        recovery_reason=reason,
    )
    result.validate()
    return result


def quarantine(checkpoint: UpdateCheckpoint, artifact_sha256: str, *, now: datetime) -> UpdateCheckpoint:
    checkpoint.validate()
    _string(artifact_sha256, HEX64, "quarantine_digest_rejected")
    return require_recovery(
        replace(checkpoint, quarantined_artifact_sha256=artifact_sha256),
        "artifact_quarantined",
        now=now,
    )


def identities_preserved(before: NodeReleaseIdentity, after: NodeReleaseIdentity) -> bool:
    """Prove Web and peer PKI identity continuity across an update/rollback."""

    before.validate()
    after.validate()
    if before.node_id != after.node_id:
        _reject("identity_node_mismatch")
    return before.cryptographic_identity() == after.cryptographic_identity()


def classify_cluster(
    release: VerifiedRelease,
    dc01: NodeReleaseIdentity,
    dc02: NodeReleaseIdentity,
    checkpoint: UpdateCheckpoint | None,
) -> ClusterUpdateStatus:
    """Return bounded monitoring state without performing any mutation."""

    _validate_release(release)
    dc01.validate()
    dc02.validate()
    if dc01.node_id != "dc01" or dc02.node_id != "dc02":
        _reject("node_order_rejected")
    if checkpoint is not None:
        checkpoint.validate()
        if checkpoint.phase is ReconcilePhase.RECOVERY_REQUIRED:
            return ClusterUpdateStatus.RECOVERY_REQUIRED
        if checkpoint.quarantined_artifact_sha256 == release.artifact_sha256:
            return ClusterUpdateStatus.QUARANTINED
        if checkpoint.phase is not ReconcilePhase.SUCCEEDED and not checkpoint.target_matches(release):
            return ClusterUpdateStatus.RECOVERY_REQUIRED
    if not dc01.ready or not dc02.ready:
        return ClusterUpdateStatus.BLOCKED
    if dc01.matches(release) and dc02.matches(release):
        return ClusterUpdateStatus.CURRENT
    return ClusterUpdateStatus.DRIFTED


class SingleWriterLock:
    """Non-blocking flock protecting one reconcile writer."""

    def __init__(self, path: Path, *, expected_uid: int = 0, expected_gid: int = 0) -> None:
        self.path = Path(path)
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid
        self._fd: int | None = None

    def __enter__(self) -> "SingleWriterLock":
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise UpdateReconcileError("reconcile_lock_open_rejected") from exc
        try:
            os.fchmod(fd, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                _reject("reconcile_lock_type_rejected")
            if info.st_uid != self.expected_uid or info.st_gid != self.expected_gid:
                _reject("reconcile_lock_owner_rejected")
            if stat.S_IMODE(info.st_mode) != 0o600:
                _reject("reconcile_lock_mode_rejected")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise UpdateReconcileError("reconcile_lock_busy") from exc
        except Exception:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


class CheckpointStore:
    """Atomic, bounded, no-symlink checkpoint persistence.

    Production callers use the defaults (root:root, 0600). Tests may pass the
    runner uid/gid while exercising the same file-safety semantics.
    """

    def __init__(self, path: Path, *, expected_uid: int = 0, expected_gid: int = 0) -> None:
        self.path = Path(path)
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid

    def _validate_parent(self) -> None:
        try:
            info = self.path.parent.stat(follow_symlinks=False)
        except OSError as exc:
            raise UpdateReconcileError("checkpoint_parent_unavailable") from exc
        if not stat.S_ISDIR(info.st_mode) or self.path.parent.is_symlink():
            _reject("checkpoint_parent_rejected")
        if info.st_uid != self.expected_uid or info.st_gid != self.expected_gid:
            _reject("checkpoint_parent_owner_rejected")

    def _validate_file_stat(self, info: os.stat_result) -> None:
        if not stat.S_ISREG(info.st_mode):
            _reject("checkpoint_file_type_rejected")
        if info.st_uid != self.expected_uid or info.st_gid != self.expected_gid:
            _reject("checkpoint_file_owner_rejected")
        if stat.S_IMODE(info.st_mode) != 0o600:
            _reject("checkpoint_file_mode_rejected")
        if not 0 < info.st_size <= MAX_CHECKPOINT_BYTES:
            _reject("checkpoint_file_size_rejected")

    def load(self) -> UpdateCheckpoint | None:
        self._validate_parent()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(self.path, flags)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise UpdateReconcileError("checkpoint_open_rejected") from exc
        try:
            info = os.fstat(fd)
            self._validate_file_stat(info)
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, min(8192, MAX_CHECKPOINT_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_CHECKPOINT_BYTES:
                    _reject("checkpoint_read_rejected")
            data = b"".join(chunks)
            if len(data) != info.st_size:
                _reject("checkpoint_read_rejected")
        finally:
            os.close(fd)
        return UpdateCheckpoint.from_document(_strict_json(data))

    def _validate_progress(self, current: UpdateCheckpoint, candidate: UpdateCheckpoint) -> None:
        current.validate()
        candidate.validate()
        if candidate.ledger_sequence < current.ledger_sequence:
            _reject("checkpoint_sequence_rollback")
        if candidate.ledger_sequence == current.ledger_sequence:
            same_target = (
                candidate.target_version == current.target_version
                and candidate.target_revision == current.target_revision
                and candidate.target_artifact_sha256 == current.target_artifact_sha256
                and candidate.stable_record_sha256 == current.stable_record_sha256
            )
            if not same_target:
                _reject("checkpoint_sequence_equivocation")
        if candidate.transaction_id == current.transaction_id:
            if current.phase is ReconcilePhase.RECOVERY_REQUIRED and candidate != current:
                _reject("checkpoint_recovery_overwrite_rejected")
            if candidate.phase is not ReconcilePhase.RECOVERY_REQUIRED:
                old_rank = _PHASE_RANK.get(current.phase)
                new_rank = _PHASE_RANK.get(candidate.phase)
                if old_rank is None or new_rank is None or new_rank < old_rank:
                    _reject("checkpoint_phase_rollback")
        elif current.phase not in {ReconcilePhase.SUCCEEDED}:
            _reject("checkpoint_overlapping_transaction")

    def save(self, checkpoint: UpdateCheckpoint) -> None:
        self._validate_parent()
        checkpoint.validate()
        current = self.load()
        if current is not None:
            self._validate_progress(current, checkpoint)
        payload = json.dumps(
            checkpoint.to_document(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        if not payload or len(payload) > MAX_CHECKPOINT_BYTES:
            _reject("checkpoint_payload_size_rejected")

        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(temporary, flags, 0o600)
        except OSError as exc:
            raise UpdateReconcileError("checkpoint_temp_create_rejected") from exc
        try:
            os.fchmod(fd, 0o600)
            written = 0
            while written < len(payload):
                count = os.write(fd, payload[written:])
                if count <= 0:
                    _reject("checkpoint_write_rejected")
                written += count
            os.fsync(fd)
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            finally:
                os.close(fd)
            raise
        else:
            os.close(fd)

        try:
            info = temporary.stat(follow_symlinks=False)
            self._validate_file_stat(info)
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
