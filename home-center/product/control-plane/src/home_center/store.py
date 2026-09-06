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
)


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

    def jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self._lock:
            rows = self._connection.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("preflight_json", "result_json", "evidence_json", "recovery_json"):
                item[key.removesuffix("_json")] = json.loads(item.pop(key)) if item[key] else None
            result.append(item)
        return result

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
