from datetime import date
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from earnings.corp_codes import DartCorporation  # noqa: E402
from earnings.discover_backfill_filings import filing_window  # noqa: E402
from earnings.supabase_rest import EarningsStoreError, SupabaseEarningsStore  # noqa: E402
from earnings.sync_corp_codes import build_identifier_rows  # noqa: E402


class LeakyStoreSession:
    def get(self, url, *, params, headers, timeout):
        raise RuntimeError(f"failed with {headers['Authorization']}")


class RpcHttpErrorResponse:
    status_code = 504

    def raise_for_status(self):
        response_error = __import__("requests").HTTPError("gateway body with a secret")
        response_error.response = self
        raise response_error

    def json(self):
        return {"code": "57014", "message": "secret-bearing database detail"}


class RpcHttpErrorSession:
    def post(self, url, *, json, headers, timeout):
        return RpcHttpErrorResponse()


class EarningsCorpCodeSyncTests(unittest.TestCase):
    def test_historical_filing_window_never_extends_past_today(self) -> None:
        self.assertEqual(
            filing_window(2026, date(2026, 8, 28)),
            (date(2026, 1, 1), date(2026, 8, 28)),
        )
        self.assertIsNone(filing_window(2027, date(2026, 8, 28)))

    def test_only_exact_listed_ticker_matches_are_saved(self) -> None:
        companies = [
            {"id": "company-a", "ticker": "005930", "company_name": "삼성전자"},
            {"id": "company-b", "ticker": "999999", "company_name": "미확인"},
        ]
        listed = {
            "005930": DartCorporation("00126380", "삼성전자", "005930", "20260828"),
        }
        rows, unresolved = build_identifier_rows(
            companies,
            listed,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["corp_code"], "00126380")
        self.assertEqual(rows[0]["ticker"], "005930")
        self.assertEqual(unresolved, ["999999"])

    def test_output_rows_never_contain_api_credentials(self) -> None:
        rows, _ = build_identifier_rows(
            [{"id": "company-a", "ticker": "005930"}],
            {"005930": DartCorporation("00126380", "삼성전자", "005930", None)},
        )
        serialized = repr(rows).lower()
        self.assertNotIn("crtfc_key", serialized)
        self.assertNotIn("authorization", serialized)

    def test_store_transport_error_cannot_echo_service_role_key(self) -> None:
        store = SupabaseEarningsStore(
            "https://example.supabase.co",
            "service-role-secret",
            session=LeakyStoreSession(),
        )
        with self.assertRaises(EarningsStoreError) as context:
            store.list_active_korean_companies()
        self.assertNotIn("service-role-secret", str(context.exception))

    def test_rpc_error_keeps_safe_status_and_code_only(self) -> None:
        store = SupabaseEarningsStore(
            "https://example.supabase.co",
            "service-role-secret",
            session=RpcHttpErrorSession(),
        )
        with self.assertRaises(EarningsStoreError) as context:
            store.enqueue_open_dart_backfill(as_of_year=2026)
        message = str(context.exception)
        self.assertIn("HTTP 504", message)
        self.assertIn("code 57014", message)
        self.assertNotIn("service-role-secret", message)
        self.assertNotIn("secret-bearing", message)


if __name__ == "__main__":
    unittest.main()
