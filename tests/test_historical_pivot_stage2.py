from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from historical_pivot_shared import ChartGeometry, PivotPoint
from historical_pivot_stage1 import BasePivotResult, PivotPolicy
from historical_pivot_stage2 import _entry_before_peak, finalize_sideways_protection


class HistoricalPivotStage2Tests(unittest.TestCase):

    def test_rapid_up_entry_falls_back_to_a_when_no_lower_plateau_exists(self):
        d = date(2020, 1, 1)
        a = PivotPoint(d, 5.0, "low")
        b = PivotPoint(d + timedelta(days=20), 9.0, "high")
        plateau = PivotPoint(d + timedelta(days=10), 6.0, "low")

        entry = _entry_before_peak(
            a_point=a,
            peak=b,
            opposite_rdp=(plateau,),
            opposite_candidates=(plateau,),
            direction="up",
        )

        self.assertEqual(a, entry)

    def test_rapid_down_entry_falls_back_to_a_when_no_higher_plateau_exists(self):
        d = date(2020, 1, 1)
        a = PivotPoint(d, 5.0, "high")
        b = PivotPoint(d + timedelta(days=20), 1.0, "low")
        plateau = PivotPoint(d + timedelta(days=10), 4.0, "high")

        entry = _entry_before_peak(
            a_point=a,
            peak=b,
            opposite_rdp=(plateau,),
            opposite_candidates=(plateau,),
            direction="down",
        )

        self.assertEqual(a, entry)

    def test_rapid_entry_uses_only_plateau_that_improves_on_a(self):
        d = date(2020, 1, 1)
        up_a = PivotPoint(d, 5.0, "low")
        up_b = PivotPoint(d + timedelta(days=30), 10.0, "high")
        up_nonqualifying = PivotPoint(d + timedelta(days=5), 6.0, "low")
        up_qualifying = PivotPoint(d + timedelta(days=10), 4.0, "low")
        down_a = PivotPoint(d, 5.0, "high")
        down_b = PivotPoint(d + timedelta(days=30), 0.0, "low")
        down_nonqualifying = PivotPoint(d + timedelta(days=5), 4.0, "high")
        down_qualifying = PivotPoint(d + timedelta(days=10), 7.0, "high")

        self.assertEqual(
            up_qualifying,
            _entry_before_peak(
                a_point=up_a,
                peak=up_b,
                opposite_rdp=(up_qualifying,),
                opposite_candidates=(up_nonqualifying, up_qualifying),
                direction="up",
            ),
        )
        self.assertEqual(
            down_qualifying,
            _entry_before_peak(
                a_point=down_a,
                peak=down_b,
                opposite_rdp=(down_qualifying,),
                opposite_candidates=(down_nonqualifying, down_qualifying),
                direction="down",
            ),
        )

    def test_rejects_high_sideways_after_falling_highs(self):
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

    def test_sideways_same_direction_before_and_after_is_not_protected(self):
        d = date(2020, 1, 1)
        highs = (
            PivotPoint(d + timedelta(days=10), 2.0, "high"),
            PivotPoint(d + timedelta(days=30), 6.0, "high"),
            PivotPoint(d + timedelta(days=60), 6.1, "high"),
            PivotPoint(d + timedelta(days=90), 9.0, "high"),
        )
        lows = (
            PivotPoint(d + timedelta(days=15), 1.0, "low"),
            PivotPoint(d + timedelta(days=45), 2.0, "low"),
            PivotPoint(d + timedelta(days=75), 3.0, "low"),
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
        self.assertFalse(stage2.high_sideways_segments[0].protected)

    def test_sideways_direction_change_protects_both_boundaries(self):
        d = date(2020, 1, 1)
        highs = (
            PivotPoint(d + timedelta(days=10), 2.0, "high"),
            PivotPoint(d + timedelta(days=30), 6.0, "high"),
            PivotPoint(d + timedelta(days=60), 6.1, "high"),
            PivotPoint(d + timedelta(days=90), 3.0, "high"),
        )
        lows = (
            PivotPoint(d + timedelta(days=15), 1.0, "low"),
            PivotPoint(d + timedelta(days=45), 2.0, "low"),
            PivotPoint(d + timedelta(days=75), 1.5, "low"),
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
        self.assertTrue(stage2.high_sideways_segments[0].protected)

    def test_graph_start_sideways_is_protected(self):
        d = date(2020, 1, 1)
        highs = (
            PivotPoint(d + timedelta(days=10), 6.0, "high"),
            PivotPoint(d + timedelta(days=40), 6.1, "high"),
            PivotPoint(d + timedelta(days=80), 9.0, "high"),
        )
        lows = (
            PivotPoint(d + timedelta(days=20), 2.0, "low"),
            PivotPoint(d + timedelta(days=60), 3.0, "low"),
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
        self.assertTrue(stage2.high_sideways_segments[0].protected)


if __name__ == "__main__":
    unittest.main()
