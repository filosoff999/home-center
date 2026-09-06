"""Fail-closed verification for the Home Center signed stable release channel.

P2.4 deliberately stops at a verified release decision.  This module has no
network client, deployment primitive, service control, or private-key support.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import stat
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


OPENSSL = "/usr/bin/openssl"
PAYLOAD_TYPE = "application/vnd.home-center.release-ledger.v1+json"
PRODUCT = "home-center"
CHANNEL = "stable"
REPOSITORY = "ControlCenterSoft/home-center"
SOURCE_REF = "refs/heads/main"
WORKFLOW = ".github/workflows/ci.yml"
PLATFORM_OS = "linux"
PLATFORM_ARCHITECTURE = "amd64"

MAX_ENVELOPE_BYTES = 2 * 1024 * 1024
MAX_PAYLOAD_BYTES = 1024 * 1024
MAX_TRUST_POLICY_BYTES = 128 * 1024
MAX_CHECKPOINT_BYTES = 64 * 1024
MAX_EVENTS = 4096
MAX_SIGNATURES = 8
MAX_ARCHIVE_MEMBERS = 4096
MAX_ARCHIVE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 256 * 1024 * 1024
MAX_LEDGER_LIFETIME = timedelta(days=7)
MAX_FUTURE_SKEW = timedelta(minutes=10)

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
KEY_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
REASON = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
ASCII_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#@+-]{0,255}$")
OBJECT_KEY = re.compile(
    r"^sha256/[0-9a-f]{2}/[0-9a-f]{64}/home-center-(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-linux-amd64\.tar\.gz$"
)
UTC_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

REQUIRED_ACCEPTANCE_CHECKS = (
    "artifact_integrity",
    "dc02_canary",
    "dc02_soak",
    "dc01_promotion",
    "exact_release_parity",
    "ready_both",
    "web_tls_both",
    "peer_mtls_both",
    "drs_healthy",
    "domain_sid_unchanged",
    "control_agent_unchanged",
    "samba_services_unchanged",
)


class ReleaseChannelError(ValueError):
    """A stable, non-secret rejection reason suitable for audit output."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VerifiedRelease:
    version: str
    revision: str
    filename: str
    artifact_sha256: str
    artifact_bytes: int
    manifest_sha256: str
    object_key: str
    record_sha256: str
    record_bytes: bytes


@dataclass(frozen=True)
class VerifiedLedger:
    generation: int
    ledger_sequence: int
    payload_sha256: str
    events_sha256: str
    last_event_sha256: str | None
    stable_artifact_sha256: str | None
    stable_release: VerifiedRelease | None
    verified_at: datetime
    signing_key_ids: tuple[str, ...]


def _reject(code: str) -> None:
    raise ReleaseChannelError(code)


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _reject("duplicate_json_key")
        result[key] = value
    return result


def _reject_float(_: str) -> None:
    _reject("json_float_rejected")


def _reject_constant(_: str) -> None:
    _reject("json_constant_rejected")


def load_strict_json(data: bytes, *, maximum: int, kind: str) -> Any:
    if not isinstance(data, bytes) or not data or len(data) > maximum:
        _reject(f"{kind}_size_rejected")
    if data.startswith(b"\xef\xbb\xbf"):
        _reject(f"{kind}_bom_rejected")
    try:
        text = data.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except ReleaseChannelError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ReleaseChannelError(f"{kind}_json_rejected") from exc


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise ReleaseChannelError("canonical_json_rejected") from exc


def _exact(value: Any, keys: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _reject(code)
    return value


def _string(value: Any, pattern: re.Pattern[str] | None, code: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        _reject(code)
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ReleaseChannelError(code) from exc
    if pattern is not None and pattern.fullmatch(value) is None:
        _reject(code)
    return value


def _integer(value: Any, code: str, *, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _reject(code)
    return value


def _timestamp(value: Any, code: str) -> datetime:
    raw = _string(value, UTC_TIMESTAMP, code, maximum=20)
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ReleaseChannelError(code) from exc


def _b64(value: Any, code: str, *, maximum: int) -> bytes:
    raw = _string(value, None, code, maximum=maximum)
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ReleaseChannelError(code) from exc
    if base64.b64encode(decoded).decode("ascii") != raw:
        _reject(code)
    return decoded


def _semver(value: str) -> tuple[int, int, int]:
    matched = SEMVER.fullmatch(value)
    if matched is None:
        _reject("release_version_rejected")
    return tuple(int(part) for part in matched.groups())


def _run_openssl(arguments: list[str], *, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            [OPENSSL, *arguments],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            close_fds=True,
            timeout=5,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseChannelError("openssl_unavailable") from exc


def _validate_trust_policy(policy: Any) -> tuple[int, dict[str, tuple[str, bytes]]]:
    value = _exact(
        policy,
        {"schema", "product", "channel", "payload_type", "threshold", "keys"},
        "trust_policy_shape_rejected",
    )
    if value["schema"] != "home-center.release-trust-policy.v1":
        _reject("trust_policy_schema_rejected")
    if value["product"] != PRODUCT or value["channel"] != CHANNEL or value["payload_type"] != PAYLOAD_TYPE:
        _reject("trust_policy_scope_rejected")
    threshold = _integer(value["threshold"], "trust_policy_threshold_rejected", minimum=1, maximum=MAX_SIGNATURES)
    raw_keys = value["keys"]
    if not isinstance(raw_keys, list) or not 1 <= len(raw_keys) <= MAX_SIGNATURES:
        _reject("trust_policy_keys_rejected")

    keys: dict[str, tuple[str, bytes]] = {}
    active = 0
    with tempfile.TemporaryDirectory(prefix="home-center-release-key-") as temporary:
        for index, raw_key in enumerate(raw_keys):
            key = _exact(raw_key, {"keyid", "algorithm", "state", "public_key_pem"}, "trust_key_shape_rejected")
            keyid = _string(key["keyid"], KEY_ID, "trust_key_id_rejected", maximum=71)
            if keyid in keys:
                _reject("trust_key_duplicate")
            if key["algorithm"] != "ecdsa-p256-sha256" or key["state"] not in {"active", "retired", "revoked"}:
                _reject("trust_key_policy_rejected")
            pem = _string(key["public_key_pem"], None, "trust_key_pem_rejected", maximum=4096).encode("ascii")
            if not pem.startswith(b"-----BEGIN PUBLIC KEY-----\n") or not pem.endswith(b"-----END PUBLIC KEY-----\n"):
                _reject("trust_key_pem_rejected")
            public_path = Path(temporary) / f"key-{index}.pem"
            public_path.write_bytes(pem)
            public_path.chmod(0o600)
            der_result = _run_openssl(["pkey", "-pubin", "-in", str(public_path), "-outform", "DER"])
            text_result = _run_openssl(["pkey", "-pubin", "-in", str(public_path), "-text_pub", "-noout"])
            if der_result.returncode != 0 or text_result.returncode != 0:
                _reject("trust_key_parse_rejected")
            description = text_result.stdout.decode("ascii", errors="replace")
            if "Public-Key: (256 bit)" not in description or "ASN1 OID: prime256v1" not in description:
                _reject("trust_key_algorithm_rejected")
            expected_keyid = "sha256:" + hashlib.sha256(der_result.stdout).hexdigest()
            if keyid != expected_keyid:
                _reject("trust_key_id_mismatch")
            keys[keyid] = (key["state"], pem)
            active += key["state"] == "active"
    if threshold > active:
        _reject("trust_policy_active_threshold_rejected")
    return threshold, keys


def _dsse_pae(payload_type: str, payload: bytes) -> bytes:
    type_bytes = payload_type.encode("ascii")
    return b"DSSEv1 " + str(len(type_bytes)).encode() + b" " + type_bytes + b" " + str(len(payload)).encode() + b" " + payload


def _verify_signatures(envelope: dict[str, Any], policy: Any, payload: bytes) -> tuple[str, ...]:
    threshold, keys = _validate_trust_policy(policy)
    signatures = envelope["signatures"]
    if not isinstance(signatures, list) or not 1 <= len(signatures) <= MAX_SIGNATURES:
        _reject("signature_count_rejected")
    pae = _dsse_pae(PAYLOAD_TYPE, payload)
    seen: set[str] = set()
    active_signers: list[str] = []
    with tempfile.TemporaryDirectory(prefix="home-center-release-signature-") as temporary:
        for index, raw_signature in enumerate(signatures):
            signature = _exact(raw_signature, {"keyid", "sig"}, "signature_shape_rejected")
            keyid = _string(signature["keyid"], KEY_ID, "signature_key_id_rejected", maximum=71)
            if keyid in seen:
                _reject("signature_key_duplicate")
            seen.add(keyid)
            if keyid not in keys:
                _reject("signature_unknown_key")
            state, pem = keys[keyid]
            if state == "revoked":
                _reject("signature_revoked_key")
            signature_bytes = _b64(signature["sig"], "signature_base64_rejected", maximum=1024)
            if not 8 <= len(signature_bytes) <= 128:
                _reject("signature_encoding_rejected")
            public_path = Path(temporary) / f"key-{index}.pem"
            signature_path = Path(temporary) / f"signature-{index}.der"
            public_path.write_bytes(pem)
            signature_path.write_bytes(signature_bytes)
            public_path.chmod(0o600)
            signature_path.chmod(0o600)
            result = _run_openssl(
                ["dgst", "-sha256", "-verify", str(public_path), "-signature", str(signature_path)],
                input_bytes=pae,
            )
            if result.returncode != 0:
                _reject("signature_verification_failed")
            if state == "active":
                active_signers.append(keyid)
    if len(active_signers) < threshold:
        _reject("signature_threshold_not_met")
    return tuple(sorted(active_signers))


def _validate_release_record(record: Any) -> dict[str, Any]:
    value = _exact(
        record,
        {"schema", "product", "version", "revision", "platform", "artifact", "provenance", "acceptance"},
        "release_record_shape_rejected",
    )
    if value["schema"] != "home-center.release-record.v1" or value["product"] != PRODUCT:
        _reject("release_record_scope_rejected")
    version = _string(value["version"], SEMVER, "release_version_rejected", maximum=32)
    _semver(version)
    revision = _string(value["revision"], HEX40, "release_revision_rejected", maximum=40)

    platform = _exact(value["platform"], {"os", "architecture"}, "release_platform_shape_rejected")
    if platform != {"os": PLATFORM_OS, "architecture": PLATFORM_ARCHITECTURE}:
        _reject("release_platform_rejected")

    artifact = _exact(
        value["artifact"],
        {"filename", "sha256", "bytes", "manifest_sha256", "media_type", "object_key"},
        "release_artifact_shape_rejected",
    )
    artifact_sha = _string(artifact["sha256"], HEX64, "release_artifact_sha256_rejected", maximum=64)
    filename = f"home-center-{version}-linux-amd64.tar.gz"
    if artifact["filename"] != filename or artifact["media_type"] != "application/gzip":
        _reject("release_artifact_identity_rejected")
    _integer(artifact["bytes"], "release_artifact_bytes_rejected", minimum=1, maximum=MAX_ARCHIVE_TOTAL_BYTES)
    _string(artifact["manifest_sha256"], HEX64, "release_manifest_sha256_rejected", maximum=64)
    object_key = _string(artifact["object_key"], OBJECT_KEY, "release_object_key_rejected", maximum=512)
    if object_key != f"sha256/{artifact_sha[:2]}/{artifact_sha}/{filename}":
        _reject("release_object_key_mismatch")

    provenance = _exact(
        value["provenance"],
        {
            "repository",
            "source_ref",
            "workflow",
            "workflow_sha256",
            "run_id",
            "run_attempt",
            "ci_artifact_id",
            "ci_outer_zip_sha256",
            "source_date_epoch",
        },
        "release_provenance_shape_rejected",
    )
    if provenance["repository"] != REPOSITORY or provenance["source_ref"] != SOURCE_REF or provenance["workflow"] != WORKFLOW:
        _reject("release_provenance_scope_rejected")
    _string(provenance["workflow_sha256"], HEX64, "release_workflow_sha256_rejected", maximum=64)
    _string(provenance["ci_outer_zip_sha256"], HEX64, "release_ci_zip_sha256_rejected", maximum=64)
    for field in ("run_id", "run_attempt", "ci_artifact_id", "source_date_epoch"):
        _integer(provenance[field], f"release_{field}_rejected", minimum=1)

    acceptance = _exact(
        value["acceptance"],
        {"profile", "status", "transaction_id", "accepted_at", "evidence_bundle_sha256", "evidence_refs", "required_checks"},
        "release_acceptance_shape_rejected",
    )
    if acceptance["profile"] != "hm-dm-two-node.v1" or acceptance["status"] not in {"passed", "failed"}:
        _reject("release_acceptance_scope_rejected")
    _string(acceptance["transaction_id"], ASCII_TOKEN, "release_transaction_id_rejected", maximum=96)
    _timestamp(acceptance["accepted_at"], "release_accepted_at_rejected")
    _string(acceptance["evidence_bundle_sha256"], HEX64, "release_evidence_bundle_rejected", maximum=64)
    evidence_refs = acceptance["evidence_refs"]
    if not isinstance(evidence_refs, list) or not 1 <= len(evidence_refs) <= 32:
        _reject("release_evidence_refs_rejected")
    seen_refs: set[tuple[str, str]] = set()
    for raw_reference in evidence_refs:
        reference = _exact(raw_reference, {"kind", "locator", "content_sha256"}, "release_evidence_ref_shape_rejected")
        if reference["kind"] not in {"github-issue-comment", "gdrive-document-revision", "server-command-result"}:
            _reject("release_evidence_ref_kind_rejected")
        locator = _string(reference["locator"], ASCII_TOKEN, "release_evidence_ref_locator_rejected", maximum=256)
        _string(reference["content_sha256"], HEX64, "release_evidence_ref_digest_rejected", maximum=64)
        identity = (reference["kind"], locator)
        if identity in seen_refs:
            _reject("release_evidence_ref_duplicate")
        seen_refs.add(identity)
    checks = acceptance["required_checks"]
    if (
        not isinstance(checks, list)
        or any(not isinstance(item, str) or REASON.fullmatch(item) is None for item in checks)
        or len(set(checks)) != len(checks)
    ):
        _reject("release_acceptance_checks_rejected")
    return value


def release_record_sha256(record: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(record)).hexdigest()


def _validate_checkpoint(checkpoint: Any) -> dict[str, Any]:
    value = _exact(
        checkpoint,
        {
            "schema",
            "product",
            "channel",
            "generation",
            "ledger_sequence",
            "payload_sha256",
            "events_sha256",
            "last_event_sha256",
            "stable_artifact_sha256",
            "verified_at",
        },
        "checkpoint_shape_rejected",
    )
    if value["schema"] != "home-center.release-channel-checkpoint.v1":
        _reject("checkpoint_schema_rejected")
    if value["product"] != PRODUCT or value["channel"] != CHANNEL:
        _reject("checkpoint_scope_rejected")
    _integer(value["generation"], "checkpoint_generation_rejected", minimum=1)
    _integer(value["ledger_sequence"], "checkpoint_sequence_rejected", minimum=0, maximum=MAX_EVENTS)
    for field in ("payload_sha256", "events_sha256"):
        _string(value[field], HEX64, f"checkpoint_{field}_rejected", maximum=64)
    for field in ("last_event_sha256", "stable_artifact_sha256"):
        if value[field] is not None:
            _string(value[field], HEX64, f"checkpoint_{field}_rejected", maximum=64)
    _timestamp(value["verified_at"], "checkpoint_verified_at_rejected")
    if (value["ledger_sequence"] == 0) != (value["last_event_sha256"] is None):
        _reject("checkpoint_head_rejected")
    return value


def _fold_ledger(ledger: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None, str]:
    events = ledger["events"]
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        _reject("ledger_events_rejected")
    if ledger["ledger_sequence"] != len(events):
        _reject("ledger_sequence_mismatch")

    states: dict[str, str] = {}
    records: dict[str, dict[str, Any]] = {}
    artifact_records: dict[str, str] = {}
    version_identities: dict[str, tuple[str, str, str]] = {}
    revision_identities: dict[str, tuple[str, str, str]] = {}
    stable_record_id: str | None = None
    last_stable_version: tuple[int, int, int] | None = None
    previous_event_sha: str | None = None
    previous_recorded_at: datetime | None = None

    for index, raw_event in enumerate(events, start=1):
        event = _exact(
            raw_event,
            {"sequence", "previous_event_sha256", "recorded_at", "changes", "resulting_stable_artifact_sha256"},
            "ledger_event_shape_rejected",
        )
        if _integer(event["sequence"], "ledger_event_sequence_rejected", minimum=1, maximum=MAX_EVENTS) != index:
            _reject("ledger_event_sequence_mismatch")
        if event["previous_event_sha256"] != previous_event_sha:
            _reject("ledger_event_chain_rejected")
        recorded_at = _timestamp(event["recorded_at"], "ledger_event_time_rejected")
        if previous_recorded_at is not None and recorded_at <= previous_recorded_at:
            _reject("ledger_event_time_order_rejected")
        previous_recorded_at = recorded_at
        changes = event["changes"]
        if not isinstance(changes, list) or len(changes) not in {1, 2}:
            _reject("ledger_event_changes_rejected")
        stable_before = stable_record_id

        if len(changes) == 2:
            first = changes[0] if isinstance(changes[0], dict) else {}
            second = changes[1] if isinstance(changes[1], dict) else {}
            if not (
                stable_before is not None
                and first.get("release_record_sha256") == stable_before
                and first.get("from") == "stable"
                and first.get("to") == "superseded"
                and second.get("from") == "unlisted"
                and second.get("to") == "stable"
            ):
                _reject("ledger_atomic_promotion_rejected")

        for raw_change in changes:
            change = _exact(
                raw_change,
                {"release_record_sha256", "record", "from", "to", "reason_code", "evidence_sha256"},
                "ledger_change_shape_rejected",
            )
            record_id = _string(change["release_record_sha256"], HEX64, "ledger_record_sha_rejected", maximum=64)
            _string(change["reason_code"], REASON, "ledger_reason_rejected", maximum=64)
            _string(change["evidence_sha256"], HEX64, "ledger_evidence_sha_rejected", maximum=64)
            transition_from = change["from"]
            transition_to = change["to"]
            if transition_from not in {"unlisted", "stable", "superseded"} or transition_to not in {
                "stable",
                "superseded",
                "quarantined",
            }:
                _reject("ledger_transition_state_rejected")
            current_state = states.get(record_id, "unlisted")
            if transition_from != current_state:
                _reject("ledger_transition_source_rejected")
            if transition_from == "unlisted":
                record = _validate_release_record(change["record"])
                if release_record_sha256(record) != record_id:
                    _reject("ledger_release_record_digest_mismatch")
                artifact_sha = record["artifact"]["sha256"]
                if artifact_sha in artifact_records:
                    _reject("ledger_artifact_reused")
                identity = (record["revision"], artifact_sha, record_id)
                previous_identity = version_identities.get(record["version"])
                if previous_identity is not None and previous_identity != identity:
                    _reject("ledger_version_fork_rejected")
                revision_identity = (record["version"], artifact_sha, record_id)
                previous_revision_identity = revision_identities.get(record["revision"])
                if previous_revision_identity is not None and previous_revision_identity != revision_identity:
                    _reject("ledger_revision_fork_rejected")
                accepted_at = _timestamp(record["acceptance"]["accepted_at"], "release_accepted_at_rejected")
                if accepted_at > recorded_at:
                    _reject("ledger_acceptance_after_event")
                version_identities[record["version"]] = identity
                revision_identities[record["revision"]] = revision_identity
                artifact_records[artifact_sha] = record_id
                records[record_id] = record
            elif change["record"] is not None or record_id not in records:
                _reject("ledger_release_record_reference_rejected")

            allowed = {
                ("unlisted", "stable"),
                ("unlisted", "quarantined"),
                ("stable", "superseded"),
                ("stable", "quarantined"),
                ("superseded", "quarantined"),
            }
            if (transition_from, transition_to) not in allowed:
                _reject("ledger_transition_rejected")
            if transition_to == "stable":
                record = records[record_id]
                if record["acceptance"]["status"] != "passed":
                    _reject("ledger_unaccepted_stable_rejected")
                if tuple(record["acceptance"]["required_checks"]) != REQUIRED_ACCEPTANCE_CHECKS:
                    _reject("ledger_stable_acceptance_checks_rejected")
                version = _semver(record["version"])
                if last_stable_version is not None and version <= last_stable_version:
                    _reject("ledger_stable_version_not_increasing")
                if stable_record_id is not None:
                    _reject("ledger_multiple_stable_rejected")
                stable_record_id = record_id
                last_stable_version = version
            elif record_id == stable_record_id:
                stable_record_id = None
            states[record_id] = transition_to

        if any(change.get("to") == "stable" for change in changes if isinstance(change, dict)):
            if stable_before is not None and len(changes) != 2:
                _reject("ledger_non_atomic_promotion_rejected")
            if stable_before is None and len(changes) != 1:
                _reject("ledger_initial_promotion_rejected")
        expected_stable_sha = records[stable_record_id]["artifact"]["sha256"] if stable_record_id else None
        if event["resulting_stable_artifact_sha256"] != expected_stable_sha:
            _reject("ledger_event_stable_pointer_rejected")
        previous_event_sha = hashlib.sha256(canonical_json(event)).hexdigest()

    stable_record = records[stable_record_id] if stable_record_id else None
    if ledger["stable_artifact_sha256"] != (stable_record["artifact"]["sha256"] if stable_record else None):
        _reject("ledger_stable_pointer_rejected")
    events_sha = hashlib.sha256(canonical_json(events)).hexdigest()
    return stable_record, previous_event_sha, events_sha


def verify_envelope(
    envelope_bytes: bytes,
    trust_policy: Mapping[str, Any],
    *,
    checkpoint: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> VerifiedLedger:
    """Verify DSSE, canonical ledger state, freshness and monotonic checkpoint."""

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        _reject("verification_time_timezone_rejected")
    current_time = current_time.astimezone(timezone.utc).replace(microsecond=0)

    envelope = _exact(
        load_strict_json(envelope_bytes, maximum=MAX_ENVELOPE_BYTES, kind="envelope"),
        {"payloadType", "payload", "signatures"},
        "envelope_shape_rejected",
    )
    if envelope["payloadType"] != PAYLOAD_TYPE:
        _reject("envelope_payload_type_rejected")
    payload = _b64(envelope["payload"], "envelope_payload_base64_rejected", maximum=MAX_ENVELOPE_BYTES)
    if not payload or len(payload) > MAX_PAYLOAD_BYTES:
        _reject("ledger_payload_size_rejected")
    ledger = load_strict_json(payload, maximum=MAX_PAYLOAD_BYTES, kind="ledger")
    if canonical_json(ledger) != payload:
        _reject("ledger_payload_not_canonical")
    signing_key_ids = _verify_signatures(envelope, trust_policy, payload)

    value = _exact(
        ledger,
        {
            "schema",
            "product",
            "channel",
            "generation",
            "ledger_sequence",
            "issued_at",
            "expires_at",
            "events",
            "stable_artifact_sha256",
        },
        "ledger_shape_rejected",
    )
    if value["schema"] != "home-center.release-ledger.v1" or value["product"] != PRODUCT or value["channel"] != CHANNEL:
        _reject("ledger_scope_rejected")
    _integer(value["generation"], "ledger_generation_rejected", minimum=1)
    _integer(value["ledger_sequence"], "ledger_sequence_rejected", minimum=0, maximum=MAX_EVENTS)
    if value["stable_artifact_sha256"] is not None:
        _string(value["stable_artifact_sha256"], HEX64, "ledger_stable_sha_rejected", maximum=64)
    issued_at = _timestamp(value["issued_at"], "ledger_issued_at_rejected")
    expires_at = _timestamp(value["expires_at"], "ledger_expires_at_rejected")
    if expires_at <= issued_at or expires_at - issued_at > MAX_LEDGER_LIFETIME:
        _reject("ledger_lifetime_rejected")
    if issued_at - current_time > MAX_FUTURE_SKEW:
        _reject("ledger_not_yet_valid")
    if current_time >= expires_at:
        _reject("ledger_expired")

    stable_record, last_event_sha, events_sha = _fold_ledger(value)
    if value["events"]:
        last_event_time = _timestamp(value["events"][-1]["recorded_at"], "ledger_event_time_rejected")
        if issued_at < last_event_time:
            _reject("ledger_issued_before_event")
    payload_sha = hashlib.sha256(payload).hexdigest()

    if checkpoint is not None:
        prior = _validate_checkpoint(checkpoint)
        prior_time = _timestamp(prior["verified_at"], "checkpoint_verified_at_rejected")
        if current_time < prior_time:
            _reject("checkpoint_clock_rollback")
        if value["generation"] < prior["generation"]:
            _reject("ledger_generation_rollback")
        if value["generation"] == prior["generation"]:
            if payload_sha != prior["payload_sha256"]:
                _reject("ledger_generation_equivocation")
        else:
            if value["ledger_sequence"] < prior["ledger_sequence"]:
                _reject("ledger_sequence_rollback")
            if prior["ledger_sequence"] == 0:
                if prior["last_event_sha256"] is not None:
                    _reject("checkpoint_head_rejected")
            else:
                prefix_event = value["events"][prior["ledger_sequence"] - 1]
                prefix_sha = hashlib.sha256(canonical_json(prefix_event)).hexdigest()
                if prefix_sha != prior["last_event_sha256"]:
                    _reject("ledger_history_rewritten")
            if value["ledger_sequence"] == prior["ledger_sequence"]:
                if events_sha != prior["events_sha256"] or value["stable_artifact_sha256"] != prior["stable_artifact_sha256"]:
                    _reject("ledger_refresh_state_changed")

    verified_release = None
    if stable_record is not None:
        record_bytes = canonical_json(stable_record)
        verified_release = VerifiedRelease(
            version=stable_record["version"],
            revision=stable_record["revision"],
            filename=stable_record["artifact"]["filename"],
            artifact_sha256=stable_record["artifact"]["sha256"],
            artifact_bytes=stable_record["artifact"]["bytes"],
            manifest_sha256=stable_record["artifact"]["manifest_sha256"],
            object_key=stable_record["artifact"]["object_key"],
            record_sha256=hashlib.sha256(record_bytes).hexdigest(),
            record_bytes=record_bytes,
        )
    return VerifiedLedger(
        generation=value["generation"],
        ledger_sequence=value["ledger_sequence"],
        payload_sha256=payload_sha,
        events_sha256=events_sha,
        last_event_sha256=last_event_sha,
        stable_artifact_sha256=value["stable_artifact_sha256"],
        stable_release=verified_release,
        verified_at=current_time,
        signing_key_ids=signing_key_ids,
    )


def checkpoint_for(verified: VerifiedLedger) -> dict[str, Any]:
    return {
        "schema": "home-center.release-channel-checkpoint.v1",
        "product": PRODUCT,
        "channel": CHANNEL,
        "generation": verified.generation,
        "ledger_sequence": verified.ledger_sequence,
        "payload_sha256": verified.payload_sha256,
        "events_sha256": verified.events_sha256,
        "last_event_sha256": verified.last_event_sha256,
        "stable_artifact_sha256": verified.stable_artifact_sha256,
        "verified_at": verified.verified_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _safe_archive_name(name: str) -> str:
    if name == ".":
        return name
    if not name.startswith("./") or "//" in name or "\\" in name or "\x00" in name:
        _reject("artifact_member_path_rejected")
    relative = name[2:]
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _reject("artifact_member_path_rejected")
    return str(path)


def _open_object(root: Path, object_key: str) -> int:
    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise ReleaseChannelError("artifact_root_rejected") from exc
    try:
        parts = object_key.split("/")
        for part in parts[:-1]:
            next_descriptor = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        file_descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
    except OSError as exc:
        os.close(descriptor)
        raise ReleaseChannelError("artifact_object_rejected") from exc
    os.close(descriptor)
    info = os.fstat(file_descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(file_descriptor)
        _reject("artifact_object_metadata_rejected")
    return file_descriptor


def verify_artifact(verified: VerifiedLedger, artifact_root: Path) -> str:
    """Verify the content-addressed archive and its complete internal manifest."""

    release = verified.stable_release
    if release is None:
        _reject("artifact_without_stable_release")
    validated = _validate_release_record(
        load_strict_json(release.record_bytes, maximum=MAX_PAYLOAD_BYTES, kind="release_record")
    )
    if release_record_sha256(validated) != release.record_sha256:
        _reject("artifact_release_record_binding_rejected")
    artifact_identity = validated["artifact"]
    if (
        validated["version"] != release.version
        or validated["revision"] != release.revision
        or artifact_identity["filename"] != release.filename
        or artifact_identity["sha256"] != release.artifact_sha256
        or artifact_identity["bytes"] != release.artifact_bytes
        or artifact_identity["manifest_sha256"] != release.manifest_sha256
        or artifact_identity["object_key"] != release.object_key
        or verified.stable_artifact_sha256 != release.artifact_sha256
    ):
        _reject("artifact_release_identity_binding_rejected")
    artifact = validated["artifact"]
    descriptor = _open_object(Path(artifact_root), artifact["object_key"])
    try:
        info = os.fstat(descriptor)
        if info.st_size != artifact["bytes"]:
            _reject("artifact_size_mismatch")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        actual_sha = digest.hexdigest()
        if actual_sha != artifact["sha256"]:
            _reject("artifact_sha256_mismatch")
        os.lseek(descriptor, 0, os.SEEK_SET)
        with os.fdopen(os.dup(descriptor), "rb") as archive_handle:
            try:
                archive = tarfile.open(fileobj=archive_handle, mode="r:gz")
            except (tarfile.TarError, OSError) as exc:
                raise ReleaseChannelError("artifact_archive_rejected") from exc
            with archive:
                members: list[tarfile.TarInfo] = []
                for member in archive:
                    members.append(member)
                    if len(members) > MAX_ARCHIVE_MEMBERS:
                        _reject("artifact_member_count_rejected")
                if not members:
                    _reject("artifact_member_count_rejected")
                normalized: dict[str, tarfile.TarInfo] = {}
                total_size = 0
                expected_mtime = validated["provenance"]["source_date_epoch"]
                for member in members:
                    name = _safe_archive_name(member.name)
                    if name in normalized:
                        _reject("artifact_member_duplicate")
                    if not (member.isfile() or member.isdir()):
                        _reject("artifact_member_type_rejected")
                    if member.uid != 0 or member.gid != 0:
                        _reject("artifact_member_owner_rejected")
                    if member.mtime != expected_mtime:
                        _reject("artifact_member_mtime_rejected")
                    if member.mode != (0o644 if member.isfile() else 0o755):
                        _reject("artifact_member_mode_rejected")
                    if member.isfile():
                        if member.size < 0 or member.size > MAX_ARCHIVE_MEMBER_BYTES:
                            _reject("artifact_member_size_rejected")
                        total_size += member.size
                        if total_size > MAX_ARCHIVE_TOTAL_BYTES:
                            _reject("artifact_total_size_rejected")
                    normalized[name] = member
                required = {"MANIFEST.sha256", "VERSION", "REVISION"}
                if not required.issubset(normalized) or any(not normalized[name].isfile() for name in required):
                    _reject("artifact_required_member_rejected")

                def member_bytes(name: str) -> bytes:
                    handle = archive.extractfile(normalized[name])
                    if handle is None:
                        _reject("artifact_member_read_rejected")
                    with handle:
                        data = handle.read(MAX_ARCHIVE_MEMBER_BYTES + 1)
                    if len(data) != normalized[name].size or len(data) > MAX_ARCHIVE_MEMBER_BYTES:
                        _reject("artifact_member_read_rejected")
                    return data

                manifest_bytes = member_bytes("MANIFEST.sha256")
                if hashlib.sha256(manifest_bytes).hexdigest() != artifact["manifest_sha256"]:
                    _reject("artifact_manifest_sha256_mismatch")
                try:
                    manifest_text = manifest_bytes.decode("ascii")
                except UnicodeDecodeError as exc:
                    raise ReleaseChannelError("artifact_manifest_encoding_rejected") from exc
                expected_files = {name for name, member in normalized.items() if member.isfile() and name != "MANIFEST.sha256"}
                manifest_entries: dict[str, str] = {}
                for line in manifest_text.splitlines():
                    matched = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._/-]*)", line)
                    if matched is None:
                        _reject("artifact_manifest_line_rejected")
                    expected_sha, name = matched.groups()
                    if name in manifest_entries or name not in expected_files or ".." in PurePosixPath(name).parts:
                        _reject("artifact_manifest_entry_rejected")
                    manifest_entries[name] = expected_sha
                if set(manifest_entries) != expected_files:
                    _reject("artifact_manifest_coverage_rejected")
                for name, expected_sha in manifest_entries.items():
                    if hashlib.sha256(member_bytes(name)).hexdigest() != expected_sha:
                        _reject("artifact_manifest_content_mismatch")
                try:
                    version = member_bytes("VERSION").decode("ascii").rstrip("\n")
                    revision = member_bytes("REVISION").decode("ascii").rstrip("\n")
                except UnicodeDecodeError as exc:
                    raise ReleaseChannelError("artifact_identity_encoding_rejected") from exc
                if version != validated["version"] or revision != validated["revision"]:
                    _reject("artifact_release_identity_mismatch")
        return actual_sha
    except ReleaseChannelError:
        raise
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ReleaseChannelError("artifact_archive_rejected") from exc
    finally:
        os.close(descriptor)


def verification_result(verified: VerifiedLedger, *, artifact_verified: bool) -> dict[str, Any]:
    release = verified.stable_release
    return {
        "schema": "home-center.release-verification-result.v1",
        "status": "verified-stable" if release is not None else "verified-no-stable",
        "reason_code": None,
        "generation": verified.generation,
        "ledger_sequence": verified.ledger_sequence,
        "payload_sha256": verified.payload_sha256,
        "signing_key_ids": list(verified.signing_key_ids),
        "stable_release": None
        if release is None
        else {
            "version": release.version,
            "revision": release.revision,
            "artifact_sha256": release.artifact_sha256,
            "artifact_verified": artifact_verified,
        },
        "next_checkpoint": checkpoint_for(verified),
    }
