from decimal import Decimal
import unittest

from earnings.financial_quality import validate_canonical_quarter


def row(year, quarter, operating=10, net=8):
    return {
        "fiscal_year": year,
        "fiscal_quarter": quarter,
        "market_year": year,
        "operating_income": str(operating),
        "net_income": str(net),
        "currency": "KRW",
    }


class EarningsFinancialQualityTests(unittest.TestCase):
    def test_rejects_only_an_all_zero_profit_pair(self):
        self.assertIn("all_zero_income_statement", validate_canonical_quarter(row(2026, 1, 0, 0)))
        self.assertEqual(validate_canonical_quarter(row(2026, 1, -2, -1)), [])
        self.assertEqual(validate_canonical_quarter(row(2026, 1, 20, 16)), [])


if __name__ == "__main__":
    unittest.main()
