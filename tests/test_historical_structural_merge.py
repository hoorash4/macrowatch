"""Pure endpoint tests; no database, provider calls, or production writes."""
import copy
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from historical_pivot_base import (
    BasePivotResult, ChartGeometry, PivotPoint, PivotPolicy,
    SimplifiedLineResult, SimplifiedLineSegment, SpikeAugmentedPivotResult,
    StructuralMergePolicy, prune_structural_trends, simplify_pivot_lines,
)


def path(values, kinds=None):
    points = tuple(PivotPoint(date(2000, 1, 1) + timedelta(days=day), value, side)
                   for day, value, side in values)
    kinds = kinds or ["trend"] * (len(points) - 1)
    return SimplifiedLineResult(points, tuple(
        SimplifiedLineSegment(a, b, kind)
        for a, b, kind in zip(points, points[1:], kinds)
    ), ())


SAMPLE = [(0, 0, "low"), (100, 10, "high"), (110, 7, "low"),
          (140, 13, "high"), (150, 10, "low"), (180, 16, "high")]


class StructuralMergeTests(unittest.TestCase):
    def test_recovered_corrections_collapse_to_endpoints_without_mutation(self):
        source = path(SAMPLE)
        before = copy.deepcopy(source)
        result = prune_structural_trends(source)
        self.assertEqual((source.markers[0], source.markers[-1]), result.markers)
        self.assertEqual(1, len(result.segments))
        self.assertEqual(before, source)
        selected = [item for item in result.merge_diagnostics if item.get("selected")]
        self.assertEqual(1, len(selected))
        self.assertEqual(2, len(selected[0]["excursions"]))

    def test_downtrend_is_exact_mirror(self):
        source = path([(day, -value, "high" if side == "low" else "low")
                       for day, value, side in SAMPLE])
        self.assertEqual((source.markers[0], source.markers[-1]),
                         prune_structural_trends(source).markers)

    def test_units_level_and_uniform_time_scaling_do_not_change_decisions(self):
        expected = [point.pivot_type for point in prune_structural_trends(path(SAMPLE)).markers]
        for scale, offset, time_scale in [(1000, 0, 1), (0.001, -100, 7), (1, 5, 30)]:
            result = prune_structural_trends(path([
                (day * time_scale, offset + value * scale, side) for day, value, side in SAMPLE
            ]))
            self.assertEqual(expected, [point.pivot_type for point in result.markers])

    def test_deep_reversal_not_erased_by_distant_huge_new_high(self):
        source = path([(0, 0, "low"), (100, 10, "high"), (120, 2, "low"), (1000, 100, "high")])
        result = prune_structural_trends(source)
        self.assertIn(source.markers[1], result.markers)
        self.assertIn(source.markers[2], result.markers)
        self.assertTrue(any(item["reason"] == "deep_reversal" for item in result.merge_diagnostics))

    def test_long_recovery_and_marginal_breakout_are_not_enough(self):
        for rows in [
            [(0, 0, "low"), (10, 10, "high"), (100, 7, "low"), (200, 13, "high")],
            [(0, 0, "low"), (100, 10, "high"), (110, 7, "low"), (140, 10.01, "high")],
        ]:
            source = path(rows)
            self.assertEqual(source.markers, prune_structural_trends(source).markers)

    def test_broken_origin_or_unrecovered_tail_keeps_reversal(self):
        for final, low in [(13, -1), (9, 7)]:
            source = path([(0, 0, "low"), (100, 10, "high"), (110, low, "low"), (140, final, "high")])
            self.assertEqual(source.markers, prune_structural_trends(source).markers)

    def test_internal_sideways_and_spike_can_be_absorbed_after_validation(self):
        for kind in ("sideways", "spike"):
            source = path(SAMPLE, ["trend", kind, "trend", "trend", "trend"])
            self.assertEqual((source.markers[0], source.markers[-1]), prune_structural_trends(source).markers)

    def test_marker_only_spike_cannot_be_deleted_without_validation(self):
        source = path(SAMPLE[:4])
        spike = PivotPoint(date(2000, 1, 1) + timedelta(days=105), -10, "low")
        source = replace(source, markers=source.markers + (spike,))
        self.assertIn(spike, prune_structural_trends(source).markers)

    def test_disconnected_segments_are_not_bridged(self):
        source = path(SAMPLE)
        source = replace(source, segments=(source.segments[0], source.segments[-1]))
        self.assertEqual(source.segments, prune_structural_trends(source).segments)

    def test_invalid_policy_rejected(self):
        for value in (0, -1, float("nan"), float("inf"), 1):
            with self.assertRaises(ValueError):
                StructuralMergePolicy(maximum_retracement=value)

    def test_thresholds_are_configurable_without_changing_the_base_path(self):
        source = path(SAMPLE[:4])
        self.assertEqual(source.markers, prune_structural_trends(
            source, policy=StructuralMergePolicy(maximum_retracement=0.2)
        ).markers)
        self.assertEqual(2, len(prune_structural_trends(source).markers))

    def test_invalid_segment_rejected(self):
        source = path(SAMPLE)
        with self.assertRaises(ValueError):
            prune_structural_trends(replace(source, segments=(
                SimplifiedLineSegment(source.markers[1], source.markers[0], "trend"),
            )))

    def test_strategy_switch_retains_legacy_result_and_none_skips_postpass(self):
        points = path(SAMPLE).markers
        highs, lows = tuple(p for p in points if p.pivot_type == "high"), tuple(p for p in points if p.pivot_type == "low")
        augmented = SpikeAugmentedPivotResult(BasePivotResult("D", PivotPolicy(35, 14), highs, lows, highs, lows), (), ())
        geometry = ChartGeometry(points[0].day, points[-1].day, 0, 20, 100, 100)
        raw = simplify_pivot_lines(augmented, geometry, merge_method="none")
        from historical_pivot_base import prune_same_trend_extremes
        self.assertEqual(prune_same_trend_extremes(raw, geometry),
                         simplify_pivot_lines(augmented, geometry, merge_method="angle"))
        self.assertEqual(prune_structural_trends(raw), simplify_pivot_lines(augmented, geometry))
        with self.assertRaises(ValueError):
            simplify_pivot_lines(augmented, geometry, merge_method="typo")


if __name__ == "__main__":
    unittest.main()
