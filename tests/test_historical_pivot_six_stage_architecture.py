from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from historical_pivot_shared import (
    ChartGeometry,
    PivotPoint,
    RapidMoveCandidate,
    SimplifiedLineResult,
    SimplifiedLineSegment,
    Stage3LineResult,
)
from historical_pivot_stage1 import BasePivotResult, PivotPolicy
from historical_pivot_stage2 import Stage2Result, classify_special_structures
from historical_pivot_stage3 import simplify_pivot_lines
from historical_pivot_stage4 import finalize_rapid_moves
from historical_pivot_stage5 import prune_same_trend_extremes
from historical_pivot_stage6 import prune_unconfirmed_retracements


class SixStageArchitectureTests(unittest.TestCase):
    def test_stage1_is_not_mutated_by_stage2(self):
        d = date(2020, 1, 1)
        highs = (
            PivotPoint(d, 1.0, "high"),
            PivotPoint(d + timedelta(days=20), 8.0, "high"),
            PivotPoint(d + timedelta(days=40), 2.0, "high"),
        )
        lows = (
            PivotPoint(d + timedelta(days=5), 0.0, "low"),
            PivotPoint(d + timedelta(days=15), -2.0, "low"),
            PivotPoint(d + timedelta(days=45), -1.0, "low"),
        )
        stage1 = BasePivotResult(
            frequency="W",
            policy=PivotPolicy(5, 14),
            high_candidates=highs,
            low_candidates=lows,
            high_pivots=highs,
            low_pivots=lows,
        )
        geometry = ChartGeometry(d, d + timedelta(days=60), -5.0, 10.0, 600, 300)
        stage2 = classify_special_structures(stage1, geometry)
        self.assertEqual(stage1.high_pivots, stage2.high_pivots)
        self.assertEqual(stage1.low_pivots, stage2.low_pivots)

    def test_stage2_does_not_duplicate_rapid_endpoints_as_provisional_state(self):
        self.assertNotIn(
            "provisional_protected_points",
            Stage2Result.__dataclass_fields__,
        )

    def test_stage3_drops_rapid_candidate_when_wave_merge_drops_endpoint(self):
        d = date(2020, 1, 1)
        entry = PivotPoint(d, 0.0, "low")
        provisional_peak = PivotPoint(d + timedelta(days=20), 8.0, "high")
        later_peak = PivotPoint(d + timedelta(days=30), 10.0, "high")
        later_low = PivotPoint(d + timedelta(days=31), 2.0, "low")
        candidate = RapidMoveCandidate(entry, provisional_peak, 1, 0.4)
        stage2 = Stage2Result(
            high_pivots=(provisional_peak, later_peak),
            low_pivots=(entry, later_low),
            rapid_move_candidates=(candidate,),
        )
        geometry = ChartGeometry(d, d + timedelta(days=60), -5.0, 15.0, 600, 300)
        stage3 = simplify_pivot_lines(stage2, geometry)
        self.assertIn(entry, stage3.markers)
        self.assertIn(later_peak, stage3.markers)
        self.assertNotIn(provisional_peak, stage3.markers)
        self.assertEqual((), stage3.rapid_move_candidates)

    def test_stage3_does_not_manufacture_detached_rapid_structure(self):
        d = date(2020, 1, 1)
        entry = PivotPoint(d, 0.0, "low")
        peak = PivotPoint(d + timedelta(days=20), 8.0, "high")
        candidate = RapidMoveCandidate(entry, peak, 1, 0.4)
        stage2 = Stage2Result(
            high_pivots=(peak,),
            low_pivots=(entry,),
            rapid_move_candidates=(candidate,),
        )
        geometry = ChartGeometry(d, d + timedelta(days=60), -5.0, 15.0, 600, 300)
        stage3 = simplify_pivot_lines(stage2, geometry)
        self.assertEqual((), stage3.rapid_move_candidates)

    def test_detached_marker_requires_explicit_marker_only_role(self):
        d = date(2020, 1, 1)
        start = PivotPoint(d, 0.0, "low")
        end = PivotPoint(d + timedelta(days=20), 8.0, "high")
        detached = PivotPoint(d + timedelta(days=10), 5.0, "high")
        with self.assertRaises(ValueError):
            SimplifiedLineResult(
                markers=(start, detached, end),
                segments=(SimplifiedLineSegment(start, end, "trend"),),
                sideways_segments=(),
            )
        result = SimplifiedLineResult(
            markers=(start, detached, end),
            segments=(SimplifiedLineSegment(start, end, "trend"),),
            sideways_segments=(),
            marker_only_points=(detached,),
        )
        self.assertEqual((detached,), result.marker_only_points)

    def test_stage4_output_contains_no_rapid_candidate_or_provisional_state(self):
        self.assertNotIn(
            "rapid_move_candidates",
            SimplifiedLineResult.__dataclass_fields__,
        )
        self.assertNotIn(
            "provisional_protected_points",
            SimplifiedLineResult.__dataclass_fields__,
        )

    def test_stage4_extends_same_direction_rapid_move_within_ten_degrees(self):
        d = date(2020, 1, 1)
        entry = PivotPoint(d, 0.0, "low")
        entry2 = PivotPoint(d + timedelta(days=8), 2.0, "low")
        peak1 = PivotPoint(d + timedelta(days=10), 6.0, "high")
        peak2 = PivotPoint(d + timedelta(days=20), 12.0, "high")
        c1 = RapidMoveCandidate(entry, peak1, 1, 0.3)
        c2 = RapidMoveCandidate(entry2, peak2, 1, 0.5)
        result = Stage3LineResult(
            markers=(entry, entry2, peak1, peak2),
            segments=(
                SimplifiedLineSegment(entry, entry2, "trend"),
                SimplifiedLineSegment(entry2, peak1, "trend"),
                SimplifiedLineSegment(peak1, peak2, "trend"),
            ),
            sideways_segments=(),
            rapid_move_candidates=(c1, c2),
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), -10.0, 20.0, 1000, 500)
        stage4 = finalize_rapid_moves(result, geometry, angle_threshold_deg=10.0)
        self.assertIn(entry, stage4.protected_points)
        self.assertIn(peak2, stage4.protected_points)
        self.assertNotIn(peak1, stage4.protected_points)

    def test_stage5_and_stage6_keep_final_rapid_endpoints(self):
        d = date(2020, 1, 1)
        entry = PivotPoint(d, 0.0, "low")
        mid = PivotPoint(d + timedelta(days=10), 5.0, "high")
        peak = PivotPoint(d + timedelta(days=20), 10.0, "high")
        result = SimplifiedLineResult(
            markers=(entry, mid, peak),
            segments=(
                SimplifiedLineSegment(entry, mid, "trend"),
                SimplifiedLineSegment(mid, peak, "trend"),
            ),
            sideways_segments=(),
            protected_points=(entry, peak),
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), -10.0, 20.0, 1000, 500)
        stage5 = prune_same_trend_extremes(result, geometry)
        stage6 = prune_unconfirmed_retracements(stage5)
        self.assertIn(entry, stage6.markers)
        self.assertIn(peak, stage6.markers)

    def test_stage4_treats_opposite_wave_as_retracement_when_peak_is_rebroken(self):
        d = date(2020, 1, 1)
        entry = PivotPoint(d, 0.0, "low")
        first_peak = PivotPoint(d + timedelta(days=10), 6.0, "high")
        pullback = PivotPoint(d + timedelta(days=20), 3.0, "low")
        later_peak = PivotPoint(d + timedelta(days=30), 18.0, "high")
        candidate = RapidMoveCandidate(entry, later_peak, 1, 0.5)
        result = Stage3LineResult(
            markers=(entry, first_peak, pullback, later_peak),
            segments=(
                SimplifiedLineSegment(entry, first_peak, "trend"),
                SimplifiedLineSegment(first_peak, pullback, "trend"),
                SimplifiedLineSegment(pullback, later_peak, "trend"),
            ),
            sideways_segments=(),
            rapid_move_candidates=(candidate,),
        )
        geometry = ChartGeometry(
            d, d + timedelta(days=100), -10.0, 20.0, 1000, 500
        )
        stage4 = finalize_rapid_moves(result, geometry)
        self.assertIn(entry, stage4.protected_points)
        self.assertIn(later_peak, stage4.protected_points)

    def test_stage4_confirms_prior_peak_when_first_recovery_fails(self):
        d = date(2020, 1, 1)
        entry1 = PivotPoint(d, 0.0, "low")
        peak1 = PivotPoint(d + timedelta(days=10), 8.0, "high")
        pullback1 = PivotPoint(d + timedelta(days=20), 3.0, "low")
        failed_recovery = PivotPoint(d + timedelta(days=30), 7.0, "high")
        entry2 = PivotPoint(d + timedelta(days=40), 2.0, "low")
        peak2 = PivotPoint(d + timedelta(days=50), 10.0, "high")
        c1 = RapidMoveCandidate(entry1, peak1, 1, 0.4)
        c2 = RapidMoveCandidate(entry2, peak2, 1, 0.4)
        result = Stage3LineResult(
            markers=(entry1, peak1, pullback1, failed_recovery, entry2, peak2),
            segments=(
                SimplifiedLineSegment(entry1, peak1, "trend"),
                SimplifiedLineSegment(peak1, pullback1, "trend"),
                SimplifiedLineSegment(pullback1, failed_recovery, "trend"),
                SimplifiedLineSegment(failed_recovery, entry2, "trend"),
                SimplifiedLineSegment(entry2, peak2, "trend"),
            ),
            sideways_segments=(),
            rapid_move_candidates=(c1, c2),
        )
        geometry = ChartGeometry(
            d, d + timedelta(days=100), -10.0, 20.0, 1000, 500
        )
        stage4 = finalize_rapid_moves(result, geometry)
        self.assertIn(entry1, stage4.protected_points)
        self.assertIn(peak1, stage4.protected_points)
        self.assertIn(entry2, stage4.protected_points)
        self.assertIn(peak2, stage4.protected_points)

    def test_stage4_expands_rapid_entry_backward_before_forward_scan(self):
        d = date(2020, 1, 1)
        earlier_entry = PivotPoint(d, 0.0, "low")
        provisional_entry = PivotPoint(d + timedelta(days=10), 5.0, "low")
        peak = PivotPoint(d + timedelta(days=20), 8.0, "high")
        candidate = RapidMoveCandidate(provisional_entry, peak, 1, 0.3)
        result = Stage3LineResult(
            markers=(earlier_entry, provisional_entry, peak),
            segments=(
                SimplifiedLineSegment(earlier_entry, provisional_entry, "trend"),
                SimplifiedLineSegment(provisional_entry, peak, "trend"),
            ),
            sideways_segments=(),
            rapid_move_candidates=(candidate,),
        )
        geometry = ChartGeometry(
            d, d + timedelta(days=100), -10.0, 30.0, 1000, 500
        )
        stage4 = finalize_rapid_moves(result, geometry, angle_threshold_deg=10.0)
        self.assertIn(earlier_entry, stage4.protected_points)
        self.assertIn(peak, stage4.protected_points)
        self.assertNotIn(provisional_entry, stage4.protected_points)

    def test_stage4_rechecks_forward_angle_after_backward_entry_changes(self):
        d = date(2020, 1, 1)
        earlier_entry = PivotPoint(d, 0.0, "low")
        provisional_entry = PivotPoint(d + timedelta(days=10), 5.0, "low")
        first_peak = PivotPoint(d + timedelta(days=20), 8.0, "high")
        second_entry = PivotPoint(d + timedelta(days=25), 6.0, "low")
        later_peak = PivotPoint(d + timedelta(days=50), 11.0, "high")
        first = RapidMoveCandidate(provisional_entry, first_peak, 1, 0.3)
        second = RapidMoveCandidate(second_entry, later_peak, 1, 0.3)
        result = Stage3LineResult(
            markers=(
                earlier_entry,
                provisional_entry,
                first_peak,
                second_entry,
                later_peak,
            ),
            segments=(
                SimplifiedLineSegment(earlier_entry, provisional_entry, "trend"),
                SimplifiedLineSegment(provisional_entry, first_peak, "trend"),
                SimplifiedLineSegment(first_peak, second_entry, "trend"),
                SimplifiedLineSegment(second_entry, later_peak, "trend"),
            ),
            sideways_segments=(),
            rapid_move_candidates=(first, second),
        )
        geometry = ChartGeometry(
            d, d + timedelta(days=100), -10.0, 30.0, 1000, 500
        )
        stage4 = finalize_rapid_moves(result, geometry, angle_threshold_deg=10.0)
        self.assertIn(earlier_entry, stage4.protected_points)
        self.assertIn(first_peak, stage4.protected_points)
        self.assertNotIn(later_peak, stage4.protected_points)


if __name__ == "__main__":
    unittest.main()
