from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from earnings.company_price_gap import (
    QuarterlyAdjustedPrice,
    QuarterlyOperatingIncome,
    calculate_company_price_gaps,
)
from earnings.market_breadth import MarketQuarter


class CompanyPriceGapTests(unittest.TestCase):
    def test_rebases_price_and_ttm_op_and_stores_gap_delta(self):
        ops = []
        prices = []
        for ordinal, (year, quarter, op, price) in enumerate([
            (2024, 1, 25, 100), (2024, 2, 25, 100),
            (2024, 3, 25, 100), (2024, 4, 25, 100),
            (2025, 1, 30, 120), (2025, 2, 30, 132),
        ]):
            period = MarketQuarter(year, quarter)
            ops.append(QuarterlyOperatingIncome("company", period, Decimal(op)))
            prices.append(QuarterlyAdjustedPrice(
                "company", period, date(year, quarter * 3, 28), Decimal(price)
            ))

        results = calculate_company_price_gaps(
            company_id="company", operating_income=ops, prices=prices
        )

        self.assertEqual(results[0].period, MarketQuarter(2024, 4))
        self.assertEqual(results[0].normalized_price, Decimal("100"))
        self.assertEqual(results[0].normalized_ttm_operating_income, Decimal("100"))
        self.assertEqual(results[0].gap_points, Decimal("0"))
        self.assertIsNone(results[0].gap_delta_points)
        # 2025Q1: rebased price 120 - rebased TTM OP 105 = +15 points.
        self.assertEqual(results[1].gap_points, Decimal("15.00"))
        self.assertEqual(
            results[2].gap_delta_points,
            results[2].gap_points - results[1].gap_points,
        )

    def test_nonpositive_ttm_keeps_a_finite_index_point_distance(self):
        periods = [MarketQuarter(2024, quarter) for quarter in range(1, 5)]
        ops = [QuarterlyOperatingIncome("company", period, Decimal("10")) for period in periods]
        ops.append(QuarterlyOperatingIncome("company", MarketQuarter(2025, 1), Decimal("-100")))
        prices = [QuarterlyAdjustedPrice(
            "company", period, date(period.year, period.quarter * 3, 28), Decimal("100")
        ) for period in periods + [MarketQuarter(2025, 1)]]
        results = calculate_company_price_gaps(
            company_id="company", operating_income=ops, prices=prices
        )
        self.assertEqual(results[-1].calculation_state, "nonpositive_ttm")
        self.assertEqual(results[-1].normalized_ttm_operating_income, Decimal("-175.00"))
        self.assertEqual(results[-1].gap_points, Decimal("275.00"))
        self.assertIsNotNone(results[-1].gap_delta_points)


class CompanyPriceGapIntegrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.migration = (root / "supabase/migrations/20260829_add_company_earnings_price_gaps.sql").read_text(encoding="utf-8")
        cls.analysis_migration = (root / "supabase/migrations/20260830_add_market_earnings_analysis.sql").read_text(encoding="utf-8")
        cls.collector = (root / "supabase/functions/earnings-company-prices/index.ts").read_text(encoding="utf-8")
        cls.kis = (root / "supabase/functions/_shared/kis-client.ts").read_text(encoding="utf-8")
        cls.workflow = (root / ".github/workflows/earnings-company-price-gaps.yml").read_text(encoding="utf-8")
        cls.growth_workflow = (root / ".github/workflows/earnings-growth-metrics.yml").read_text(encoding="utf-8")
        cls.deploy = (root / ".github/workflows/deploy-supabase.yml").read_text(encoding="utf-8")

    def test_storage_is_quarterly_and_compact(self):
        self.assertIn("earnings_company_quarterly_prices", self.migration)
        self.assertIn("earnings_company_price_gaps", self.migration)
        self.assertIn("adjusted_close", self.migration)
        self.assertNotIn("volume numeric", self.migration.lower())
        self.assertIn("rename column gap_pct to gap_points", self.analysis_migration)
        self.assertIn("rename column gap_delta_pp to gap_delta_points", self.analysis_migration)

    def test_kis_uses_official_adjusted_price_options(self):
        self.assertIn('FID_ORG_ADJ_PRC: "0"', self.kis)
        self.assertIn('GUBN: "2"', self.kis)
        self.assertIn('MODP: "1"', self.kis)
        self.assertIn("quarterRows", self.collector)

    def test_deploy_backfill_and_python_calculation_are_connected(self):
        self.assertIn("20260829_add_company_earnings_price_gaps.sql", self.deploy)
        self.assertIn("earnings-company-prices", self.workflow)
        self.assertIn("python -m earnings.calculate_company_price_gaps", self.workflow)
        self.assertIn("python -m earnings.calculate_company_price_gaps", self.growth_workflow)
        self.assertIn('default: "2016"', self.workflow)


if __name__ == "__main__":
    unittest.main()
