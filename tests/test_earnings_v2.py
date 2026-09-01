from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from earnings_v2.models import FinancialFact
from earnings_v2.cli import completed_successfully
from earnings_v2.providers import KisClient, OpenDartClient
from earnings_v2.transform import (
    aggregate_market,
    calculate_financial_series,
    extract_company_fact,
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


class OpenDartTransportTests(unittest.TestCase):
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


class CliContractTests(unittest.TestCase):
    def test_only_ready_results_are_successful(self):
        self.assertTrue(completed_successfully({"status": "ready"}))
        self.assertTrue(completed_successfully([{"status": "ready"}, {"status": "ready"}]))
        self.assertFalse(completed_successfully({"status": "incomplete"}))
        self.assertFalse(completed_successfully([{"status": "ready"}, {"status": "incomplete"}]))
        self.assertFalse(completed_successfully([]))


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

    def test_market_average_uses_available_company_count_without_filling_missing_companies(self):
        market = aggregate_market("kr_largecap", 2026, 2, [fact(2026, 2, "10", company="a"), fact(2026, 2, "30", company="b")], 100, historical=True)
        self.assertEqual(market.average_operating_income, Decimal("20"))
        self.assertEqual(market.actual_company_count, 2)
        self.assertEqual(market.completion_status, "historical_partial")


if __name__ == "__main__":
    unittest.main()
