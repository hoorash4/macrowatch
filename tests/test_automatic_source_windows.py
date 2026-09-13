from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from common import SupabaseRest  # noqa: E402
from signals import canonical_series  # noqa: E402
from signals.economic_chart_pipeline import _insert_missing  # noqa: E402


class AutomaticSourceWindowTests(unittest.TestCase):
    def test_confirmed_recent_row_is_refreshed_only_when_a_compared_value_changes(self):
        database = object.__new__(SupabaseRest)
        database.request = lambda *_args, **_kwargs: [
            {"month": "2026-09-01", "is_provisional": False, "value": 1.0},
        ]
        unchanged = database.automatic_rows(
            "sample", [{"month": "2026-09-01", "is_provisional": False, "value": 1.0}],
            key="month", provisional="is_provisional", compare_fields=("value",),
        )
        changed = database.automatic_rows(
            "sample", [{"month": "2026-09-01", "is_provisional": False, "value": 1.1}],
            key="month", provisional="is_provisional", compare_fields=("value",),
        )
        self.assertEqual(unchanged, [])
        self.assertEqual(changed[0]["value"], 1.1)

    def test_economic_chart_recent_window_corrects_changed_source_value(self):
        class Database:
            def __init__(self):
                self.written = []

            def request(self, *_args, **_kwargs):
                return [{"observation_date": "2026-09-10", "value": 1.0}]

            def upsert(self, _table, rows, **_kwargs):
                self.written.extend(rows)

        database = Database()
        count = _insert_missing(database, [{
            "series_code": "TEST", "observation_date": "2026-09-10", "value": 1.1,
            "frequency": "D", "source": "test",
        }], date(2026, 9, 1))
        self.assertEqual(count, 1)
        self.assertEqual(database.written[0]["value"], 1.1)

    def test_canonical_source_round_trips_observation_dates(self):
        class Database:
            def __init__(self):
                self.rows = []

            def upsert(self, _table, rows, **_kwargs):
                self.rows.extend(rows)

            def request(self, *_args, **kwargs):
                offset = int(kwargs["params"].get("offset", 0))
                return self.rows[offset:offset + 1000]

        database = Database()
        canonical_series.store(database, canonical_series.rows(
            "SERIES", {date(2026, 9, 3): 1.25}, frequency="D", source="test",
        ), owner="test")
        loaded = canonical_series.load(database, "SERIES")
        self.assertEqual(loaded[date(2026, 9, 3)], 1.25)

    def test_historical_cache_initialization_is_manual_only(self):
        workflows = {
            path.name: path.read_text(encoding="utf-8")
            for path in (ROOT / ".github" / "workflows").glob("*.yml")
        }
        for name in (
            "em-capital-capacity.yml",
            "equity-bond-attractiveness.yml",
            "equity-bond-relative-value.yml",
        ):
            workflow = workflows[name]
            self.assertIn("initialize_sources", workflow)
            self.assertIn("workflow_dispatch:", workflow)
            self.assertIn("inputs.initialize_sources", workflow)
            self.assertNotIn("schedule' && 'true'", workflow)

        inflation_backfill = workflows["inflation-rate-backfill.yml"]
        self.assertIn("workflow_dispatch:", inflation_backfill)
        self.assertNotIn("schedule:", inflation_backfill)
        self.assertIn("signals.inflation_rate_backfill", inflation_backfill)
        self.assertNotIn("signals.inflation_model_backfill", inflation_backfill)
        self.assertFalse((ROOT / ".github/workflows/inflation-model.yml").exists())


if __name__ == "__main__":
    unittest.main()
