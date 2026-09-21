from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from historical_pivot_stage2 import Stage2Result  # noqa: E402
from historical_pivot_stage3 import simplify_pivot_lines as merge_stage3  # noqa: E402

from historical_pivot_base import (  # noqa: E402
    BasePivotResult,
    ChartGeometry,
    PivotPoint,
    PivotPolicy,
    SpikeAugmentedPivotResult,
    SimplifiedLineResult,
    SimplifiedLineSegment,
    SidewaysSegment,
    augment_spike_entry_points,
    prune_same_trend_extremes,
    prune_unconfirmed_retracements,
    simplify_pivot_lines,
)


class PivotStageBoundaryTests(unittest.TestCase):
    def test_stage1_output_is_sealed_and_contains_no_prior_candidates(self):
        d = date(2020, 1, 1)
        highs = (
            PivotPoint(d, 5.0, "high"),
            PivotPoint(d + timedelta(days=10), 10.0, "high"),
            PivotPoint(d + timedelta(days=20), 5.0, "high"),
            PivotPoint(d + timedelta(days=40), 6.0, "high"),
        )
        lows = (
            PivotPoint(d - timedelta(days=10), 0.0, "low"),
            PivotPoint(d + timedelta(days=30), 1.0, "low"),
            PivotPoint(d + timedelta(days=50), 0.5, "low"),
        )
        deleted_candidate = PivotPoint(d + timedelta(days=4), 2.0, "low")
        recovered_entry = PivotPoint(d + timedelta(days=6), 1.0, "low")
        base = BasePivotResult(
            frequency="D",
            policy=PivotPolicy(35, 14, 17),
            high_candidates=highs,
            low_candidates=(deleted_candidate, recovered_entry),
            high_pivots=highs,
            low_pivots=lows,
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), 0.0, 10.0, 100.0, 100.0)

        stage1 = augment_spike_entry_points(base, geometry)

        self.assertIsInstance(stage1, SpikeAugmentedPivotResult)
        self.assertFalse(hasattr(stage1, "base"))
        self.assertFalse(hasattr(stage1, "added_high_pivots"))
        self.assertFalse(hasattr(stage1, "added_low_pivots"))
        self.assertNotIn(deleted_candidate, stage1.display_markers)
        self.assertIn(recovered_entry, stage1.display_markers)

        stage2 = simplify_pivot_lines(stage1, geometry)
        stage1_keys = {
            (p.day, p.value, p.pivot_type)
            for p in stage1.display_markers
        }
        stage2_keys = {
            (p.day, p.value, p.pivot_type)
            for p in stage2.markers
        }
        self.assertTrue(stage2_keys.issubset(stage1_keys))

    def test_stage_result_rejects_hidden_deleted_segment_endpoint(self):
        d = date(2020, 1, 1)
        low = PivotPoint(d, 1.0, "low")
        deleted_high = PivotPoint(d + timedelta(days=10), 5.0, "high")

        with self.assertRaises(ValueError):
            SimplifiedLineResult(
                markers=(low,),
                segments=(SimplifiedLineSegment(low, deleted_high, "trend"),),
                sideways_segments=(),
            )

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

    def test_lower_low_cancels_provisional_up_reversal(self):
        d = date(2020, 1, 1)
        start_high = PivotPoint(d, 7.0, "high")
        provisional_low = PivotPoint(d + timedelta(days=10), 6.0, "low")
        rebound_high = PivotPoint(d + timedelta(days=20), 10.0, "high")
        lower_low = PivotPoint(d + timedelta(days=30), 3.0, "low")
        existing = SimplifiedLineResult(
            markers=(start_high, provisional_low, rebound_high, lower_low),
            segments=(
                SimplifiedLineSegment(start_high, provisional_low, "trend"),
                SimplifiedLineSegment(provisional_low, rebound_high, "trend"),
                SimplifiedLineSegment(rebound_high, lower_low, "trend"),
            ),
            sideways_segments=(),
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), 0.0, 12.0, 100.0, 100.0)

        result = prune_same_trend_extremes(existing, geometry)

        self.assertNotIn(provisional_low, result.markers)
        self.assertNotIn(rebound_high, result.markers)
        self.assertEqual((start_high, lower_low), result.markers)
        self.assertEqual(
            (SimplifiedLineSegment(start_high, lower_low, "trend"),),
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

    def test_stage3_normal_up_wave_keeps_only_start_low_and_end_high(self):
        d = date(2020, 1, 1)
        h1 = PivotPoint(d, 5.0, "high")
        l1 = PivotPoint(d + timedelta(days=1), 4.0, "low")
        l2 = PivotPoint(d + timedelta(days=9), 5.0, "low")
        h2 = PivotPoint(d + timedelta(days=10), 6.0, "high")
        stage2 = Stage2Result(
            high_pivots=(h1, h2),
            low_pivots=(l1, l2),
            spike_peaks=(),
            high_sideways_segments=(),
            low_sideways_segments=(),
        )
        geometry = ChartGeometry(d, d + timedelta(days=20), 0.0, 10.0, 100.0, 100.0)

        result = merge_stage3(stage2, geometry)

        self.assertEqual((l1, h2), result.markers)
        self.assertEqual(
            (SimplifiedLineSegment(l1, h2, "trend"),),
            result.segments,
        )

    def test_stage3_normal_down_wave_keeps_only_start_high_and_end_low(self):
        d = date(2020, 1, 1)
        h1 = PivotPoint(d, 8.0, "high")
        l1 = PivotPoint(d + timedelta(days=1), 6.0, "low")
        h2 = PivotPoint(d + timedelta(days=9), 7.0, "high")
        l2 = PivotPoint(d + timedelta(days=10), 5.0, "low")
        stage2 = Stage2Result(
            high_pivots=(h1, h2),
            low_pivots=(l1, l2),
            spike_peaks=(),
            high_sideways_segments=(),
            low_sideways_segments=(),
        )
        geometry = ChartGeometry(d, d + timedelta(days=20), 0.0, 10.0, 100.0, 100.0)

        result = merge_stage3(stage2, geometry)

        self.assertEqual((h1, l2), result.markers)
        self.assertEqual(
            (SimplifiedLineSegment(h1, l2, "trend"),),
            result.segments,
        )

    def test_stage3_provisional_up_wave_is_cancelled_by_lower_following_low(self):
        d = date(2020, 1, 1)
        h0 = PivotPoint(d, 10.0, "high")
        l1 = PivotPoint(d + timedelta(days=10), 5.0, "low")
        h1 = PivotPoint(d + timedelta(days=20), 8.0, "high")
        l2 = PivotPoint(d + timedelta(days=30), 4.0, "low")
        stage2 = Stage2Result(
            high_pivots=(h0, h1),
            low_pivots=(l1, l2),
            spike_peaks=(),
            high_sideways_segments=(),
            low_sideways_segments=(),
        )
        geometry = ChartGeometry(d, d + timedelta(days=40), 0.0, 12.0, 100.0, 100.0)

        result = merge_stage3(stage2, geometry)

        self.assertIn(h0, result.markers)
        self.assertIn(l2, result.markers)
        self.assertNotIn(h1, result.markers)
        self.assertIn(SimplifiedLineSegment(h0, l2, "trend"), result.segments)

    def test_stage3_provisional_down_wave_is_cancelled_by_higher_following_high(self):
        d = date(2020, 1, 1)
        l0 = PivotPoint(d, 1.0, "low")
        h1 = PivotPoint(d + timedelta(days=10), 6.0, "high")
        l1 = PivotPoint(d + timedelta(days=20), 3.0, "low")
        h2 = PivotPoint(d + timedelta(days=30), 7.0, "high")
        stage2 = Stage2Result(
            high_pivots=(h1, h2),
            low_pivots=(l0, l1),
            spike_peaks=(),
            high_sideways_segments=(),
            low_sideways_segments=(),
        )
        geometry = ChartGeometry(d, d + timedelta(days=40), 0.0, 8.0, 100.0, 100.0)

        result = merge_stage3(stage2, geometry)

        self.assertIn(l0, result.markers)
        self.assertIn(h2, result.markers)
        self.assertNotIn(l1, result.markers)
        self.assertIn(SimplifiedLineSegment(l0, h2, "trend"), result.segments)

    def test_stage3_ongoing_up_wave_updates_only_terminal_high(self):
        d = date(2020, 1, 1)
        h1 = PivotPoint(d, 5.0, "high")
        l1 = PivotPoint(d + timedelta(days=1), 4.0, "low")
        h2 = PivotPoint(d + timedelta(days=10), 6.0, "high")
        l2 = PivotPoint(d + timedelta(days=11), 5.0, "low")
        h3 = PivotPoint(d + timedelta(days=20), 7.0, "high")
        l3 = PivotPoint(d + timedelta(days=21), 6.0, "low")
        stage2 = Stage2Result(
            high_pivots=(h1, h2, h3),
            low_pivots=(l1, l2, l3),
            spike_peaks=(),
            high_sideways_segments=(),
            low_sideways_segments=(),
        )
        geometry = ChartGeometry(d, d + timedelta(days=30), 0.0, 10.0, 100.0, 100.0)

        result = merge_stage3(stage2, geometry)

        self.assertIn(l1, result.markers)
        self.assertIn(h3, result.markers)
        self.assertNotIn(h1, result.markers)
        self.assertNotIn(h2, result.markers)
        self.assertNotIn(l2, result.markers)
        self.assertNotIn(l3, result.markers)
        self.assertIn(SimplifiedLineSegment(l1, h3, "trend"), result.segments)

    def test_stage5_requires_at_least_three_line_points(self):
        d = date(2020, 1, 1)
        p1 = PivotPoint(d, 10.0, "high")
        p2 = PivotPoint(d + timedelta(days=10), 7.0, "low")
        existing = SimplifiedLineResult(
            markers=(p1, p2),
            segments=(SimplifiedLineSegment(p1, p2, "trend"),),
            sideways_segments=(),
        )

        result = prune_unconfirmed_retracements(existing)

        self.assertEqual(existing, result)

    def test_stage5_collapses_three_consecutive_points_moving_down(self):
        d = date(2020, 1, 1)
        p1 = PivotPoint(d, 10.0, "high")
        p2 = PivotPoint(d + timedelta(days=10), 7.0, "low")
        p3 = PivotPoint(d + timedelta(days=20), 6.0, "low")
        existing = SimplifiedLineResult(
            markers=(p1, p2, p3),
            segments=(
                SimplifiedLineSegment(p1, p2, "trend"),
                SimplifiedLineSegment(p2, p3, "trend"),
            ),
            sideways_segments=(),
        )

        result = prune_unconfirmed_retracements(existing)

        self.assertEqual((p1, p3), result.markers)
        self.assertEqual(
            (SimplifiedLineSegment(p1, p3, "trend"),),
            result.segments,
        )

    def test_stage5_collapses_three_consecutive_points_moving_up(self):
        d = date(2020, 1, 1)
        p1 = PivotPoint(d, 1.0, "low")
        p2 = PivotPoint(d + timedelta(days=10), 5.0, "high")
        p3 = PivotPoint(d + timedelta(days=20), 6.0, "high")
        existing = SimplifiedLineResult(
            markers=(p1, p2, p3),
            segments=(
                SimplifiedLineSegment(p1, p2, "trend"),
                SimplifiedLineSegment(p2, p3, "trend"),
            ),
            sideways_segments=(),
        )

        result = prune_unconfirmed_retracements(existing)

        self.assertEqual((p1, p3), result.markers)

    def test_stage5_keeps_true_direction_change(self):
        d = date(2020, 1, 1)
        p1 = PivotPoint(d, 10.0, "high")
        p2 = PivotPoint(d + timedelta(days=10), 7.0, "low")
        p3 = PivotPoint(d + timedelta(days=20), 8.0, "high")
        existing = SimplifiedLineResult(
            markers=(p1, p2, p3),
            segments=(
                SimplifiedLineSegment(p1, p2, "trend"),
                SimplifiedLineSegment(p2, p3, "trend"),
            ),
            sideways_segments=(),
        )

        result = prune_unconfirmed_retracements(existing)

        self.assertEqual(existing, result)

    def test_stage5_collapses_long_monotonic_run_to_endpoints(self):
        d = date(2020, 1, 1)
        p1 = PivotPoint(d, 1.0, "low")
        p2 = PivotPoint(d + timedelta(days=10), 3.0, "high")
        p3 = PivotPoint(d + timedelta(days=20), 5.0, "low")
        p4 = PivotPoint(d + timedelta(days=30), 7.0, "high")
        existing = SimplifiedLineResult(
            markers=(p1, p2, p3, p4),
            segments=(
                SimplifiedLineSegment(p1, p2, "trend"),
                SimplifiedLineSegment(p2, p3, "trend"),
                SimplifiedLineSegment(p3, p4, "trend"),
            ),
            sideways_segments=(),
        )

        result = prune_unconfirmed_retracements(existing)

        self.assertEqual((p1, p4), result.markers)
        self.assertEqual(
            (SimplifiedLineSegment(p1, p4, "trend"),),
            result.segments,
        )

    def test_stage5_does_not_cross_protected_sideways_point(self):
        d = date(2020, 1, 1)
        p1 = PivotPoint(d, 1.0, "low")
        p2 = PivotPoint(d + timedelta(days=10), 3.0, "high")
        p3 = PivotPoint(d + timedelta(days=20), 5.0, "high")
        sideways = SidewaysSegment(
            start=p2,
            end=p3,
            prior_trend="up",
            reference_side="high",
            angle_deg=1.0,
        )
        existing = SimplifiedLineResult(
            markers=(p1, p2, p3),
            segments=(
                SimplifiedLineSegment(p1, p2, "trend"),
                SimplifiedLineSegment(p2, p3, "sideways"),
            ),
            sideways_segments=(sideways,),
        )

        result = prune_unconfirmed_retracements(existing)

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

    def test_failed_down_reversal_is_decided_then_next_candidate_is_judged(self):
        d = date(2020, 1, 1)
        start_low = PivotPoint(d, 0.0, "low")
        trend_high = PivotPoint(d + timedelta(days=10), 10.0, "high")
        candidate_low = PivotPoint(d + timedelta(days=20), -5.0, "low")
        rebound_high = PivotPoint(d + timedelta(days=30), 6.0, "high")
        higher_low = PivotPoint(d + timedelta(days=40), -1.0, "low")
        next_rebound_high = PivotPoint(d + timedelta(days=50), 8.0, "high")
        confirming_lower_low = PivotPoint(d + timedelta(days=60), -2.0, "low")
        existing = SimplifiedLineResult(
            markers=(
                start_low,
                trend_high,
                candidate_low,
                rebound_high,
                higher_low,
                next_rebound_high,
                confirming_lower_low,
            ),
            segments=(
                SimplifiedLineSegment(start_low, trend_high, "trend"),
                SimplifiedLineSegment(trend_high, candidate_low, "trend"),
                SimplifiedLineSegment(candidate_low, rebound_high, "trend"),
                SimplifiedLineSegment(rebound_high, higher_low, "trend"),
                SimplifiedLineSegment(higher_low, next_rebound_high, "trend"),
                SimplifiedLineSegment(next_rebound_high, confirming_lower_low, "trend"),
            ),
            sideways_segments=(),
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), -10.0, 20.0, 100.0, 100.0)

        result = prune_same_trend_extremes(existing, geometry)

        # -5 -> 6 -> -1 fails as a down-reversal candidate at -1.
        # The new candidate starts at -1, so 8 -> -2 confirms the next
        # down reversal instead of waiting for a later/final point.
        self.assertIn(trend_high, result.markers)
        self.assertIn(confirming_lower_low, result.markers)

    def test_failed_up_reversal_is_decided_then_next_candidate_is_judged(self):
        d = date(2020, 1, 1)
        start_high = PivotPoint(d, 10.0, "high")
        trend_low = PivotPoint(d + timedelta(days=10), 0.0, "low")
        candidate_high = PivotPoint(d + timedelta(days=20), 15.0, "high")
        rebound_low = PivotPoint(d + timedelta(days=30), 4.0, "low")
        lower_high = PivotPoint(d + timedelta(days=40), 11.0, "high")
        next_rebound_low = PivotPoint(d + timedelta(days=50), 2.0, "low")
        confirming_higher_high = PivotPoint(d + timedelta(days=60), 12.0, "high")
        existing = SimplifiedLineResult(
            markers=(
                start_high,
                trend_low,
                candidate_high,
                rebound_low,
                lower_high,
                next_rebound_low,
                confirming_higher_high,
            ),
            segments=(
                SimplifiedLineSegment(start_high, trend_low, "trend"),
                SimplifiedLineSegment(trend_low, candidate_high, "trend"),
                SimplifiedLineSegment(candidate_high, rebound_low, "trend"),
                SimplifiedLineSegment(rebound_low, lower_high, "trend"),
                SimplifiedLineSegment(lower_high, next_rebound_low, "trend"),
                SimplifiedLineSegment(next_rebound_low, confirming_higher_high, "trend"),
            ),
            sideways_segments=(),
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), -10.0, 20.0, 100.0, 100.0)

        result = prune_same_trend_extremes(existing, geometry)

        self.assertIn(trend_low, result.markers)
        self.assertIn(confirming_higher_high, result.markers)

    def test_stage4_keeps_confirmed_turns_and_reanchors_chronologically(self):
        d = date(2020, 1, 1)
        p0 = PivotPoint(d, 3.6, "low")
        p1 = PivotPoint(d + timedelta(days=10), 9.1, "high")
        p2 = PivotPoint(d + timedelta(days=20), -9.7, "low")
        p3 = PivotPoint(d + timedelta(days=30), 4.6, "high")
        p4 = PivotPoint(d + timedelta(days=40), 0.7, "low")
        p5 = PivotPoint(d + timedelta(days=50), 19.4, "high")
        existing = SimplifiedLineResult(
            markers=(p0, p1, p2, p3, p4, p5),
            segments=(
                SimplifiedLineSegment(p0, p1, "trend"),
                SimplifiedLineSegment(p1, p2, "trend"),
                SimplifiedLineSegment(p2, p3, "trend"),
                SimplifiedLineSegment(p3, p4, "trend"),
                SimplifiedLineSegment(p4, p5, "trend"),
            ),
            sideways_segments=(),
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), -15.0, 25.0, 1200.0, 600.0)

        result = prune_same_trend_extremes(existing, geometry)

        self.assertEqual((p0, p1, p2, p5), result.markers)
        self.assertIn(SimplifiedLineSegment(p0, p1, "trend"), result.segments)
        self.assertIn(SimplifiedLineSegment(p1, p2, "trend"), result.segments)
        self.assertIn(SimplifiedLineSegment(p2, p5, "trend"), result.segments)
        self.assertNotIn(p3, result.markers)
        self.assertNotIn(p4, result.markers)


if __name__ == "__main__":
    unittest.main()
