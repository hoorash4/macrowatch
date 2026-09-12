from datetime import date
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signals.business_credit_backfill import _validate_rows
from sources.epiq_ch11_source import _candidate_priority, _consider_candidate


class BusinessCreditBackfillIntegrityTests(unittest.TestCase):
    def test_backfill_accepts_authoritative_existing_date_for_upsert(self):
        rows = [{
            "series_code": "US_SBDFI",
            "observation_date": "2026-03-01",
            "value": 3.31,
            "frequency": "M",
            "source": "Equifax:official",
        }]
        self.assertEqual(
            _validate_rows("US_SBDFI", rows, date(2016, 9, 1), date(2026, 9, 12)),
            rows,
        )

    def test_backfill_rejects_conflicting_duplicate_month(self):
        rows = [
            {
                "series_code": "US_SBDFI",
                "observation_date": "2026-03-01",
                "value": 3.31,
                "frequency": "M",
                "source": "Equifax:a",
            },
            {
                "series_code": "US_SBDFI",
                "observation_date": "2026-03-01",
                "value": 9.99,
                "frequency": "M",
                "source": "Equifax:b",
            },
        ]
        with self.assertRaises(RuntimeError):
            _validate_rows("US_SBDFI", rows, date(2016, 9, 1), date(2026, 9, 12))

    def test_epiq_prefers_next_month_dedicated_release_over_later_comparator(self):
        values = {}
        _consider_candidate(
            values,
            year=2026,
            month=7,
            count=676,
            url="later-comparator",
            publication=(2026, 9),
        )
        _consider_candidate(
            values,
            year=2026,
            month=7,
            count=666,
            url="dedicated-release",
            publication=(2026, 8),
        )
        self.assertEqual(values[(2026, 7)][0], 666)
        self.assertEqual(_candidate_priority((2026, 8), 2026, 7), (0, 0))

    def test_epiq_rejects_equal_authority_conflict(self):
        values = {}
        _consider_candidate(
            values,
            year=2026,
            month=7,
            count=666,
            url="release-a",
            publication=(2026, 8),
        )
        with self.assertRaises(RuntimeError):
            _consider_candidate(
                values,
                year=2026,
                month=7,
                count=667,
                url="release-b",
                publication=(2026, 8),
            )


if __name__ == "__main__":
    unittest.main()
