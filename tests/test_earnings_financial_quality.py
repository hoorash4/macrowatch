from decimal import Decimal
import unittest

from earnings.financial_quality import validate_canonical_quarter


def row(year, quarter, revenue, operating=10, net=8):
    return {
        "fiscal_year": year,
        "fiscal_quarter": quarter,
        "market_year": year,
        "revenue": str(revenue),
        "operating_income": str(operating),
        "net_income": str(net),
        "currency": "KRW",
    }


class EarningsFinancialQualityTests(unittest.TestCase):
    def test_rejects_non_positive_and_impossible_revenue(self):
        self.assertIn("non_positive_revenue", validate_canonical_quarter(row(2026, 1, -1)))
        self.assertIn(
            "absolute_revenue_limit",
            validate_canonical_quarter(row(2026, 1, Decimal("1000000000000001"))),
        )

    def test_rejects_extreme_company_history_break(self):
        history = [row(year, 1, 100) for year in range(2020, 2026)]
        self.assertIn(
            "revenue_history_outlier",
            validate_canonical_quarter(row(2026, 1, 10000), history),
        )

    def test_rejects_joint_yoy_break_but_allows_ordinary_growth(self):
        history = [row(2025, 1, 100, 10, 8)]
        self.assertIn(
            "multi_metric_yoy_break",
            validate_canonical_quarter(row(2026, 1, 1100, 600, 500), history),
        )
        self.assertEqual(validate_canonical_quarter(row(2026, 1, 150, 20, 16), history), [])


if __name__ == "__main__":
    unittest.main()
