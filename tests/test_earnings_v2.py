from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from earnings_v2.financials import profit_margin, single_quarter_amount
from earnings_v2.growth import calculate_company_growth, conventional_growth
from earnings_v2.http import get_with_retries
from earnings_v2.market import aggregate_market_quarter, calculate_market_series
from earnings_v2.krx import is_eligible_common_stock
from earnings_v2.models import MarketQuarter, QuarterValue, UniverseCandidate
from earnings_v2.pilot import build_one_year_pilot, build_recent_four_quarter_pilot
from earnings_v2.pipeline import coverage_report, prepare_company_series
from earnings_v2.readiness import inspect_repository
from earnings_v2.repository import EarningsV2Store
from earnings_v2.universe import select_final_universe


def quarter(year: int, fiscal_quarter: int, op: str, net: str | None = None) -> QuarterValue:
    return QuarterValue(
        company_id="company",
        fiscal_year=year,
        fiscal_quarter=fiscal_quarter,
        market_year=year,
        market_quarter=fiscal_quarter,
        period_end=date(year, fiscal_quarter * 3, 1),
        top_line=Decimal("100"),
        operating_income=Decimal(op),
        net_income=Decimal(net if net is not None else op),
        currency="KRW",
        consolidation_scope="CFS",
    )


class EarningsV2GrowthTests(unittest.TestCase):
    def test_void_rpc_response_is_a_success(self):
        class Response:
            content = b""

            @staticmethod
            def raise_for_status():
                return None

        class Session:
            @staticmethod
            def post(*_args, **_kwargs):
                return Response()

        store = object.__new__(EarningsV2Store)
        store.url = "https://example.test"
        store.service_role_key = "test"
        store.timeout = 1
        store.session = Session()
        self.assertIsNone(store._rpc("void_rpc", {}))

    def test_get_with_retries_handles_temporary_api_failures(self):
        class Response:
            def __init__(self, status_code):
                self.status_code = status_code

        class Session:
            def __init__(self):
                self.calls = 0

            def get(self, _url, **_kwargs):
                self.calls += 1
                return Response(503 if self.calls == 1 else 200)

        session = Session()
        response = get_with_retries(
            session, "https://example.test", attempts=2, backoff_factor=0,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(session.calls, 2)

    def test_conventional_growth_keeps_turns_as_states(self):
        self.assertEqual(conventional_growth(Decimal("10"), Decimal("-5")).state, "black_turn")
        self.assertEqual(conventional_growth(Decimal("-10"), Decimal("5")).state, "red_turn")
        self.assertEqual(conventional_growth(Decimal("-3"), Decimal("-5")).state, "loss_narrowing")
        self.assertEqual(conventional_growth(Decimal("-8"), Decimal("-5")).state, "loss_widening")
        self.assertIsNone(conventional_growth(Decimal("10"), Decimal("0")).value)

    def test_yoy_is_normal_percentage_for_positive_values(self):
        rows = calculate_company_growth([quarter(2025, 1, "100"), quarter(2026, 1, "125")])
        self.assertEqual(rows[-1].operating_income_yoy_pct, Decimal("25.00"))
        self.assertEqual(rows[-1].operating_income_yoy_state, "normal")

    def test_seasonal_qoq_uses_only_prior_same_transitions_and_latest_ten(self):
        rows = []
        for year in range(2010, 2026):
            rows.extend([quarter(year, 1, "100"), quarter(year, 2, "120")])
        rows.extend([quarter(2026, 1, "100"), quarter(2026, 2, "130")])
        result = calculate_company_growth(rows)[-1]
        self.assertEqual(result.operating_income_qoq_sa_pct, Decimal("10.0"))
        self.assertEqual(result.operating_income_qoq_state, "normal")

    def test_scope_change_breaks_growth(self):
        prior = quarter(2025, 1, "100")
        current = quarter(2026, 1, "120").with_metrics(consolidation_scope="OFS")
        result = calculate_company_growth([prior, current])[-1]
        self.assertEqual(result.operating_income_yoy_state, "scope_mismatch")
        self.assertIsNone(result.operating_income_yoy_pct)

    def test_margin_delta_is_previous_quarter_percentage_point_change(self):
        prior = quarter(2026, 1, "20", "10").with_metrics(
            operating_margin_pct=Decimal("20"),
            net_margin_pct=Decimal("10"),
        )
        current = quarter(2026, 2, "24", "9").with_metrics(
            operating_margin_pct=Decimal("24"),
            net_margin_pct=Decimal("9"),
        )
        result = calculate_company_growth([prior, current])[-1]
        self.assertEqual(result.operating_margin_qoq_delta_pctp, Decimal("4"))
        self.assertEqual(result.net_margin_qoq_delta_pctp, Decimal("-1"))


class EarningsV2FinancialTests(unittest.TestCase):
    def test_profit_margin_uses_same_quarter_values_and_rejects_zero_denominator(self):
        self.assertEqual(profit_margin(Decimal("20"), Decimal("80")), Decimal("25.00000000"))
        self.assertEqual(profit_margin(Decimal("-10"), Decimal("100")), Decimal("-10.00000000"))
        self.assertIsNone(profit_margin(Decimal("10"), Decimal("0")))
        self.assertIsNone(profit_margin(None, Decimal("100")))

    def test_single_quarter_prefers_disclosed_three_month_value(self):
        self.assertEqual(single_quarter_amount(2, current_three_month=Decimal("7"), cumulative=Decimal("20"), previous_cumulative=Decimal("8")), Decimal("7"))
        self.assertEqual(single_quarter_amount(4, cumulative=Decimal("40"), previous_cumulative=Decimal("28")), Decimal("12"))

    def test_krx_filter_keeps_common_stock_and_rejects_preferred_or_spac(self):
        self.assertTrue(is_eligible_common_stock("삼성전자", "005930"))
        self.assertFalse(is_eligible_common_stock("삼성전자우", "005935"))
        self.assertFalse(is_eligible_common_stock("테스트스팩1호", "123456"))


class EarningsV2UniverseAndMarketTests(unittest.TestCase):
    def test_universe_filters_currency_and_deduplicates_company(self):
        candidates = [
            UniverseCandidate("a", "A", "USD", Decimal("100"), date(2026, 6, 30)),
            UniverseCandidate("a", "A class B", "USD", Decimal("80"), date(2026, 6, 30)),
            UniverseCandidate("b", "B", "EUR", Decimal("200"), date(2026, 6, 30)),
            UniverseCandidate("c", "C", "USD", Decimal("90"), date(2026, 6, 30)),
        ]
        result = select_final_universe(
            market_id="us_largecap", market_year=2026, market_quarter=2,
            candidates=candidates, selection_method="reconstructed_revenue500", target_count=2,
        )
        self.assertEqual([row.company_id for row in result], ["a", "c"])
        self.assertEqual([row.market_cap_rank for row in result], [1, 2])

    def test_market_uses_average_of_complete_final_members(self):
        result = aggregate_market_quarter(
            market_id="kr_largecap", market_year=2020, market_quarter=1,
            company_values=[
                (Decimal("100"), Decimal("10"), Decimal("5"), "complete"),
                (Decimal("200"), Decimal("30"), Decimal("15"), "complete"),
            ],
            historical=True,
        )
        self.assertEqual(result.average_operating_income, Decimal("20"))
        self.assertEqual(result.average_net_income, Decimal("10"))
        self.assertEqual(result.operating_margin_pct, Decimal("13.33333333"))
        self.assertEqual(result.net_margin_pct, Decimal("6.66666667"))
        self.assertEqual(result.completion_status, "historical_partial")

    def test_market_growth_is_calculated_from_average_series(self):
        rows = [
            MarketQuarter("kr_largecap", 2025, 1, Decimal("100"), Decimal("50"), 100, 100, "complete"),
            MarketQuarter("kr_largecap", 2026, 1, Decimal("120"), Decimal("75"), 100, 100, "complete"),
        ]
        calculated = calculate_market_series(rows)
        self.assertEqual(calculated[-1].operating_income_yoy_pct, Decimal("20.0"))
        self.assertEqual(calculated[-1].net_income_yoy_pct, Decimal("50.0"))


class EarningsV2BoundaryTests(unittest.TestCase):
    def test_pilot_plan_is_exactly_one_year(self):
        plan = build_one_year_pilot(2026, ("kr_largecap",))
        self.assertEqual(plan.quarters, tuple(("kr_largecap", 2026, quarter) for quarter in range(1, 5)))

    def test_recent_pilot_uses_trailing_four_confirmed_quarters(self):
        plan = build_recent_four_quarter_pilot(
            end_year=2026, end_quarter=2, markets=("kr_largecap",),
        )
        self.assertEqual(plan.quarters, (
            ("kr_largecap", 2025, 3), ("kr_largecap", 2025, 4),
            ("kr_largecap", 2026, 1), ("kr_largecap", 2026, 2),
        ))

    def test_v2_has_no_legacy_package_dependency(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(inspect_repository(root), [])

    def test_migration_keeps_private_schema_and_service_role_rpc(self):
        root = Path(__file__).resolve().parents[1]
        migration = next((root / "supabase" / "migrations").glob("*_create_earnings_v2_foundation.sql"))
        sql = migration.read_text(encoding="utf-8")
        self.assertIn("create schema if not exists earnings_v2", sql.lower())
        self.assertIn("enable row level security", sql.lower())
        self.assertIn("grant execute", sql.lower())
        self.assertNotIn("references public.earnings_", sql.lower())

    def test_pipeline_contract_supports_bulk_resume_and_incomplete_status(self):
        root = Path(__file__).resolve().parents[1]
        migration = root / "supabase" / "migrations" / "20260901111500_complete_earnings_v2_pipeline_contract.sql"
        sql = migration.read_text(encoding="utf-8").lower()
        self.assertIn("earnings_v2_get_company_quarters_many", sql)
        self.assertIn("earnings_v2_get_market_quarters", sql)
        self.assertIn("'incomplete'", sql)

    def test_profit_margin_fields_are_plain_and_not_backfilled_by_schema(self):
        root = Path(__file__).resolve().parents[1]
        migration = root / "supabase" / "migrations" / "20260901215500_store_new_earnings_v2_profit_margins.sql"
        sql = migration.read_text(encoding="utf-8").lower()
        self.assertIn("operating_margin_pct numeric(20, 8)", sql)
        self.assertIn("net_margin_pct numeric(20, 8)", sql)
        self.assertNotIn("generated always as", sql)
        self.assertIn("no automatic historical backfill", sql)

    def test_margin_delta_migration_stores_company_and_market_values(self):
        root = Path(__file__).resolve().parents[1]
        migration = root / "supabase" / "migrations" / "20260902013000_store_earnings_v2_margin_deltas.sql"
        sql = migration.read_text(encoding="utf-8").lower()
        self.assertIn("operating_margin_qoq_delta_pctp numeric(20, 8)", sql)
        self.assertIn("net_margin_qoq_delta_pctp numeric(20, 8)", sql)
        self.assertIn("earnings_v2_upsert_company_quarters", sql)
        self.assertIn("earnings_v2_upsert_market_quarters", sql)

    def test_kis_top_line_adapter_is_protected_and_uses_standard_sales_account(self):
        root = Path(__file__).resolve().parents[1]
        adapter = (root / "supabase" / "functions" / "earnings-kis-top-lines" / "index.ts").read_text(encoding="utf-8")
        shared = (root / "supabase" / "functions" / "_shared" / "kis-client.ts").read_text(encoding="utf-8")
        self.assertIn('Authorization") !== `Bearer ${serviceRole}`', adapter)
        self.assertIn("sale_account", shared)
        self.assertIn("FHKST66430200", shared)

    def test_pipeline_never_completes_a_missing_required_fact(self):
        row = quarter(2026, 1, "10").with_metrics(top_line=None)
        prepared = prepare_company_series([row])
        self.assertEqual(prepared[0].quality_status, "review_required")

    def test_coverage_reports_exact_missing_companies(self):
        result = coverage_report(
            market_id="kr_kosdaq", year=2026, quarter=2, target_count=3,
            universe_company_ids=("a", "b", "c"), complete_financial_company_ids=("a", "c"),
        )
        self.assertFalse(result.ready)
        self.assertEqual(result.missing_company_ids, ("b",))


if __name__ == "__main__":
    unittest.main()
