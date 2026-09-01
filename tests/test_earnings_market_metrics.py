from decimal import Decimal
import unittest

from earnings.growth_metrics import QuarterlyFinancial
from earnings.market_breadth import MarketQuarter
from earnings.market_metrics import calculate_market_metric_history
from earnings.market_universe import QuarterlyUniverse
from datetime import date


def row(company, year, quarter, operating, net=None, scope="CFS"):
    return QuarterlyFinancial(
        company_id=company,
        fiscal_year=year,
        fiscal_quarter=quarter,
        currency="KRW",
        consolidation_scope=scope,
        canonical_version=1,
        values={
            "operating_income": Decimal(str(operating)),
            "net_income": Decimal(str(operating if net is None else net)),
        },
    )


def result_for(results, year, quarter, metric="operating_income"):
    return next(
        item for item in results
        if item.fiscal_year == year and item.fiscal_quarter == quarter
        and item.metric == metric
    )


def universes(**periods):
    result = {}
    for key, companies in periods.items():
        year, quarter = int(key[1:5]), int(key[-1])
        period = MarketQuarter(year, quarter)
        result[period] = QuarterlyUniverse(
            "KOSPI100", period, date(year, quarter * 3, 28), frozenset(companies)
        )
    return result


class MarketAggregateMetricTests(unittest.TestCase):
    def test_sums_signed_amounts_before_calculating_growth(self):
        rows = [
            row("a", 2024, 1, 100), row("a", 2025, 1, 130),
            row("b", 2024, 1, -20), row("b", 2025, 1, -10),
        ]
        target = result_for(calculate_market_metric_history(
            index_id="KOSPI100", currency="KRW",
            universes_by_period=universes(p2024q1=["a", "b"], p2025q1=["a", "b"]), financials=rows,
        ), 2025, 1)
        self.assertEqual(target.prior_total, Decimal("80"))
        self.assertEqual(target.current_total, Decimal("120"))
        self.assertEqual(target.yoy_pct, Decimal("50.0"))

    def test_nonpositive_aggregate_baseline_has_state_but_no_percentage(self):
        rows = [
            row("a", 2024, 1, -20), row("a", 2025, 1, 10),
            row("b", 2024, 1, 10), row("b", 2025, 1, 20),
        ]
        target = result_for(calculate_market_metric_history(
            index_id="KOSDAQ50", currency="KRW",
            universes_by_period=universes(p2024q1=["a", "b"], p2025q1=["a", "b"]), financials=rows,
        ), 2025, 1)
        self.assertEqual(target.yoy_state, "black_turn")
        self.assertIsNone(target.yoy_pct)

    def test_each_quarter_uses_its_own_market_cap_constituents(self):
        rows = [
            row("old", 2024, 1, 100), row("old", 2025, 1, 999),
            row("new", 2024, 1, 999), row("new", 2025, 1, 150),
        ]
        target = result_for(calculate_market_metric_history(
            index_id="KOSPI100", currency="KRW",
            universes_by_period=universes(p2024q1=["old"], p2025q1=["new"]), financials=rows,
        ), 2025, 1)
        self.assertEqual(target.prior_total, Decimal("100"))
        self.assertEqual(target.current_total, Decimal("150"))
        self.assertEqual(target.yoy_pct, Decimal("50.0"))
        self.assertEqual(target.comparable_company_count, 1)
        self.assertEqual(target.delta_comparable_company_count, 1)

    def test_missing_companies_use_the_reported_company_average(self):
        rows = [
            row("a", 2024, 1, 100), row("a", 2025, 1, 150),
            row("b", 2024, 1, 300), row("b", 2025, 1, 450),
        ]
        target = result_for(calculate_market_metric_history(
            index_id="KOSPI100", currency="KRW",
            universes_by_period=universes(
                p2024q1=["a", "b", "missing"],
                p2025q1=["a", "b", "missing"],
            ),
            financials=rows,
        ), 2025, 1)
        self.assertEqual(target.current_total, Decimal("600"))
        self.assertEqual(target.current_average, Decimal("300"))
        self.assertEqual(target.prior_average, Decimal("200"))
        self.assertEqual(target.yoy_pct, Decimal("50.0"))
        self.assertEqual(target.comparable_company_count, 2)
        self.assertTrue(target.is_provisional)

    def test_less_than_half_coverage_is_not_published_as_a_market_signal(self):
        rows = [
            row("a", 2024, 1, 100), row("a", 2025, 1, 1000),
            row("b", 2024, 1, 100), row("b", 2025, 1, 1000),
        ]
        target = result_for(calculate_market_metric_history(
            index_id="KOSPI100", currency="KRW",
            universes_by_period=universes(
                p2024q1=["a", "b", "missing-1", "missing-2", "missing-3"],
                p2025q1=["a", "b", "missing-1", "missing-2", "missing-3"],
            ), financials=rows,
        ), 2025, 1)
        self.assertEqual(target.yoy_state, "insufficient_coverage")
        self.assertIsNone(target.current_average)
        self.assertIsNone(target.yoy_pct)

    def test_low_prior_coverage_suppresses_yoy_but_keeps_current_average(self):
        rows = [
            row("a", 2024, 1, 100), row("a", 2025, 1, 150),
            row("b", 2025, 1, 150), row("c", 2025, 1, 150),
        ]
        target = result_for(calculate_market_metric_history(
            index_id="KOSPI100", currency="KRW",
            universes_by_period=universes(
                p2024q1=["a", "b", "c"], p2025q1=["a", "b", "c"],
            ), financials=rows,
        ), 2025, 1)
        self.assertEqual(target.yoy_state, "insufficient_coverage")
        self.assertEqual(target.current_average, Decimal("150"))
        self.assertIsNone(target.yoy_pct)

    def test_explicit_cfs_ofs_mix_is_excluded(self):
        rows = [
            row("mixed", 2024, 1, 100, scope="OFS"),
            row("mixed", 2025, 1, 120, scope="CFS"),
        ]
        results = calculate_market_metric_history(
            index_id="KOSPI100", currency="KRW",
            universes_by_period=universes(p2024q1=["mixed"], p2025q1=["mixed"]), financials=rows,
        )
        self.assertEqual(result_for(results, 2025, 1).current_total, Decimal("120"))

    def test_missing_snapshot_is_never_filled_with_current_members(self):
        rows = [row("current", 2016, 1, 100), row("current", 2026, 1, 200)]
        results = calculate_market_metric_history(
            index_id="KOSPI100", currency="KRW",
            universes_by_period=universes(p2026q1=["current"]), financials=rows,
        )
        self.assertEqual([(x.fiscal_year, x.fiscal_quarter) for x in results], [(2026, 1)] * 2)
        self.assertTrue(all(x.prior_total is None for x in results))
        self.assertTrue(all(x.yoy_state == "missing_prior_snapshot" for x in results))


if __name__ == "__main__":
    unittest.main()
