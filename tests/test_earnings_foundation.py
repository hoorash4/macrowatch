from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260828_add_earnings_foundation.sql"
CONTRACT = ROOT / "docs/earnings-momentum-data-contract.md"


class EarningsFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.migration = MIGRATION.read_text(encoding="utf-8")
        cls.contract = CONTRACT.read_text(encoding="utf-8")

    def test_schema_separates_raw_filing_fact_and_canonical_layers(self) -> None:
        for table in (
            "earnings_source_payloads",
            "earnings_filings",
            "earnings_financial_facts",
            "earnings_quarterly_financials",
        ):
            self.assertIn(f"public.{table}", self.migration)

    def test_company_identity_and_membership_are_separate(self) -> None:
        self.assertIn("public.earnings_company_identifiers", self.migration)
        self.assertIn("public.earnings_index_memberships", self.migration)
        self.assertIn("unique (identifier_type, identifier_value)", self.migration)
        self.assertIn("where effective_to is null", self.migration)

    def test_raw_tables_are_service_role_only(self) -> None:
        for table in (
            "earnings_source_payloads",
            "earnings_filings",
            "earnings_financial_facts",
        ):
            self.assertIn(f"alter table public.{table} enable row level security", self.migration)
            self.assertNotIn(f"read {table}", self.migration.lower())

    def test_contract_uses_open_dart_multi_company_as_primary_path(self) -> None:
        self.assertIn("fnlttMultiAcnt.json", self.contract)
        self.assertIn("최대 100개씩", self.contract)
        self.assertIn("fnlttSinglAcntAll.json", self.contract)

    def test_contract_preserves_corrections_and_quarter_conversion_rules(self) -> None:
        self.assertIn("정정공시는 원공시를 덮어쓰지 않는다", self.contract)
        self.assertIn("H1 누적값에서 Q1 누적값", self.contract)
        self.assertIn("FY 누적값에서 9M 누적값", self.contract)

    def test_contract_forbids_secret_persistence(self) -> None:
        self.assertIn("API 키", self.contract)
        self.assertIn("DB·로그·원본 요청 파라미터에 남기지 않는다", self.contract)

    def test_contract_uses_five_year_backfill_and_preserves_adjusted_qoq(self) -> None:
        self.assertIn("최근 5년 분기재무를 백필", self.contract)
        self.assertIn("qoq_raw", self.contract)
        self.assertIn("qoq_seasonally_adjusted", self.contract)
        self.assertIn("qoq_seasonally_adjusted_delta", self.contract)
        self.assertIn("분기 주가수익률 percentile 60%", self.contract)


if __name__ == "__main__":
    unittest.main()
