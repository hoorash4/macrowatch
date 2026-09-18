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

    def test_small_directional_down_up_down_is_not_sideways(self):
        values = [90, 82, 74, 66, 58, 50, 42, 48, 55, 63, 71, 78, 72, 64, 56, 49]
        result = self.analyze(values)
        self.assertEqual(len(result["sideways_boundaries"]), 0)
        self.assertTrue(any(p["type"] == "major_reversal" for p in result["pivots"]))

    def test_higher_high_higher_low_continuation_is_merged(self):
        values = [10, 20, 16, 30, 24, 40, 34, 50, 45, 60]
        result = self.analyze(values)
        ordinary = [p for p in result["pivots"] if p["type"] == "major_reversal"]
        self.assertLessEqual(len(ordinary), 1)

    def test_spike_uses_large_y_share_and_preserves_three_anchors(self):
        # Big round-trip inside a short X share; surrounding tail makes it
        # visually short relative to the fixed case width.
        values = [30, 28, 25, 20, 15, 10, 95, 12, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3,
                  4, 5, 7, 10, 14, 20, 28, 38, 50, 62, 70, 66, 60, 54, 48, 42, 36, 30, 25, 20]
        result = self.analyze(values)
        types = {p["type"] for p in result["pivots"]}
        self.assertIn("spike_entry", types)
        self.assertIn("spike_extreme", types)
        self.assertIn("spike_retracement", types)

    def test_sub_50_percent_excursion_is_not_called_spike(self):
        values = [50, 48, 46, 44, 42, 65, 45, 44, 43, 42, 41, 40, 39, 38, 37, 36]
        result = self.analyze(values)
        self.assertFalse(any(p["type"] == "spike_extreme" for p in result["pivots"]))

    def test_ordinary_reversal_reason_does_not_use_generic_amplitude_math(self):
        values = [80, 70, 60, 50, 40, 30, 20, 30, 45, 60, 75, 70, 60, 50]
        result = self.analyze(values)
        reasons = [p["reason"] for p in result["pivots"] if p["type"] == "major_reversal"]
        self.assertTrue(reasons)
        for reason in reasons:
            self.assertNotIn("Y축", reason)
            self.assertNotIn("%", reason)
            self.assertNotIn("돌출", reason)

    def test_every_emitted_pivot_has_reason(self):
        values = [20, 35, 55, 78, 60, 84, 68, 92, 70, 50, 30]
        result = self.analyze(values)
        for pivot in result["pivots"]:
            self.assertTrue(str(pivot.get("reason") or "").strip())


if __name__ == "__main__":
    unittest.main()
