from __future__ import annotations

import unittest
from unittest import mock

from home_center import helper_client
from home_center.helper_client import HelperClientError


class HelperClientTests(unittest.TestCase):
    def test_cluster_prepare_uses_dedicated_non_persisted_validation_action(self) -> None:
        response = {
            "schema": "home-center.helper.secret-result.v1",
            "request_id": "placeholder",
            "action": "local-admin.password.validate.v1",
            "status": "succeeded",
            "reason": None,
        }

        def exchange(request, *, timeout_seconds):
            self.assertEqual(request["action"], "local-admin.password.validate.v1")
            self.assertEqual(request["params"]["current_password"], "current password 17")
            self.assertEqual(request["params"]["new_password"], "new password 42")
            self.assertEqual(timeout_seconds, helper_client.DEFAULT_HELPER_TIMEOUT_SECONDS)
            return {**response, "request_id": request["request_id"]}

        with mock.patch.object(helper_client, "_exchange", side_effect=exchange):
            result = helper_client.validate_local_admin_password_change(
                "admin", "current password 17", "new password 42"
            )
        self.assertEqual(result["status"], "succeeded")

    def test_response_deadline_is_total_not_reset_by_incremental_reads(self) -> None:
        client = mock.MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        with (
            mock.patch.object(helper_client.socket, "socket", return_value=client),
            mock.patch.object(helper_client.time, "monotonic", side_effect=[100.0, 100.0, 105.0, 111.0]),
        ):
            with self.assertRaisesRegex(HelperClientError, "helper response timeout"):
                helper_client.call_helper("helper.probe.v1", timeout_seconds=10.0)
        self.assertEqual(
            client.settimeout.call_args_list,
            [mock.call(10.0), mock.call(5.0)],
        )
        client.recv.assert_not_called()


if __name__ == "__main__":
    unittest.main()
