from datetime import date
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signals.paynet_backfill import _prepare, _required_months
from sources.paynet_loan_performance import (
    SERIES_DEFAULT,
    SERIES_DELINQUENCY,
    extract_equifax_levels,
)


def row(code: str, year: int, month: int, value: float) -> dict:
    return {
        "series_code": code,
        "observation_date": f"{year:04d}-{month:02d}-01",
        "value": value,
        "frequency": "M",
        "source": "Equifax-public:test",
    }


class EquifaxLoanPerformanceTests(unittest.TestCase):
    def test_extracts_split_buckets_and_default(self):
        text = """
        SBDI 31-90 Days ▲3bps (M/M) 1.72% (Level)
        SBDI 91-180 Days ▲0bps (M/M) 0.72% (Level)
        SBDFI ▼4bps (M/M) 3.20% (Level)
        """
        self.assertEqual(extract_equifax_levels(text), (1.72, 0.72, 3.20))

    def test_modern_table_header_order_is_supported(self):
        text = """
        SBDFI SBDI 91-180 Days SBDI 31-90 Days
        3.20% (Level) 0.72% (Level) 1.72% (Level)
        """
        self.assertEqual(extract_equifax_levels(text), (1.72, 0.72, 3.20))

    def test_interleaved_overview_table_is_supported(self):
        text = """
        Equifax Small Business Delinquency (SBDI) & Default Indices (SBDFI)
        Equifax Small Business Lending Index (SBLI)
        SBDI 31-90 Days SBLI SBLI 3MMA* SBDFI SBDI 91-180 Days
        92nd 1.6% 74th 6.2% 5bps 55bps 1bp 12bps 0bps 15bps
        Percentile 11.4% (Y/Y) 149.0 (Level) (M/M)
        3.40% (Level) (M/M) (Y/Y)
        1.82% (Level) (M/M) (Y/Y)
        0.70% (Level) (M/M)
        """
        self.assertEqual(extract_equifax_levels(text), (1.82, 0.70, 3.40))

    def test_missing_bucket_is_rejected(self):
        text = """
        SBDI 31-90 Days 1.72% (Level)
        SBDFI 3.20% (Level)
        """
        self.assertIsNone(extract_equifax_levels(text))

    def test_required_months_run_latest_to_ten_years_back(self):
        months = _required_months(date(2026, 7, 1))
        self.assertEqual(months[0], date(2026, 7, 1))
        self.assertEqual(months[-1], date(2016, 7, 1))
        self.assertEqual(len(months), 121)

    def test_prepare_accepts_complete_final_history(self):
        months = _required_months(date(2026, 7, 1))
        delinquency = [row(SERIES_DELINQUENCY, d.year, d.month, 2.0) for d in months]
        defaults = [row(SERIES_DEFAULT, d.year, d.month, 3.0) for d in months]
        latest, prepared = _prepare({SERIES_DELINQUENCY: delinquency, SERIES_DEFAULT: defaults})
        self.assertEqual(latest, date(2026, 7, 1))
        self.assertIn("2016-07-01", prepared[SERIES_DELINQUENCY])
        self.assertIn("2026-07-01", prepared[SERIES_DEFAULT])


if __name__ == "__main__":
    unittest.main()
