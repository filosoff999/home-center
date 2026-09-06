"""Bounded local content-addressed artifact admission for Home Center releases.

This module has deliberately no network client and no caller-supplied source or
destination path.  A signed stable release may only be admitted from the fixed
``inbox/<signed filename>`` into the fixed content-addressed object root derived
from its signed ``object_key``.  Full archive/manifest/release verification is
delegated to the P2.4 verifier after publication.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .release_channel import MAX_ARCHIVE_TOTAL_BYTES, ReleaseChannelError, VerifiedLedger, verify_artifact


DEFAULT_STORE_ROOT = Path("/var/lib/home-center-release-store/objects")
DEFAULT_INBOX_ROOT = Path("/var/lib/home-center-release-store/inbox")
DIRECTORY_MODE = 0o700
OBJECT_MODE = 0o600
COPY_CHUNK_BYTES = 1024 * 1024


class ArtifactStoreError(ValueError):
    """Stable non-secret store rejection suitable for audit/status output."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ArtifactStoreStatus(StrEnum):
    MISSING = "missing"
    VERIFIED = "verified"


@dataclass(frozen=True)
class AdmittedArtifact:
    schema: str
    version: str
    revision: str
    artifact_sha256: str
    artifact_bytes: int
    object_key: str
    reused: bool


def _reject(code: str) -> None:
    raise ArtifactStoreError(code)


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _file_read_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


class ContentAddressedArtifactStore:
    """Root-owned, no-symlink local release artifact store.

    Production uses the defaults and therefore exposes no path-selection API.
    Tests may inject temporary roots and the current uid/gid while exercising
    the same filesystem invariants.
    """

    def __init__(
        self,
        *,
        object_root: Path = DEFAULT_STORE_ROOT,
        inbox_root: Path = DEFAULT_INBOX_ROOT,
        expected_uid: int = 0,
        expected_gid: int = 0,
    ) -> None:
        self.object_root = Path(object_root)
        self.inbox_root = Path(inbox_root)
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid

    def _validate_directory_info(self, info: os.stat_result, code: str) -> None:
        if not stat.S_ISDIR(info.st_mode):
            _reject(code)
        if info.st_uid != self.expected_uid or info.st_gid != self.expected_gid:
            _reject(code)
        if stat.S_IMODE(info.st_mode) != DIRECTORY_MODE:
            _reject(code)

    def _open_root(self, path: Path, code: str) -> int:
        try:
            info = path.lstat()
        except OSError as exc:
            raise ArtifactStoreError(code) from exc
        if stat.S_ISLNK(info.st_mode):
            _reject(code)
        self._validate_directory_info(info, code)
        try:
            descriptor = os.open(path, _directory_flags())
        except OSError as exc:
            raise ArtifactStoreError(code) from exc
        try:
            self._validate_directory_info(os.fstat(descriptor), code)
        except Exception:
            os.close(descriptor)
            raise
        return descriptor

    def _validate_object_info(self, info: os.stat_result, code: str, *, expected_bytes: int | None = None) -> None:
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _reject(code)
        if info.st_uid != self.expected_uid or info.st_gid != self.expected_gid:
            _reject(code)
        if stat.S_IMODE(info.st_mode) != OBJECT_MODE:
            _reject(code)
        if info.st_size < 1 or info.st_size > MAX_ARCHIVE_TOTAL_BYTES:
            _reject(code)
        if expected_bytes is not None and info.st_size != expected_bytes:
            _reject(code)

    @staticmethod
    def _release(verified: VerifiedLedger):
        if not isinstance(verified, VerifiedLedger) or verified.stable_release is None:
            _reject("artifact_store_stable_release_required")
        release = verified.stable_release
        expected_filename = f"home-center-{release.version}-linux-amd64.tar.gz"
        expected_object_key = (
            f"sha256/{release.artifact_sha256[:2]}/{release.artifact_sha256}/{expected_filename}"
        )
        if release.filename != expected_filename or release.object_key != expected_object_key:
            _reject("artifact_store_release_identity_rejected")
        if release.artifact_bytes < 1 or release.artifact_bytes > MAX_ARCHIVE_TOTAL_BYTES:
            _reject("artifact_store_release_size_rejected")
        return release

    def _open_inbox_artifact(self, inbox_fd: int, filename: str, expected_bytes: int) -> int:
        try:
            descriptor = os.open(filename, _file_read_flags(), dir_fd=inbox_fd)
        except FileNotFoundError as exc:
            raise ArtifactStoreError("artifact_store_inbox_missing") from exc
        except OSError as exc:
            raise ArtifactStoreError("artifact_store_inbox_rejected") from exc
        try:
            self._validate_object_info(
                os.fstat(descriptor),
                "artifact_store_inbox_metadata_rejected",
                expected_bytes=expected_bytes,
            )
        except Exception:
            os.close(descriptor)
            raise
        return descriptor

    def _ensure_object_parent(self, root_fd: int, parts: list[str]) -> int:
        descriptor = os.dup(root_fd)
        try:
            for part in parts:
                if not part or part in {".", ".."} or "/" in part or "\\" in part:
                    _reject("artifact_store_object_key_rejected")
                try:
                    os.mkdir(part, DIRECTORY_MODE, dir_fd=descriptor)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise ArtifactStoreError("artifact_store_object_directory_rejected") from exc
                try:
                    next_descriptor = os.open(part, _directory_flags(), dir_fd=descriptor)
                except OSError as exc:
                    raise ArtifactStoreError("artifact_store_object_directory_rejected") from exc
                try:
                    self._validate_directory_info(
                        os.fstat(next_descriptor), "artifact_store_object_directory_metadata_rejected"
                    )
                except Exception:
                    os.close(next_descriptor)
                    raise
                os.close(descriptor)
                descriptor = next_descriptor
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def _verify_published(self, verified: VerifiedLedger) -> str:
        try:
            return verify_artifact(verified, self.object_root)
        except ReleaseChannelError:
            raise

    def status(self, verified: VerifiedLedger) -> ArtifactStoreStatus:
        release = self._release(verified)
        root_fd = self._open_root(self.object_root, "artifact_store_root_rejected")
        try:
            parts = release.object_key.split("/")
            descriptor = os.dup(root_fd)
            try:
                for part in parts[:-1]:
                    try:
                        next_descriptor = os.open(part, _directory_flags(), dir_fd=descriptor)
                    except FileNotFoundError:
                        return ArtifactStoreStatus.MISSING
                    except OSError as exc:
                        raise ArtifactStoreError("artifact_store_object_directory_rejected") from exc
                    os.close(descriptor)
                    descriptor = next_descriptor
                try:
                    object_fd = os.open(parts[-1], _file_read_flags(), dir_fd=descriptor)
                except FileNotFoundError:
                    return ArtifactStoreStatus.MISSING
                except OSError as exc:
                    raise ArtifactStoreError("artifact_store_object_rejected") from exc
                try:
                    self._validate_object_info(
                        os.fstat(object_fd),
                        "artifact_store_object_metadata_rejected",
                        expected_bytes=release.artifact_bytes,
                    )
                finally:
                    os.close(object_fd)
            finally:
                os.close(descriptor)
        finally:
            os.close(root_fd)
        self._verify_published(verified)
        return ArtifactStoreStatus.VERIFIED

    def admit(self, verified: VerifiedLedger) -> AdmittedArtifact:
        """Admit exactly the signed stable artifact from the fixed inbox.

        The source path is never accepted from the caller. Existing objects are
        never overwritten; an already-published digest must independently pass
        full P2.4 verification before it is reused.
        """

        release = self._release(verified)
        object_root_fd = self._open_root(self.object_root, "artifact_store_root_rejected")
        inbox_fd = self._open_root(self.inbox_root, "artifact_store_inbox_root_rejected")
        source_fd = self._open_inbox_artifact(inbox_fd, release.filename, release.artifact_bytes)
        published_here = False
        final_parent_fd: int | None = None
        temporary_name: str | None = None
        try:
            parts = release.object_key.split("/")
            if len(parts) != 4 or parts[-1] != release.filename:
                _reject("artifact_store_object_key_rejected")
            final_parent_fd = self._ensure_object_parent(object_root_fd, parts[:-1])
            final_name = parts[-1]

            try:
                existing_fd = os.open(final_name, _file_read_flags(), dir_fd=final_parent_fd)
            except FileNotFoundError:
                existing_fd = None
            except OSError as exc:
                raise ArtifactStoreError("artifact_store_object_rejected") from exc

            if existing_fd is not None:
                try:
                    self._validate_object_info(
                        os.fstat(existing_fd),
                        "artifact_store_object_metadata_rejected",
                        expected_bytes=release.artifact_bytes,
                    )
                finally:
                    os.close(existing_fd)
                self._verify_published(verified)
                os.unlink(release.filename, dir_fd=inbox_fd)
                os.fsync(inbox_fd)
                return AdmittedArtifact(
                    schema="home-center.admitted-artifact.v1",
                    version=release.version,
                    revision=release.revision,
                    artifact_sha256=release.artifact_sha256,
                    artifact_bytes=release.artifact_bytes,
                    object_key=release.object_key,
                    reused=True,
                )

            temporary_name = f".admit-{os.getpid()}-{secrets.token_hex(8)}"
            create_flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                temporary_fd = os.open(temporary_name, create_flags, OBJECT_MODE, dir_fd=final_parent_fd)
            except OSError as exc:
                raise ArtifactStoreError("artifact_store_temp_create_rejected") from exc
            try:
                os.fchmod(temporary_fd, OBJECT_MODE)
                digest = hashlib.sha256()
                copied = 0
                while True:
                    chunk = os.read(source_fd, COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > release.artifact_bytes or copied > MAX_ARCHIVE_TOTAL_BYTES:
                        _reject("artifact_store_source_size_changed")
                    digest.update(chunk)
                    offset = 0
                    while offset < len(chunk):
                        written = os.write(temporary_fd, chunk[offset:])
                        if written <= 0:
                            _reject("artifact_store_write_rejected")
                        offset += written
                if copied != release.artifact_bytes:
                    _reject("artifact_store_source_size_changed")
                if digest.hexdigest() != release.artifact_sha256:
                    _reject("artifact_store_source_digest_mismatch")
                os.fsync(temporary_fd)
                self._validate_object_info(
                    os.fstat(temporary_fd),
                    "artifact_store_temp_metadata_rejected",
                    expected_bytes=release.artifact_bytes,
                )
            finally:
                os.close(temporary_fd)

            try:
                os.link(
                    temporary_name,
                    final_name,
                    src_dir_fd=final_parent_fd,
                    dst_dir_fd=final_parent_fd,
                    follow_symlinks=False,
                )
                published_here = True
            except FileExistsError:
                published_here = False
            except OSError as exc:
                raise ArtifactStoreError("artifact_store_publish_rejected") from exc
            finally:
                try:
                    os.unlink(temporary_name, dir_fd=final_parent_fd)
                except FileNotFoundError:
                    pass
                temporary_name = None
            os.fsync(final_parent_fd)

            try:
                self._verify_published(verified)
            except Exception:
                if published_here:
                    try:
                        os.unlink(final_name, dir_fd=final_parent_fd)
                        os.fsync(final_parent_fd)
                    except OSError:
                        pass
                raise

            os.unlink(release.filename, dir_fd=inbox_fd)
            os.fsync(inbox_fd)
            return AdmittedArtifact(
                schema="home-center.admitted-artifact.v1",
                version=release.version,
                revision=release.revision,
                artifact_sha256=release.artifact_sha256,
                artifact_bytes=release.artifact_bytes,
                object_key=release.object_key,
                reused=not published_here,
            )
        finally:
            if temporary_name is not None and final_parent_fd is not None:
                try:
                    os.unlink(temporary_name, dir_fd=final_parent_fd)
                except OSError:
                    pass
            if final_parent_fd is not None:
                os.close(final_parent_fd)
            os.close(source_fd)
            os.close(inbox_fd)
            os.close(object_root_fd)
