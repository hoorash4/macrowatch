from datetime import date
import pathlib
import sys
import unittest
from unittest.mock import patch

# PayNet history is complete; runtime collection is incremental-only.
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signals import paynet_monthly  # noqa: E402
from sources.paynet_loan_performance import (
    SERIES_DEFAULT,
    SERIES_DELINQUENCY,
    extract_equifax_levels,
)


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

    def test_missing_bucket_is_rejected(self):
        text = """
        SBDI 31-90 Days 1.72% (Level)
        SBDFI 3.20% (Level)
        """
        self.assertIsNone(extract_equifax_levels(text))

    def test_automatic_collector_checks_only_recent_three_months(self):
        class Database:
            pass

        captured = {}

        def fetch(start, end):
            captured["start"] = start
            captured["end"] = end
            return {SERIES_DELINQUENCY: [], SERIES_DEFAULT: []}

        with (
            patch.object(paynet_monthly, "date", wraps=date) as mocked_date,
            patch.object(paynet_monthly, "fetch_paynet_rows", side_effect=fetch),
            patch.object(paynet_monthly, "SupabaseRest", return_value=Database()),
            patch.object(paynet_monthly, "_insert_missing", return_value=0),
        ):
            mocked_date.today.return_value = date(2026, 9, 12)
            paynet_monthly.collect_recent()

        self.assertEqual(captured["start"], date(2026, 6, 1))
        self.assertEqual(captured["end"], date(2026, 9, 12))
        self.assertFalse((ROOT / "backend/signals/paynet_backfill.py").exists())
        self.assertFalse((ROOT / "backend/sources/paynet_derived_history.py").exists())
        self.assertFalse((ROOT / ".github/workflows/business-credit-backfill-once.yml").exists())


if __name__ == "__main__":
    unittest.main()
