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

    def test_exact_visible_global_high_and_low_are_candidates(self):
        values = [45, 30, 20, 15, 10, 18, 35, 70, 100, 76, 55, 40]
        result = self.analyze(values)
        pivot_values = {pivot["value"] for pivot in result["pivots"]}
        self.assertIn(100.0, pivot_values)
        self.assertIn(10.0, pivot_values)

    def test_tiny_pullback_after_large_rise_is_not_structural(self):
        # The 2-3% chart-height wiggle beside the high must disappear.
        values = [0, 20, 40, 60, 80, 100, 98, 99, 97, 80, 60, 40, 20]
        result = self.analyze(values)
        structural_values = {pivot["value"] for pivot in result["pivots"] if pivot["grade"] in {"A", "B"}}
        self.assertNotIn(98.0, structural_values)
        self.assertNotIn(99.0, structural_values)
        self.assertNotIn(97.0, structural_values)
        # The exact high must survive.  In a coarse synthetic series it may be
        # classified as the boundary of a short reversal/consolidation box,
        # which is valid under the box rule, so require a structural A/B point.
        self.assertIn(100.0, structural_values)

    def test_small_bounce_inside_large_decline_is_not_structural(self):
        values = [100, 85, 70, 55, 40, 25, 10, 14, 11, 8, 5, 3, 2]
        result = self.analyze(values)
        a_values = {pivot["value"] for pivot in result["pivots"] if pivot["grade"] == "A"}
        self.assertNotIn(14.0, a_values)
        self.assertNotIn(11.0, a_values)

    def test_long_straight_sideways_is_detected_without_many_extrema(self):
        # Down -> long nearly straight box -> large rise.
        values = [60, 40, 20, 10, 10.2, 9.9, 10.1, 10.0, 10.2, 10.1, 9.9, 10.0, 10.1, 10.0,
                  10.2, 10.1, 10.0, 20, 40, 65, 90, 100]
        result = self.analyze(values)
        self.assertTrue(any(box["mode"] == "flat" for box in result["sideways_boundaries"]))
        self.assertTrue(any(pivot["type"] == "sideways_entry" for pivot in result["pivots"]))
        self.assertTrue(any(pivot["type"] == "sideways_exit" for pivot in result["pivots"]))

    def test_large_amplitude_directionless_box_is_detected(self):
        values = [20, 80, 18, 95, 22, 70, 19, 84, 21, 76, 20, 82]
        result = self.analyze(values)
        self.assertTrue(result["sideways_boundaries"])

    def test_hh_hl_continuation_is_merged(self):
        values = [10, 30, 20, 45, 32, 60, 48, 75, 65, 90]
        result = self.analyze(values)
        ordinary = [pivot for pivot in result["pivots"] if pivot["type"] == "major_reversal"]
        self.assertLessEqual(len(ordinary), 1)

    def test_spike_keeps_true_entry_exact_extreme_and_recovery(self):
        # Short-X, >50%-Y excursion with small wiggles near both sides.
        values = [20, 18, 16, 14, 15, 13, 12, 90, 14, 13, 12, 11, 10, 11, 12,
                  13, 15, 18, 22, 28, 36, 45, 55, 65, 75, 82, 88, 92, 95, 97]
        result = self.analyze(values)
        spikes = [pivot for pivot in result["pivots"] if pivot["type"].startswith("spike_")]
        types = {pivot["type"] for pivot in spikes}
        self.assertTrue({"spike_entry", "spike_extreme", "spike_retracement"}.issubset(types))
        self.assertTrue(any(pivot["value"] == 90.0 for pivot in spikes if pivot["type"] == "spike_extreme"))
        self.assertTrue(any(pivot["value"] <= 13.0 for pivot in spikes if pivot["type"] == "spike_entry"))

    def test_sub_half_axis_excursion_is_not_spike(self):
        values = [20, 18, 16, 14, 15, 13, 12, 45, 14, 13, 12, 11, 10, 20, 30, 50, 70, 90]
        result = self.analyze(values)
        self.assertFalse(any(pivot["type"] == "spike_extreme" for pivot in result["pivots"]))

    def test_reversal_reason_reports_axis_shares_not_big_small_raw_claims(self):
        values = [10, 30, 50, 80, 100, 85, 65, 45, 20, 30, 50, 75, 95]
        result = self.analyze(values)
        reasons = [pivot["reason"] for pivot in result["pivots"] if pivot["type"] == "major_reversal"]
        self.assertTrue(reasons)
        for reason in reasons:
            self.assertIn("고정 Y축", reason)
            self.assertNotIn("큰 상승", reason)
            self.assertNotIn("큰 하락", reason)

    def test_every_pivot_has_reason(self):
        result = self.analyze([10, 30, 20, 60, 15, 80, 25, 70, 20])
        self.assertTrue(all(str(pivot.get("reason") or "").strip() for pivot in result["pivots"]))


if __name__ == "__main__":
    unittest.main()
