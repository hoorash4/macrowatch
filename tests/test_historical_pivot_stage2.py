from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from historical_pivot_shared import ChartGeometry, PivotPoint
from historical_pivot_stage1 import BasePivotResult, PivotPolicy
from historical_pivot_stage2 import finalize_sideways_protection


class HistoricalPivotStage2Tests(unittest.TestCase):
    def test_rejects_high_sideways_when_highs_arrive_from_downtrend(self):
        d = date(2020, 1, 1)
        highs = (
            PivotPoint(d + timedelta(days=10), 10.0, "high"),
            PivotPoint(d + timedelta(days=20), 8.0, "high"),
            PivotPoint(d + timedelta(days=80), 8.1, "high"),
        )
        lows = (
            PivotPoint(d + timedelta(days=15), 5.0, "low"),
            PivotPoint(d + timedelta(days=50), 4.0, "low"),
            PivotPoint(d + timedelta(days=90), 3.0, "low"),
        )
        stage1 = BasePivotResult(
            frequency="W",
            policy=PivotPolicy(5, 14),
            high_candidates=highs,
            low_candidates=lows,
            high_pivots=highs,
            low_pivots=lows,
        )
        geometry = ChartGeometry(
            d, d + timedelta(days=100), 0.0, 10.0, 100.0, 100.0
        )

        stage2 = finalize_sideways_protection(stage1, geometry)

        self.assertEqual((), stage2.high_sideways_segments)

    def test_accepts_low_sideways_after_falling_lows(self):
        d = date(2020, 1, 1)
        highs = (
            PivotPoint(d + timedelta(days=5), 10.0, "high"),
            PivotPoint(d + timedelta(days=45), 9.0, "high"),
            PivotPoint(d + timedelta(days=95), 8.0, "high"),
        )
        lows = (
            PivotPoint(d + timedelta(days=10), 6.0, "low"),
            PivotPoint(d + timedelta(days=30), 4.0, "low"),
            PivotPoint(d + timedelta(days=80), 4.1, "low"),
        )
        stage1 = BasePivotResult(
            frequency="W",
            policy=PivotPolicy(5, 14),
            high_candidates=highs,
            low_candidates=lows,
            high_pivots=highs,
            low_pivots=lows,
        )
        geometry = ChartGeometry(
            d, d + timedelta(days=100), 0.0, 10.0, 100.0, 100.0
        )

        stage2 = finalize_sideways_protection(stage1, geometry)

        self.assertEqual(1, len(stage2.low_sideways_segments))
        self.assertEqual(lows[1], stage2.low_sideways_segments[0].start)
        self.assertEqual(lows[2], stage2.low_sideways_segments[0].end)

    def test_continues_adjacent_sideways_after_valid_entry(self):
        d = date(2020, 1, 1)
        highs = (
            PivotPoint(d + timedelta(days=10), 2.0, "high"),
            PivotPoint(d + timedelta(days=30), 6.0, "high"),
            PivotPoint(d + timedelta(days=60), 6.1, "high"),
            PivotPoint(d + timedelta(days=90), 6.0, "high"),
        )
        lows = (
            PivotPoint(d + timedelta(days=20), 1.0, "low"),
            PivotPoint(d + timedelta(days=50), 2.0, "low"),
            PivotPoint(d + timedelta(days=80), 3.0, "low"),
        )
        stage1 = BasePivotResult(
            frequency="W",
            policy=PivotPolicy(5, 14),
            high_candidates=highs,
            low_candidates=lows,
            high_pivots=highs,
            low_pivots=lows,
        )
        geometry = ChartGeometry(
            d, d + timedelta(days=100), 0.0, 10.0, 100.0, 100.0
        )

        stage2 = finalize_sideways_protection(stage1, geometry)

        self.assertEqual(1, len(stage2.high_sideways_segments))
        self.assertEqual(highs[1], stage2.high_sideways_segments[0].start)
        self.assertEqual(highs[3], stage2.high_sideways_segments[0].end)


if __name__ == "__main__":
    unittest.main()
