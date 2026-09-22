from __future__ import annotations

from types import SimpleNamespace
from datetime import date

from historical_pivot_shared import LinePoint, PivotPoint, SidewaysSegment, SimplifiedLineResult, SimplifiedLineSegment, SpikePeak
from historical_pivot_store import build_storage_rows, replace_stored_pivots, source_input_sha256


def point(day: str, value: float, pivot_type: str) -> PivotPoint:
    return PivotPoint(date.fromisoformat(day), value, pivot_type)


def test_build_storage_rows_preserves_line_links_and_reasons():
    p0 = point("2020-01-01", 1.0, "low")
    p1 = point("2020-02-01", 2.0, "high")
    p2 = point("2020-03-01", 1.5, "low")
    p3 = point("2020-02-15", 3.0, "high")

    base = SimpleNamespace(high_pivots=(p1, p3), low_pivots=(p0, p2))
    spike = SpikePeak(point=p3, direction="up", angle_deg=12.0, entry=None, marker_only=True)
    stage1 = SimpleNamespace(display_markers=(p0, p1, p2, p3))
    sideways = SidewaysSegment(
        start=p1,
        end=p2,
        prior_trend="up",
        reference_side="high",
        angle_deg=4.0,
    )
    stage2 = SimpleNamespace(
        display_markers=(p0, p1, p2, p3),
        spike_peaks=(spike,),
        high_sideways_segments=(sideways,),
        low_sideways_segments=(),
    )
    l0 = LinePoint(p0.day, p0.value)
    l1 = LinePoint(p1.day, p1.value)
    l2 = LinePoint(p2.day, p2.value)
    l3 = LinePoint(p3.day, p3.value)

    final = SimplifiedLineResult(
        markers=(l0, l1, l3, l2),
        segments=(
            SimplifiedLineSegment(l0, l1, "trend"),
            SimplifiedLineSegment(l1, l2, "sideways"),
        ),
        marker_only_points=(l3,),
    )
    stage3 = final
    stage4 = final
    stage5 = final

    rows = build_storage_rows(
        base=base,
        stage1=stage1,
        stage2=stage2,
        stage3=stage3,
        stage4=stage4,
        stage5=stage5,
        final=final,
    )

    by_date = {row["pivot_date"]: row for row in rows}
    assert by_date["2020-01-01"]["next_pivot_order"] == by_date["2020-02-01"]["pivot_order"]
    assert by_date["2020-01-01"]["segment_to_next"] == "trend"
    assert by_date["2020-02-01"]["next_pivot_order"] == by_date["2020-03-01"]["pivot_order"]
    assert by_date["2020-02-01"]["segment_to_next"] == "sideways"
    assert "sideways_boundary" in by_date["2020-02-01"]["selection_reason_codes"]
    assert "spike_marker_only" in by_date["2020-02-15"]["selection_reason_codes"]
    assert by_date["2020-02-15"]["next_pivot_order"] is None
    assert by_date["2020-02-15"]["segment_to_next"] is None


def test_replace_stored_pivots_uses_atomic_rpc():
    class FakeDb:
        def __init__(self):
            self.call = None

        def request(self, method, table, **kwargs):
            self.call = (method, table, kwargs)
            return 2

    db = FakeDb()
    count = replace_stored_pivots(
        db,
        case_code="tightening_2022",
        index_code="NASDAQ_COMPOSITE",
        series_code="US10Y_REAL",
        algorithm_version="test-version",
        frequency="D",
        buffer_start=date(2018, 3, 23),
        buffer_end=date(2024, 12, 28),
        source_point_count=2,
        input_sha256="a" * 64,
        rows=[{"pivot_order": 0}],
    )

    assert count == 2
    method, table, kwargs = db.call
    assert method == "POST"
    assert table == "rpc/replace_historical_indicator_pivots"
    assert kwargs["retry_safe"] is False
    assert kwargs["body"]["p_series_code"] == "US10Y_REAL"
    assert kwargs["body"]["p_frequency"] == "D"
    assert kwargs["body"]["p_buffer_start"] == "2018-03-23"
    assert kwargs["body"]["p_buffer_end"] == "2024-12-28"
    assert kwargs["body"]["p_source_point_count"] == 2
    assert kwargs["body"]["p_input_sha256"] == "a" * 64


def test_source_input_sha256_is_stable_for_row_order_and_numeric_representation():
    rows_a = [
        {"observation_date": "2020-01-02", "value": "1.50", "frequency": "D"},
        {"observation_date": "2020-01-01", "value": 1, "frequency": "D"},
    ]
    rows_b = [
        {"observation_date": "2020-01-01", "value": "1.0", "frequency": "D"},
        {"observation_date": "2020-01-02", "value": 1.5, "frequency": "D"},
    ]
    assert source_input_sha256(rows_a) == source_input_sha256(rows_b)
