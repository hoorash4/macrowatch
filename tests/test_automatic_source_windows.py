from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from common import SupabaseRest  # noqa: E402
from signals import automatic_source_cache  # noqa: E402
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

    def test_private_source_cache_round_trips_period_and_observation_dates(self):
        class Database:
            def __init__(self):
                self.rows = []

            def upsert(self, _table, rows, **_kwargs):
                self.rows.extend(rows)

            def request(self, *_args, **kwargs):
                offset = int(kwargs["params"].get("offset", 0))
                return self.rows[offset:offset + 1000]

        database = Database()
        automatic_source_cache.store(database, "collector", {
            "SERIES": {date(2026, 9, 1): (date(2026, 9, 3), 1.25)},
        })
        loaded = automatic_source_cache.load(database, "collector")
        self.assertEqual(loaded["SERIES"][date(2026, 9, 1)], (date(2026, 9, 3), 1.25))
        automatic_source_cache.mark_initialized(database, "collector", date(2026, 9, 12))
        self.assertTrue(automatic_source_cache.is_initialized(
            automatic_source_cache.load(database, "collector")
        ))

    def test_historical_cache_initialization_is_manual_only(self):
        workflows = {
            path.name: path.read_text(encoding="utf-8")
            for path in (ROOT / ".github" / "workflows").glob("*.yml")
        }
        for name in (
            "em-capital-capacity.yml",
            "equity-bond-attractiveness.yml",
            "equity-bond-relative-value.yml",
            "inflation-model.yml",
        ):
            workflow = workflows[name]
            self.assertIn("initialize_sources", workflow)
            self.assertIn("workflow_dispatch:", workflow)
            self.assertIn("inputs.initialize_sources", workflow)
            self.assertNotIn("schedule' && 'true'", workflow)


if __name__ == "__main__":
    unittest.main()
