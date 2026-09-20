from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

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

    def test_confirmed_line_turn_stops_prior_10_degree_run(self):
        d = date(2020, 1, 1)
        start_high = PivotPoint(d, 7.0, "high")
        first_low = PivotPoint(d + timedelta(days=10), 6.0, "low")
        confirmed_high = PivotPoint(d + timedelta(days=20), 10.0, "high")
        later_low = PivotPoint(d + timedelta(days=30), 3.0, "low")
        existing = SimplifiedLineResult(
            markers=(start_high, first_low, confirmed_high, later_low),
            segments=(
                SimplifiedLineSegment(start_high, first_low, "trend"),
                SimplifiedLineSegment(first_low, confirmed_high, "trend"),
                SimplifiedLineSegment(confirmed_high, later_low, "trend"),
            ),
            sideways_segments=(),
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), 0.0, 12.0, 100.0, 100.0)

        result = prune_same_trend_extremes(existing, geometry)

        self.assertIn(first_low, result.markers)
        self.assertIn(confirmed_high, result.markers)
        self.assertIn(
            SimplifiedLineSegment(first_low, confirmed_high, "trend"),
            result.segments,
        )
        self.assertIn(
            SimplifiedLineSegment(confirmed_high, later_low, "trend"),
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

    def test_final_cleanup_does_not_delete_with_only_two_same_side_points(self):
        d = date(2020, 1, 1)
        high = PivotPoint(d, 10.0, "high")
        low1 = PivotPoint(d + timedelta(days=10), 7.0, "low")
        low2 = PivotPoint(d + timedelta(days=20), 6.0, "low")
        existing = SimplifiedLineResult(
            markers=(high, low1, low2),
            segments=(
                SimplifiedLineSegment(high, low1, "trend"),
                SimplifiedLineSegment(low1, low2, "trend"),
            ),
            sideways_segments=(),
        )

        result = prune_unconfirmed_retracements(existing)

        self.assertEqual(existing, result)

    def test_final_cleanup_does_not_delete_opposite_retracement_marker(self):
        d = date(2020, 1, 1)
        high0 = PivotPoint(d, 10.0, "high")
        low1 = PivotPoint(d + timedelta(days=10), 7.0, "low")
        rebound = PivotPoint(d + timedelta(days=20), 8.0, "high")
        low2 = PivotPoint(d + timedelta(days=30), 6.0, "low")
        low3 = PivotPoint(d + timedelta(days=40), 5.0, "low")
        existing = SimplifiedLineResult(
            markers=(high0, low1, rebound, low2, low3),
            segments=(
                SimplifiedLineSegment(high0, low1, "trend"),
                SimplifiedLineSegment(low1, rebound, "trend"),
                SimplifiedLineSegment(rebound, low2, "trend"),
                SimplifiedLineSegment(low2, low3, "trend"),
            ),
            sideways_segments=(),
        )

        result = prune_unconfirmed_retracements(existing)

        self.assertEqual(existing, result)

    def test_final_cleanup_collapses_consecutive_higher_highs(self):
        d = date(2020, 1, 1)
        low = PivotPoint(d, 1.0, "low")
        high1 = PivotPoint(d + timedelta(days=10), 5.0, "high")
        high2 = PivotPoint(d + timedelta(days=20), 6.0, "high")
        high3 = PivotPoint(d + timedelta(days=30), 7.0, "high")
        existing = SimplifiedLineResult(
            markers=(low, high1, high2, high3),
            segments=(
                SimplifiedLineSegment(low, high1, "trend"),
                SimplifiedLineSegment(high1, high2, "trend"),
                SimplifiedLineSegment(high2, high3, "trend"),
            ),
            sideways_segments=(),
        )

        result = prune_unconfirmed_retracements(existing)

        self.assertEqual((low, high1, high3), result.markers)
        self.assertEqual(
            (
                SimplifiedLineSegment(low, high1, "trend"),
                SimplifiedLineSegment(high1, high3, "trend"),
            ),
            result.segments,
        )

    def test_final_cleanup_does_not_treat_separated_highs_as_consecutive(self):
        d = date(2020, 1, 1)
        low0 = PivotPoint(d, 0.0, "low")
        high1 = PivotPoint(d + timedelta(days=10), 5.0, "high")
        low1 = PivotPoint(d + timedelta(days=20), 1.0, "low")
        high2 = PivotPoint(d + timedelta(days=30), 6.0, "high")
        low2 = PivotPoint(d + timedelta(days=40), 2.0, "low")
        high3 = PivotPoint(d + timedelta(days=50), 7.0, "high")
        existing = SimplifiedLineResult(
            markers=(low0, high1, low1, high2, low2, high3),
            segments=(
                SimplifiedLineSegment(low0, high1, "trend"),
                SimplifiedLineSegment(high1, low1, "trend"),
                SimplifiedLineSegment(low1, high2, "trend"),
                SimplifiedLineSegment(high2, low2, "trend"),
                SimplifiedLineSegment(low2, high3, "trend"),
            ),
            sideways_segments=(),
        )

        result = prune_unconfirmed_retracements(existing)

        self.assertEqual(existing, result)

    def test_final_cleanup_collapses_consecutive_lower_lows(self):
        d = date(2020, 1, 1)
        high = PivotPoint(d, 10.0, "high")
        low1 = PivotPoint(d + timedelta(days=10), 7.0, "low")
        low2 = PivotPoint(d + timedelta(days=20), 6.0, "low")
        low3 = PivotPoint(d + timedelta(days=30), 5.0, "low")
        existing = SimplifiedLineResult(
            markers=(high, low1, low2, low3),
            segments=(
                SimplifiedLineSegment(high, low1, "trend"),
                SimplifiedLineSegment(low1, low2, "trend"),
                SimplifiedLineSegment(low2, low3, "trend"),
            ),
            sideways_segments=(),
        )

        result = prune_unconfirmed_retracements(existing)

        self.assertEqual((high, low1, low3), result.markers)
        self.assertEqual(
            (
                SimplifiedLineSegment(high, low1, "trend"),
                SimplifiedLineSegment(low1, low3, "trend"),
            ),
            result.segments,
        )

    def test_final_cleanup_preserves_sideways_boundary(self):
        d = date(2020, 1, 1)
        high = PivotPoint(d, 10.0, "high")
        low1 = PivotPoint(d + timedelta(days=10), 7.0, "low")
        sideways_start = PivotPoint(d + timedelta(days=20), 6.0, "low")
        sideways_end = PivotPoint(d + timedelta(days=30), 6.1, "low")
        lower_low = PivotPoint(d + timedelta(days=40), 5.0, "low")
        sideways = SidewaysSegment(
            start=sideways_start,
            end=sideways_end,
            prior_trend="down",
            reference_side="low",
            angle_deg=1.0,
        )
        existing = SimplifiedLineResult(
            markers=(high, low1, sideways_start, sideways_end, lower_low),
            segments=(
                SimplifiedLineSegment(high, low1, "trend"),
                SimplifiedLineSegment(low1, sideways_start, "trend"),
                SimplifiedLineSegment(sideways_start, sideways_end, "sideways"),
                SimplifiedLineSegment(sideways_end, lower_low, "trend"),
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


if __name__ == "__main__":
    unittest.main()
