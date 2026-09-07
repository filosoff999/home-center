"""Trusted publication evidence for verified content-addressed module artifacts.

The canonical publisher entry point stages exact verified bytes before recording
immutable metadata.  Lifecycle planning only reads the resulting evidence; it
cannot acquire, mutate, install, execute, or activate an artifact.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from home_center.module_manifest import KEY_ID, MODULE_ID, SEMVER
from home_center.util import canonical_json


DIGEST = re.compile(r"^[0-9a-f]{64}$")
PREFIXED_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
OBJECT_KEY = re.compile(
    r"^sha256/(?P<prefix>[0-9a-f]{2})/(?P<digest>[0-9a-f]{64})/artifact\.tar\.gz$"
)
CORRELATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")
MAX_SIGNING_KEY_IDS = 16
MEDIA_TYPE = "application/vnd.home-center.module.v1+tar+gzip"
INSTALLATION_AUTHORITY = False
LIFECYCLE_EXECUTION_ENABLED = False
PRODUCTION_ACTIVATION_ENABLED = False


class ModuleArtifactPublicationError(ValueError):
    """A bounded publication-evidence failure without caller-controlled text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PublicationStore(Protocol):
    def record_module_artifact_publication(
        self, *, publication: dict[str, Any], correlation_id: str
    ) -> tuple[dict[str, Any], bool]: ...


@dataclass(frozen=True)
class PublishedModuleArtifact:
    publication: dict[str, Any]
    artifact_created: bool
    publication_created: bool


def _reject(code: str) -> None:
    raise ModuleArtifactPublicationError(code)


def _string(value: Any, pattern: re.Pattern[str], code: str, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or pattern.fullmatch(value) is None
    ):
        _reject(code)
    return value


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _reject("publication_time_rejected")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _identity(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "home-center.module-artifact-publication-identity.v1",
        "module": {
            "id": record["module_id"],
            "version": record["version"],
            "publisher": record["publisher"],
        },
        "manifest_binding_sha256": record["manifest_binding_sha256"],
        "statement_sha256": record["statement_sha256"],
        "artifact_sha256": record["artifact_sha256"],
        "artifact_size_bytes": record["artifact_size_bytes"],
        "signing_key_ids": record["signing_key_ids"],
        "object_key": record["object_key"],
    }


def _publication_id(record: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(_identity(record)).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def prepare_module_artifact_publication(
    staged: Any, *, now: datetime | None = None
) -> dict[str, Any]:
    """Create immutable evidence only from the verified staging result type."""

    from home_center.module_artifact import StagedModuleArtifact

    if not isinstance(staged, StagedModuleArtifact):
        _reject("publication_staging_evidence_rejected")
    verified = staged.verified
    signing_key_ids = list(verified.signing_key_ids)
    record = {
        "module_id": verified.module_id,
        "version": verified.version,
        "publisher": verified.publisher,
        "manifest_binding_sha256": verified.manifest_binding_sha256,
        "statement_sha256": verified.statement_sha256,
        "artifact_sha256": verified.artifact_sha256,
        "artifact_size_bytes": verified.artifact_size_bytes,
        "signing_key_ids": signing_key_ids,
        "object_key": verified.object_key,
        "state": "published",
        "published_at": _timestamp(now or datetime.now(UTC)),
    }
    validated = validate_module_artifact_publication_record(record)
    return {**validated, "publication_id": _publication_id(validated)}


def validate_module_artifact_publication_record(record: Any) -> dict[str, Any]:
    """Validate the closed internal record and its exact content address."""

    fields = {
        "module_id",
        "version",
        "publisher",
        "manifest_binding_sha256",
        "statement_sha256",
        "artifact_sha256",
        "artifact_size_bytes",
        "signing_key_ids",
        "object_key",
        "state",
        "published_at",
    }
    optional_id = isinstance(record, dict) and "publication_id" in record
    optional_correlation = isinstance(record, dict) and "correlation_id" in record
    optional_fields = ({"publication_id"} if optional_id else set()) | (
        {"correlation_id"} if optional_correlation else set()
    )
    if not isinstance(record, dict) or set(record) != fields | optional_fields:
        _reject("publication_record_shape_rejected")
    normalized = {
        "module_id": _string(record["module_id"], MODULE_ID, "publication_module_rejected", maximum=128),
        "version": _string(record["version"], SEMVER, "publication_version_rejected", maximum=64),
        "publisher": _string(record["publisher"], MODULE_ID, "publication_publisher_rejected", maximum=128),
        "manifest_binding_sha256": _string(
            record["manifest_binding_sha256"], DIGEST, "publication_manifest_binding_rejected", maximum=64
        ),
        "statement_sha256": _string(
            record["statement_sha256"], DIGEST, "publication_statement_rejected", maximum=64
        ),
        "artifact_sha256": _string(
            record["artifact_sha256"], DIGEST, "publication_artifact_rejected", maximum=64
        ),
        "artifact_size_bytes": record["artifact_size_bytes"],
        "signing_key_ids": record["signing_key_ids"],
        "object_key": record["object_key"],
        "state": record["state"],
        "published_at": record["published_at"],
    }
    if (
        isinstance(normalized["artifact_size_bytes"], bool)
        or not isinstance(normalized["artifact_size_bytes"], int)
        or not 1 <= normalized["artifact_size_bytes"] <= 2**63 - 1
    ):
        _reject("publication_artifact_size_rejected")
    signing_ids = normalized["signing_key_ids"]
    if (
        not isinstance(signing_ids, list)
        or not 1 <= len(signing_ids) <= MAX_SIGNING_KEY_IDS
        or signing_ids != sorted(set(signing_ids))
        or any(
            not isinstance(key_id, str) or KEY_ID.fullmatch(key_id) is None
            for key_id in signing_ids
        )
    ):
        _reject("publication_signers_rejected")
    object_key = normalized["object_key"]
    match = OBJECT_KEY.fullmatch(object_key) if isinstance(object_key, str) else None
    if (
        match is None
        or match["digest"] != normalized["artifact_sha256"]
        or match["prefix"] != normalized["artifact_sha256"][:2]
    ):
        _reject("publication_content_address_rejected")
    if normalized["state"] != "published":
        _reject("publication_state_rejected")
    published_at = normalized["published_at"]
    if not isinstance(published_at, str) or len(published_at) > 32:
        _reject("publication_time_rejected")
    try:
        parsed = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ModuleArtifactPublicationError("publication_time_rejected") from exc
    if parsed.tzinfo is None or _timestamp(parsed) != published_at:
        _reject("publication_time_rejected")
    if optional_id:
        publication_id = _string(
            record["publication_id"], PREFIXED_DIGEST, "publication_id_rejected", maximum=71
        )
        if not hmac.compare_digest(publication_id, _publication_id(normalized)):
            _reject("publication_id_mismatch")
        normalized = {**normalized, "publication_id": publication_id}
    if optional_correlation:
        normalized = {
            **normalized,
            "correlation_id": _string(
                record["correlation_id"],
                CORRELATION_ID,
                "publication_correlation_rejected",
                maximum=96,
            ),
        }
    return normalized


def render_module_artifact_publication(record: Any) -> dict[str, Any]:
    """Render safe public evidence without exposing a host filesystem path."""

    value = validate_module_artifact_publication_record(record)
    if "publication_id" not in value:
        _reject("publication_id_rejected")
    return {
        "schema": "home-center.module-artifact-publication.v1",
        "publication_id": value["publication_id"],
        "module": {
            "id": value["module_id"],
            "version": value["version"],
            "publisher": value["publisher"],
        },
        "artifact": {
            "sha256": value["artifact_sha256"],
            "size_bytes": value["artifact_size_bytes"],
            "media_type": MEDIA_TYPE,
            "content_address": f"sha256:{value['artifact_sha256']}",
        },
        "verification": {
            "manifest_binding_sha256": f"sha256:{value['manifest_binding_sha256']}",
            "statement_sha256": f"sha256:{value['statement_sha256']}",
            "signing_key_ids": list(value["signing_key_ids"]),
        },
        "status": "published",
        "published_at": value["published_at"],
        "installation_authority": INSTALLATION_AUTHORITY,
        "lifecycle_execution_enabled": LIFECYCLE_EXECUTION_ENABLED,
        "production_activation_enabled": PRODUCTION_ACTIVATION_ENABLED,
    }


def stage_and_publish_module_artifact(
    manifest_bytes: bytes,
    envelope_bytes: bytes,
    trust_policy_bytes: bytes,
    artifact_bytes: bytes,
    *,
    object_store_root: Any,
    store: PublicationStore,
    correlation_id: str,
    now: datetime | None = None,
) -> PublishedModuleArtifact:
    """Stage verified bytes, then atomically record immutable publication evidence."""

    from home_center.module_artifact import stage_module_artifact

    staged = stage_module_artifact(
        manifest_bytes,
        envelope_bytes,
        trust_policy_bytes,
        artifact_bytes,
        object_store_root=object_store_root,
    )
    draft = prepare_module_artifact_publication(staged, now=now)
    stored, publication_created = store.record_module_artifact_publication(
        publication=draft, correlation_id=correlation_id
    )
    return PublishedModuleArtifact(
        publication=render_module_artifact_publication(stored),
        artifact_created=staged.created,
        publication_created=publication_created,
    )
