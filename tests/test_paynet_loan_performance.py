from datetime import date, datetime, timezone
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signals.paynet_backfill import _prepare, _required_months
from sources.paynet_loan_performance import (
    SERIES_DEFAULT,
    SERIES_DELINQUENCY,
    rows_from_highcharts_payload,
)


def ms(year: int, month: int) -> int:
    return int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp() * 1000)


def row(code: str, year: int, month: int, value: float) -> dict:
    return {
        "series_code": code,
        "observation_date": f"{year:04d}-{month:02d}-01",
        "value": value,
        "frequency": "M",
        "source": "PayNet-RIS:test",
    }


class PayNetLoanPerformanceTests(unittest.TestCase):
    def test_reads_direct_31_180_and_default_series_only(self):
        payload = [
            {"name": "SBDI 31-90 Days", "data": [[ms(2026, 7), 1.66]]},
            {"name": "SBDI 91-180 Days", "data": [[ms(2026, 7), 0.73]]},
            {"name": "SBDI 31-180 Days", "data": [[ms(2026, 7), 2.41]]},
            {"name": "SBDFI Annualized Default Index", "data": [[ms(2026, 7), 3.20]]},
        ]
        result = rows_from_highcharts_payload(payload, date(2026, 7, 1), date(2026, 7, 31))
        self.assertEqual(result[SERIES_DELINQUENCY][0]["value"], 2.41)
        self.assertEqual(result[SERIES_DEFAULT][0]["value"], 3.20)
        self.assertNotEqual(result[SERIES_DELINQUENCY][0]["value"], 1.66 + 0.73)

    def test_direct_rows_are_sorted_latest_first(self):
        payload = [
            {"name": "SBDI 31-180 Days", "data": [[ms(2026, 6), 2.30], [ms(2026, 7), 2.39]]},
            {"name": "SBDFI", "data": [[ms(2026, 6), 3.26], [ms(2026, 7), 3.20]]},
        ]
        result = rows_from_highcharts_payload(payload, date(2026, 6, 1), date(2026, 7, 31))
        self.assertEqual(
            [r["observation_date"] for r in result[SERIES_DELINQUENCY]],
            ["2026-07-01", "2026-06-01"],
        )

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
