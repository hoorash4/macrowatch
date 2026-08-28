from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260828_add_earnings_foundation.sql"
OPS_MIGRATION = ROOT / "supabase/migrations/20260828_add_earnings_ingestion_ops.sql"
OPEN_DART_OPS_MIGRATION = ROOT / "supabase/migrations/20260828_add_open_dart_ingestion_functions.sql"
OPEN_DART_WORKER_MIGRATION = ROOT / "supabase/migrations/20260828_add_open_dart_financial_worker_functions.sql"
SLIM_STORAGE_MIGRATION = ROOT / "supabase/migrations/20260828_slim_earnings_storage.sql"
SEC_MIGRATION = ROOT / "supabase/migrations/20260828_add_sec_earnings_functions.sql"
DEPLOY_WORKFLOW = ROOT / ".github/workflows/deploy-supabase.yml"
OPEN_DART_WORKFLOW = ROOT / ".github/workflows/earnings-open-dart.yml"
SEC_WORKFLOW = ROOT / ".github/workflows/earnings-sec.yml"
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
        cls.open_dart_ops_migration = OPEN_DART_OPS_MIGRATION.read_text(encoding="utf-8")
        cls.open_dart_worker_migration = OPEN_DART_WORKER_MIGRATION.read_text(encoding="utf-8")
        cls.slim_storage_migration = SLIM_STORAGE_MIGRATION.read_text(encoding="utf-8")
        cls.sec_migration = SEC_MIGRATION.read_text(encoding="utf-8")
        cls.deploy_workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
        cls.open_dart_workflow = OPEN_DART_WORKFLOW.read_text(encoding="utf-8")
        cls.sec_workflow = SEC_WORKFLOW.read_text(encoding="utf-8")
        cls.universe_migration = UNIVERSE_MIGRATION.read_text(encoding="utf-8")
        cls.universe_function = UNIVERSE_FUNCTION.read_text(encoding="utf-8")
        cls.kis_client = KIS_CLIENT.read_text(encoding="utf-8")
        cls.universe_sources = UNIVERSE_SOURCES.read_text(encoding="utf-8")
        cls.contract = CONTRACT.read_text(encoding="utf-8")

    def test_final_schema_keeps_only_filing_and_canonical_layers(self) -> None:
        self.assertIn("public.earnings_filings", self.migration)
        self.assertIn("public.earnings_quarterly_financials", self.migration)
        self.assertIn("drop table if exists public.earnings_source_payloads", self.slim_storage_migration)
        self.assertIn("drop table if exists public.earnings_financial_facts", self.slim_storage_migration)
        self.assertIn("drop column if exists eps", self.slim_storage_migration)

    def test_company_identity_and_membership_are_separate(self) -> None:
        self.assertIn("public.earnings_company_identifiers", self.migration)
        self.assertIn("public.earnings_index_memberships", self.migration)
        self.assertIn("unique (identifier_type, identifier_value)", self.migration)
        self.assertIn("where effective_to is null", self.migration)

    def test_filing_history_is_service_role_only(self) -> None:
        self.assertIn("alter table public.earnings_filings enable row level security", self.migration)
        self.assertNotIn("read earnings_filings", self.migration.lower())

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
        self.assertIn("DB·로그에 남기지 않는다", self.contract)

    def test_contract_uses_ten_year_backfill_and_preserves_adjusted_qoq(self) -> None:
        self.assertIn("최근 10년 분기재무를 백필", self.contract)
        self.assertIn("p_years integer default 10", self.open_dart_ops_migration)
        self.assertIn("qoq_raw", self.contract)
        self.assertIn("qoq_seasonally_adjusted", self.contract)
        self.assertIn("qoq_seasonally_adjusted_delta", self.contract)
        self.assertIn("분기 주가수익률 percentile 60%", self.contract)

    def test_contract_pauses_fully_exited_companies_and_fills_reentry_gaps(self) -> None:
        self.assertIn("모든 추적 지수에서 이탈하면", self.contract)
        self.assertIn("정기 공시 수집 대상에서는 제외", self.contract)
        self.assertIn("과거 추적 이력이 없는 완전 신규 기업", self.contract)
        self.assertIn("최근 10년을 백필", self.contract)
        self.assertIn("비어 있는 모든 분기를 기간 제한 없이 자동 백필", self.contract)
        self.assertIn("7년 만의 재진입이면 최대 7년의 공백", self.contract)
        self.assertIn("소속 기간을 기준으로 계산", self.contract)

    def test_ingestion_jobs_are_resumable_and_service_role_only(self) -> None:
        self.assertIn("public.earnings_collection_checkpoints", self.ops_migration)
        self.assertIn("public.earnings_ingestion_jobs", self.ops_migration)
        self.assertIn("where status in ('pending', 'running', 'retry')", self.ops_migration)
        self.assertIn("enable row level security", self.ops_migration)
        self.assertIn("sync_earnings_open_dart_identifiers", self.open_dart_ops_migration)
        self.assertIn("enqueue_earnings_open_dart_backfill", self.open_dart_ops_migration)
        self.assertIn("enqueue_earnings_open_dart_filings", self.open_dart_ops_migration)
        self.assertIn("drop function if exists public.save_earnings_open_dart_payload", self.slim_storage_migration)
        self.assertIn("to service_role", self.open_dart_ops_migration)
        self.assertIn("claim_earnings_open_dart_jobs", self.open_dart_worker_migration)
        self.assertIn("for update skip locked", self.open_dart_worker_migration.lower())
        self.assertIn("complete_earnings_open_dart_job", self.open_dart_worker_migration)
        self.assertIn("fail_earnings_open_dart_job", self.open_dart_worker_migration)
        self.assertIn("to service_role", self.open_dart_worker_migration)
        self.assertIn(OPEN_DART_WORKER_MIGRATION.name, self.deploy_workflow)
        self.assertIn(SLIM_STORAGE_MIGRATION.name, self.deploy_workflow)
        self.assertIn("inputs.sync_identifiers == true", self.open_dart_workflow)
        self.assertIn("github.event.schedule == '30 10 * * 1-5'", self.open_dart_workflow)

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

    def test_market_cap_sync_filters_korean_master_instrument_types(self) -> None:
        self.assertIn("parseKisKoreanMaster", self.universe_sources)
        self.assertIn("etpIndex", self.universe_sources)
        self.assertIn("hasClassification(etp)", self.universe_sources)
        self.assertNotIn("isTruthyFlag(etp)", self.universe_sources)
        self.assertIn("spacIndex", self.universe_sources)
        self.assertIn("preferredIndex", self.universe_sources)
        self.assertIn("result.length !== limit", self.universe_sources)
        self.assertIn("fetchKisOverseasMarketCapRanking", self.kis_client)
        self.assertIn('admin.rpc("authorize_earnings_ingestion")', self.universe_function)

    def test_us_universes_are_separate_and_explicitly_exclude_nasdaq_etfs(self) -> None:
        self.assertIn("fetchNasdaqOperatingSymbols", self.universe_sources)
        self.assertIn('values[etfIndex] !== "N"', self.universe_sources)
        self.assertIn('p_index_id: "SP100"', self.universe_function)
        self.assertIn('p_index_id: "NASDAQ100"', self.universe_function)
        self.assertIn("list_current_sec_earnings_companies", self.sec_migration)
        self.assertIn("upsert_sec_company_quarters", self.sec_migration)
        self.assertIn(SEC_MIGRATION.name, self.deploy_workflow)
        self.assertIn('cron: "30 4 * * 2-6"', self.sec_workflow)


if __name__ == "__main__":
    unittest.main()
