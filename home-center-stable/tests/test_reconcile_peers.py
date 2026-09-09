from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from home_center.config import Peer
from home_center.reconcile import Reconciler


def _capability(peer: Peer) -> dict[str, object]:
    return {
        "schema": "home-center.node-capability.v1",
        "node": {"id": peer.node_id, "name": peer.name, "address": peer.address},
    }


class _Store:
    def __init__(self) -> None:
        self.upserts: list[tuple[dict[str, object], str]] = []
        self.marks: list[tuple[str, str]] = []
        self.audit_events: list[dict[str, object]] = []

    def upsert_node(self, value: dict[str, object], state: str) -> None:
        self.upserts.append((value, state))

    def mark_node(self, node_id: str, state: str) -> None:
        self.marks.append((node_id, state))

    def audit(self, **value: object) -> None:
        self.audit_events.append(value)


class ReconcilePeerCollectionTests(unittest.TestCase):
    def test_each_peer_is_reconciled_and_audited_independently(self) -> None:
        peer_a = Peer("node-b", "node-b", "192.0.2.11", "https://192.0.2.11:9443", "node-b")
        peer_b = Peer("node-c", "node-c", "192.0.2.12", "https://192.0.2.12:9443", "node-c")
        config = SimpleNamespace(
            node_id="node-a",
            node_name="node-a",
            role="control-plane",
            management_address="192.0.2.10",
            peers=(peer_a, peer_b),
            reconcile_interval_seconds=15,
        )
        store = _Store()
        reconciler = Reconciler(config, store)

        def fetch(peer: Peer) -> dict[str, object]:
            if peer is peer_b:
                raise TimeoutError("unavailable")
            return _capability(peer)

        local = {"schema": "home-center.node-capability.v1", "node": {"id": "node-a"}}
        with mock.patch("home_center.reconcile.collect", return_value=local):
            with mock.patch.object(reconciler, "_fetch_peer", side_effect=fetch):
                reconciler.reconcile_once()
                reconciler.reconcile_once()

        self.assertEqual(store.upserts.count((local, "ready")), 2)
        self.assertEqual(store.upserts.count((_capability(peer_a), "ready")), 2)
        self.assertEqual(store.marks, [("node-c", "unreachable"), ("node-c", "unreachable")])
        self.assertEqual(
            [(event["target"], event["outcome"]) for event in store.audit_events],
            [("node-b", "ready"), ("node-c", "unreachable")],
        )


if __name__ == "__main__":
    unittest.main()
