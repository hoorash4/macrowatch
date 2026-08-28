from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from earnings.sec_parser import canonical_sec_quarters  # noqa: E402


def company_facts(*, include_operating_income: bool = True):
    aliases = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": [100, 120, 140, 500],
        "NetIncomeLoss": [10, 12, 14, 50],
    }
    if include_operating_income:
        aliases["OperatingIncomeLoss"] = [20, 24, 28, 100]
    periods = [
        ("Q1", "2025-01-01", "2025-03-31", "10-Q", "0001-25-000001"),
        ("Q2", "2025-04-01", "2025-06-30", "10-Q", "0001-25-000002"),
        ("Q3", "2025-07-01", "2025-09-30", "10-Q/A", "0001-25-000003"),
        ("FY", "2025-01-01", "2025-12-31", "10-K", "0001-26-000004"),
    ]
    facts = {}
    for alias, values in aliases.items():
        facts[alias] = {"units": {"USD": [
            {
                "fy": 2025, "fp": fp, "start": start, "end": end,
                "form": form, "accn": accession, "filed": "2026-02-15" if fp == "FY" else end,
                "val": value,
            }
            for value, (fp, start, end, form, accession) in zip(values, periods)
        ]}}
    return {"facts": {"us-gaap": facts}}


class SecEarningsTests(unittest.TestCase):
    def test_complete_company_facts_produce_four_quarters_and_derive_q4(self):
        rows, gaps = canonical_sec_quarters(
            company_facts(), cik="0000000001", as_of_year=2025, years=1,
        )
        self.assertEqual(gaps, [])
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[2]["filing"]["filing_kind"], "amendment")
        self.assertEqual(rows[3]["quarter"]["revenue"], "140")
        self.assertEqual(rows[3]["quarter"]["operating_income"], "28")
        self.assertEqual(rows[3]["quarter"]["net_income"], "14")
        self.assertNotIn("eps", rows[0]["quarter"])

    def test_missing_core_metric_is_a_gap_not_a_null_canonical_row(self):
        rows, gaps = canonical_sec_quarters(
            company_facts(include_operating_income=False),
            cik="0000000001", as_of_year=2025, years=1,
        )
        self.assertEqual(rows, [])
        self.assertEqual(gaps, [(2025, 1), (2025, 2), (2025, 3), (2025, 4)])


if __name__ == "__main__":
    unittest.main()
