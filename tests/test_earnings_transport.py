"""Transport policy contracts: shared mechanics must not merge pipeline diagnostics."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from earnings_common.http import bounded_request, safe_request_failure
from earnings_v25.transport_policy import bounded_request as legacy_request
from earnings_v25.transport_policy import safe_request_failure as legacy_failure


class TransportPolicyTests(unittest.TestCase):
    def error(self, headers=None):
        response = requests.Response()
        response.status_code = 503
        response.headers.update(headers or {})
        return requests.HTTPError("secret URL must not appear", response=response)

    def test_diagnostics_remain_pipeline_specific(self):
        error = self.error({"X-Financial-Source-Stage": "scan", "X-Financial-Source-Reason": "unavailable"})
        self.assertEqual(safe_request_failure("FSC", "read", error), "FSC read returned HTTP 503")
        self.assertEqual(legacy_failure("FSC", "read", error), "FSC read returned HTTP 503 (scan: unavailable)")

    def test_missing_headers_keep_original_message(self):
        error = self.error()
        self.assertEqual(legacy_failure("FSC", "read", error), safe_request_failure("FSC", "read", error))

    def test_non_http_errors_share_safe_message(self):
        for error in (requests.ConnectTimeout("secret"), requests.ReadTimeout("secret"), ValueError("secret")):
            self.assertEqual(legacy_failure("FSC", "read", error), safe_request_failure("FSC", "read", error))
            self.assertNotIn("secret", legacy_failure("FSC", "read", error))

    def test_retry_events_and_request_parameters_are_preserved(self):
        for request, suffix in ((bounded_request, ""), (legacy_request, " (scan)")):
            with self.subTest(request=request):
                error = self.error({"X-Financial-Source-Stage": "scan"})
                session = Mock()
                session.get.side_effect = error
                retry, sleep = Mock(), Mock()
                with self.assertRaises(requests.HTTPError) as raised:
                    request(session, "GET", "https://example.invalid", provider="FSC", operation="read",
                            total_timeout=None, attempt_timeout=None, connect_timeout=10, read_timeout=30,
                            on_retry=retry, sleep=sleep, params={"year": 2016})
                self.assertIs(raised.exception, error)
                self.assertEqual(session.get.call_count, 3)
                self.assertEqual(sleep.call_args_list, [unittest.mock.call(1.0), unittest.mock.call(2.0)])
                self.assertEqual(retry.call_args_list, [unittest.mock.call(1, "FSC read returned HTTP 503" + suffix, None),
                                                       unittest.mock.call(2, "FSC read returned HTTP 503" + suffix, None)])
                session.get.assert_called_with("https://example.invalid", timeout=(10, 30), stream=True, params={"year": 2016})
