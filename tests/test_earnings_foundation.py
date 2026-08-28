from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260828_add_earnings_foundation.sql"
OPS_MIGRATION = ROOT / "supabase/migrations/20260828_add_earnings_ingestion_ops.sql"
UNIVERSE_MIGRATION = ROOT / "supabase/migrations/20260828_define_market_cap_earnings_universes.sql"
UNIVERSE_FUNCTION = ROOT / "supabase/functions/earnings-universe/index.ts"
KIS_CLIENT = ROOT / "supabase/functions/_shared/kis-client.ts"
UNIVERSE_SOURCES = ROOT / "supabase/functions/_shared/earnings-universe-sources.ts"
CONTRACT = ROOT / "docs/earnings-momentum-data-contract.md"


class EarningsFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.migration = MIGRATION.read_text(encoding="utf-8")
        cls.ops_migration = OPS_MIGRATION.read_text(encoding="utf-8")
        cls.universe_migration = UNIVERSE_MIGRATION.read_text(encoding="utf-8")
        cls.universe_function = UNIVERSE_FUNCTION.read_text(encoding="utf-8")
        cls.kis_client = KIS_CLIENT.read_text(encoding="utf-8")
        cls.universe_sources = UNIVERSE_SOURCES.read_text(encoding="utf-8")
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

    def test_contract_pauses_fully_exited_companies_and_fills_reentry_gaps(self) -> None:
        self.assertIn("모든 추적 지수에서 이탈하면", self.contract)
        self.assertIn("정기 공시 수집 대상에서는 제외", self.contract)
        self.assertIn("과거 추적 이력이 없는 완전 신규 기업", self.contract)
        self.assertIn("최근 5년을 백필", self.contract)
        self.assertIn("비어 있는 모든 분기를 기간 제한 없이 자동 백필", self.contract)
        self.assertIn("7년 만의 재진입이면 최대 7년의 공백", self.contract)
        self.assertIn("소속 기간을 기준으로 계산", self.contract)

    def test_ingestion_jobs_are_resumable_and_service_role_only(self) -> None:
        self.assertIn("public.earnings_collection_checkpoints", self.ops_migration)
        self.assertIn("public.earnings_ingestion_jobs", self.ops_migration)
        self.assertIn("where status in ('pending', 'running', 'retry')", self.ops_migration)
        self.assertIn("enable row level security", self.ops_migration)

    def test_universes_are_market_cap_rankings_not_official_indices(self) -> None:
        for name in (
            "S&P 500 시가총액 상위 100",
            "NASDAQ 시가총액 상위 100",
            "KOSPI 시가총액 상위 100",
            "KOSDAQ 시가총액 상위 50",
        ):
            self.assertIn(name, self.universe_migration)
        self.assertIn("public.earnings_universe_snapshots", self.universe_migration)
        self.assertIn("sync_earnings_market_cap_universe", self.universe_migration)
        self.assertIn("authorize_earnings_ingestion", self.universe_migration)
        self.assertIn("jsonb_array_length(p_constituents) <> v_target_count", self.universe_migration)

    def test_market_cap_sync_filters_korean_rows_through_open_dart(self) -> None:
        self.assertIn("fetchOpenDartListedCompanies", self.universe_sources)
        self.assertIn("parseNaverMarketCapHtml", self.universe_sources)
        self.assertIn("if (!listed) continue", self.universe_sources)
        self.assertIn("result.length !== limit", self.universe_sources)
        self.assertIn("fetchKisOverseasMarketCapRanking", self.kis_client)
        self.assertIn('admin.rpc("authorize_earnings_ingestion")', self.universe_function)


if __name__ == "__main__":
    unittest.main()
