"""Shared immutable types and raw-graph helpers for Historical Insight stages.

Stage modules exchange only their explicit result DTOs plus the untouched raw graph
context. No stage result contains earlier candidate sets.
"""
from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Sequence

BUFFER_MONTHS = 24
SPIKE_ANGLE_THRESHOLD_DEG = 40.0
SIDEWAYS_ANGLE_THRESHOLD_DEG = 6.0
SAME_TREND_ANGLE_THRESHOLD_DEG = 10.0
SUPABASE_REST_PAGE_SIZE = 1000

@dataclass(frozen=True)
class PivotPolicy:
    envelope_points: int
    rdp_points: int
    envelope_calendar_radius_days: int | None = None


PIVOT_POLICIES = {
    "D": PivotPolicy(envelope_points=35, rdp_points=14, envelope_calendar_radius_days=17),
    "W": PivotPolicy(envelope_points=5, rdp_points=14),
    "M": PivotPolicy(envelope_points=3, rdp_points=10),
}


@dataclass(frozen=True)
class SeriesPoint:
    day: date
    value: float


@dataclass(frozen=True)
class EnvelopePoint:
    day: date
    value: float
    upper: float
    lower: float


@dataclass(frozen=True)
class PivotPoint:
    day: date
    value: float
    pivot_type: str


@dataclass(frozen=True)
class BasePivotResult:
    frequency: str
    policy: PivotPolicy
    high_candidates: tuple[PivotPoint, ...]
    low_candidates: tuple[PivotPoint, ...]
    high_pivots: tuple[PivotPoint, ...]
    low_pivots: tuple[PivotPoint, ...]

    @property
    def display_markers(self) -> tuple[PivotPoint, ...]:
        """Return only marker coordinates for overlay on the untouched raw chart."""
        return tuple(sorted(
            (*self.high_pivots, *self.low_pivots),
            key=lambda item: (item.day, item.pivot_type),
        ))


@dataclass(frozen=True)
class ChartGeometry:
    """Visible chart coordinate system used to measure the angle seen on screen."""

    display_start: date
    display_end: date
    y_min: float
    y_max: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if self.display_end <= self.display_start:
            raise ValueError("display_end must be after display_start")
        if self.y_max <= self.y_min:
            raise ValueError("y_max must be greater than y_min")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("chart width and height must be positive")


@dataclass(frozen=True)
class SpikePeak:
    point: PivotPoint
    direction: str  # up | down
    angle_deg: float
    entry: PivotPoint | None = None
    marker_only: bool = False


@dataclass(frozen=True)
class SidewaysSegment:
    start: PivotPoint
    end: PivotPoint
    prior_trend: str  # up | down
    reference_side: str  # high | low
    angle_deg: float

    @property
    def pivot_points(self) -> tuple[PivotPoint, PivotPoint]:
        """A confirmed sideways segment always protects both boundary pivots."""
        return self.start, self.end


@dataclass(frozen=True)
class SimplifiedLineSegment:
    start: PivotPoint
    end: PivotPoint
    kind: str  # trend | sideways | spike


@dataclass(frozen=True)
class SimplifiedLineResult:
    markers: tuple[PivotPoint, ...]
    segments: tuple[SimplifiedLineSegment, ...]
    sideways_segments: tuple[SidewaysSegment, ...]

    def __post_init__(self) -> None:
        marker_keys = {
            (item.day, item.value, item.pivot_type)
            for item in self.markers
        }
        for segment in self.segments:
            for point in (segment.start, segment.end):
                point_key = (point.day, point.value, point.pivot_type)
                if point_key not in marker_keys:
                    raise ValueError(
                        "stage output contains a segment endpoint that is not a surviving marker"
                    )
        for sideways in self.sideways_segments:
            for point in sideways.pivot_points:
                point_key = (point.day, point.value, point.pivot_type)
                if point_key not in marker_keys:
                    raise ValueError(
                        "stage output contains sideways metadata for a deleted marker"
                    )


@dataclass(frozen=True)
class SpikeAugmentedPivotResult:
    """Sealed final stage-1 output.

    Only pivots that are confirmed at the end of stage 1 are retained here.
    Earlier plateau candidates, pre-spike RDP state, and deleted/debug points are
    intentionally absent so later stages cannot inspect or resurrect them.
    """

    high_pivots: tuple[PivotPoint, ...]
    low_pivots: tuple[PivotPoint, ...]
    spike_peaks: tuple[SpikePeak, ...] = ()

    def __post_init__(self) -> None:
        final_keys = {
            (item.day, item.value, item.pivot_type)
            for item in (*self.high_pivots, *self.low_pivots)
        }
        for spike in self.spike_peaks:
            peak_key = (
                spike.point.day,
                spike.point.value,
                spike.point.pivot_type,
            )
            if peak_key not in final_keys:
                raise ValueError("stage-1 spike metadata references a non-final peak")
            if spike.entry is not None:
                entry_key = (
                    spike.entry.day,
                    spike.entry.value,
                    spike.entry.pivot_type,
                )
                if entry_key not in final_keys:
                    raise ValueError("stage-1 spike metadata references a non-final entry")

    @property
    def display_markers(self) -> tuple[PivotPoint, ...]:
        return tuple(sorted(
            (*self.high_pivots, *self.low_pivots),
            key=lambda item: (item.day, item.pivot_type),
        ))


def shift_months(value: date, months: int) -> date:
    """Shift a date by whole calendar months, clamping the day to the target month."""
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)



def buffer_bounds(cycle_start: date, cycle_trough: date) -> tuple[date, date]:
    if cycle_trough < cycle_start:
        raise ValueError("cycle_trough must not precede cycle_start")
    return shift_months(cycle_start, -BUFFER_MONTHS), shift_months(cycle_trough, BUFFER_MONTHS)



def normalize_rows(rows: Iterable[dict[str, Any]]) -> tuple[SeriesPoint, ...]:
    points: list[SeriesPoint] = []
    for row in rows:
        raw_day = str(row.get("observation_date") or "")[:10]
        try:
            observed = date.fromisoformat(raw_day)
            value = float(row["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("economic-chart row has an invalid date or value") from exc
        if not math.isfinite(value):
            raise ValueError("economic-chart row has a non-finite value")
        points.append(SeriesPoint(observed, value))
    points.sort(key=lambda item: item.day)
    if any(left.day >= right.day for left, right in zip(points, points[1:])):
        raise ValueError("economic-chart rows must have unique dates")
    if not points:
        raise ValueError("economic-chart series is empty")
    return tuple(points)



def _screen_xy(point: PivotPoint, geometry: ChartGeometry) -> tuple[float, float]:
    x_span = (geometry.display_end - geometry.display_start).days
    x = (point.day - geometry.display_start).days / x_span * geometry.width
    y = (geometry.y_max - point.value) / (geometry.y_max - geometry.y_min) * geometry.height
    return x, y



def screen_angle_degrees(
    left: PivotPoint,
    pivot: PivotPoint,
    right: PivotPoint,
    geometry: ChartGeometry,
) -> float:
    """Measure the pivot's interior angle in the chart's visible coordinate system."""
    ax, ay = _screen_xy(left, geometry)
    bx, by = _screen_xy(pivot, geometry)
    cx, cy = _screen_xy(right, geometry)
    v1 = (ax - bx, ay - by)
    v2 = (cx - bx, cy - by)
    norm1 = math.hypot(*v1)
    norm2 = math.hypot(*v2)
    if norm1 == 0 or norm2 == 0:
        raise ValueError("angle points must have distinct screen coordinates")
    cosine = (v1[0] * v2[0] + v1[1] * v2[1]) / (norm1 * norm2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))




def screen_segment_angle_degrees(
    start: PivotPoint,
    end: PivotPoint,
    geometry: ChartGeometry,
) -> float:
    """Signed chart angle from the x-axis; positive means rising."""
    sx, sy = _screen_xy(start, geometry)
    ex, ey = _screen_xy(end, geometry)
    dx = ex - sx
    if dx <= 0:
        raise ValueError("sideways reference line must move forward in time")
    dy = sy - ey
    return math.degrees(math.atan2(dy, dx))



def classify_sideways_reference_line(
    start: PivotPoint,
    end: PivotPoint,
    prior_trend: str,
    geometry: ChartGeometry,
    *,
    angle_threshold_deg: float = SIDEWAYS_ANGLE_THRESHOLD_DEG,
) -> SidewaysSegment | None:
    """Classify an already-selected reference line as sideways.

    After an uptrend, the reference line is high -> high.
    After a downtrend, the reference line is low -> low.
    Absolute chart angle <= 6 degrees is sideways.
    """
    if prior_trend not in {"up", "down"}:
        raise ValueError("prior_trend must be up or down")
    expected_side = "high" if prior_trend == "up" else "low"
    if start.pivot_type != expected_side or end.pivot_type != expected_side:
        raise ValueError(
            f"{prior_trend} prior trend requires a {expected_side}-to-{expected_side} reference line"
        )
    angle = screen_segment_angle_degrees(start, end, geometry)
    if abs(angle) > angle_threshold_deg:
        return None
    return SidewaysSegment(
        start=start,
        end=end,
        prior_trend=prior_trend,
        reference_side=expected_side,
        angle_deg=angle,
    )


def screen_origin_angle_degrees(
    origin: PivotPoint,
    left: PivotPoint,
    right: PivotPoint,
    geometry: ChartGeometry,
) -> float:
    """Angle at origin between two candidate extremes in screen coordinates."""
    ox, oy = _screen_xy(origin, geometry)
    lx, ly = _screen_xy(left, geometry)
    rx, ry = _screen_xy(right, geometry)
    v1 = (lx - ox, ly - oy)
    v2 = (rx - ox, ry - oy)
    norm1 = math.hypot(*v1)
    norm2 = math.hypot(*v2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    cosine = (v1[0] * v2[0] + v1[1] * v2[1]) / (norm1 * norm2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))



