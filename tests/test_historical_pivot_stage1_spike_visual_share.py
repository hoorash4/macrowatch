from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from historical_pivot_shared import ChartGeometry, PivotPoint
from historical_pivot_stage1 import BasePivotResult, PivotPolicy
from historical_pivot_stage2 import classify_special_structures


class SpikeVisualShareTests(unittest.TestCase):
    def test_upward_spike_requires_visual_share_of_visible_y_axis(self):
        d = date(2020, 1, 1)
        highs = (
            PivotPoint(d, 3.2, "high"),
            PivotPoint(d + timedelta(days=10), 9.2, "high"),
            PivotPoint(d + timedelta(days=20), 4.4, "high"),
        )
        lows = (
            PivotPoint(d + timedelta(days=5), 1.1, "low"),
            PivotPoint(d + timedelta(days=30), 0.0, "low"),
        )
        base = BasePivotResult(
            frequency="W",
            policy=PivotPolicy(5, 14),
            high_candidates=highs,
            low_candidates=lows,
            high_pivots=highs,
            low_pivots=lows,
        )
        # 6 / 36.656 ~= 16.4%, so this must not be protected as a spike.
        geometry = ChartGeometry(d, d + timedelta(days=100), -12.228, 24.428, 1200, 600)
        result = classify_special_structures(base, geometry, spike_angle_threshold_deg=179.0)
        self.assertEqual((), result.spike_peaks)

    def test_upward_spike_passes_when_visual_y_share_reaches_visual_share(self):
        d = date(2020, 1, 1)
        highs = (
            PivotPoint(d, 0.0, "high"),
            PivotPoint(d + timedelta(days=10), 3.0, "high"),
            PivotPoint(d + timedelta(days=20), 0.5, "high"),
        )
        lows = (
            PivotPoint(d + timedelta(days=5), -1.0, "low"),
            PivotPoint(d + timedelta(days=30), -2.0, "low"),
        )
        base = BasePivotResult(
            frequency="W",
            policy=PivotPolicy(5, 14),
            high_candidates=highs,
            low_candidates=lows,
            high_pivots=highs,
            low_pivots=lows,
        )
        # 3 / 10 = 30%.
        geometry = ChartGeometry(d, d + timedelta(days=100), -5.0, 5.0, 1200, 600)
        result = classify_special_structures(base, geometry, spike_angle_threshold_deg=179.0)
        self.assertEqual(1, len(result.spike_peaks))

    def test_downward_spike_uses_previous_low_to_peak_visual_share(self):
        d = date(2020, 1, 1)
        lows = (
            PivotPoint(d, 0.0, "low"),
            PivotPoint(d + timedelta(days=10), -3.0, "low"),
            PivotPoint(d + timedelta(days=20), -0.5, "low"),
        )
        highs = (
            PivotPoint(d + timedelta(days=5), 1.0, "high"),
            PivotPoint(d + timedelta(days=30), 2.0, "high"),
        )
        base = BasePivotResult(
            frequency="W",
            policy=PivotPolicy(5, 14),
            high_candidates=highs,
            low_candidates=lows,
            high_pivots=highs,
            low_pivots=lows,
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), -5.0, 5.0, 1200, 600)
        result = classify_special_structures(base, geometry, spike_angle_threshold_deg=179.0)
        self.assertEqual(1, len(result.spike_peaks))
        self.assertEqual("down", result.spike_peaks[0].direction)


if __name__ == "__main__":
    unittest.main()
