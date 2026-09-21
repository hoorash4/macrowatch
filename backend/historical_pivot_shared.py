"""Shared immutable types and raw-graph helpers for Historical Insight stages.

Stage modules exchange only explicit result DTOs plus untouched graph geometry.
Stage 1 retains plateau candidates because Stage 2 must search them for entries.
Stage 3 alone may carry rapid candidates; Stage 4+ never carries provisional or
rapid-candidate state. Detached markers are legal only when explicitly declared
as marker-only spikes.
"""
from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Sequence

BUFFER_MONTHS = 24
SIDEWAYS_ANGLE_THRESHOLD_DEG = 6.0
SAME_TREND_ANGLE_THRESHOLD_DEG = 10.0
SUPABASE_REST_PAGE_SIZE = 1000

@dataclass(frozen=True)
class SeriesPoint:
    day: date
    value: float


@dataclass(frozen=True)
class PivotPoint:
    day: date
    value: float
    pivot_type: str


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
    prior_trend: str  # up | down | unknown
    reference_side: str  # high | low
    angle_deg: float
    protected: bool = True

    @property
    def pivot_points(self) -> tuple[PivotPoint, PivotPoint]:
        """Return the two boundaries of the classified sideways segment."""
        return self.start, self.end


@dataclass(frozen=True)
class SimplifiedLineSegment:
    start: PivotPoint
    end: PivotPoint
    kind: str  # trend | sideways | spike


@dataclass(frozen=True)
class RapidMoveCandidate:
    start: PivotPoint
    end: PivotPoint
    direction: int  # +1 up, -1 down
    visual_y_share: float

    @property
    def protected_points(self) -> tuple[PivotPoint, PivotPoint]:
        return self.start, self.end


def _pivot_key(point: PivotPoint) -> tuple[date, float, str]:
    return point.day, point.value, point.pivot_type


def _validate_line_contract(
    *,
    markers: Sequence[PivotPoint],
    segments: Sequence[SimplifiedLineSegment],
    marker_only_points: Sequence[PivotPoint],
) -> set[tuple[date, float, str]]:
    marker_keys = {_pivot_key(item) for item in markers}
    endpoint_keys: set[tuple[date, float, str]] = set()

    for segment in segments:
        for point in (segment.start, segment.end):
            point_key = _pivot_key(point)
            if point_key not in marker_keys:
                raise ValueError(
                    "stage output contains a segment endpoint that is not a surviving marker"
                )
            endpoint_keys.add(point_key)

    marker_only_keys = {_pivot_key(point) for point in marker_only_points}
    if not marker_only_keys.issubset(marker_keys):
        raise ValueError("stage output contains marker-only metadata for a deleted marker")
    if marker_only_keys & endpoint_keys:
        raise ValueError("marker-only spike may not be connected to the line")

    detached = marker_keys - endpoint_keys
    if detached != marker_only_keys:
        raise ValueError(
            "only explicit marker-only spikes may survive as detached markers"
        )
    return marker_keys


@dataclass(frozen=True)
class Stage3LineResult:
    """Stage-3 output contract consumed by Stage 4 only."""

    markers: tuple[PivotPoint, ...]
    segments: tuple[SimplifiedLineSegment, ...]
    marker_only_points: tuple[PivotPoint, ...] = ()
    rapid_move_candidates: tuple[RapidMoveCandidate, ...] = ()

    def __post_init__(self) -> None:
        marker_keys = _validate_line_contract(
            markers=self.markers,
            segments=self.segments,
            marker_only_points=self.marker_only_points,
        )
        for candidate in self.rapid_move_candidates:
            for point in candidate.protected_points:
                if _pivot_key(point) not in marker_keys:
                    raise ValueError(
                        "stage3 contains rapid metadata for a deleted marker"
                    )


@dataclass(frozen=True)
class SimplifiedLineResult:
    """Stage-4+ line contract; no provisional or rapid-candidate state survives."""

    markers: tuple[PivotPoint, ...]
    segments: tuple[SimplifiedLineSegment, ...]
    marker_only_points: tuple[PivotPoint, ...] = ()
    protected_points: tuple[PivotPoint, ...] = ()

    def __post_init__(self) -> None:
        marker_keys = _validate_line_contract(
            markers=self.markers,
            segments=self.segments,
            marker_only_points=self.marker_only_points,
        )
        for point in self.protected_points:
            if _pivot_key(point) not in marker_keys:
                raise ValueError(
                    "stage output contains protected metadata for a deleted marker"
                )


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



