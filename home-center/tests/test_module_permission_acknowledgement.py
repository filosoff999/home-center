from __future__ import annotations

import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))
sys.path.insert(0, str(ROOT / "tests"))

from home_center.module_permission_acknowledgement import (  # noqa: E402
    ACKNOWLEDGEMENT_TTL_SECONDS,
    ModulePermissionAcknowledgementError,
    acknowledgement_list,
    load_and_prepare_module_permission_acknowledgement,
    render_module_permission_acknowledgement,
)
from home_center.module_permission_review import build_module_permission_review  # noqa: E402
from home_center.store import IdempotencyConflict, StateStore  # noqa: E402
from test_module_admission import candidate, request  # noqa: E402


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def acknowledgement_payload(
    *,
    module_id: str = "org.test.module",
    idempotency_key: str = "ack-case-0001",
    review_id: str | None = None,
    manifest: dict | None = None,
) -> bytes:
    selected = manifest or candidate(module_id)
    admission = request([selected], [(module_id, selected["module"]["version"])])
    exact_review_id = build_module_permission_review(admission).review_id
    return json.dumps(
        {
            "schema": "home-center.module-permission-acknowledgement-request.v1",
            "review_id": review_id or exact_review_id,
            "acknowledgement": "permissions-reviewed",
            "idempotency_key": idempotency_key,
            "admission_request": admission,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class ModulePermissionAcknowledgementTests(unittest.TestCase):
    def test_valid_request_recomputes_and_binds_exact_review(self) -> None:
        draft = load_and_prepare_module_permission_acknowledgement(
            acknowledgement_payload(), actor="local-admin:admin", now=NOW
        )
        self.assertEqual(draft.actor, "local-admin:admin")
        self.assertEqual(draft.review_id, draft.review["review_id"])
        self.assertRegex(draft.request_hash, r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(draft.scope_id, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(draft.created_at, "2026-09-07T12:00:00Z")
        self.assertEqual(
            draft.expires_at,
            (NOW + timedelta(seconds=ACKNOWLEDGEMENT_TTL_SECONDS))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
        )

    def test_scope_is_independent_of_requested_module_order(self) -> None:
        first = candidate("org.test.first")
        second = candidate("org.test.second")
        forward = request(
            [first, second],
            [("org.test.first", "1.0.0"), ("org.test.second", "1.0.0")],
        )
        reverse = request(
            [second, first],
            [("org.test.second", "1.0.0"), ("org.test.first", "1.0.0")],
        )

        def prepare(admission: dict, key: str):
            review_id = build_module_permission_review(admission).review_id
            payload = json.dumps(
                {
                    "schema": "home-center.module-permission-acknowledgement-request.v1",
                    "review_id": review_id,
                    "acknowledgement": "permissions-reviewed",
                    "idempotency_key": key,
                    "admission_request": admission,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            return load_and_prepare_module_permission_acknowledgement(
                payload, actor="local-admin:admin", now=NOW
            )

        forward_draft = prepare(forward, "scope-order-0001")
        reverse_draft = prepare(reverse, "scope-order-0002")
        self.assertEqual(forward_draft.review_id, reverse_draft.review_id)
        self.assertEqual(forward_draft.scope_id, reverse_draft.scope_id)

    def test_review_mismatch_and_ambiguous_json_fail_without_echo(self) -> None:
        with self.assertRaises(ModulePermissionAcknowledgementError) as mismatch:
            load_and_prepare_module_permission_acknowledgement(
                acknowledgement_payload(review_id="sha256:" + "0" * 64),
                actor="local-admin:admin",
                now=NOW,
            )
        self.assertEqual(mismatch.exception.code, "acknowledgement_review_mismatch")
        self.assertNotIn("org.test.module", str(mismatch.exception))

        cases = (
            b'{"schema":"one","schema":"two"}',
            b"\xef\xbb\xbf{}",
            b'{"value":1.5}',
            b'{"value":NaN}',
        )
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(
                ModulePermissionAcknowledgementError
            ):
                load_and_prepare_module_permission_acknowledgement(
                    payload, actor="local-admin:admin", now=NOW
                )

    def test_unknown_fields_and_non_explicit_intent_fail_closed(self) -> None:
        value = json.loads(acknowledgement_payload())
        value["approve"] = True
        with self.assertRaises(ModulePermissionAcknowledgementError) as unknown:
            load_and_prepare_module_permission_acknowledgement(
                json.dumps(value).encode(), actor="local-admin:admin", now=NOW
            )
        self.assertEqual(unknown.exception.code, "acknowledgement_request_fields_rejected")

        value.pop("approve")
        value["acknowledgement"] = "approve"
        with self.assertRaises(ModulePermissionAcknowledgementError) as intent:
            load_and_prepare_module_permission_acknowledgement(
                json.dumps(value).encode(), actor="local-admin:admin", now=NOW
            )
        self.assertEqual(intent.exception.code, "acknowledgement_intent_rejected")

    def test_render_is_expiring_and_never_authorizing(self) -> None:
        draft = load_and_prepare_module_permission_acknowledgement(
            acknowledgement_payload(), actor="local-admin:admin", now=NOW
        )
        record = {
            **draft.__dict__,
            "acknowledgement_id": "12345678-1234-4234-9234-123456789abc",
            "state": "recorded",
        }
        active = render_module_permission_acknowledgement(record, now=NOW)
        self.assertEqual(active["status"], "recorded")
        self.assertEqual(active["lifecycle_handoff"]["status"], "precondition-recorded")
        expired = render_module_permission_acknowledgement(
            record, now=NOW + timedelta(seconds=ACKNOWLEDGEMENT_TTL_SECONDS)
        )
        self.assertEqual(expired["status"], "expired")
        self.assertEqual(expired["lifecycle_handoff"]["status"], "blocked")
        for value in (active, expired):
            self.assertFalse(value["lifecycle_handoff"]["consumable"])
            self.assertFalse(value["authorization_decision_persisted"])
            self.assertFalse(value["permission_grants_applied"])
            self.assertFalse(value["lifecycle_execution_enabled"])
            self.assertFalse(value["production_activation_enabled"])

    def test_render_rejects_review_scope_and_ttl_drift(self) -> None:
        draft = load_and_prepare_module_permission_acknowledgement(
            acknowledgement_payload(), actor="local-admin:admin", now=NOW
        )
        record = {
            **draft.__dict__,
            "acknowledgement_id": "12345678-1234-4234-9234-123456789abc",
            "state": "recorded",
        }
        cases = []
        review_drift = copy.deepcopy(record)
        review_drift["review"]["permission_grants_applied"] = True
        cases.append(review_drift)
        scope_drift = copy.deepcopy(record)
        scope_drift["scope_id"] = "sha256:" + "0" * 64
        cases.append(scope_drift)
        ttl_drift = copy.deepcopy(record)
        ttl_drift["expires_at"] = "2026-09-07T12:16:00Z"
        cases.append(ttl_drift)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(
                ModulePermissionAcknowledgementError
            ):
                render_module_permission_acknowledgement(case, now=NOW)

    def test_list_contract_is_closed_and_non_authorizing(self) -> None:
        value = acknowledgement_list([], now=NOW)
        self.assertEqual(
            value,
            {
                "schema": "home-center.module-permission-acknowledgement-list.v1",
                "items": [],
                "acknowledgement_persistence_enabled": True,
                "authorization_decisions_enabled": False,
                "permission_grants_applied": False,
                "lifecycle_execution_enabled": False,
                "production_activation_enabled": False,
            },
        )


class ModulePermissionAcknowledgementStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.sqlite3"
        self.store = StateStore(self.path, b"a" * 32, "test-cluster")

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def draft(
        self,
        *,
        actor: str = "local-admin:admin",
        idempotency_key: str = "ack-store-0001",
        manifest: dict | None = None,
    ):
        return load_and_prepare_module_permission_acknowledgement(
            acknowledgement_payload(
                module_id=(manifest or {}).get("module", {}).get("id", "org.test.module"),
                idempotency_key=idempotency_key,
                manifest=manifest,
            ),
            actor=actor,
            now=NOW,
        )

    def persist(self, draft, correlation_id: str = "case-1"):
        return self.store.record_module_permission_acknowledgement(
            actor=draft.actor,
            idempotency_key=draft.idempotency_key,
            request_hash=draft.request_hash,
            review_id=draft.review_id,
            scope_id=draft.scope_id,
            review=draft.review,
            created_at=draft.created_at,
            expires_at=draft.expires_at,
            correlation_id=correlation_id,
        )

    def test_idempotent_replay_returns_exact_record(self) -> None:
        draft = self.draft()
        first, created = self.persist(draft)
        second, replay_created = self.persist(draft, "case-2")
        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.module_permission_acknowledgements(actor=draft.actor)), 1)

    def test_conflicting_key_reuse_is_atomic(self) -> None:
        first = self.draft()
        self.persist(first)
        changed_manifest = candidate("org.test.module")
        changed_manifest["artifact"]["sha256"] = "b" * 64
        changed = self.draft(manifest=changed_manifest)
        with self.assertRaises(IdempotencyConflict):
            self.persist(changed)
        records = self.store.module_permission_acknowledgements(actor=first.actor)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["review_id"], first.review_id)
        self.assertEqual(records[0]["state"], "recorded")

    def test_new_same_scope_record_supersedes_only_same_actor(self) -> None:
        first = self.draft()
        first_record, _ = self.persist(first)
        other_actor = self.draft(actor="ad:other@HM.DM")
        other_record, _ = self.persist(other_actor, "other-1")

        changed_manifest = copy.deepcopy(candidate("org.test.module"))
        changed_manifest["artifact"]["sha256"] = "c" * 64
        replacement = self.draft(
            idempotency_key="ack-store-0002", manifest=changed_manifest
        )
        replacement_record, _ = self.persist(replacement, "case-3")

        first_after = self.store.module_permission_acknowledgement(
            first_record["acknowledgement_id"], actor=first.actor
        )
        other_after = self.store.module_permission_acknowledgement(
            other_record["acknowledgement_id"], actor=other_actor.actor
        )
        self.assertEqual(first_after["state"], "superseded")
        self.assertEqual(other_after["state"], "recorded")
        self.assertEqual(replacement_record["state"], "recorded")

    def test_record_lookup_is_actor_scoped(self) -> None:
        draft = self.draft()
        record, _ = self.persist(draft)
        self.assertIsNone(
            self.store.module_permission_acknowledgement(
                record["acknowledgement_id"], actor="local-admin:different"
            )
        )

    def test_hmac_integrity_detects_database_tampering(self) -> None:
        record, _ = self.persist(self.draft())
        connection = sqlite3.connect(self.path)
        connection.execute(
            "UPDATE module_permission_acknowledgements SET actor='tampered' WHERE acknowledgement_id=?",
            (record["acknowledgement_id"],),
        )
        connection.commit()
        connection.close()
        with self.assertRaises(RuntimeError):
            self.store.verify_module_permission_acknowledgements()


if __name__ == "__main__":
    unittest.main()
