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
        values = [0, 20, 40, 60, 80, 100, 98, 99, 97, 94, 90, 86, 82, 78, 74, 70, 66, 62, 58, 54, 50, 46, 42, 38, 34, 30, 26, 22, 18, 14, 10]
        result = self.analyze(values)
        structural_values = {pivot["value"] for pivot in result["pivots"] if pivot["grade"] in {"A", "B"}}
        self.assertNotIn(98.0, structural_values)
        self.assertNotIn(99.0, structural_values)
        self.assertNotIn(97.0, structural_values)
        self.assertIn(100.0, structural_values)

    def test_small_bounce_inside_large_decline_is_not_structural(self):
        values = [100, 85, 70, 55, 40, 25, 10, 14, 11, 8, 5, 3, 2]
        result = self.analyze(values)
        a_values = {pivot["value"] for pivot in result["pivots"] if pivot["grade"] == "A"}
        self.assertNotIn(14.0, a_values)
        self.assertNotIn(11.0, a_values)

    def test_long_straight_sideways_is_detected_without_many_extrema(self):
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

    def test_spike_is_relative_amplitude_outlier_not_fixed_half_axis(self):
        # Most swings are about 8-14% of the frozen Y axis.  The spike is about
        # 40%, so it should be caught even though it is below an arbitrary 50%.
        values = [20, 28, 22, 31, 24, 35, 27, 68, 29, 36, 28, 38, 30, 40, 32, 42, 34, 44, 36, 46]
        result = self.analyze(values)
        self.assertTrue(any(pivot["type"] == "spike_extreme" for pivot in result["pivots"]))

    def test_similar_49_and_50_percent_swings_are_not_spikes(self):
        # A 50% move is not exceptional if neighboring moves are essentially
        # the same size. Relative comparison must reject it as a spike.
        values = [0, 49, 0, 50, 1, 50, 2, 51, 3, 52]
        result = self.analyze(values)
        self.assertFalse(any(pivot["type"] == "spike_extreme" for pivot in result["pivots"]))

    def test_reversal_reason_reports_axis_shares_and_relative_validation(self):
        values = [10, 30, 50, 80, 100, 85, 65, 45, 20, 30, 50, 75, 95]
        result = self.analyze(values)
        reasons = [pivot["reason"] for pivot in result["pivots"] if pivot["type"] == "major_reversal"]
        self.assertTrue(reasons)
        for reason in reasons:
            self.assertIn("고정 Y축", reason)
            self.assertIn("전형적 스윙", reason)
            self.assertNotIn("큰 상승", reason)
            self.assertNotIn("큰 하락", reason)

    def test_every_pivot_has_reason(self):
        result = self.analyze([10, 30, 20, 60, 15, 80, 25, 70, 20])
        self.assertTrue(all(str(pivot.get("reason") or "").strip() for pivot in result["pivots"]))


if __name__ == "__main__":
    unittest.main()
