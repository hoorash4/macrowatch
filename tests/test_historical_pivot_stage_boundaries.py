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
    prune_same_trend_extremes,
)


class PivotStageBoundaryTests(unittest.TestCase):
    def test_first_angle_only_does_not_confirm_collapse(self):
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

        self.assertEqual(existing, result)

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
