from datetime import date, timedelta
import unittest

from historical_pivot_shared import (
    ChartGeometry,
    LinePoint,
    LineRapidMoveCandidate,
    SimplifiedLineResult,
    SimplifiedLineSegment,
    Stage3LineResult,
)
from historical_pivot_stage4 import finalize_rapid_moves
from historical_pivot_stage5 import prune_same_trend_extremes
from historical_pivot_stage6 import prune_unconfirmed_retracements


def seg(a, b):
    return SimplifiedLineSegment(a, b, "trend")


class HistoricalPivotProtectionSemanticsTests(unittest.TestCase):
    def test_stage4_rejects_rapid_candidate_if_later_wave_crosses_entry(self):
        d = date(2020, 1, 1)
        entry = LinePoint(d, 0.0)
        peak = LinePoint(d + timedelta(days=10), 10.0)
        reversal = LinePoint(d + timedelta(days=20), -1.0)
        stage3 = Stage3LineResult(
            markers=(entry, peak, reversal),
            segments=(seg(entry, peak), seg(peak, reversal)),
            rapid_move_candidates=(
                LineRapidMoveCandidate(entry, peak, 1, 0.5),
            ),
        )
        geometry = ChartGeometry(d, d + timedelta(days=40), -5.0, 15.0, 400.0, 200.0)

        result = finalize_rapid_moves(stage3, geometry)

        self.assertEqual((), result.protected_points)

    def test_stage4_scans_beyond_seed_and_moves_protection_to_later_peak(self):
        d = date(2020, 1, 1)
        entry = LinePoint(d, 0.0)
        seed_peak = LinePoint(d + timedelta(days=10), 10.0)
        pullback = LinePoint(d + timedelta(days=15), 5.0)
        later_peak = LinePoint(d + timedelta(days=20), 19.0)
        stage3 = Stage3LineResult(
            markers=(entry, seed_peak, pullback, later_peak),
            segments=(
                seg(entry, seed_peak),
                seg(seed_peak, pullback),
                seg(pullback, later_peak),
            ),
            rapid_move_candidates=(
                LineRapidMoveCandidate(entry, seed_peak, 1, 0.5),
            ),
        )
        geometry = ChartGeometry(d, d + timedelta(days=40), -5.0, 25.0, 400.0, 300.0)

        result = finalize_rapid_moves(stage3, geometry)

        self.assertEqual((entry, later_peak), result.protected_points)

    def test_stage5_can_absorb_rapid_protected_interior_point(self):
        d = date(2020, 1, 1)
        p0 = LinePoint(d, 0.0)
        p1 = LinePoint(d + timedelta(days=10), 5.0)
        p2 = LinePoint(d + timedelta(days=20), 10.0)
        existing = SimplifiedLineResult(
            markers=(p0, p1, p2),
            segments=(seg(p0, p1), seg(p1, p2)),
            protected_points=(p1,),
        )
        geometry = ChartGeometry(d, d + timedelta(days=40), -5.0, 15.0, 400.0, 200.0)

        result = prune_same_trend_extremes(existing, geometry)

        self.assertEqual((p0, p2), result.markers)
        self.assertEqual((), result.protected_points)

    def test_stage6_can_absorb_rapid_protected_interior_point(self):
        d = date(2020, 1, 1)
        p0 = LinePoint(d, 0.0)
        p1 = LinePoint(d + timedelta(days=10), 5.0)
        p2 = LinePoint(d + timedelta(days=20), 10.0)
        existing = SimplifiedLineResult(
            markers=(p0, p1, p2),
            segments=(seg(p0, p1), seg(p1, p2)),
            protected_points=(p1,),
        )
        geometry = ChartGeometry(d, d + timedelta(days=40), -5.0, 15.0, 400.0, 200.0)

        result = prune_unconfirmed_retracements(existing, geometry)

        self.assertEqual((p0, p2), result.markers)
        self.assertEqual((), result.protected_points)

    def test_hard_sideways_endpoint_is_not_deleted_by_stage5_merge(self):
        d = date(2020, 1, 1)
        p0 = LinePoint(d, 0.0)
        p1 = LinePoint(d + timedelta(days=10), 5.0)
        p2 = LinePoint(d + timedelta(days=20), 10.0)
        existing = SimplifiedLineResult(
            markers=(p0, p1, p2),
            segments=(
                seg(p0, p1),
                SimplifiedLineSegment(p1, p2, "sideways"),
            ),
        )
        geometry = ChartGeometry(d, d + timedelta(days=40), -5.0, 15.0, 400.0, 200.0)

        result = prune_same_trend_extremes(existing, geometry)

        self.assertIn(p1, result.markers)
        self.assertIn(p2, result.markers)


if __name__ == "__main__":
    unittest.main()
