from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE_TREE = HERE.parents[1] / "product" / "control-plane" / "src"
sys.path.insert(0, str(SOURCE_TREE if SOURCE_TREE.is_dir() else HERE))

from home_center.release_channel import (  # noqa: E402
    MAX_CHECKPOINT_BYTES,
    MAX_ENVELOPE_BYTES,
    MAX_TRUST_POLICY_BYTES,
    ReleaseChannelError,
    load_strict_json,
    verification_result,
    verify_artifact,
    verify_envelope,
)


EXIT_CODES = {
    "envelope": 65,
    "ledger": 65,
    "release": 65,
    "trust": 66,
    "signature": 66,
    "openssl": 66,
    "checkpoint": 68,
    "artifact": 69,
}


def _read(path: Path, maximum: int, code: str) -> bytes:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ReleaseChannelError(code) from exc
    if not data or len(data) > maximum:
        raise ReleaseChannelError(code)
    return data


def _exit_code(reason: str) -> int:
    if reason in {"ledger_expired", "ledger_not_yet_valid"}:
        return 67
    if reason.startswith("ledger_") and any(
        word in reason for word in ("rollback", "equivocation", "rewritten", "transition", "stable", "history")
    ):
        return 68
    return next((code for prefix, code in EXIT_CODES.items() if reason.startswith(prefix)), 65)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Verify a Home Center DSSE stable-channel snapshot without deploying it")
    result.add_argument("--envelope", type=Path, required=True)
    result.add_argument("--trust-policy", type=Path, required=True)
    history = result.add_mutually_exclusive_group(required=True)
    history.add_argument("--checkpoint", type=Path)
    history.add_argument(
        "--bootstrap-no-checkpoint",
        action="store_true",
        help="explicit one-time trust bootstrap; never use after a checkpoint has been accepted",
    )
    result.add_argument("--artifact-root", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        envelope = _read(args.envelope, MAX_ENVELOPE_BYTES, "envelope_file_rejected")
        policy = load_strict_json(
            _read(args.trust_policy, MAX_TRUST_POLICY_BYTES, "trust_policy_file_rejected"),
            maximum=MAX_TRUST_POLICY_BYTES,
            kind="trust_policy",
        )
        checkpoint = None
        if args.checkpoint is not None:
            checkpoint = load_strict_json(
                _read(args.checkpoint, MAX_CHECKPOINT_BYTES, "checkpoint_file_rejected"),
                maximum=MAX_CHECKPOINT_BYTES,
                kind="checkpoint",
            )
        verified = verify_envelope(envelope, policy, checkpoint=checkpoint)
        artifact_verified = False
        if verified.stable_release is not None:
            verify_artifact(verified, args.artifact_root)
            artifact_verified = True
        print(json.dumps(verification_result(verified, artifact_verified=artifact_verified), sort_keys=True, separators=(",", ":")))
        return 0 if verified.stable_release is not None else 2
    except ReleaseChannelError as exc:
        print(
            json.dumps(
                {
                    "schema": "home-center.release-verification-result.v1",
                    "status": "rejected",
                    "reason_code": exc.code,
                    "generation": None,
                    "ledger_sequence": None,
                    "payload_sha256": None,
                    "signing_key_ids": [],
                    "stable_release": None,
                    "next_checkpoint": None,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return _exit_code(exc.code)


if __name__ == "__main__":
    raise SystemExit(main())
