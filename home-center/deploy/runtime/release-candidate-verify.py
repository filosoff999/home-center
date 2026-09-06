#!/usr/bin/env python3
"""Verify bounded 0.9 release-candidate evidence without deploying it."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE_TREE = HERE.parents[1] / "product" / "control-plane" / "src"
sys.path.insert(0, str(SOURCE_TREE if SOURCE_TREE.is_dir() else HERE))

from home_center.release_candidate import (  # noqa: E402
    MAX_ACCEPTANCE_BYTES,
    ReleaseCandidateAcceptanceError,
    load_acceptance_document,
    rejected_result,
    verify_release_candidate,
)


def _read_evidence(path: Path) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ReleaseCandidateAcceptanceError("acceptance_file_rejected")
        if info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022:
            raise ReleaseCandidateAcceptanceError("acceptance_file_permissions_rejected")
        if info.st_size < 1 or info.st_size > MAX_ACCEPTANCE_BYTES:
            raise ReleaseCandidateAcceptanceError("acceptance_document_size_rejected")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            data = stream.read(MAX_ACCEPTANCE_BYTES + 1)
        if len(data) != info.st_size:
            raise ReleaseCandidateAcceptanceError("acceptance_file_changed")
        return data
    except ReleaseCandidateAcceptanceError:
        raise
    except OSError as exc:
        raise ReleaseCandidateAcceptanceError("acceptance_file_rejected") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Verify Home Center 0.9 release-candidate acceptance evidence")
    result.add_argument("--evidence", type=Path, required=True)
    result.add_argument("--candidate-revision", required=True)
    result.add_argument("--candidate-artifact-sha256", required=True)
    result.add_argument("--predecessor-revision", required=True)
    result.add_argument("--predecessor-artifact-sha256", required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        document = load_acceptance_document(_read_evidence(args.evidence))
        verified = verify_release_candidate(
            document,
            expected_candidate_revision=args.candidate_revision,
            expected_candidate_artifact_sha256=args.candidate_artifact_sha256,
            expected_predecessor_revision=args.predecessor_revision,
            expected_predecessor_artifact_sha256=args.predecessor_artifact_sha256,
        )
        result = verified.result()
        status = 0
    except ReleaseCandidateAcceptanceError as exc:
        result = rejected_result(exc.code)
        status = 65
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
