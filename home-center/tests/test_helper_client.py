from __future__ import annotations

import unittest
from unittest import mock

from home_center import helper_client
from home_center.helper_client import HelperClientError


class HelperClientTests(unittest.TestCase):
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
