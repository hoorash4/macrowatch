from __future__ import annotations

import unittest
from datetime import date, timedelta

from historical_pivot_rule_backfill import normalize_points, structure


def rows(values, start="2020-01-01", step_days=14):
    base = date.fromisoformat(start)
    return [
        {"date": (base + timedelta(days=i * step_days)).isoformat(), "value": float(value)}
        for i, value in enumerate(values)
    ]


class HistoricalPivotRuleEngineTests(unittest.TestCase):
    def analyze(self, values):
        source = rows(values)
        points, _, _ = normalize_points(source, source[0]["date"], source[-1]["date"])
        return structure(points)

    def test_large_amplitude_directionless_range_can_be_sideways(self):
        values = [20, 80, 18, 95, 22, 70, 19, 84, 21, 76, 20, 82]
        result = self.analyze(values)
        self.assertGreaterEqual(len(result["sideways_boundaries"]), 1)
        self.assertGreaterEqual(sum(p["grade"] == "B" for p in result["pivots"]), 2)

    def test_directional_v_shape_is_not_boxed_as_one_range(self):
        values = [90, 82, 72, 61, 50, 42, 34, 45, 58, 70, 82, 74, 64, 54]
        result = self.analyze(values)
        self.assertEqual(len(result["sideways_boundaries"]), 0)
        self.assertTrue(any(p["grade"] == "A" for p in result["pivots"]))

    def test_short_sharp_excursion_is_kept_as_spike(self):
        values = [70, 68, 66, 64, 62, 25, 61, 59, 57, 55, 53, 51]
        result = self.analyze(values)
        self.assertTrue(any(p["type"] == "spike_reversal" and p["grade"] == "A" for p in result["pivots"]))

    def test_every_emitted_pivot_has_reason(self):
        values = [20, 35, 55, 78, 60, 84, 68, 92, 70, 50, 30]
        result = self.analyze(values)
        for pivot in result["pivots"]:
            self.assertTrue(str(pivot.get("reason") or "").strip())


if __name__ == "__main__":
    unittest.main()
