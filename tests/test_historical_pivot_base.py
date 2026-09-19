from __future__ import annotations

import copy
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from historical_pivot_base import (  # noqa: E402
    PIVOT_POLICIES,
    BasePivotResult,
    ChartGeometry,
    PivotPoint,
    PivotPolicy,
    SeriesPoint,
    SpikePeak,
    buffer_bounds,
    build_envelope,
    calculate_base_pivots,
    calculate_case_series,
    fixed_count_rdp,
    frontend_payload,
    plateau_extrema,
    augment_spike_entry_points,
    screen_angle_degrees,
)


class FakeDatabase:
    def __init__(self):
        self.requests = []

    def request(self, method, table, params=None):
        self.requests.append((method, table, dict(params or {})))
        if table == "historical_case_market_cycles":
            return [{"start_date": "2020-03-23", "trough_date": "2022-12-28"}]
        if table == "economic_chart_points":
            start = date(2018, 3, 23)
            return [
                {
                    "observation_date": (start + timedelta(days=index)).isoformat(),
                    "value": float((index % 9) - 4),
                    "frequency": "D",
                }
                for index in range(80)
            ]
        return []


class HistoricalPivotBaseTests(unittest.TestCase):
    def test_approved_frequency_policy_is_fixed(self):
        self.assertEqual(
            (35, 14, 17),
            (
                PIVOT_POLICIES["D"].envelope_points,
                PIVOT_POLICIES["D"].rdp_points,
                PIVOT_POLICIES["D"].envelope_calendar_radius_days,
            ),
        )
        self.assertEqual(
            (5, 14),
            (PIVOT_POLICIES["W"].envelope_points, PIVOT_POLICIES["W"].rdp_points),
        )
        self.assertEqual(
            (3, 10),
            (PIVOT_POLICIES["M"].envelope_points, PIVOT_POLICIES["M"].rdp_points),
        )
        self.assertEqual({"D", "W", "M"}, set(PIVOT_POLICIES))

    def test_buffer_is_start_minus_24m_and_trough_plus_24m(self):
        self.assertEqual(
            (date(2018, 3, 23), date(2024, 12, 28)),
            buffer_bounds(date(2020, 3, 23), date(2022, 12, 28)),
        )

    def test_daily_envelope_uses_centered_35_calendar_day_window(self):
        start = date(2020, 1, 1)
        points = tuple(
            SeriesPoint(start + timedelta(days=index * 2), float(index))
            for index in range(30)
        )
        envelope = build_envelope(points, "D")
        # At Jan 21 (index 10), +/-17 calendar days includes Jan 5..Feb 7,
        # i.e. indices 2 through 18 in this every-other-day sample.
        self.assertEqual(18.0, envelope[10].upper)
        self.assertEqual(2.0, envelope[10].lower)

    def test_plateau_extrema_returns_actual_raw_point(self):
        points = (
            SeriesPoint(date(2020, 1, 1), 1.0),
            SeriesPoint(date(2020, 1, 2), 5.0),
            SeriesPoint(date(2020, 1, 3), 2.0),
            SeriesPoint(date(2020, 1, 4), 1.0),
            SeriesPoint(date(2020, 1, 5), 0.0),
        )
        envelope = build_envelope(points, "M")
        highs = plateau_extrema(envelope, "high")
        self.assertIn(PivotPoint(date(2020, 1, 2), 5.0, "high"), highs)

    def test_rdp_keeps_fixed_count_and_original_candidate_coordinates(self):
        points = tuple(
            PivotPoint(
                date(2020, 1, 1) + timedelta(days=index),
                float(index % 4),
                "high",
            )
            for index in range(20)
        )
        result = fixed_count_rdp(points, 10)
        self.assertEqual(10, len(result))
        self.assertTrue(all(item in points for item in result))
        self.assertEqual(points[0], result[0])
        self.assertEqual(points[-1], result[-1])

    def test_calculation_does_not_mutate_raw_rows(self):
        rows = [
            {
                "observation_date": (
                    date(2020, 1, 1) + timedelta(days=index)
                ).isoformat(),
                "value": float(index % 7),
            }
            for index in range(90)
        ]
        original = copy.deepcopy(rows)
        calculate_base_pivots(rows, "D")
        self.assertEqual(original, rows)

    def test_frontend_payload_contains_markers_not_replacement_chart_series(self):
        rows = [
            {
                "observation_date": (
                    date(2020, 1, 1) + timedelta(days=index)
                ).isoformat(),
                "value": float((index % 11) - 5),
            }
            for index in range(120)
        ]
        result = calculate_base_pivots(rows, "D")
        payload = frontend_payload(
            result,
            case_code="tightening_2022",
            index_code="NASDAQ_COMPOSITE",
            series_code="US10Y_REAL",
            buffer_start=date(2018, 3, 23),
            buffer_end=date(2024, 12, 28),
        )
        self.assertIn("markers", payload)
        self.assertNotIn("raw", payload)
        self.assertNotIn("envelope", payload)
        self.assertNotIn("series", payload)
        self.assertTrue(all(
            set(marker) == {"pivot_date", "pivot_value", "pivot_type"}
            for marker in payload["markers"]
        ))

    def test_case_calculation_reads_only_and_uses_anchor_buffer(self):
        db = FakeDatabase()
        result, buffer_start, buffer_end = calculate_case_series(
            db,
            case_code="tightening_2022",
            index_code="NASDAQ_COMPOSITE",
            series_code="US10Y_REAL",
        )
        self.assertEqual("D", result.frequency)
        self.assertEqual(date(2018, 3, 23), buffer_start)
        self.assertEqual(date(2024, 12, 28), buffer_end)
        self.assertTrue(all(call[0] == "GET" for call in db.requests))
        point_query = db.requests[1][2]
        self.assertEqual("gte.2018-03-23", point_query["observation_date"])
        self.assertIn("observation_date.lte.2024-12-28", point_query["and"])


    def test_spike_augmentation_preserves_base_rdp_and_adds_upward_entry_only(self):
        d = date(2020, 1, 1)
        high_rdp = (
            PivotPoint(d, 5.0, "high"),
            PivotPoint(d + timedelta(days=10), 10.0, "high"),
            PivotPoint(d + timedelta(days=20), 5.0, "high"),
            PivotPoint(d + timedelta(days=40), 6.0, "high"),
        )
        low_rdp = (
            PivotPoint(d - timedelta(days=10), 0.0, "low"),
            PivotPoint(d + timedelta(days=30), 1.0, "low"),
            PivotPoint(d + timedelta(days=50), 0.5, "low"),
        )
        low_candidates = (
            PivotPoint(d + timedelta(days=3), 2.0, "low"),
            PivotPoint(d + timedelta(days=6), 1.0, "low"),
            PivotPoint(d + timedelta(days=8), 1.5, "low"),
        )
        base = BasePivotResult(
            frequency="D",
            policy=PivotPolicy(35, 14),
            high_candidates=high_rdp,
            low_candidates=low_candidates,
            high_pivots=high_rdp,
            low_pivots=low_rdp,
        )
        geometry = ChartGeometry(
            display_start=d,
            display_end=d + timedelta(days=100),
            y_min=0.0,
            y_max=10.0,
            width=100.0,
            height=100.0,
        )
        result = augment_spike_entry_points(base, geometry)
        self.assertEqual(high_rdp, result.base.high_pivots)
        self.assertEqual(low_rdp, result.base.low_pivots)
        self.assertEqual((PivotPoint(d + timedelta(days=6), 1.0, "low"),), result.added_low_pivots)
        self.assertEqual((), result.added_high_pivots)
        self.assertTrue(all(item in result.low_pivots for item in low_rdp))

    def test_spike_allows_opposite_rdp_inside_when_it_does_not_follow_peak(self):
        d = date(2020, 1, 1)
        high_rdp = (
            PivotPoint(d, 5.0, "high"),
            PivotPoint(d + timedelta(days=10), 10.0, "high"),
            PivotPoint(d + timedelta(days=20), 5.0, "high"),
        )
        low_rdp = (
            PivotPoint(d + timedelta(days=5), 1.0, "low"),
            PivotPoint(d + timedelta(days=30), 0.0, "low"),
        )
        base = BasePivotResult(
            frequency="D",
            policy=PivotPolicy(35, 14),
            high_candidates=high_rdp,
            low_candidates=(PivotPoint(d + timedelta(days=6), 0.5, "low"),),
            high_pivots=high_rdp,
            low_pivots=low_rdp,
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), 0.0, 10.0, 100.0, 100.0)
        result = augment_spike_entry_points(base, geometry)
        self.assertEqual((), result.added_low_pivots)
        self.assertEqual(low_rdp[0], result.low_pivots[0])
        self.assertEqual(1, len(result.spike_peaks))

    def test_upward_spike_rejected_when_inside_low_follows_peak_above_adjacent_highs(self):
        d = date(2020, 1, 1)
        high_rdp = (
            PivotPoint(d, 5.0, "high"),
            PivotPoint(d + timedelta(days=10), 10.0, "high"),
            PivotPoint(d + timedelta(days=20), 6.0, "high"),
        )
        low_rdp = (
            PivotPoint(d + timedelta(days=5), 7.0, "low"),
            PivotPoint(d + timedelta(days=30), 1.0, "low"),
        )
        base = BasePivotResult(
            frequency="D",
            policy=PivotPolicy(35, 14),
            high_candidates=high_rdp,
            low_candidates=(PivotPoint(d + timedelta(days=6), 2.0, "low"),),
            high_pivots=high_rdp,
            low_pivots=low_rdp,
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), 0.0, 10.0, 100.0, 100.0)
        result = augment_spike_entry_points(base, geometry)
        self.assertEqual((), result.added_low_pivots)
        self.assertEqual((), result.spike_peaks)

    def test_downward_spike_rejected_when_inside_high_follows_peak_below_adjacent_lows(self):
        d = date(2020, 1, 1)
        low_rdp = (
            PivotPoint(d, 5.0, "low"),
            PivotPoint(d + timedelta(days=10), 0.0, "low"),
            PivotPoint(d + timedelta(days=20), 4.0, "low"),
        )
        high_rdp = (
            PivotPoint(d + timedelta(days=5), 3.0, "high"),
            PivotPoint(d + timedelta(days=30), 9.0, "high"),
        )
        base = BasePivotResult(
            frequency="D",
            policy=PivotPolicy(35, 14),
            high_candidates=(PivotPoint(d + timedelta(days=6), 8.0, "high"),),
            low_candidates=low_rdp,
            high_pivots=high_rdp,
            low_pivots=low_rdp,
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), 0.0, 10.0, 100.0, 100.0)
        result = augment_spike_entry_points(base, geometry)
        self.assertEqual((), result.added_high_pivots)
        self.assertEqual((), result.spike_peaks)

    def test_spike_augmentation_mirrors_for_downward_spike(self):
        d = date(2020, 1, 1)
        low_rdp = (
            PivotPoint(d, 5.0, "low"),
            PivotPoint(d + timedelta(days=10), 0.0, "low"),
            PivotPoint(d + timedelta(days=20), 5.0, "low"),
            PivotPoint(d + timedelta(days=40), 4.0, "low"),
        )
        high_rdp = (
            PivotPoint(d - timedelta(days=10), 10.0, "high"),
            PivotPoint(d + timedelta(days=30), 9.0, "high"),
            PivotPoint(d + timedelta(days=50), 9.5, "high"),
        )
        high_candidates = (
            PivotPoint(d + timedelta(days=3), 7.0, "high"),
            PivotPoint(d + timedelta(days=6), 9.0, "high"),
            PivotPoint(d + timedelta(days=8), 8.0, "high"),
        )
        base = BasePivotResult(
            frequency="D",
            policy=PivotPolicy(35, 14),
            high_candidates=high_candidates,
            low_candidates=low_rdp,
            high_pivots=high_rdp,
            low_pivots=low_rdp,
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), 0.0, 10.0, 100.0, 100.0)
        result = augment_spike_entry_points(base, geometry)
        self.assertEqual((PivotPoint(d + timedelta(days=6), 9.0, "high"),), result.added_high_pivots)
        self.assertEqual((), result.added_low_pivots)

    def test_spike_reuses_existing_entry_rdp_instead_of_adding_duplicate(self):
        d = date(2020, 1, 1)
        high_rdp = (
            PivotPoint(d, 5.0, "high"),
            PivotPoint(d + timedelta(days=10), 10.0, "high"),
            PivotPoint(d + timedelta(days=20), 5.0, "high"),
        )
        existing_entry = PivotPoint(d + timedelta(days=5), 1.0, "low")
        low_rdp = (
            existing_entry,
            PivotPoint(d + timedelta(days=30), 0.0, "low"),
        )
        base = BasePivotResult(
            frequency="D",
            policy=PivotPolicy(35, 14),
            high_candidates=high_rdp,
            low_candidates=(
                PivotPoint(d + timedelta(days=4), 2.0, "low"),
                existing_entry,
            ),
            high_pivots=high_rdp,
            low_pivots=low_rdp,
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), 0.0, 10.0, 100.0, 100.0)
        result = augment_spike_entry_points(base, geometry)
        self.assertEqual((), result.added_low_pivots)
        self.assertIn(existing_entry, result.low_pivots)

    def test_spike_peak_is_recorded_separately_from_added_entry(self):
        d = date(2020, 1, 1)
        high_rdp = (
            PivotPoint(d, 5.0, "high"),
            PivotPoint(d + timedelta(days=10), 10.0, "high"),
            PivotPoint(d + timedelta(days=20), 5.0, "high"),
            PivotPoint(d + timedelta(days=40), 6.0, "high"),
        )
        low_rdp = (
            PivotPoint(d - timedelta(days=10), 0.0, "low"),
            PivotPoint(d + timedelta(days=30), 1.0, "low"),
        )
        base = BasePivotResult(
            frequency="D",
            policy=PivotPolicy(35, 14, 17),
            high_candidates=high_rdp,
            low_candidates=(PivotPoint(d + timedelta(days=6), 1.0, "low"),),
            high_pivots=high_rdp,
            low_pivots=low_rdp,
        )
        geometry = ChartGeometry(d, d + timedelta(days=100), 0.0, 10.0, 100.0, 100.0)
        result = augment_spike_entry_points(base, geometry)
        self.assertEqual(1, len(result.spike_peaks))
        self.assertEqual(high_rdp[1], result.spike_peaks[0].point)
        self.assertEqual("up", result.spike_peaks[0].direction)

    def test_screen_angle_uses_chart_geometry(self):
        d = date(2020, 1, 1)
        geometry = ChartGeometry(d, d + timedelta(days=100), 0.0, 10.0, 100.0, 100.0)
        angle = screen_angle_degrees(
            PivotPoint(d, 5.0, "high"),
            PivotPoint(d + timedelta(days=10), 10.0, "high"),
            PivotPoint(d + timedelta(days=20), 5.0, "high"),
            geometry,
        )
        self.assertLess(angle, 40.0)


if __name__ == "__main__":
    unittest.main()
