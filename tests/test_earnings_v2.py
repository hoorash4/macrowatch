from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from earnings_v2.aggregation import aggregate_market
from earnings_v2.models import CompanyIdentity, FinancialFact, PeriodicFiling
from earnings_v2.cli import completed_successfully, parser
from earnings_v2.pipeline import KoreaEarningsV2Pipeline, _eligible_name, filing_period, latest_completed_quarter
from earnings_v2.providers import KisClient, OpenDartClient
from earnings_v2.transform import (
    calculate_financial_series,
    conventional_growth,
    extract_company_fact,
    profit_margin,
)


def row(
    corp: str,
    name: str,
    *,
    current: str = "",
    cumulative: str = "",
    scope: str = "CFS",
    account_id: str = "",
) -> dict[str, str]:
    return {
        "corp_code": corp,
        "account_nm": name,
        "account_id": account_id,
        "thstrm_amount": current,
        "thstrm_add_amount": cumulative,
        "fs_div": scope,
        "sj_div": "IS",
        "rcept_no": "20260814000001",
        "currency": "KRW",
        "ord": "1",
    }


def complete(corp: str, *, current: str, cumulative: str, scope: str = "CFS") -> list[dict[str, str]]:
    return [
        row(corp, "매출액", current=current, cumulative=cumulative, scope=scope, account_id="ifrs-full_Revenue"),
        row(corp, "영업이익", current=current, cumulative=cumulative, scope=scope, account_id="dart_OperatingIncomeLoss"),
        row(corp, "당기순이익", current=current, cumulative=cumulative, scope=scope, account_id="ifrs-full_ProfitLoss"),
    ]


def fact(year: int, quarter: int, value: str, *, company: str = "kr:1") -> FinancialFact:
    amount = Decimal(value)
    return FinancialFact(
        company_id=company,
        fiscal_year=year,
        fiscal_quarter=quarter,
        period_end=date(year, quarter * 3, 1),
        top_line=amount * 10,
        operating_income=amount,
        net_income=amount,
        currency="KRW",
        consolidation_scope="CFS",
        source_filing_id="test",
        filing_date=date(year, quarter * 3, 1),
    )


def member(company: str, rank: int, *, year: int = 2026, quarter: int = 2) -> CompanyIdentity:
    return CompanyIdentity(
        company_id=company,
        company_name=company,
        stock_code=f"{rank:06d}",
        corp_code=f"{rank:08d}",
        market_id="kr_largecap",
        rank=rank,
        market_cap=Decimal(1000 - rank),
        reference_date=date(year, quarter * 3, 30),
    )


class OpenDartTransportTests(unittest.TestCase):
    def test_corporation_map_streams_the_archive(self):
        class Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def iter_content(*, chunk_size):
                self.assertEqual(chunk_size, 64 * 1024)
                return iter([b"not-a-zip"])

        class Session:
            def __init__(self):
                self.kwargs = None

            def get(self, _url, **kwargs):
                self.kwargs = kwargs
                return Response()

        session = Session()
        client = OpenDartClient("secret", session=session, interval=0)
        with self.assertRaises(RuntimeError):
            client.corporation_map()
        self.assertTrue(session.kwargs["stream"])

    def test_multi_account_batches_are_capped_at_one_hundred(self):
        class Response:
            content = b""

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"status": "000", "list": []}

        class Session:
            def __init__(self):
                self.calls = []

            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Response()

        session = Session()
        client = OpenDartClient("secret", session=session, interval=0)
        client.multi_accounts([f"{index:08d}" for index in range(150)], 2026, 2)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[0][1]["timeout"], (5, 20))
        self.assertEqual(len(session.calls[0][1]["params"]["corp_code"].split(",")), 100)
        self.assertEqual(len(session.calls[1][1]["params"]["corp_code"].split(",")), 50)

    def test_provider_error_does_not_expose_key(self):
        class Session:
            @staticmethod
            def get(*_args, **_kwargs):
                raise RuntimeError("secret")

        client = OpenDartClient("secret", session=Session(), interval=0)
        with self.assertRaises(RuntimeError) as captured:
            client.multi_accounts(["00000001"], 2026, 1)
        self.assertNotIn("secret", str(captured.exception))

    def test_periodic_filings_preserve_unique_receipt_numbers(self):
        class Response:
            content = b"1"

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {
                    "status": "000", "total_page": 1,
                    "list": [
                        {"corp_code": "00123456", "rcept_no": "20260814000001", "rcept_dt": "20260814", "report_nm": "분기보고서 (2026.03)"},
                        {"corp_code": "00123456", "rcept_no": "20260814000001", "rcept_dt": "20260814", "report_nm": "분기보고서 (2026.03)"},
                        {"corp_code": "00999999", "rcept_no": "20260814000002", "rcept_dt": "20260814", "report_nm": "주요사항보고서"},
                    ],
                }

        class Session:
            @staticmethod
            def get(*_args, **_kwargs):
                return Response()

        client = OpenDartClient("secret", session=Session(), interval=0)
        filings = client.periodic_filings(date(2026, 8, 1), date(2026, 8, 14))
        self.assertEqual(len(filings), 1)
        self.assertEqual(filings[0].receipt_no, "20260814000001")

    def test_periodic_filing_period_uses_report_reference_month(self):
        filing = PeriodicFiling("00123456", "20260814000001", date(2026, 8, 14), "[기재정정]반기보고서 (2026.06)")
        self.assertEqual(filing_period(filing), (2026, 2))


class CliContractTests(unittest.TestCase):
    def test_only_ready_results_are_successful(self):
        self.assertTrue(completed_successfully({"status": "ready"}))
        self.assertTrue(completed_successfully([{"status": "ready"}, {"status": "ready"}]))
        self.assertFalse(completed_successfully({"status": "incomplete"}))
        self.assertFalse(completed_successfully([{"status": "ready"}, {"status": "incomplete"}]))
        self.assertFalse(completed_successfully([]))

    def test_recalculation_mode_is_an_explicit_cli_path(self):
        args = parser().parse_args(["--year", "2026", "--quarter", "2", "--write", "--recalculate-only"])
        self.assertTrue(args.recalculate_only)


class DailyCheckpointTests(unittest.TestCase):
    def test_daily_run_deduplicates_boundary_receipts_and_advances_after_success(self):
        class Repository:
            saved = []

            @staticmethod
            def pipeline_state(_operation):
                return {"cursor": {
                    "last_checked_date": "2026-09-01",
                    "boundary_receipt_ids": ["20260901000001"],
                }}

            def save_state(self, operation, status, cursor, error=None):
                self.saved.append((operation, status, cursor, error))

        class Dart:
            @staticmethod
            def periodic_filings(start, end):
                self.assertEqual((start, end), (date(2026, 9, 1), date(2026, 9, 2)))
                return [
                    PeriodicFiling("00000001", "20260901000001", date(2026, 9, 1), "반기보고서 (2026.06)"),
                    PeriodicFiling("00000002", "20260902000001", date(2026, 9, 2), "반기보고서 (2026.06)"),
                ]

        repository = Repository()
        pipeline = KoreaEarningsV2Pipeline(krx=object(), dart=Dart(), repository=repository)
        captured = {}

        def run_quarter(year, quarter, **kwargs):
            captured.update({"year": year, "quarter": quarter, **kwargs})
            return {"status": "incomplete"}

        pipeline.run_quarter = run_quarter
        result = pipeline.run_daily(write=True, today=date(2026, 9, 2))
        self.assertEqual(captured["refresh_corp_codes"], {"00000002"})
        self.assertEqual(result["filing_discovery"]["new_receipts"], 1)
        self.assertEqual(repository.saved[-1][0:2], ("daily_filings", "ready"))
        self.assertEqual(repository.saved[-1][2]["last_checked_date"], "2026-09-02")
        self.assertEqual(repository.saved[-1][2]["boundary_receipt_ids"], ["20260902000001"])

    def test_first_daily_run_starts_from_the_day_it_is_enabled(self):
        class Repository:
            @staticmethod
            def pipeline_state(_operation):
                return None

            @staticmethod
            def save_state(*_args, **_kwargs):
                return None

        class Dart:
            @staticmethod
            def periodic_filings(start, end):
                self.assertEqual((start, end), (date(2026, 9, 2), date(2026, 9, 2)))
                return []

        pipeline = KoreaEarningsV2Pipeline(krx=object(), dart=Dart(), repository=Repository())
        pipeline.run_quarter = lambda *_args, **_kwargs: {"status": "incomplete"}
        pipeline.run_daily(write=True, today=date(2026, 9, 2))


class QuarterlyExtractionTests(unittest.TestCase):
    def test_q1_uses_current_cumulative_value(self):
        value = extract_company_fact("00000001", "kr:1", 2026, 1, complete("00000001", current="40", cumulative="40"), [])
        self.assertEqual(value.operating_income, Decimal("40"))

    def test_q2_always_subtracts_q1_cumulative(self):
        value = extract_company_fact(
            "00000001", "kr:1", 2026, 2,
            complete("00000001", current="60", cumulative="100"),
            complete("00000001", current="40", cumulative="40"),
        )
        self.assertEqual(value.operating_income, Decimal("60"))

    def test_q3_always_subtracts_q2_cumulative(self):
        value = extract_company_fact(
            "00000001", "kr:1", 2026, 3,
            complete("00000001", current="70", cumulative="170"),
            complete("00000001", current="60", cumulative="100"),
        )
        self.assertEqual(value.net_income, Decimal("70"))

    def test_q4_uses_annual_minus_q3_cumulative(self):
        value = extract_company_fact(
            "00000001", "kr:1", 2026, 4,
            complete("00000001", current="250", cumulative=""),
            complete("00000001", current="70", cumulative="170"),
        )
        self.assertEqual(value.top_line, Decimal("80"))

    def test_cfs_and_ofs_are_not_mixed(self):
        current = [
            row("1", "매출액", current="100", cumulative="100", scope="CFS"),
            row("1", "영업이익", current="20", cumulative="20", scope="CFS"),
            *complete("1", current="50", cumulative="50", scope="OFS"),
        ]
        value = extract_company_fact("1", "kr:1", 2026, 1, current, [])
        self.assertEqual(value.consolidation_scope, "OFS")
        self.assertEqual(value.net_income, Decimal("50"))

    def test_income_leaves_are_not_summed_as_top_line(self):
        current = [
            row("1", "이자수익", current="70", cumulative="70"),
            row("1", "수수료수익", current="30", cumulative="30"),
            row("1", "영업이익", current="20", cumulative="20"),
            row("1", "당기순이익", current="10", cumulative="10"),
        ]
        value = extract_company_fact("1", "kr:1", 2026, 1, current, [])
        self.assertIsNone(value.top_line)
        self.assertEqual(value.operating_income, Decimal("20"))

    def test_standard_revenue_id_does_not_override_an_unrelated_label(self):
        current = [
            row("1", "보험수익", current="100", cumulative="100", account_id="ifrs-full_Revenue"),
            row("1", "영업이익", current="20", cumulative="20"),
            row("1", "당기순이익", current="10", cumulative="10"),
        ]
        value = extract_company_fact("1", "kr:1", 2026, 1, current, [])
        self.assertIsNone(value.top_line)

    def test_explicit_financial_top_line_is_allowed(self):
        current = [
            row("1", "순영업이익", current="100", cumulative="100"),
            row("1", "영업이익", current="20", cumulative="20"),
            row("1", "당기순이익", current="10", cumulative="10"),
        ]
        value = extract_company_fact("1", "kr:1", 2026, 1, current, [])
        self.assertEqual(value.top_line, Decimal("100"))


class KisFallbackTests(unittest.TestCase):
    def test_kis_converts_cumulative_hundred_million_krw_to_standalone_won(self):
        class Response:
            ok = True

            @staticmethod
            def json():
                return {
                    "rt_cd": "0",
                    "output": [
                        {"stac_yymm": "202603", "sale_account": "100"},
                        {"stac_yymm": "202606", "sale_account": "250"},
                    ],
                }

        class Session:
            def __init__(self):
                self.kwargs = None

            def get(self, *_args, **kwargs):
                self.kwargs = kwargs
                return Response()

        session = Session()
        client = KisClient("key", "secret", cached_token=lambda: "token", session=session, interval=0)
        self.assertEqual(client.quarter_top_line("005930", 2026, 2), Decimal("15000000000"))
        self.assertEqual(session.kwargs["params"]["FID_DIV_CLS_CODE"], "1")


class GrowthAndAggregationTests(unittest.TestCase):
    def test_latest_completed_quarter_uses_previous_calendar_quarter(self):
        self.assertEqual(latest_completed_quarter(date(2026, 9, 2)), (2026, 2))
        self.assertEqual(latest_completed_quarter(date(2026, 1, 5)), (2025, 4))

    def test_yoy_requires_prior_year_and_turns_are_states(self):
        rows = calculate_financial_series([fact(2025, 1, "-10"), fact(2026, 1, "20")])
        self.assertIsNone(rows[-1].operating_income_yoy_pct)
        self.assertEqual(rows[-1].operating_income_yoy_state, "black_turn")

    def test_seasonal_qoq_waits_for_two_historical_same_quarter_transitions(self):
        rows = [
            fact(2020, 4, "100"), fact(2021, 1, "110"), fact(2021, 2, "100"), fact(2021, 3, "100"), fact(2021, 4, "100"),
            fact(2022, 1, "120"), fact(2022, 2, "100"), fact(2022, 3, "100"), fact(2022, 4, "100"), fact(2023, 1, "130"),
        ]
        calculated = calculate_financial_series(rows)
        self.assertEqual(calculated[1].operating_income_qoq_state, "insufficient_history")
        self.assertEqual(calculated[-1].operating_income_qoq_state, "normal")

    def test_loss_to_loss_growth_uses_absolute_prior_denominator(self):
        self.assertEqual(conventional_growth(Decimal("-70"), Decimal("-100")), (Decimal("30.0"), "normal"))
        self.assertEqual(conventional_growth(Decimal("-130"), Decimal("-100")), (Decimal("-30.0"), "normal"))

    def test_turns_and_zero_denominator_are_not_numeric_growth(self):
        self.assertEqual(conventional_growth(Decimal("10"), Decimal("-10")), (None, "black_turn"))
        self.assertEqual(conventional_growth(Decimal("-10"), Decimal("10")), (None, "red_turn"))
        self.assertEqual(conventional_growth(Decimal("10"), Decimal("0")), (None, "from_zero"))
        self.assertEqual(conventional_growth(Decimal("0"), Decimal("-10")), (Decimal("100"), "normal"))
        self.assertEqual(conventional_growth(Decimal("0"), Decimal("10")), (Decimal("-100"), "normal"))

    def test_individual_margin_is_null_for_nonpositive_top_line(self):
        self.assertIsNone(profit_margin(Decimal("10"), Decimal("0")))
        self.assertIsNone(profit_margin(Decimal("10"), Decimal("-5")))
        self.assertEqual(profit_margin(Decimal("0"), Decimal("100")), Decimal("0"))

    def test_company_db_row_contains_v6_pending_state(self):
        stored = fact(2026, 2, "10").db_row(calculation_version=6)
        self.assertFalse(stored["is_pending"])

    def test_pending_company_is_excluded_even_when_profit_values_exist(self):
        current_members = [member("a", 1), member("b", 2)]
        previous_members = [member("a", 1, year=2026, quarter=1), member("b", 2, year=2026, quarter=1)]
        current = {
            "a": fact(2026, 2, "12", company="a"),
            "b": fact(2026, 2, "999", company="b").with_changes(top_line=None, is_pending=True),
        }
        previous = {"a": fact(2026, 1, "10", company="a"), "b": fact(2026, 1, "20", company="b")}
        market = aggregate_market(
            "kr_largecap", 2026, 2, current_members, current, 2,
            comparison_members=previous_members, comparison_facts=previous,
        )
        self.assertEqual(market.operating_income_total, Decimal("32"))
        self.assertEqual(market.reported_company_count, 1)
        self.assertEqual(market.completion_status, "provisional")

    def test_missing_baseline_uses_available_actuals_as_provisional(self):
        current_members = [member("a", 1), member("b", 2)]
        current = {"a": fact(2026, 2, "12", company="a")}
        market = aggregate_market("kr_largecap", 2026, 2, current_members, current, 2)
        self.assertEqual(market.operating_income_total, Decimal("12"))
        self.assertEqual(market.reported_company_count, 1)
        self.assertEqual(market.completion_status, "provisional")

    def test_incomplete_prior_placeholder_is_omitted_from_provisional_total(self):
        current_members = [member("a", 1), member("b", 2)]
        previous_members = [member("a", 1, year=2026, quarter=1), member("b", 2, year=2026, quarter=1)]
        current = {"a": fact(2026, 2, "12", company="a")}
        previous = {"b": fact(2026, 1, "20", company="b").with_changes(top_line=None, is_pending=True)}
        market = aggregate_market(
            "kr_largecap", 2026, 2, current_members, current, 2,
            comparison_members=previous_members, comparison_facts=previous,
        )
        self.assertEqual(market.operating_income_total, Decimal("12"))
        self.assertEqual(market.completion_status, "provisional")

    def test_market_db_row_maps_domain_status_to_database_lifecycle(self):
        current_members = [member("a", 1)]
        market = aggregate_market(
            "kr_largecap", 2026, 2, current_members,
            {"a": fact(2026, 2, "12", company="a")}, 1,
        )
        stored = market.db_row(calculation_version=6)
        self.assertEqual(stored["lifecycle_status"], "complete")
        self.assertNotIn("completion_status", stored)

    def test_provisional_total_replaces_reported_firms_and_keeps_placeholders(self):
        current_members = [member("a", 1), member("b", 2)]
        previous_members = [member("a", 1, year=2026, quarter=1), member("x", 2, year=2026, quarter=1)]
        current = {"a": fact(2026, 2, "12", company="a"), "b": fact(2026, 2, "30", company="b").with_changes(is_pending=True)}
        previous = {"a": fact(2026, 1, "10", company="a"), "x": fact(2026, 1, "20", company="x")}
        market = aggregate_market(
            "kr_largecap", 2026, 2, current_members, current, 2,
            comparison_members=previous_members, comparison_facts=previous,
        )
        self.assertEqual(market.operating_income_total, Decimal("32"))
        self.assertEqual(market.reported_company_count, 1)
        self.assertEqual(market.completion_status, "provisional")

    def test_final_total_uses_only_current_basket(self):
        current_members = [member("a", 1), member("b", 2)]
        previous_members = [member("a", 1, year=2026, quarter=1), member("x", 2, year=2026, quarter=1)]
        current = {"a": fact(2026, 2, "12", company="a"), "b": fact(2026, 2, "30", company="b")}
        previous = {"a": fact(2026, 1, "10", company="a"), "x": fact(2026, 1, "999", company="x")}
        market = aggregate_market(
            "kr_largecap", 2026, 2, current_members, current, 2,
            comparison_members=previous_members, comparison_facts=previous,
        )
        self.assertEqual(market.operating_income_total, Decimal("42"))
        self.assertEqual(market.completion_status, "complete")

    def test_preferred_shares_are_excluded(self):
        self.assertFalse(_eligible_name("삼성전자우"))
        self.assertFalse(_eligible_name("현대차2우B"))
        self.assertTrue(_eligible_name("삼성전자"))


if __name__ == "__main__":
    unittest.main()
