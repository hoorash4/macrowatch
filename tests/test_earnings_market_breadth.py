from decimal import Decimal
import unittest

from earnings.market_breadth import (
    MarketQuarter,
    OperatingIncomeObservation,
    calculate_market_earnings_breadth,
    calculate_market_earnings_history,
    observations_from_rows,
)


def observation(company, year, quarter, value):
    return OperatingIncomeObservation(
        company_id=company,
        period=MarketQuarter(year, quarter),
        operating_income=Decimal(str(value)) if value is not None else None,
    )


class MarketEarningsBreadthTests(unittest.TestCase):
    def test_calculates_growth_breadth_contributions_and_turns(self):
        companies = [f"c{number}" for number in range(1, 11)]
        rows = []
        prior_values = [100, 100, 100, 100, 100, 100, 100, 100, -10, 20]
        current_values = [110, 110, 110, 110, 110, 110, 90, 90, 10, -5]
        for company, prior, current in zip(companies, prior_values, current_values):
            rows.extend([
                observation(company, 2025, 2, prior),
                observation(company, 2026, 2, current),
                observation(company, 2025, 1, prior),
                observation(company, 2026, 1, current),
            ])

        result = calculate_market_earnings_breadth(
            index_id="KOSPI100", target=MarketQuarter(2026, 2),
            universe_company_ids=companies, observations=rows,
        )

        self.assertEqual(result.comparable_company_count, 10)
        self.assertFalse(result.is_provisional)
        self.assertEqual(result.positive_company_count, 7)
        self.assertEqual(result.negative_company_count, 3)
        self.assertEqual(result.earnings_breadth_pct, Decimal("70.0"))
        self.assertEqual(result.black_turn_count, 1)
        self.assertEqual(result.red_turn_count, 1)
        self.assertEqual(result.profit_turn_net, 0)
        self.assertEqual(result.positive_contribution_total, Decimal("80"))
        self.assertEqual(result.negative_contribution_total_abs, Decimal("45"))
        self.assertEqual(result.negative_offset_ratio_pct, Decimal("56.2500"))
        self.assertEqual(result.op_growth_pct, Decimal("35") / Decimal("810") * Decimal("100"))
        self.assertEqual(result.breadth_delta_pp, Decimal("0.0"))

    def test_growth_is_null_when_prior_market_op_is_non_positive(self):
        rows = [
            observation("a", 2025, 2, -20), observation("a", 2026, 2, 10),
            observation("b", 2025, 2, 10), observation("b", 2026, 2, 20),
        ]
        result = calculate_market_earnings_breadth(
            index_id="KOSDAQ50", target=MarketQuarter(2026, 2),
            universe_company_ids=["a", "b"], observations=rows,
        )
        self.assertIsNone(result.op_growth_pct)
        self.assertEqual(result.aggregate_turn, "black_turn")
        self.assertEqual(result.net_op_change, Decimal("40"))

    def test_breadth_delta_compares_each_periods_point_in_time_breadth(self):
        rows = [
            observation("complete", 2025, 1, 10), observation("complete", 2025, 2, 10),
            observation("complete", 2026, 1, 20), observation("complete", 2026, 2, 5),
            observation("missing_previous", 2025, 1, 10),
            observation("missing_previous", 2025, 2, 10),
            observation("missing_previous", 2026, 2, 30),
        ]
        result = calculate_market_earnings_breadth(
            index_id="SP100", target=MarketQuarter(2026, 2),
            universe_company_ids=["complete", "missing_previous"], observations=rows,
        )
        # Both companies improve in the headline current-quarter breadth.
        self.assertEqual(result.earnings_breadth_pct, Decimal("50.0"))
        # Current-quarter breadth is 50%; the previous point-in-time universe's
        # available breadth was 100%, so the actual market breadth delta is -50%p.
        self.assertEqual(result.breadth_delta_comparable_count, 1)
        self.assertEqual(result.breadth_delta_pp, Decimal("-50"))

    def test_breadth_delta_coverage_uses_previous_quarters_own_yoy_pair(self):
        rows = [
            observation("current", 2025, 2, 10),
            observation("current", 2026, 2, 20),
            observation("previous", 2025, 1, 10),
            observation("previous", 2026, 1, 15),
        ]
        result = calculate_market_earnings_breadth(
            index_id="SP100",
            target=MarketQuarter(2026, 2),
            universe_company_ids=["current"],
            prior_universe_company_ids=["current"],
            previous_universe_company_ids=["previous"],
            observations=rows,
        )
        # The previous quarter's cohort need not exist in the target quarter's
        # YoY baseline. Coverage must use 2026Q1/2025Q1, not 2025Q2/2025Q1.
        self.assertEqual(result.breadth_delta_comparable_count, 1)
        self.assertEqual(result.breadth_delta_company_coverage_pct, Decimal("100"))
        self.assertEqual(result.breadth_delta_op_coverage_pct, Decimal("100"))

    def test_negative_offset_is_not_capped_at_one_hundred_percent(self):
        rows = [
            observation("up", 2025, 2, 100), observation("up", 2026, 2, 110),
            observation("down", 2025, 2, 100), observation("down", 2026, 2, 20),
        ]
        result = calculate_market_earnings_breadth(
            index_id="NASDAQ100", target=MarketQuarter(2026, 2),
            universe_company_ids=["up", "down"], observations=rows,
        )
        self.assertEqual(result.negative_offset_ratio_pct, Decimal("800"))
        self.assertEqual(result.classification, "mixed_deterioration")

    def test_missing_current_values_reduce_both_coverage_measures(self):
        rows = [
            observation("reported", 2025, 2, 80), observation("reported", 2026, 2, 100),
            observation("pending", 2025, 2, 20),
        ]
        result = calculate_market_earnings_breadth(
            index_id="KOSPI100", target=MarketQuarter(2026, 2),
            universe_company_ids=["reported", "pending"], observations=rows,
        )
        self.assertEqual(result.company_coverage_pct, Decimal("50.0"))
        self.assertEqual(result.op_coverage_pct, Decimal("80.0"))
        self.assertTrue(result.is_provisional)

    def test_rows_adapter_and_record_serializer_preserve_exact_numbers(self):
        rows = observations_from_rows([{
            "company_id": "company", "market_year": 2026,
            "market_quarter": 1, "operating_income": "123.4500",
        }])
        self.assertEqual(rows[0].operating_income, Decimal("123.4500"))
        result = calculate_market_earnings_breadth(
            index_id="SP100", target=MarketQuarter(2026, 1),
            universe_company_ids=["company"], observations=rows,
        )
        self.assertEqual(result.as_record()["company_coverage_pct"], "100")

    def test_history_reconstructs_only_quarters_with_a_yoy_baseline(self):
        rows = [
            observation("company", 2025, 1, 10),
            observation("company", 2025, 2, 20),
            observation("company", 2026, 1, 15),
            observation("company", 2026, 2, 30),
        ]
        results = calculate_market_earnings_history(
            index_id="KOSPI100",
            universes_by_period={
                MarketQuarter(2025, 1): ["company"],
                MarketQuarter(2025, 2): ["company"],
                MarketQuarter(2026, 1): ["company"],
                MarketQuarter(2026, 2): ["company"],
            },
            observations=rows,
        )
        self.assertEqual(
            [(result.market_year, result.market_quarter) for result in results],
            [(2026, 1), (2026, 2)],
        )
        self.assertTrue(all(
            result.universe_basis == "point_in_time_market_cap_snapshot"
            for result in results
        ))

    def test_aggregate_totals_use_different_point_in_time_universes(self):
        rows = [
            observation("old", 2025, 2, 100), observation("old", 2026, 2, 999),
            observation("new", 2025, 2, 999), observation("new", 2026, 2, 150),
        ]
        result = calculate_market_earnings_breadth(
            index_id="KOSPI100", target=MarketQuarter(2026, 2),
            universe_company_ids=["new"], prior_universe_company_ids=["old"],
            observations=rows,
        )
        self.assertEqual(result.current_total_op, Decimal("150"))
        self.assertEqual(result.prior_total_op, Decimal("100"))
        self.assertEqual(result.op_growth_pct, Decimal("50.0"))


if __name__ == "__main__":
    unittest.main()
