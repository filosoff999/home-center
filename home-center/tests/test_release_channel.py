from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center import __version__  # noqa: E402
from home_center.release_channel import (  # noqa: E402
    PAYLOAD_TYPE,
    REQUIRED_ACCEPTANCE_CHECKS,
    ReleaseChannelError,
    canonical_json,
    checkpoint_for,
    release_record_sha256,
    verify_artifact,
    verify_envelope,
)


NOW = datetime(2026, 9, 6, 18, 0, 0, tzinfo=timezone.utc)


class ReleaseChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.key, cls.public, cls.keyid = cls.generate_key("primary", "prime256v1")
        cls.second_key, cls.second_public, cls.second_keyid = cls.generate_key("secondary", "prime256v1")
        cls.p384_key, cls.p384_public, cls.p384_keyid = cls.generate_key("p384", "secp384r1")
        cls.policy = {
            "schema": "home-center.release-trust-policy.v1",
            "product": "home-center",
            "channel": "stable",
            "payload_type": PAYLOAD_TYPE,
            "threshold": 1,
            "keys": [cls.policy_key(cls.public, cls.keyid)],
        }

    @classmethod
    def generate_key(cls, name: str, curve: str) -> tuple[Path, Path, str]:
        key = Path(cls._temporary.name) / f"fixture-only-{name}-private-key.pem"
        public = Path(cls._temporary.name) / f"fixture-only-{name}-public-key.pem"
        subprocess.run(
            ["/usr/bin/openssl", "ecparam", "-name", curve, "-genkey", "-noout", "-out", str(key)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["/usr/bin/openssl", "pkey", "-in", str(key), "-pubout", "-out", str(public)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        der = subprocess.run(
            ["/usr/bin/openssl", "pkey", "-pubin", "-in", str(public), "-outform", "DER"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        return key, public, "sha256:" + hashlib.sha256(der).hexdigest()

    @staticmethod
    def policy_key(public: Path, keyid: str, *, state: str = "active") -> dict:
        return {
            "keyid": keyid,
            "algorithm": "ecdsa-p256-sha256",
            "state": state,
            "public_key_pem": public.read_text(encoding="ascii"),
        }

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def record(
        self,
        *,
        version: str = "0.4.3",
        revision: str = "a" * 40,
        artifact_sha: str = "b" * 64,
        artifact_bytes: int = 98295,
        manifest_sha: str = "c" * 64,
        acceptance_status: str = "passed",
    ) -> dict:
        filename = f"home-center-{version}-linux-amd64.tar.gz"
        return {
            "schema": "home-center.release-record.v1",
            "product": "home-center",
            "version": version,
            "revision": revision,
            "platform": {"os": "linux", "architecture": "amd64"},
            "artifact": {
                "filename": filename,
                "sha256": artifact_sha,
                "bytes": artifact_bytes,
                "manifest_sha256": manifest_sha,
                "media_type": "application/gzip",
                "object_key": f"sha256/{artifact_sha[:2]}/{artifact_sha}/{filename}",
            },
            "provenance": {
                "repository": "ControlCenterSoft/home-center",
                "source_ref": "refs/heads/main",
                "workflow": ".github/workflows/ci.yml",
                "workflow_sha256": "d" * 64,
                "run_id": 111,
                "run_attempt": 1,
                "ci_artifact_id": 222,
                "ci_outer_zip_sha256": "e" * 64,
                "source_date_epoch": 1767225600,
            },
            "acceptance": {
                "profile": "hm-dm-two-node.v1",
                "status": acceptance_status,
                "transaction_id": "20260906T160641Z-4618b29b86a5",
                "accepted_at": "2026-09-06T16:30:00Z",
                "evidence_bundle_sha256": "f" * 64,
                "evidence_refs": [
                    {
                        "kind": "github-issue-comment",
                        "locator": "ControlCenterSoft/serverops-control#1624@5560568239",
                        "content_sha256": "1" * 64,
                    }
                ],
                "required_checks": list(REQUIRED_ACCEPTANCE_CHECKS),
            },
        }

    @staticmethod
    def promotion_event(record: dict, *, sequence: int = 1, previous: str | None = None) -> dict:
        record_id = release_record_sha256(record)
        return {
            "sequence": sequence,
            "previous_event_sha256": previous,
            "recorded_at": "2026-09-06T17:00:00Z",
            "changes": [
                {
                    "release_record_sha256": record_id,
                    "record": record,
                    "from": "unlisted",
                    "to": "stable",
                    "reason_code": "production_acceptance_passed",
                    "evidence_sha256": "2" * 64,
                }
            ],
            "resulting_stable_artifact_sha256": record["artifact"]["sha256"],
        }

    @staticmethod
    def event_sha(event: dict) -> str:
        return hashlib.sha256(canonical_json(event)).hexdigest()

    def ledger(self, events: list[dict], *, generation: int = 1, issued: str = "2026-09-06T17:30:00Z") -> dict:
        stable: str | None = None
        for event in events:
            stable = event["resulting_stable_artifact_sha256"]
        return {
            "schema": "home-center.release-ledger.v1",
            "product": "home-center",
            "channel": "stable",
            "generation": generation,
            "ledger_sequence": len(events),
            "issued_at": issued,
            "expires_at": "2026-09-07T17:30:00Z",
            "events": events,
            "stable_artifact_sha256": stable,
        }

    def envelope(self, ledger: dict, *, raw_payload: bytes | None = None, key: Path | None = None) -> bytes:
        payload = canonical_json(ledger) if raw_payload is None else raw_payload
        payload_type = PAYLOAD_TYPE.encode("ascii")
        pae = (
            b"DSSEv1 "
            + str(len(payload_type)).encode()
            + b" "
            + payload_type
            + b" "
            + str(len(payload)).encode()
            + b" "
            + payload
        )
        signature = subprocess.run(
            ["/usr/bin/openssl", "dgst", "-sha256", "-sign", str(key or self.key)],
            input=pae,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        return canonical_json(
            {
                "payloadType": PAYLOAD_TYPE,
                "payload": base64.b64encode(payload).decode("ascii"),
                "signatures": [{"keyid": self.keyid, "sig": base64.b64encode(signature).decode("ascii")}],
            }
        )

    def assert_rejected(self, code: str, envelope: bytes, *, policy: dict | None = None, checkpoint: dict | None = None) -> None:
        with self.assertRaises(ReleaseChannelError) as captured:
            verify_envelope(envelope, policy or self.policy, checkpoint=checkpoint, now=NOW)
        self.assertEqual(captured.exception.code, code)

    def test_valid_dsse_stable_ledger_and_checkpoint(self) -> None:
        record = self.record()
        verified = verify_envelope(self.envelope(self.ledger([self.promotion_event(record)])), self.policy, now=NOW)
        self.assertEqual(json.loads(verified.stable_release.record_bytes), record)  # type: ignore[union-attr]
        self.assertEqual(verified.signing_key_ids, (self.keyid,))
        checkpoint = checkpoint_for(verified)
        repeated = verify_envelope(
            self.envelope(self.ledger([self.promotion_event(record)])),
            self.policy,
            checkpoint=checkpoint,
            now=NOW,
        )
        self.assertEqual(repeated.payload_sha256, verified.payload_sha256)

    def test_verified_release_identity_is_immutable_and_bound_to_signed_bytes(self) -> None:
        record = self.record()
        verified = verify_envelope(self.envelope(self.ledger([self.promotion_event(record)])), self.policy, now=NOW)
        release = verified.stable_release
        self.assertIsNotNone(release)
        with self.assertRaises(FrozenInstanceError):
            release.revision = "0" * 40  # type: ignore[misc,union-attr]
        unsigned_copy = json.loads(release.record_bytes)  # type: ignore[union-attr]
        unsigned_copy["revision"] = "0" * 40
        self.assertEqual(release.revision, "a" * 40)  # type: ignore[union-attr]
        self.assertEqual(release.record_sha256, release_record_sha256(record))  # type: ignore[union-attr]

    def test_signature_payload_key_and_policy_tampering_fail_closed(self) -> None:
        record = self.record()
        envelope = self.envelope(self.ledger([self.promotion_event(record)]))
        parsed = json.loads(envelope)
        signature = bytearray(base64.b64decode(parsed["signatures"][0]["sig"]))
        signature[-1] ^= 1
        parsed["signatures"][0]["sig"] = base64.b64encode(signature).decode("ascii")
        self.assert_rejected("signature_verification_failed", canonical_json(parsed))

        parsed = json.loads(envelope)
        parsed["signatures"][0]["keyid"] = "sha256:" + "0" * 64
        self.assert_rejected("signature_unknown_key", canonical_json(parsed))

        revoked = copy.deepcopy(self.policy)
        revoked["keys"][0]["state"] = "revoked"
        revoked["keys"].append(self.policy_key(self.second_public, self.second_keyid))
        self.assert_rejected("signature_revoked_key", envelope, policy=revoked)

        threshold = copy.deepcopy(self.policy)
        threshold["threshold"] = 2
        threshold["keys"].append(self.policy_key(self.second_public, self.second_keyid))
        self.assert_rejected("signature_threshold_not_met", envelope, policy=threshold)

        duplicate = json.loads(envelope)
        duplicate["signatures"].append(copy.deepcopy(duplicate["signatures"][0]))
        self.assert_rejected("signature_key_duplicate", canonical_json(duplicate))

        wrong_curve = copy.deepcopy(self.policy)
        wrong_curve["keys"] = [self.policy_key(self.p384_public, self.p384_keyid)]
        self.assert_rejected("trust_key_algorithm_rejected", envelope, policy=wrong_curve)

        tampered = json.loads(envelope)
        payload = bytearray(base64.b64decode(tampered["payload"]))
        payload[-2] ^= 1
        tampered["payload"] = base64.b64encode(payload).decode("ascii")
        self.assert_rejected("ledger_json_rejected", canonical_json(tampered))

    def test_noncanonical_duplicate_float_and_unknown_fields_rejected(self) -> None:
        record = self.record()
        ledger = self.ledger([self.promotion_event(record)])
        noncanonical = json.dumps(ledger, sort_keys=False, indent=1).encode("ascii")
        self.assert_rejected("ledger_payload_not_canonical", self.envelope(ledger, raw_payload=noncanonical))

        canonical = canonical_json(ledger)
        duplicate = canonical.replace(b'{"channel":"stable",', b'{"channel":"stable","channel":"stable",', 1)
        self.assert_rejected("duplicate_json_key", self.envelope(ledger, raw_payload=duplicate))

        floated = canonical.replace(b'"generation":1', b'"generation":1.0', 1)
        self.assert_rejected("json_float_rejected", self.envelope(ledger, raw_payload=floated))

        unknown = copy.deepcopy(ledger)
        unknown["authority"] = "github"
        self.assert_rejected("ledger_shape_rejected", self.envelope(unknown))

    def test_atomic_supersede_promote_then_terminal_quarantine(self) -> None:
        first = self.record(version="0.4.3", artifact_sha="b" * 64)
        first_event = self.promotion_event(first)
        first_id = release_record_sha256(first)
        second = self.record(version="0.5.0", revision="3" * 40, artifact_sha="4" * 64)
        second_id = release_record_sha256(second)
        second_event = {
            "sequence": 2,
            "previous_event_sha256": self.event_sha(first_event),
            "recorded_at": "2026-09-06T17:15:00Z",
            "changes": [
                {
                    "release_record_sha256": first_id,
                    "record": None,
                    "from": "stable",
                    "to": "superseded",
                    "reason_code": "new_stable_release",
                    "evidence_sha256": "5" * 64,
                },
                {
                    "release_record_sha256": second_id,
                    "record": second,
                    "from": "unlisted",
                    "to": "stable",
                    "reason_code": "production_acceptance_passed",
                    "evidence_sha256": "6" * 64,
                },
            ],
            "resulting_stable_artifact_sha256": second["artifact"]["sha256"],
        }
        ledger = self.ledger([first_event, second_event], generation=2, issued="2026-09-06T17:30:00Z")
        verified = verify_envelope(self.envelope(ledger), self.policy, now=NOW)
        self.assertEqual(json.loads(verified.stable_release.record_bytes), second)  # type: ignore[union-attr]

        quarantine = {
            "sequence": 3,
            "previous_event_sha256": self.event_sha(second_event),
            "recorded_at": "2026-09-06T17:30:00Z",
            "changes": [
                {
                    "release_record_sha256": second_id,
                    "record": None,
                    "from": "stable",
                    "to": "quarantined",
                    "reason_code": "terminal_health_regression",
                    "evidence_sha256": "7" * 64,
                }
            ],
            "resulting_stable_artifact_sha256": None,
        }
        quarantined = self.ledger([first_event, second_event, quarantine], generation=3, issued="2026-09-06T18:00:00Z")
        quarantined["expires_at"] = "2026-09-07T18:00:00Z"
        verified = verify_envelope(self.envelope(quarantined), self.policy, now=NOW)
        self.assertIsNone(verified.stable_release)

        replay = copy.deepcopy(quarantined)
        replay_event = {
            "sequence": 4,
            "previous_event_sha256": self.event_sha(quarantine),
            "recorded_at": "2026-09-06T19:00:00Z",
            "changes": [
                {
                    "release_record_sha256": second_id,
                    "record": None,
                    "from": "stable",
                    "to": "stable",
                    "reason_code": "unsafe_unquarantine",
                    "evidence_sha256": "8" * 64,
                }
            ],
            "resulting_stable_artifact_sha256": second["artifact"]["sha256"],
        }
        replay["events"].append(replay_event)
        replay["ledger_sequence"] = 4
        replay["stable_artifact_sha256"] = second["artifact"]["sha256"]
        self.assert_rejected("ledger_transition_source_rejected", self.envelope(replay))

    def test_checkpoint_blocks_replay_equivocation_and_history_rewrite(self) -> None:
        record = self.record()
        first_event = self.promotion_event(record)
        initial = self.ledger([first_event], generation=2)
        verified = verify_envelope(self.envelope(initial), self.policy, now=NOW)
        checkpoint = checkpoint_for(verified)

        replay = self.ledger([first_event], generation=1)
        self.assert_rejected("ledger_generation_rollback", self.envelope(replay), checkpoint=checkpoint)

        equivocation = copy.deepcopy(initial)
        equivocation["expires_at"] = "2026-09-08T17:30:00Z"
        self.assert_rejected("ledger_generation_equivocation", self.envelope(equivocation), checkpoint=checkpoint)

        rewritten_record = self.record(revision="9" * 40, artifact_sha="8" * 64)
        rewritten_event = self.promotion_event(rewritten_record)
        rewritten = self.ledger([rewritten_event], generation=3)
        self.assert_rejected("ledger_history_rewritten", self.envelope(rewritten), checkpoint=checkpoint)

    def test_acceptance_causality_and_revision_identity_are_unique(self) -> None:
        future_acceptance = self.record()
        future_acceptance["acceptance"]["accepted_at"] = "2099-01-01T00:00:00Z"
        self.assert_rejected(
            "ledger_acceptance_after_event",
            self.envelope(self.ledger([self.promotion_event(future_acceptance)])),
        )

        first = self.record(version="0.4.3", revision="a" * 40, artifact_sha="b" * 64)
        first_event = self.promotion_event(first)
        first_id = release_record_sha256(first)
        second = self.record(version="0.5.0", revision="a" * 40, artifact_sha="4" * 64)
        second_id = release_record_sha256(second)
        second_event = {
            "sequence": 2,
            "previous_event_sha256": self.event_sha(first_event),
            "recorded_at": "2026-09-06T17:15:00Z",
            "changes": [
                {
                    "release_record_sha256": first_id,
                    "record": None,
                    "from": "stable",
                    "to": "superseded",
                    "reason_code": "new_stable_release",
                    "evidence_sha256": "5" * 64,
                },
                {
                    "release_record_sha256": second_id,
                    "record": second,
                    "from": "unlisted",
                    "to": "stable",
                    "reason_code": "production_acceptance_passed",
                    "evidence_sha256": "6" * 64,
                },
            ],
            "resulting_stable_artifact_sha256": second["artifact"]["sha256"],
        }
        ledger = self.ledger([first_event, second_event], generation=2)
        self.assert_rejected("ledger_revision_fork_rejected", self.envelope(ledger))

    def test_freshness_and_stable_acceptance_are_required(self) -> None:
        record = self.record()
        event = self.promotion_event(record)
        expired = self.ledger([event], issued="2026-09-05T17:30:00Z")
        expired["expires_at"] = "2026-09-06T17:30:00Z"
        self.assert_rejected("ledger_expired", self.envelope(expired))

        future = self.ledger([event], issued="2026-09-06T19:00:00Z")
        future["expires_at"] = "2026-09-07T19:00:00Z"
        self.assert_rejected("ledger_not_yet_valid", self.envelope(future))

        failed = self.record(acceptance_status="failed")
        self.assert_rejected("ledger_unaccepted_stable_rejected", self.envelope(self.ledger([self.promotion_event(failed)])))

    def test_artifact_exact_digest_manifest_and_identity(self) -> None:
        revision = "7" * 40
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            output = temporary_path / "build"
            subprocess.run(
                ["bash", "deploy/scripts/build-artifact.sh", str(output)],
                cwd=ROOT,
                env={**os.environ, "HOME_CENTER_VERSION": __version__, "HOME_CENTER_REVISION": revision},
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            archive = output / f"home-center-{__version__}-linux-amd64.tar.gz"
            artifact_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
            with tarfile.open(archive, "r:gz") as bundle:
                manifest = bundle.extractfile("./MANIFEST.sha256")
                self.assertIsNotNone(manifest)
                manifest_sha = hashlib.sha256(manifest.read()).hexdigest()  # type: ignore[union-attr]
            record = self.record(
                version=__version__,
                revision=revision,
                artifact_sha=artifact_sha,
                artifact_bytes=archive.stat().st_size,
                manifest_sha=manifest_sha,
            )
            object_path = temporary_path / record["artifact"]["object_key"]
            object_path.parent.mkdir(parents=True)
            shutil.copyfile(archive, object_path)
            event = self.promotion_event(record)
            verified = verify_envelope(self.envelope(self.ledger([event])), self.policy, now=NOW)
            self.assertEqual(verify_artifact(verified, temporary_path), artifact_sha)

            content = bytearray(object_path.read_bytes())
            content[-1] ^= 1
            object_path.write_bytes(content)
            with self.assertRaises(ReleaseChannelError) as captured:
                verify_artifact(verified, temporary_path)
            self.assertEqual(captured.exception.code, "artifact_sha256_mismatch")

    def test_signed_non_archive_is_rejected_without_traceback(self) -> None:
        content = b"not-a-gzip-or-tar"
        artifact_sha = hashlib.sha256(content).hexdigest()
        record = self.record(
            artifact_sha=artifact_sha,
            artifact_bytes=len(content),
            manifest_sha="0" * 64,
        )
        verified = verify_envelope(
            self.envelope(self.ledger([self.promotion_event(record)])),
            self.policy,
            now=NOW,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            object_path = root / record["artifact"]["object_key"]
            object_path.parent.mkdir(parents=True)
            object_path.write_bytes(content)
            with self.assertRaises(ReleaseChannelError) as captured:
                verify_artifact(verified, root)
        self.assertEqual(captured.exception.code, "artifact_archive_rejected")

    def test_release_verifier_entrypoint_is_directly_runnable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary)
            shutil.copy2(ROOT / "deploy/runtime/release-channel-verify.py", release)
            shutil.copytree(ROOT / "product/control-plane/src/home_center", release / "home_center")
            result = subprocess.run(
                [sys.executable, "-I", str(release / "release-channel-verify.py"), "--help"],
                check=False,
                capture_output=True,
                text=True,
                cwd=release,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("without deploying", result.stdout)


if __name__ == "__main__":
    unittest.main()
