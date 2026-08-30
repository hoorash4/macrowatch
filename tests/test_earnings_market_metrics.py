from decimal import Decimal
import unittest

from earnings.growth_metrics import QuarterlyFinancial
from earnings.market_breadth import MarketQuarter
from earnings.market_metrics import calculate_market_metric_history
from earnings.market_universe import QuarterlyUniverse
from datetime import date


def row(company, year, quarter, revenue, operating=None, net=None, scope="CFS"):
    return QuarterlyFinancial(
        company_id=company,
        fiscal_year=year,
        fiscal_quarter=quarter,
        currency="KRW",
        consolidation_scope=scope,
        canonical_version=1,
        values={
            "revenue": Decimal(str(revenue)),
            "operating_income": Decimal(str(revenue if operating is None else operating)),
            "net_income": Decimal(str(revenue if net is None else net)),
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
            row("a", 2024, 1, 100, 100), row("a", 2025, 1, 100, 130),
            row("b", 2024, 1, 100, -20), row("b", 2025, 1, 100, -10),
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
            row("a", 2024, 1, 100, -20), row("a", 2025, 1, 100, 10),
            row("b", 2024, 1, 100, 10), row("b", 2025, 1, 100, 20),
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
        ), 2025, 1, "revenue")
        self.assertEqual(target.prior_total, Decimal("100"))
        self.assertEqual(target.current_total, Decimal("150"))
        self.assertEqual(target.yoy_pct, Decimal("50.0"))
        self.assertEqual(target.comparable_company_count, 1)
        self.assertEqual(target.delta_comparable_company_count, 1)

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
        self.assertEqual([(x.fiscal_year, x.fiscal_quarter) for x in results], [(2026, 1)] * 3)
        self.assertTrue(all(x.prior_total is None for x in results))
        self.assertTrue(all(x.yoy_state == "missing_prior_snapshot" for x in results))


if __name__ == "__main__":
    unittest.main()
