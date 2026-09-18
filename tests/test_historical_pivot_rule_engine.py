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

    def test_exact_visible_global_high_and_low_are_not_lost_as_candidates(self):
        values = [45, 30, 20, 15, 10, 18, 35, 70, 100, 76, 55, 40]
        result = self.analyze(values)
        # At least one of the exact extrema must survive structural validation,
        # and no persisted extreme may be shifted off its actual raw value.
        persisted = {p["value"] for p in result["pivots"]}
        self.assertTrue(100.0 in persisted or 10.0 in persisted)
        self.assertFalse(any(v in persisted for v in {99.0, 101.0, 9.0, 11.0}))

    def test_tiny_pullback_after_huge_axis_rise_is_pruned(self):
        values = [0, 20, 40, 60, 80, 100, 98, 99, 97, 94, 90, 80, 70, 60, 50, 40, 30, 20, 10]
        result = self.analyze(values)
        structural = {p["value"] for p in result["pivots"] if p["grade"] in {"A", "B"}}
        self.assertNotIn(98.0, structural)
        self.assertNotIn(99.0, structural)
        self.assertNotIn(97.0, structural)

    def test_small_bounce_inside_full_axis_decline_is_pruned(self):
        values = [100, 85, 70, 55, 40, 25, 10, 14, 11, 8, 5, 3, 2]
        result = self.analyze(values)
        structural = {p["value"] for p in result["pivots"] if p["grade"] in {"A", "B"}}
        self.assertNotIn(14.0, structural)
        self.assertNotIn(11.0, structural)

    def test_long_straight_sideways_is_detected(self):
        values = [60, 40, 20, 10, 10.2, 9.9, 10.1, 10.0, 10.2, 10.1, 9.9, 10.0, 10.1,
                  10.0, 10.2, 10.1, 10.0, 20, 40, 65, 90, 100]
        result = self.analyze(values)
        self.assertTrue(any(b["mode"] == "flat" for b in result["sideways_boundaries"]))
        self.assertTrue(any(p["type"] == "sideways_entry" for p in result["pivots"]))
        self.assertTrue(any(p["type"] == "sideways_exit" for p in result["pivots"]))

    def test_large_amplitude_directionless_box_is_allowed(self):
        values = [20, 80, 18, 95, 22, 70, 19, 84, 21, 76, 20, 82]
        result = self.analyze(values)
        self.assertTrue(result["sideways_boundaries"])

    def test_hh_hl_continuation_is_compressed(self):
        values = [10, 30, 20, 45, 32, 60, 48, 75, 65, 90]
        result = self.analyze(values)
        ordinary = [p for p in result["pivots"] if p["type"] == "major_reversal"]
        self.assertLessEqual(len(ordinary), 1)

    def test_spike_requires_at_least_half_of_fixed_y_axis_and_short_x(self):
        values = [30, 28, 25, 20, 15, 10, 95, 12, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3,
                  4, 5, 7, 10, 14, 20, 28, 38, 50, 62, 70, 66, 60, 54, 48, 42, 36, 30, 25, 20]
        result = self.analyze(values)
        types = {p["type"] for p in result["pivots"]}
        self.assertIn("spike_entry", types)
        self.assertIn("spike_extreme", types)
        self.assertIn("spike_retracement", types)

    def test_sub_half_axis_excursion_is_not_spike(self):
        values = [0, 10, 20, 30, 40, 75, 42, 41, 40, 39, 38, 37, 36, 35, 34]
        result = self.analyze(values)
        self.assertFalse(any(p["type"] == "spike_extreme" for p in result["pivots"]))

    def test_reason_uses_fixed_axis_shares_not_raw_percent_change(self):
        values = [10, 30, 50, 80, 100, 70, 40, 20, 35, 55, 80, 95]
        result = self.analyze(values)
        reasons = [p["reason"] for p in result["pivots"]]
        self.assertTrue(reasons)
        for reason in reasons:
            self.assertNotIn("수익률", reason)
            self.assertNotIn("배", reason)
        self.assertTrue(any("Y축" in r or "X축" in r for r in reasons))

    def test_every_pivot_has_reason(self):
        result = self.analyze([10, 30, 20, 60, 15, 80, 25, 70, 20])
        self.assertTrue(all(str(p.get("reason") or "").strip() for p in result["pivots"]))


if __name__ == "__main__":
    unittest.main()
