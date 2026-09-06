"""Offline signed-channel coordinator for the Home Center 0.7 foundation.

The coordinator has no network client, no deployment primitive and no service
control.  It reads a fixed root-owned trust policy and signed stable snapshot,
verifies them through P2.4, admits an already-local artifact through the
content-addressed store, and only then atomically advances the anti-replay
checkpoint.  Production install activation remains separately gated.
"""

from __future__ import annotations

import hashlib
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator, Mapping

from .artifact_store import ArtifactStoreStatus, ContentAddressedArtifactStore
from .release_channel import (
    MAX_CHECKPOINT_BYTES,
    MAX_ENVELOPE_BYTES,
    MAX_TRUST_POLICY_BYTES,
    VerifiedLedger,
    canonical_json,
    checkpoint_for,
    load_strict_json,
    verify_envelope,
)
from .update_reconcile import SingleWriterLock


PRODUCTION_RELEASE_MANAGER_ENABLED = False
DEFAULT_TRUST_ROOT = Path("/etc/home-center/release")
DEFAULT_CHANNEL_ROOT = Path("/var/lib/home-center-release-channel")
TRUST_POLICY_NAME = "trust-policy.json"
ENVELOPE_DIRECTORY = "inbox"
ENVELOPE_NAME = "stable.dsse.json"
CHECKPOINT_NAME = "checkpoint.json"
LOCK_NAME = "manager.lock"
DIRECTORY_MODE = 0o700
TRUST_POLICY_MODE = 0o644
STATE_FILE_MODE = 0o600


class ReleaseManagerError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ReleaseManagerStatus(StrEnum):
    NO_STABLE = "no_stable"
    ARTIFACT_MISSING = "artifact_missing"
    ARTIFACT_VERIFIED = "artifact_verified"
    ADMITTED = "admitted"


@dataclass(frozen=True)
class SignedSnapshot:
    envelope: bytes
    trust_policy: Mapping[str, Any]
    trust_policy_sha256: str


@dataclass(frozen=True)
class LoadedCheckpoint:
    document: Mapping[str, Any]
    sha256: str


@dataclass(frozen=True)
class ReleaseManagerDecision:
    schema: str
    status: ReleaseManagerStatus
    generation: int
    ledger_sequence: int
    trust_policy_sha256: str
    version: str | None
    revision: str | None
    artifact_sha256: str | None
    artifact_bytes: int | None
    artifact_object_key: str | None
    checkpoint_sha256: str | None


def _reject(code: str) -> None:
    raise ReleaseManagerError(code)


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _file_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _validate_directory(path: Path, *, uid: int, gid: int, code: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReleaseManagerError(code) from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != uid
        or info.st_gid != gid
        or stat.S_IMODE(info.st_mode) != DIRECTORY_MODE
    ):
        _reject(code)
    try:
        descriptor = os.open(path, _directory_flags())
    except OSError as exc:
        raise ReleaseManagerError(code) from exc
    try:
        actual = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(actual.st_mode)
            or actual.st_uid != uid
            or actual.st_gid != gid
            or stat.S_IMODE(actual.st_mode) != DIRECTORY_MODE
        ):
            _reject(code)
    finally:
        os.close(descriptor)


def _read_fixed_file(
    path: Path,
    *,
    maximum: int,
    mode: int,
    uid: int,
    gid: int,
    code: str,
) -> bytes:
    try:
        descriptor = os.open(path, _file_flags())
    except OSError as exc:
        raise ReleaseManagerError(code) from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != uid
            or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) != mode
            or info.st_size < 1
            or info.st_size > maximum
        ):
            _reject(code)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(8192, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                _reject(code)
        data = b"".join(chunks)
        if len(data) != info.st_size:
            _reject(code)
        return data
    finally:
        os.close(descriptor)


class FixedSignedSnapshotSource:
    """Read the signed snapshot and public trust policy from fixed locations."""

    def __init__(
        self,
        *,
        trust_root: Path = DEFAULT_TRUST_ROOT,
        channel_root: Path = DEFAULT_CHANNEL_ROOT,
        expected_uid: int = 0,
        expected_gid: int = 0,
    ) -> None:
        self.trust_root = Path(trust_root)
        self.channel_root = Path(channel_root)
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid

    def load(self) -> SignedSnapshot:
        _validate_directory(
            self.trust_root,
            uid=self.expected_uid,
            gid=self.expected_gid,
            code="release_trust_root_rejected",
        )
        _validate_directory(
            self.channel_root,
            uid=self.expected_uid,
            gid=self.expected_gid,
            code="release_channel_root_rejected",
        )
        inbox = self.channel_root / ENVELOPE_DIRECTORY
        _validate_directory(
            inbox,
            uid=self.expected_uid,
            gid=self.expected_gid,
            code="release_channel_inbox_rejected",
        )
        policy_bytes = _read_fixed_file(
            self.trust_root / TRUST_POLICY_NAME,
            maximum=MAX_TRUST_POLICY_BYTES,
            mode=TRUST_POLICY_MODE,
            uid=self.expected_uid,
            gid=self.expected_gid,
            code="release_trust_policy_file_rejected",
        )
        envelope = _read_fixed_file(
            inbox / ENVELOPE_NAME,
            maximum=MAX_ENVELOPE_BYTES,
            mode=STATE_FILE_MODE,
            uid=self.expected_uid,
            gid=self.expected_gid,
            code="release_envelope_file_rejected",
        )
        policy = load_strict_json(policy_bytes, maximum=MAX_TRUST_POLICY_BYTES, kind="trust_policy")
        if not isinstance(policy, dict):
            _reject("release_trust_policy_shape_rejected")
        return SignedSnapshot(
            envelope=envelope,
            trust_policy=policy,
            trust_policy_sha256=hashlib.sha256(policy_bytes).hexdigest(),
        )


class ReleaseChannelCheckpointStore:
    """Atomic anti-replay checkpoint storage under one fixed state root."""

    def __init__(
        self,
        *,
        channel_root: Path = DEFAULT_CHANNEL_ROOT,
        expected_uid: int = 0,
        expected_gid: int = 0,
    ) -> None:
        self.channel_root = Path(channel_root)
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid

    def _validate_root(self) -> None:
        _validate_directory(
            self.channel_root,
            uid=self.expected_uid,
            gid=self.expected_gid,
            code="release_channel_root_rejected",
        )

    def load(self) -> LoadedCheckpoint | None:
        self._validate_root()
        path = self.channel_root / CHECKPOINT_NAME
        try:
            data = _read_fixed_file(
                path,
                maximum=MAX_CHECKPOINT_BYTES,
                mode=STATE_FILE_MODE,
                uid=self.expected_uid,
                gid=self.expected_gid,
                code="release_checkpoint_file_rejected",
            )
        except ReleaseManagerError as exc:
            if exc.code == "release_checkpoint_file_rejected" and not path.exists() and not path.is_symlink():
                return None
            raise
        document = load_strict_json(data, maximum=MAX_CHECKPOINT_BYTES, kind="checkpoint")
        if not isinstance(document, dict):
            _reject("release_checkpoint_shape_rejected")
        return LoadedCheckpoint(document=document, sha256=hashlib.sha256(data).hexdigest())

    @contextmanager
    def writer_lock(self) -> Iterator[None]:
        self._validate_root()
        lock = SingleWriterLock(
            self.channel_root / LOCK_NAME,
            expected_uid=self.expected_uid,
            expected_gid=self.expected_gid,
        )
        with lock:
            yield

    def save_verified(self, verified: VerifiedLedger, *, expected: LoadedCheckpoint | None) -> str:
        self._validate_root()
        current = self.load()
        if expected is None:
            if current is not None:
                _reject("release_checkpoint_changed")
        elif current is None or current.sha256 != expected.sha256:
            _reject("release_checkpoint_changed")

        payload = canonical_json(checkpoint_for(verified))
        if not payload or len(payload) > MAX_CHECKPOINT_BYTES:
            _reject("release_checkpoint_payload_rejected")
        path = self.channel_root / CHECKPOINT_NAME
        temporary_name = f".{CHECKPOINT_NAME}.{os.getpid()}.tmp"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        root_fd = os.open(self.channel_root, _directory_flags())
        try:
            try:
                descriptor = os.open(temporary_name, flags, STATE_FILE_MODE, dir_fd=root_fd)
            except OSError as exc:
                raise ReleaseManagerError("release_checkpoint_temp_rejected") from exc
            try:
                os.fchmod(descriptor, STATE_FILE_MODE)
                written = 0
                while written < len(payload):
                    count = os.write(descriptor, payload[written:])
                    if count <= 0:
                        _reject("release_checkpoint_write_rejected")
                    written += count
                os.fsync(descriptor)
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_uid != self.expected_uid
                    or info.st_gid != self.expected_gid
                    or stat.S_IMODE(info.st_mode) != STATE_FILE_MODE
                ):
                    _reject("release_checkpoint_temp_metadata_rejected")
            finally:
                os.close(descriptor)
            os.replace(temporary_name, CHECKPOINT_NAME, src_dir_fd=root_fd, dst_dir_fd=root_fd)
            os.fsync(root_fd)
        except Exception:
            try:
                os.unlink(temporary_name, dir_fd=root_fd)
            except OSError:
                pass
            raise
        finally:
            os.close(root_fd)
        return hashlib.sha256(payload).hexdigest()


class OfflineReleaseManager:
    """Verify and locally admit one signed stable release without deploying it."""

    def __init__(
        self,
        *,
        snapshots: FixedSignedSnapshotSource,
        checkpoints: ReleaseChannelCheckpointStore,
        artifacts: ContentAddressedArtifactStore,
    ) -> None:
        self.snapshots = snapshots
        self.checkpoints = checkpoints
        self.artifacts = artifacts

    def _verified(self, *, now: datetime | None) -> tuple[SignedSnapshot, LoadedCheckpoint | None, VerifiedLedger]:
        snapshot = self.snapshots.load()
        checkpoint = self.checkpoints.load()
        verified = verify_envelope(
            snapshot.envelope,
            snapshot.trust_policy,
            checkpoint=None if checkpoint is None else checkpoint.document,
            now=now,
        )
        return snapshot, checkpoint, verified

    @staticmethod
    def _decision(
        snapshot: SignedSnapshot,
        checkpoint: LoadedCheckpoint | None,
        verified: VerifiedLedger,
        status: ReleaseManagerStatus,
    ) -> ReleaseManagerDecision:
        release = verified.stable_release
        return ReleaseManagerDecision(
            schema="home-center.release-manager-decision.v1",
            status=status,
            generation=verified.generation,
            ledger_sequence=verified.ledger_sequence,
            trust_policy_sha256=snapshot.trust_policy_sha256,
            version=None if release is None else release.version,
            revision=None if release is None else release.revision,
            artifact_sha256=None if release is None else release.artifact_sha256,
            artifact_bytes=None if release is None else release.artifact_bytes,
            artifact_object_key=None if release is None else release.object_key,
            checkpoint_sha256=None if checkpoint is None else checkpoint.sha256,
        )

    def evaluate(self, *, now: datetime | None = None) -> ReleaseManagerDecision:
        snapshot, checkpoint, verified = self._verified(now=now)
        if verified.stable_release is None:
            return self._decision(snapshot, checkpoint, verified, ReleaseManagerStatus.NO_STABLE)
        store_status = self.artifacts.status(verified)
        status = (
            ReleaseManagerStatus.ARTIFACT_VERIFIED
            if store_status is ArtifactStoreStatus.VERIFIED
            else ReleaseManagerStatus.ARTIFACT_MISSING
        )
        return self._decision(snapshot, checkpoint, verified, status)

    def admit_local(self, *, now: datetime | None = None) -> ReleaseManagerDecision:
        """Admit local bytes and advance anti-replay state; never deploy nodes."""

        with self.checkpoints.writer_lock():
            snapshot, checkpoint, verified = self._verified(now=now)
            if verified.stable_release is None:
                return self._decision(snapshot, checkpoint, verified, ReleaseManagerStatus.NO_STABLE)
            if self.artifacts.status(verified) is ArtifactStoreStatus.MISSING:
                self.artifacts.admit(verified)
            self.artifacts.status(verified)
            checkpoint_sha = self.checkpoints.save_verified(verified, expected=checkpoint)
            decision = self._decision(snapshot, checkpoint, verified, ReleaseManagerStatus.ADMITTED)
            return ReleaseManagerDecision(
                schema=decision.schema,
                status=decision.status,
                generation=decision.generation,
                ledger_sequence=decision.ledger_sequence,
                trust_policy_sha256=decision.trust_policy_sha256,
                version=decision.version,
                revision=decision.revision,
                artifact_sha256=decision.artifact_sha256,
                artifact_bytes=decision.artifact_bytes,
                artifact_object_key=decision.artifact_object_key,
                checkpoint_sha256=checkpoint_sha,
            )
