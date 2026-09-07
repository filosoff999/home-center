from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest.mock import Mock

from home_center.api import PeerRequestHandler
from home_center.local_admin_cluster import COMMAND_SCHEMA, RESULT_SCHEMA
from home_center.server import HomeCenterServer


TRANSACTION_ID = "la-20260907T120000Z-0123456789abcdef"


class TrustedPeerHandler(PeerRequestHandler):
    def _peer_identity_matches(self) -> bool:
        return True


class RejectedPeerHandler(PeerRequestHandler):
    def _peer_identity_matches(self) -> bool:
        return False


class LocalAdminPeerApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.participant = Mock()
        self.participant.handle.return_value = {
            "schema": RESULT_SCHEMA,
            "transaction_id": TRANSACTION_ID,
            "node_id": "hm-dm-dc02",
            "status": "succeeded",
            "phase": "prepared",
            "reason": None,
            "observed_at": "2026-09-07T12:00:00Z",
        }
        self.runtime = SimpleNamespace(
            config=SimpleNamespace(
                cluster_id="cluster-test",
                peer=SimpleNamespace(node_id="hm-dm-dc01", certificate_name="home-center-dc01"),
            ),
            local_admin_transaction_participant=self.participant,
        )

    def _server(self, handler=TrustedPeerHandler):
        server = HomeCenterServer(("127.0.0.1", 0), handler, self.runtime)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    @staticmethod
    def _command() -> dict[str, object]:
        return {
            "schema": COMMAND_SCHEMA,
            "cluster_id": "cluster-test",
            "transaction_id": TRANSACTION_ID,
            "intent_token": "1" * 32,
            "action": "prepare",
            "username": "admin",
            "current_password": "current password 17",
            "new_password": "new password 42",
        }

    def test_trusted_peer_dispatches_exact_command_without_echoing_secret(self) -> None:
        server = self._server()
        payload = json.dumps(self._command()).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/internal/v1/local-admin-transaction",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request) as response:
            result = json.load(response)
        self.assertEqual(result["phase"], "prepared")
        self.assertNotIn("current_password", result)
        self.assertNotIn("new_password", result)
        self.participant.handle.assert_called_once()
        self.assertEqual(self.participant.handle.call_args.kwargs["coordinator_node_id"], "hm-dm-dc01")

    def test_duplicate_or_extended_command_is_rejected_before_dispatch(self) -> None:
        server = self._server()
        invalid = json.dumps({**self._command(), "path": "/tmp/credential"}).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/internal/v1/local-admin-transaction",
            data=invalid,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 400)
        self.participant.handle.assert_not_called()

    def test_untrusted_peer_is_rejected_before_body_dispatch(self) -> None:
        server = self._server(RejectedPeerHandler)
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/internal/v1/local-admin-transaction",
            data=json.dumps(self._command()).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 403)
        self.participant.handle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
