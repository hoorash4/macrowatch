from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from earnings.corp_codes import DartCorporation  # noqa: E402
from earnings.supabase_rest import EarningsStoreError, SupabaseEarningsStore  # noqa: E402
from earnings.sync_corp_codes import build_identifier_rows  # noqa: E402


class LeakyStoreSession:
    def get(self, url, *, params, headers, timeout):
        raise RuntimeError(f"failed with {headers['Authorization']}")


class EarningsCorpCodeSyncTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
