import unittest

from earnings.calculate_market_earnings import _latest_market_quarter_rows


class CalculateMarketEarningsTests(unittest.TestCase):
    def test_selects_latest_period_end_when_two_fiscal_quarters_share_market_quarter(self):
        rows = [
            {
                "company_id": "company",
                "fiscal_year": 2021,
                "fiscal_quarter": 3,
                "market_year": 2021,
                "market_quarter": 4,
                "period_end": "2021-10-01",
                "operating_income": "100",
            },
            {
                "company_id": "company",
                "fiscal_year": 2021,
                "fiscal_quarter": 4,
                "market_year": 2021,
                "market_quarter": 4,
                "period_end": "2021-12-31",
                "operating_income": "200",
            },
        ]

        selected = _latest_market_quarter_rows(rows)

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["fiscal_quarter"], 4)
        self.assertEqual(selected[0]["operating_income"], "200")

    def test_keeps_different_companies_and_market_quarters(self):
        rows = [
            {
                "company_id": "a", "fiscal_year": 2025, "fiscal_quarter": 1,
                "market_year": 2025, "market_quarter": 1, "period_end": "2025-03-31",
            },
            {
                "company_id": "a", "fiscal_year": 2025, "fiscal_quarter": 2,
                "market_year": 2025, "market_quarter": 2, "period_end": "2025-06-30",
            },
            {
                "company_id": "b", "fiscal_year": 2025, "fiscal_quarter": 1,
                "market_year": 2025, "market_quarter": 1, "period_end": "2025-03-31",
            },
        ]

        self.assertEqual(len(_latest_market_quarter_rows(rows)), 3)


if __name__ == "__main__":
    unittest.main()
