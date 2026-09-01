from datetime import date
from decimal import Decimal
import unittest

from earnings_v2.ecos import EcosFxClient, EcosFxError


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    @staticmethod
    def raise_for_status():
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return FakeResponse(self.payload)


class EcosFxClientTests(unittest.TestCase):
    def test_uses_latest_rate_on_or_before_quarter_end_and_caches_it(self):
        session = FakeSession({
            "StatisticSearch": {
                "row": [
                    {"TIME": "20260327", "DATA_VALUE": "1,390.50"},
                    {"TIME": "20260330", "DATA_VALUE": "1,401.20"},
                ],
            },
        })
        client = EcosFxClient("secret-key", session=session)

        first = client.usd_krw_on_or_before(date(2026, 3, 31))
        second = client.usd_krw_on_or_before(date(2026, 3, 31))

        self.assertEqual(first, Decimal("1401.20"))
        self.assertEqual(second, Decimal("1401.20"))
        self.assertEqual(len(session.calls), 1)
        self.assertIn("/20260321/20260331/", session.calls[0][0])

    def test_transport_error_never_exposes_embedded_api_key(self):
        client = EcosFxClient(
            "do-not-log-this-key",
            session=FakeSession(error=RuntimeError("failed URL do-not-log-this-key")),
        )

        with self.assertRaises(EcosFxError) as raised:
            client.usd_krw_on_or_before(date(2026, 3, 31))

        self.assertNotIn("do-not-log-this-key", str(raised.exception))

    def test_rejects_nested_ecos_error_response(self):
        client = EcosFxClient(
            "secret-key",
            session=FakeSession({
                "StatisticSearch": {"RESULT": {"CODE": "INFO-200"}},
            }),
        )

        with self.assertRaises(EcosFxError):
            client.usd_krw_on_or_before(date(2026, 3, 31))


if __name__ == "__main__":
    unittest.main()
