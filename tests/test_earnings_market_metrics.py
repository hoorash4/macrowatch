from decimal import Decimal
import unittest

from earnings.growth_metrics import QuarterlyFinancial
from earnings.market_metrics import calculate_market_metric_history


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


class MarketAggregateMetricTests(unittest.TestCase):
    def test_sums_signed_amounts_before_calculating_growth(self):
        rows = [
            row("a", 2024, 1, 100, 100), row("a", 2025, 1, 100, 130),
            row("b", 2024, 1, 100, -20), row("b", 2025, 1, 100, -10),
        ]
        target = result_for(calculate_market_metric_history(
            index_id="KOSPI100", currency="KRW",
            universe_company_ids=["a", "b"], financials=rows,
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
            universe_company_ids=["a", "b"], financials=rows,
        ), 2025, 1)
        self.assertEqual(target.yoy_state, "black_turn")
        self.assertIsNone(target.yoy_pct)

    def test_delta_uses_four_period_common_cohort(self):
        rows = [
            row("complete", 2024, 1, 100), row("complete", 2024, 2, 100),
            row("complete", 2025, 1, 110), row("complete", 2025, 2, 130),
            row("partial", 2024, 2, 100), row("partial", 2025, 2, 200),
        ]
        target = result_for(calculate_market_metric_history(
            index_id="KOSPI100", currency="KRW",
            universe_company_ids=["complete", "partial"], financials=rows,
        ), 2025, 2, "revenue")
        self.assertEqual(target.comparable_company_count, 2)
        self.assertEqual(target.delta_comparable_company_count, 1)
        self.assertEqual(target.yoy_delta_pp, Decimal("20.0"))

    def test_explicit_cfs_ofs_mix_is_excluded(self):
        rows = [
            row("mixed", 2024, 1, 100, scope="OFS"),
            row("mixed", 2025, 1, 120, scope="CFS"),
        ]
        results = calculate_market_metric_history(
            index_id="KOSPI100", currency="KRW",
            universe_company_ids=["mixed"], financials=rows,
        )
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
