"""SQLite state store with migrations and a keyed append-only audit chain."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from .util import canonical_json, utc_now


MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cluster_meta (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS nodes (
            node_id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL,
            address TEXT NOT NULL,
            status TEXT NOT NULL,
            capabilities_json TEXT NOT NULL,
            last_seen TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS desired_state (
            resource_key TEXT PRIMARY KEY,
            generation INTEGER NOT NULL,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            job_type TEXT NOT NULL,
            state TEXT NOT NULL,
            initiator TEXT NOT NULL,
            reason TEXT NOT NULL,
            preflight_json TEXT NOT NULL,
            result_json TEXT,
            evidence_json TEXT,
            recovery_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            occurred_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT NOT NULL,
            outcome TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            details_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            entry_hash TEXT NOT NULL UNIQUE
        );
        """,
    ),
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS action_job_metadata (
            job_id TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
            actor TEXT NOT NULL,
            action_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            steps_json TEXT NOT NULL,
            UNIQUE(actor, action_id, idempotency_key)
        );
        """,
    ),
    (
        3,
        """
        CREATE TABLE IF NOT EXISTS module_permission_acknowledgements (
            acknowledgement_id TEXT PRIMARY KEY,
            review_id TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            review_json TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('recorded', 'superseded')),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            record_hash TEXT NOT NULL UNIQUE,
            UNIQUE(actor, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS module_permission_acknowledgements_actor_created
        ON module_permission_acknowledgements(actor, created_at DESC);
        CREATE INDEX IF NOT EXISTS module_permission_acknowledgements_actor_scope
        ON module_permission_acknowledgements(actor, scope_id, state);
        """,
    ),
    (
        4,
        """
        CREATE TABLE IF NOT EXISTS module_artifact_publications (
            publication_id TEXT PRIMARY KEY,
            module_id TEXT NOT NULL,
            version TEXT NOT NULL,
            publisher TEXT NOT NULL,
            manifest_binding_sha256 TEXT NOT NULL,
            statement_sha256 TEXT NOT NULL,
            artifact_sha256 TEXT NOT NULL,
            artifact_size_bytes INTEGER NOT NULL,
            signing_key_ids_json TEXT NOT NULL,
            object_key TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state = 'published'),
            published_at TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            record_hash TEXT NOT NULL UNIQUE,
            UNIQUE(module_id, version, artifact_sha256)
        );
        CREATE INDEX IF NOT EXISTS module_artifact_publications_module_version
        ON module_artifact_publications(module_id, version, published_at DESC);
        """,
    ),
)


class IdempotencyConflict(ValueError):
    """The same idempotency key was reused with different request material."""


class StateStore:
    def __init__(self, path: Path, audit_key: bytes, cluster_id: str) -> None:
        self.path = path
        self.audit_key = audit_key
        self.cluster_id = cluster_id
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o750)
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=5)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()
        os.chmod(path, 0o640)
        self.set_meta("cluster_id", cluster_id)
        self.verify_audit_chain()
        self.verify_module_permission_acknowledgements()
        self.verify_module_artifact_publications()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _migrate(self) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            current = {
                int(row[0]) for row in self._connection.execute("SELECT version FROM schema_migrations")
            }
            for version, sql in MIGRATIONS:
                if version in current:
                    continue
                self._connection.executescript(sql)
                self._connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (version, utc_now())
                )

    def set_meta(self, key: str, value: Any) -> None:
        payload = canonical_json(value)
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO cluster_meta(key, value_json, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
                (key, payload, utc_now()),
            )

    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._connection.execute("SELECT value_json FROM cluster_meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def upsert_node(self, capability: dict[str, Any], status: str) -> None:
        node = capability["node"]
        now = utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO nodes(node_id,name,role,address,status,capabilities_json,last_seen,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(node_id) DO UPDATE SET
                  name=excluded.name, role=excluded.role, address=excluded.address,
                  status=excluded.status, capabilities_json=excluded.capabilities_json,
                  last_seen=excluded.last_seen, updated_at=excluded.updated_at""",
                (
                    node["id"],
                    node["name"],
                    node["role"],
                    node["address"],
                    status,
                    canonical_json(capability),
                    capability.get("observed_at", now),
                    now,
                ),
            )

    def mark_node(self, node_id: str, status: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE nodes SET status=?, updated_at=? WHERE node_id=?", (status, utc_now(), node_id)
            )

    def nodes(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT node_id,name,role,address,status,capabilities_json,last_seen,updated_at FROM nodes ORDER BY name"
            ).fetchall()
        return [
            {
                "node_id": row["node_id"],
                "name": row["name"],
                "role": row["role"],
                "address": row["address"],
                "status": row["status"],
                "capabilities": json.loads(row["capabilities_json"]),
                "last_seen": row["last_seen"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def audit(
        self,
        *,
        actor: str,
        action: str,
        target: str,
        outcome: str,
        correlation_id: str,
        details: dict[str, Any] | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        occurred_at = utc_now()
        details_json = canonical_json(details or {})
        with self._lock, self._connection:
            row = self._connection.execute("SELECT entry_hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            previous_hash = row[0] if row else "0" * 64
            material = canonical_json(
                {
                    "event_id": event_id,
                    "occurred_at": occurred_at,
                    "actor": actor,
                    "action": action,
                    "target": target,
                    "outcome": outcome,
                    "correlation_id": correlation_id,
                    "details": json.loads(details_json),
                    "previous_hash": previous_hash,
                }
            ).encode("utf-8")
            entry_hash = hmac.new(self.audit_key, material, hashlib.sha256).hexdigest()
            self._connection.execute(
                """INSERT INTO audit(event_id,occurred_at,actor,action,target,outcome,correlation_id,
                details_json,previous_hash,entry_hash) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    occurred_at,
                    actor,
                    action,
                    target,
                    outcome,
                    correlation_id,
                    details_json,
                    previous_hash,
                    entry_hash,
                ),
            )
        return event_id

    def verify_audit_chain(self) -> str:
        previous_hash = "0" * 64
        with self._lock:
            rows = self._connection.execute("SELECT * FROM audit ORDER BY seq").fetchall()
        for row in rows:
            if row["previous_hash"] != previous_hash:
                raise RuntimeError(f"audit chain discontinuity at seq {row['seq']}")
            material = canonical_json(
                {
                    "event_id": row["event_id"],
                    "occurred_at": row["occurred_at"],
                    "actor": row["actor"],
                    "action": row["action"],
                    "target": row["target"],
                    "outcome": row["outcome"],
                    "correlation_id": row["correlation_id"],
                    "details": json.loads(row["details_json"]),
                    "previous_hash": row["previous_hash"],
                }
            ).encode("utf-8")
            expected = hmac.new(self.audit_key, material, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, row["entry_hash"]):
                raise RuntimeError(f"audit chain signature mismatch at seq {row['seq']}")
            previous_hash = row["entry_hash"]
        return previous_hash

    def audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self._lock:
            rows = self._connection.execute(
                """SELECT seq,event_id,occurred_at,actor,action,target,outcome,correlation_id,
                details_json,previous_hash,entry_hash FROM audit ORDER BY seq DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                **{key: row[key] for key in row.keys() if key != "details_json"},
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ]

    @staticmethod
    def _decode_job(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": "home-center.job.v1",
            "job_id": row["job_id"],
            "job_type": row["job_type"],
            "state": row["state"],
            "initiator": row["initiator"],
            "reason": row["reason"],
            "idempotency_key": row["idempotency_key"] or f"legacy:{row['job_id']}",
            "preflight": json.loads(row["preflight_json"]),
            "steps": json.loads(row["steps_json"]) if row["steps_json"] else [],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "evidence": json.loads(row["evidence_json"]) if row["evidence_json"] else None,
            "recovery": json.loads(row["recovery_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self._lock:
            rows = self._connection.execute(
                """SELECT j.*,m.idempotency_key,m.request_hash,m.steps_json
                FROM jobs AS j LEFT JOIN action_job_metadata AS m ON m.job_id=j.job_id
                ORDER BY j.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [self._decode_job(row) for row in rows]

    def job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT j.*,m.idempotency_key,m.request_hash,m.steps_json
                FROM jobs AS j LEFT JOIN action_job_metadata AS m ON m.job_id=j.job_id
                WHERE j.job_id=?""",
                (job_id,),
            ).fetchone()
        return self._decode_job(row) if row else None

    def create_action_job(
        self,
        *,
        action_id: str,
        actor: str,
        reason: str,
        idempotency_key: str,
        request_hash: str,
        preflight: dict[str, Any],
        steps: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        now = utc_now()
        job_id = str(uuid.uuid4())
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """SELECT j.*,m.idempotency_key,m.request_hash,m.steps_json
                    FROM action_job_metadata AS m JOIN jobs AS j ON j.job_id=m.job_id
                    WHERE m.actor=? AND m.action_id=? AND m.idempotency_key=?""",
                    (actor, action_id, idempotency_key),
                ).fetchone()
                if row:
                    if row["request_hash"] != request_hash:
                        raise IdempotencyConflict(idempotency_key)
                    self._connection.commit()
                    return self._decode_job(row), False
                self._connection.execute(
                    """INSERT INTO jobs(
                    job_id,job_type,state,initiator,reason,preflight_json,result_json,evidence_json,
                    recovery_json,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        job_id,
                        action_id,
                        "preflight",
                        actor,
                        reason,
                        canonical_json(preflight),
                        None,
                        None,
                        canonical_json({"strategy": "none-read-only", "checkpoint": None}),
                        now,
                        now,
                    ),
                )
                self._connection.execute(
                    """INSERT INTO action_job_metadata(
                    job_id,actor,action_id,idempotency_key,request_hash,steps_json
                    ) VALUES(?,?,?,?,?,?)""",
                    (job_id, actor, action_id, idempotency_key, request_hash, canonical_json(steps)),
                )
                row = self._connection.execute(
                    """SELECT j.*,m.idempotency_key,m.request_hash,m.steps_json
                    FROM jobs AS j JOIN action_job_metadata AS m ON m.job_id=j.job_id
                    WHERE j.job_id=?""",
                    (job_id,),
                ).fetchone()
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        if row is None:
            raise RuntimeError("action job persistence failed")
        return self._decode_job(row), True

    def transition_action_job(
        self,
        job_id: str,
        *,
        expected_state: str,
        new_state: str,
        result: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        steps: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        allowed = {
            "preflight": {"running", "failed"},
            "running": {"verifying", "failed"},
            "verifying": {"succeeded", "failed"},
        }
        if new_state not in allowed.get(expected_state, set()):
            raise ValueError(f"invalid job transition {expected_state}->{new_state}")
        with self._lock, self._connection:
            row = self._connection.execute(
                """SELECT j.*,m.idempotency_key,m.request_hash,m.steps_json
                FROM jobs AS j JOIN action_job_metadata AS m ON m.job_id=j.job_id
                WHERE j.job_id=?""",
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["state"] != expected_state:
                raise RuntimeError("job state changed concurrently")
            self._connection.execute(
                """UPDATE jobs SET state=?, result_json=COALESCE(?,result_json),
                evidence_json=COALESCE(?,evidence_json), updated_at=?
                WHERE job_id=? AND state=?""",
                (
                    new_state,
                    canonical_json(result) if result is not None else None,
                    canonical_json(evidence) if evidence is not None else None,
                    utc_now(),
                    job_id,
                    expected_state,
                ),
            )
            if steps is not None:
                self._connection.execute(
                    "UPDATE action_job_metadata SET steps_json=? WHERE job_id=?",
                    (canonical_json(steps), job_id),
                )
            row = self._connection.execute(
                """SELECT j.*,m.idempotency_key,m.request_hash,m.steps_json
                FROM jobs AS j JOIN action_job_metadata AS m ON m.job_id=j.job_id
                WHERE j.job_id=?""",
                (job_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("action job transition failed")
        return self._decode_job(row)

    def _module_acknowledgement_hash(self, record: dict[str, Any]) -> str:
        material = canonical_json(
            {
                "schema": "home-center.module-permission-acknowledgement-record.v1",
                "acknowledgement_id": record["acknowledgement_id"],
                "review_id": record["review_id"],
                "scope_id": record["scope_id"],
                "actor": record["actor"],
                "idempotency_key": record["idempotency_key"],
                "request_hash": record["request_hash"],
                "review": record["review"],
                "state": record["state"],
                "created_at": record["created_at"],
                "expires_at": record["expires_at"],
                "correlation_id": record["correlation_id"],
            }
        ).encode("utf-8")
        return hmac.new(
            self.audit_key,
            b"module-permission-acknowledgement.v1\0" + material,
            hashlib.sha256,
        ).hexdigest()

    def _decode_module_permission_acknowledgement(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            review = json.loads(row["review_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError("module permission acknowledgement encoding failure") from exc
        record = {
            "acknowledgement_id": row["acknowledgement_id"],
            "review_id": row["review_id"],
            "scope_id": row["scope_id"],
            "actor": row["actor"],
            "idempotency_key": row["idempotency_key"],
            "request_hash": row["request_hash"],
            "review": review,
            "state": row["state"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "correlation_id": row["correlation_id"],
        }
        expected = self._module_acknowledgement_hash(record)
        if not hmac.compare_digest(expected, row["record_hash"]):
            raise RuntimeError("module permission acknowledgement integrity failure")
        return record

    def record_module_permission_acknowledgement(
        self,
        *,
        actor: str,
        idempotency_key: str,
        request_hash: str,
        review_id: str,
        scope_id: str,
        review: dict[str, Any],
        created_at: str,
        expires_at: str,
        correlation_id: str,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically persist or replay an actor-bound acknowledgement."""

        acknowledgement_id = str(uuid.uuid4())
        record = {
            "acknowledgement_id": acknowledgement_id,
            "review_id": review_id,
            "scope_id": scope_id,
            "actor": actor,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "review": review,
            "state": "recorded",
            "created_at": created_at,
            "expires_at": expires_at,
            "correlation_id": correlation_id,
        }
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    """SELECT * FROM module_permission_acknowledgements
                    WHERE actor=? AND idempotency_key=?""",
                    (actor, idempotency_key),
                ).fetchone()
                if existing is not None:
                    decoded = self._decode_module_permission_acknowledgement(existing)
                    if decoded["request_hash"] != request_hash:
                        raise IdempotencyConflict("module acknowledgement conflict")
                    self._connection.commit()
                    return decoded, False

                superseded = self._connection.execute(
                    """SELECT * FROM module_permission_acknowledgements
                    WHERE actor=? AND scope_id=? AND state='recorded'""",
                    (actor, scope_id),
                ).fetchall()
                for old_row in superseded:
                    old_record = self._decode_module_permission_acknowledgement(old_row)
                    old_record["state"] = "superseded"
                    updated = self._connection.execute(
                        """UPDATE module_permission_acknowledgements
                        SET state='superseded', record_hash=?
                        WHERE acknowledgement_id=? AND state='recorded'""",
                        (
                            self._module_acknowledgement_hash(old_record),
                            old_record["acknowledgement_id"],
                        ),
                    )
                    if updated.rowcount != 1:
                        raise RuntimeError("module acknowledgement state changed concurrently")

                self._connection.execute(
                    """INSERT INTO module_permission_acknowledgements(
                    acknowledgement_id,review_id,scope_id,actor,idempotency_key,request_hash,
                    review_json,state,created_at,expires_at,correlation_id,record_hash
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        acknowledgement_id,
                        review_id,
                        scope_id,
                        actor,
                        idempotency_key,
                        request_hash,
                        canonical_json(review),
                        "recorded",
                        created_at,
                        expires_at,
                        correlation_id,
                        self._module_acknowledgement_hash(record),
                    ),
                )
                row = self._connection.execute(
                    "SELECT * FROM module_permission_acknowledgements WHERE acknowledgement_id=?",
                    (acknowledgement_id,),
                ).fetchone()
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        if row is None:
            raise RuntimeError("module acknowledgement persistence failed")
        return self._decode_module_permission_acknowledgement(row), True

    def module_permission_acknowledgement(
        self, acknowledgement_id: str, *, actor: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT * FROM module_permission_acknowledgements
                WHERE acknowledgement_id=? AND actor=?""",
                (acknowledgement_id, actor),
            ).fetchone()
        return self._decode_module_permission_acknowledgement(row) if row is not None else None

    def module_permission_acknowledgements(
        self, *, actor: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(limit, 100))
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM module_permission_acknowledgements
                WHERE actor=? ORDER BY created_at DESC, acknowledgement_id DESC LIMIT ?""",
                (actor, bounded_limit),
            ).fetchall()
        return [self._decode_module_permission_acknowledgement(row) for row in rows]

    def verify_module_permission_acknowledgements(self) -> None:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM module_permission_acknowledgements ORDER BY acknowledgement_id"
            ).fetchall()
        for row in rows:
            self._decode_module_permission_acknowledgement(row)

    def _module_artifact_publication_hash(self, record: dict[str, Any]) -> str:
        material = canonical_json(
            {
                "schema": "home-center.module-artifact-publication-record.v1",
                "publication_id": record["publication_id"],
                "module_id": record["module_id"],
                "version": record["version"],
                "publisher": record["publisher"],
                "manifest_binding_sha256": record["manifest_binding_sha256"],
                "statement_sha256": record["statement_sha256"],
                "artifact_sha256": record["artifact_sha256"],
                "artifact_size_bytes": record["artifact_size_bytes"],
                "signing_key_ids": record["signing_key_ids"],
                "object_key": record["object_key"],
                "state": record["state"],
                "published_at": record["published_at"],
                "correlation_id": record["correlation_id"],
            }
        ).encode("utf-8")
        return hmac.new(
            self.audit_key,
            b"module-artifact-publication.v1\0" + material,
            hashlib.sha256,
        ).hexdigest()

    def _decode_module_artifact_publication(self, row: sqlite3.Row) -> dict[str, Any]:
        from .module_artifact_publication import (
            ModuleArtifactPublicationError,
            validate_module_artifact_publication_record,
        )

        try:
            signing_key_ids = json.loads(row["signing_key_ids_json"])
            publication = validate_module_artifact_publication_record(
                {
                    "publication_id": row["publication_id"],
                    "module_id": row["module_id"],
                    "version": row["version"],
                    "publisher": row["publisher"],
                    "manifest_binding_sha256": row["manifest_binding_sha256"],
                    "statement_sha256": row["statement_sha256"],
                    "artifact_sha256": row["artifact_sha256"],
                    "artifact_size_bytes": row["artifact_size_bytes"],
                    "signing_key_ids": signing_key_ids,
                    "object_key": row["object_key"],
                    "state": row["state"],
                    "published_at": row["published_at"],
                    "correlation_id": row["correlation_id"],
                }
            )
        except (json.JSONDecodeError, TypeError, ModuleArtifactPublicationError) as exc:
            raise RuntimeError("module artifact publication encoding failure") from exc
        expected = self._module_artifact_publication_hash(publication)
        if not hmac.compare_digest(expected, row["record_hash"]):
            raise RuntimeError("module artifact publication integrity failure")
        return publication

    def record_module_artifact_publication(
        self, *, publication: dict[str, Any], correlation_id: str
    ) -> tuple[dict[str, Any], bool]:
        """Atomically persist or replay immutable verified publication evidence."""

        from .module_artifact_publication import validate_module_artifact_publication_record

        validated = validate_module_artifact_publication_record(
            {**publication, "correlation_id": correlation_id}
        )
        if "publication_id" not in validated:
            raise ValueError("publication id required")
        record = validated
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    "SELECT * FROM module_artifact_publications WHERE publication_id=?",
                    (record["publication_id"],),
                ).fetchone()
                if existing is not None:
                    decoded = self._decode_module_artifact_publication(existing)
                    immutable_fields = set(validated) - {"published_at", "correlation_id"}
                    if any(decoded[field] != validated[field] for field in immutable_fields):
                        raise IdempotencyConflict("module artifact publication conflict")
                    self._connection.commit()
                    return decoded, False

                occupied = self._connection.execute(
                    """SELECT * FROM module_artifact_publications
                    WHERE module_id=? AND version=? AND artifact_sha256=?""",
                    (
                        record["module_id"],
                        record["version"],
                        record["artifact_sha256"],
                    ),
                ).fetchone()
                if occupied is not None:
                    self._decode_module_artifact_publication(occupied)
                    raise IdempotencyConflict("module artifact publication conflict")

                self._connection.execute(
                    """INSERT INTO module_artifact_publications(
                    publication_id,module_id,version,publisher,manifest_binding_sha256,
                    statement_sha256,artifact_sha256,artifact_size_bytes,signing_key_ids_json,
                    object_key,state,published_at,correlation_id,record_hash
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record["publication_id"],
                        record["module_id"],
                        record["version"],
                        record["publisher"],
                        record["manifest_binding_sha256"],
                        record["statement_sha256"],
                        record["artifact_sha256"],
                        record["artifact_size_bytes"],
                        canonical_json(record["signing_key_ids"]),
                        record["object_key"],
                        record["state"],
                        record["published_at"],
                        record["correlation_id"],
                        self._module_artifact_publication_hash(record),
                    ),
                )
                row = self._connection.execute(
                    "SELECT * FROM module_artifact_publications WHERE publication_id=?",
                    (record["publication_id"],),
                ).fetchone()
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        if row is None:
            raise RuntimeError("module artifact publication persistence failed")
        return self._decode_module_artifact_publication(row), True

    def module_artifact_publications(
        self, identities: list[tuple[str, str, str]]
    ) -> list[dict[str, Any]]:
        """Read exact module/version/digest publications for lifecycle preflight."""

        if len(identities) > 128:
            raise ValueError("too many module artifact identities")
        records: list[dict[str, Any]] = []
        with self._lock:
            for module_id, version, artifact_sha256 in sorted(set(identities)):
                row = self._connection.execute(
                    """SELECT * FROM module_artifact_publications
                    WHERE module_id=? AND version=? AND artifact_sha256=?""",
                    (module_id, version, artifact_sha256),
                ).fetchone()
                if row is not None:
                    records.append(self._decode_module_artifact_publication(row))
        return records

    def verify_module_artifact_publications(self) -> None:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM module_artifact_publications ORDER BY publication_id"
            ).fetchall()
        for row in rows:
            self._decode_module_artifact_publication(row)

    def desired_state(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT resource_key,generation,value_json,updated_at FROM desired_state ORDER BY resource_key"
            ).fetchall()
        return [
            {
                "resource_key": row["resource_key"],
                "generation": row["generation"],
                "value": json.loads(row["value_json"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def backup_to(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            target = sqlite3.connect(destination)
            try:
                self._connection.backup(target)
                target.execute("PRAGMA wal_checkpoint(FULL)")
                target.commit()
            finally:
                target.close()

    def integrity_check(self) -> bool:
        with self._lock:
            result = self._connection.execute("PRAGMA integrity_check").fetchone()[0]
        return result == "ok"
