from decimal import Decimal
import unittest

from earnings.calculate_growth_metrics import _compact_records
from earnings.growth_metrics import (
    QuarterlyFinancial,
    calculate_growth_metrics,
    financials_from_rows,
)


def quarter(year, fiscal_quarter, revenue, operating_income=None, net_income=None):
    return QuarterlyFinancial(
        company_id="company",
        fiscal_year=year,
        fiscal_quarter=fiscal_quarter,
        currency="KRW",
        consolidation_scope="CFS",
        canonical_version=1,
        values={
            "revenue": Decimal(str(revenue)),
            "operating_income": Decimal(str(
                revenue if operating_income is None else operating_income
            )),
            "net_income": Decimal(str(revenue if net_income is None else net_income)),
        },
    )


def metric_for(results, year, fiscal_quarter, metric="revenue"):
    return next(
        row for row in results
        if row.fiscal_year == year
        and row.fiscal_quarter == fiscal_quarter
        and row.metric == metric
    )


class EarningsGrowthMetricTests(unittest.TestCase):
    def test_calculates_yoy_and_yoy_delta_for_all_three_metrics(self):
        results = calculate_growth_metrics([
            quarter(2024, 1, 100, 50, 20),
            quarter(2024, 2, 100, 50, 20),
            quarter(2025, 1, 110, 60, 30),
            quarter(2025, 2, 120, 75, 40),
        ])

        revenue = metric_for(results, 2025, 2)
        operating = metric_for(results, 2025, 2, "operating_income")
        net = metric_for(results, 2025, 2, "net_income")
        self.assertEqual(revenue.yoy_pct, Decimal("20.0"))
        self.assertEqual(revenue.yoy_delta_pp, Decimal("10.0"))
        self.assertEqual(operating.yoy_pct, Decimal("50.0"))
        self.assertEqual(operating.yoy_delta_pp, Decimal("30.0"))
        self.assertEqual(net.yoy_pct, Decimal("100"))
        self.assertEqual(net.yoy_delta_pp, Decimal("50.0"))

    def test_qoq_baseline_uses_only_preceding_same_quarter_transitions(self):
        source = []
        for year in range(2020, 2026):
            source.extend([quarter(year, 1, 100), quarter(year, 2, 120)])
        source.extend([quarter(2026, 1, 100), quarter(2026, 2, 130)])

        target = metric_for(calculate_growth_metrics(source), 2026, 2)
        self.assertEqual(target.qoq_raw_pct, Decimal("30.0"))
        self.assertEqual(target.qoq_seasonal_sample_count, 5)
        self.assertEqual(target.qoq_seasonal_baseline_pct, Decimal("20.0"))
        self.assertEqual(target.qoq_seasonally_adjusted_pct, Decimal("10.0"))

    def test_turns_and_incompatible_comparisons_are_explicit_states(self):
        turns = calculate_growth_metrics([
            quarter(2024, 1, 100, -10, 0),
            quarter(2025, 1, 100, 10, 5),
        ])
        self.assertEqual(
            metric_for(turns, 2025, 1, "operating_income").yoy_state,
            "black_turn",
        )
        self.assertEqual(metric_for(turns, 2025, 1, "net_income").yoy_state, "from_zero")

        rows = financials_from_rows([
            {
                "company_id": "company", "fiscal_year": 2024, "fiscal_quarter": 1,
                "revenue": "100", "operating_income": "10", "net_income": "5",
                "currency": "USD", "consolidation_scope": "CFS", "canonical_version": 1,
            },
            {
                "company_id": "company", "fiscal_year": 2025, "fiscal_quarter": 1,
                "revenue": "120", "operating_income": "12", "net_income": "6",
                "currency": "KRW", "consolidation_scope": "CFS", "canonical_version": 1,
            },
        ])
        self.assertEqual(
            metric_for(calculate_growth_metrics(rows), 2025, 1).yoy_state,
            "currency_mismatch",
        )

    def test_loss_periods_and_turns_never_enter_seasonal_adjustment(self):
        source = [
            quarter(2020, 1, 100, -100, -100),
            quarter(2020, 2, 120, -50, -50),
            quarter(2021, 1, 100, -100, -100),
            quarter(2021, 2, 120, -50, -50),
            quarter(2022, 1, 100, -100, -100),
            quarter(2022, 2, 120, 50, 50),
        ]
        results = calculate_growth_metrics(source)
        target = metric_for(results, 2022, 2, "operating_income")
        self.assertEqual(target.qoq_state, "black_turn")
        self.assertEqual(target.qoq_seasonal_sample_count, 0)
        self.assertIsNone(target.qoq_seasonal_baseline_pct)
        self.assertIsNone(target.qoq_seasonally_adjusted_pct)

    def test_yoy_delta_requires_two_ordinary_positive_base_growth_rates(self):
        results = calculate_growth_metrics([
            quarter(2023, 1, 100, -20, -20),
            quarter(2023, 2, 100, -10, -10),
            quarter(2024, 1, 110, -10, -10),
            quarter(2024, 2, 120, 10, 10),
        ])
        target = metric_for(results, 2024, 2, "operating_income")
        self.assertEqual(target.yoy_state, "black_turn")
        self.assertIsNone(target.yoy_delta_pp)

    def test_persistence_pivots_three_metrics_into_one_quarter_row(self):
        metrics = calculate_growth_metrics([quarter(2025, 1, 100)])
        records = _compact_records(metrics, calculated_at="2026-08-29T00:00:00+00:00")
        self.assertEqual(len(records), 1)
        self.assertIn("revenue_yoy_pct", records[0])
        self.assertIn("operating_income_yoy_pct", records[0])
        self.assertIn("net_income_yoy_pct", records[0])
        self.assertNotIn("revenue", records[0])
        self.assertIn("revenue_qoq_raw_pct", records[0])
        self.assertIn("revenue_qoq_seasonal_baseline_pct", records[0])


if __name__ == "__main__":
    unittest.main()
