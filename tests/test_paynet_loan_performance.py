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
    extract_direct_levels,
)


def row(code: str, year: int, month: int, value: float) -> dict:
    return {
        "series_code": code,
        "observation_date": f"{year:04d}-{month:02d}-01",
        "value": value,
        "frequency": "M",
        "source": "Equifax-public:test",
    }


class PayNetLoanPerformanceTests(unittest.TestCase):
    def test_reads_explicit_31_180_and_sbdfi(self):
        text = """
        PayNet Small Business Delinquency Index (SBDI) 31-180 Days Past Due was at 2.41%.
        Small Business Default Index (SBDFI) was at 3.20%.
        """
        self.assertEqual(extract_direct_levels(text), (2.41, 3.20))

    def test_split_buckets_are_never_used_as_31_180(self):
        text = """
        SBDI 31-90 Days 1.66% (Level)
        SBDI 91-180 Days 0.73% (Level)
        SBDFI 3.20% (Level)
        """
        self.assertEqual(extract_direct_levels(text), (None, 3.20))

    def test_direct_31_180_level_label_is_accepted(self):
        text = "SBDI 31-180 Days 2.41% (Level) SBDFI 3.20% (Level)"
        self.assertEqual(extract_direct_levels(text), (2.41, 3.20))

    def test_required_months_run_latest_to_ten_years_back(self):
        months = _required_months(date(2026, 7, 1))
        self.assertEqual(months[0], date(2026, 7, 1))
        self.assertEqual(months[-1], date(2016, 7, 1))
        self.assertEqual(len(months), 121)

    def test_prepare_rejects_incomplete_ten_year_history(self):
        months = _required_months(date(2026, 7, 1))
        delinquency = [row(SERIES_DELINQUENCY, d.year, d.month, 2.0) for d in months]
        defaults = [row(SERIES_DEFAULT, d.year, d.month, 3.0) for d in months if d != date(2024, 5, 1)]
        with self.assertRaises(RuntimeError):
            _prepare({SERIES_DELINQUENCY: delinquency, SERIES_DEFAULT: defaults})

    def test_prepare_rejects_wrong_latest_month(self):
        months = _required_months(date(2026, 8, 1))
        delinquency = [row(SERIES_DELINQUENCY, d.year, d.month, 2.0) for d in months]
        defaults = [row(SERIES_DEFAULT, d.year, d.month, 3.0) for d in months]
        with self.assertRaises(RuntimeError):
            _prepare({SERIES_DELINQUENCY: delinquency, SERIES_DEFAULT: defaults})

    def test_prepare_accepts_complete_direct_history(self):
        months = _required_months(date(2026, 7, 1))
        delinquency = [row(SERIES_DELINQUENCY, d.year, d.month, 2.0) for d in months]
        defaults = [row(SERIES_DEFAULT, d.year, d.month, 3.0) for d in months]
        latest, prepared = _prepare({SERIES_DELINQUENCY: delinquency, SERIES_DEFAULT: defaults})
        self.assertEqual(latest, date(2026, 7, 1))
        self.assertIn("2016-07-01", prepared[SERIES_DELINQUENCY])
        self.assertIn("2026-07-01", prepared[SERIES_DEFAULT])


if __name__ == "__main__":
    unittest.main()
