from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from historical_pivot_base import (  # noqa: E402
    ChartGeometry,
    PivotPoint,
    SimplifiedLineResult,
    SimplifiedLineSegment,
    SidewaysSegment,
    prune_same_trend_extremes,
)


class PivotStageBoundaryTests(unittest.TestCase):
    def test_first_angle_always_collapses_improved_extreme(self):
        d = date(2020, 1, 1)
        high = PivotPoint(d, 10.0, "high")
        low1 = PivotPoint(d + timedelta(days=10), 4.0, "low")
        low2 = PivotPoint(d + timedelta(days=20), 3.0, "low")
        existing = SimplifiedLineResult(
            markers=(high, low1, low2),
            segments=(
                SimplifiedLineSegment(high, low1, "trend"),
                SimplifiedLineSegment(low1, low2, "trend"),
            ),
            sideways_segments=(),
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), 0.0, 20.0, 100.0, 100.0)

        result = prune_same_trend_extremes(existing, geometry)

        self.assertEqual((high, low2), result.markers)
        self.assertEqual(
            (SimplifiedLineSegment(high, low2, "trend"),),
            result.segments,
        )

    def test_confirmed_reversal_becomes_new_anchor_and_first_angle_collapses(self):
        d = date(2020, 1, 1)
        start_high = PivotPoint(d, 10.0, "high")
        reversal_low = PivotPoint(d + timedelta(days=10), 5.0, "low")
        high2 = PivotPoint(d + timedelta(days=20), 8.0, "high")
        higher_low = PivotPoint(d + timedelta(days=30), 6.0, "low")
        high4 = PivotPoint(d + timedelta(days=40), 9.0, "high")
        existing = SimplifiedLineResult(
            markers=(start_high, reversal_low, high2, higher_low, high4),
            segments=(
                SimplifiedLineSegment(start_high, reversal_low, "trend"),
                SimplifiedLineSegment(reversal_low, high2, "trend"),
                SimplifiedLineSegment(high2, higher_low, "trend"),
                SimplifiedLineSegment(higher_low, high4, "trend"),
            ),
            sideways_segments=(),
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), 0.0, 20.0, 100.0, 100.0)

        result = prune_same_trend_extremes(existing, geometry)

        self.assertIn(reversal_low, result.markers)
        self.assertIn(high4, result.markers)
        self.assertNotIn(high2, result.markers)
        self.assertNotIn(higher_low, result.markers)
        self.assertIn(
            SimplifiedLineSegment(reversal_low, high4, "trend"),
            result.segments,
        )

    def test_failed_reversal_candidate_never_becomes_anchor(self):
        d = date(2020, 1, 1)
        start_high = PivotPoint(d, 10.0, "high")
        candidate_low = PivotPoint(d + timedelta(days=10), 5.0, "low")
        rebound_high = PivotPoint(d + timedelta(days=20), 8.0, "high")
        lower_low = PivotPoint(d + timedelta(days=30), 4.0, "low")
        later_high = PivotPoint(d + timedelta(days=40), 9.0, "high")
        existing = SimplifiedLineResult(
            markers=(start_high, candidate_low, rebound_high, lower_low, later_high),
            segments=(
                SimplifiedLineSegment(start_high, candidate_low, "trend"),
                SimplifiedLineSegment(candidate_low, rebound_high, "trend"),
                SimplifiedLineSegment(rebound_high, lower_low, "trend"),
                SimplifiedLineSegment(lower_low, later_high, "trend"),
            ),
            sideways_segments=(),
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), 0.0, 20.0, 100.0, 100.0)

        result = prune_same_trend_extremes(existing, geometry)

        self.assertNotIn(candidate_low, result.markers)
        self.assertIn(lower_low, result.markers)

    def test_reversal_confirmed_across_sideways_anchors_at_sideways_end(self):
        d = date(2020, 1, 1)
        low0 = PivotPoint(d, 1.0, "low")
        sideways_start = PivotPoint(d + timedelta(days=10), 10.0, "high")
        sideways_end = PivotPoint(d + timedelta(days=20), 10.1, "high")
        low3 = PivotPoint(d + timedelta(days=30), 7.0, "low")
        lower_high = PivotPoint(d + timedelta(days=40), 9.0, "high")
        low5 = PivotPoint(d + timedelta(days=50), 6.0, "low")
        sideways = SidewaysSegment(
            start=sideways_start,
            end=sideways_end,
            prior_trend="up",
            reference_side="high",
            angle_deg=1.0,
        )
        existing = SimplifiedLineResult(
            markers=(low0, sideways_start, sideways_end, low3, lower_high, low5),
            segments=(
                SimplifiedLineSegment(low0, sideways_start, "trend"),
                SimplifiedLineSegment(sideways_start, sideways_end, "sideways"),
                SimplifiedLineSegment(sideways_end, low3, "trend"),
                SimplifiedLineSegment(low3, lower_high, "trend"),
                SimplifiedLineSegment(lower_high, low5, "trend"),
            ),
            sideways_segments=(sideways,),
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), 0.0, 20.0, 100.0, 100.0)

        result = prune_same_trend_extremes(existing, geometry)

        self.assertIn(sideways_start, result.markers)
        self.assertIn(sideways_end, result.markers)
        self.assertNotIn(low3, result.markers)
        self.assertNotIn(lower_high, result.markers)
        self.assertIn(
            SimplifiedLineSegment(sideways_end, low5, "trend"),
            result.segments,
        )

    def test_post_pass_output_is_subset_of_previous_stage_markers(self):
        d = date(2020, 1, 1)
        low = PivotPoint(d, 0.0, "low")
        high1 = PivotPoint(d + timedelta(days=10), 10.0, "high")
        dip1 = PivotPoint(d + timedelta(days=15), 3.0, "low")
        high2 = PivotPoint(d + timedelta(days=20), 11.0, "high")
        dip2 = PivotPoint(d + timedelta(days=25), 4.0, "low")
        high3 = PivotPoint(d + timedelta(days=30), 12.0, "high")
        existing = SimplifiedLineResult(
            markers=(low, high1, dip1, high2, dip2, high3),
            segments=(
                SimplifiedLineSegment(low, high1, "trend"),
                SimplifiedLineSegment(high1, dip1, "trend"),
                SimplifiedLineSegment(dip1, high2, "trend"),
                SimplifiedLineSegment(high2, dip2, "trend"),
                SimplifiedLineSegment(dip2, high3, "trend"),
            ),
            sideways_segments=(),
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), 0.0, 100.0, 100.0, 100.0)

        result = prune_same_trend_extremes(existing, geometry)
        before = {(p.day, p.value, p.pivot_type) for p in existing.markers}
        after = {(p.day, p.value, p.pivot_type) for p in result.markers}

        self.assertTrue(after.issubset(before))


if __name__ == "__main__":
    unittest.main()
