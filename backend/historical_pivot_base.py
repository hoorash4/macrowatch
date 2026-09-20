"""Base Historical Insight pivot extraction.

The approved base pipeline is:
raw economic-chart points -> centered envelope -> plateau extrema -> fixed-count RDP.

Spike classification is a separate post-RDP step. RDP is a sealed stage boundary:
later stages may only use pivots that survived RDP and may never restore candidates
removed by earlier stages.

It is read-only with respect to source data. The frontend must continue to draw the
original economic series from its canonical source; this module only produces marker
coordinates that may later be overlaid on that original chart.
"""
from __future__ import annotations

import argparse
import calendar
import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Sequence

from common import SupabaseRest


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


@dataclass(frozen=True)
class SpikeAugmentedPivotResult:
    """Final stage-1 RDP result after spike confirmation."""

    base: BasePivotResult
    added_high_pivots: tuple[PivotPoint, ...]
    added_low_pivots: tuple[PivotPoint, ...]
    spike_peaks: tuple[SpikePeak, ...] = ()

    @property
    def high_pivots(self) -> tuple[PivotPoint, ...]:
        return tuple(sorted(
            (*self.base.high_pivots, *self.added_high_pivots),
            key=lambda item: item.day,
        ))

    @property
    def low_pivots(self) -> tuple[PivotPoint, ...]:
        return tuple(sorted(
            (*self.base.low_pivots, *self.added_low_pivots),
            key=lambda item: item.day,
        ))

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


def build_envelope(points: Sequence[SeriesPoint], frequency: str) -> tuple[EnvelopePoint, ...]:
    policy = PIVOT_POLICIES.get(frequency)
    if policy is None:
        raise ValueError(f"unsupported pivot frequency: {frequency}")
    result: list[EnvelopePoint] = []

    if policy.envelope_calendar_radius_days is not None:
        radius_days = policy.envelope_calendar_radius_days
        for point in points:
            window = [
                item for item in points
                if abs((item.day - point.day).days) <= radius_days
            ]
            values = [item.value for item in window]
            result.append(EnvelopePoint(point.day, point.value, max(values), min(values)))
        return tuple(result)

    radius = policy.envelope_points // 2
    for index, point in enumerate(points):
        window = points[max(0, index - radius):min(len(points), index + radius + 1)]
        values = [item.value for item in window]
        result.append(EnvelopePoint(point.day, point.value, max(values), min(values)))
    return tuple(result)


def plateau_extrema(
    envelope: Sequence[EnvelopePoint],
    pivot_type: str,
) -> tuple[PivotPoint, ...]:
    if pivot_type not in {"high", "low"}:
        raise ValueError("pivot_type must be high or low")
    attribute = "upper" if pivot_type == "high" else "lower"
    candidates: list[PivotPoint] = []
    run_start = 0
    while run_start < len(envelope):
        level = getattr(envelope[run_start], attribute)
        run_end = run_start + 1
        while run_end < len(envelope) and getattr(envelope[run_end], attribute) == level:
            run_end += 1
        run = envelope[run_start:run_end]
        if len(run) >= 2:
            chosen = (
                max(run, key=lambda item: item.value)
                if pivot_type == "high"
                else min(run, key=lambda item: item.value)
            )
            candidates.append(PivotPoint(chosen.day, chosen.value, pivot_type))
        run_start = run_end
    unique = {(item.day, item.value): item for item in candidates}
    return tuple(sorted(unique.values(), key=lambda item: item.day))


def _perpendicular_distance(
    point: PivotPoint,
    left: PivotPoint,
    right: PivotPoint,
    origin: date,
) -> float:
    px = float((point.day - origin).days)
    py = point.value
    x1 = float((left.day - origin).days)
    y1 = left.value
    x2 = float((right.day - origin).days)
    y2 = right.value
    denominator = math.hypot(y2 - y1, x2 - x1)
    if denominator == 0:
        return math.hypot(px - x1, py - y1)
    return abs((y2 - y1) * px - (x2 - x1) * py + x2 * y1 - y2 * x1) / denominator


def fixed_count_rdp(
    points: Sequence[PivotPoint],
    target_count: int,
) -> tuple[PivotPoint, ...]:
    """Greedy fixed-count RDP-style simplification used in the approved experiments."""
    if target_count < 2:
        raise ValueError("target_count must be at least 2")
    ordered = tuple(sorted(points, key=lambda item: item.day))
    if len(ordered) <= target_count:
        return ordered
    origin = ordered[0].day
    selected = {0, len(ordered) - 1}
    while len(selected) < target_count:
        best_index: int | None = None
        best_distance = -1.0
        indices = sorted(selected)
        for left_index, right_index in zip(indices, indices[1:]):
            for index in range(left_index + 1, right_index):
                distance = _perpendicular_distance(
                    ordered[index], ordered[left_index], ordered[right_index], origin,
                )
                if distance > best_distance:
                    best_index = index
                    best_distance = distance
        if best_index is None:
            break
        selected.add(best_index)
    return tuple(ordered[index] for index in sorted(selected))


def calculate_base_pivots(
    rows: Iterable[dict[str, Any]],
    frequency: str,
) -> BasePivotResult:
    points = normalize_rows(rows)
    policy = PIVOT_POLICIES.get(frequency)
    if policy is None:
        raise ValueError(f"unsupported pivot frequency: {frequency}")
    envelope = build_envelope(points, frequency)
    high_candidates = plateau_extrema(envelope, "high")
    low_candidates = plateau_extrema(envelope, "low")
    return BasePivotResult(
        frequency=frequency,
        policy=policy,
        high_candidates=high_candidates,
        low_candidates=low_candidates,
        high_pivots=fixed_count_rdp(high_candidates, policy.rdp_points),
        low_pivots=fixed_count_rdp(low_candidates, policy.rdp_points),
    )



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

def _has_pivot_between(
    pivots: Sequence[PivotPoint],
    start: date,
    end: date,
) -> bool:
    return any(start <= item.day <= end for item in pivots)


def _first_pivot_after(
    pivots: Sequence[PivotPoint],
    after: date,
) -> PivotPoint | None:
    return next((item for item in sorted(pivots, key=lambda point: point.day) if item.day > after), None)


def augment_spike_entry_points(
    base: BasePivotResult,
    geometry: ChartGeometry,
    *,
    angle_threshold_deg: float = SPIKE_ANGLE_THRESHOLD_DEG,
) -> SpikeAugmentedPivotResult:
    """Finish stage 1 by confirming spikes on top of the RDP result.

    RDP selection and spike correction belong to the same stage. This function may
    inspect plateau candidates only to recover a missing NORMAL spike entry before
    stage 1 is finalized.

    Normal spike:
      - keep the spike peak
      - use an existing opposite-side RDP entry when present
      - otherwise recover the best opposite-side plateau candidate as the entry
      - the recovered entry becomes part of the FINAL stage-1 RDP result

    Spike inside an opposite sideways segment:
      - keep ONLY the spike peak
      - do NOT select or add an entry point
      - marker_only=True, so later stages never connect a line to that peak

    After this function returns, stage 1 is sealed. Later stages may use only this
    returned final point set and may not consult base candidates again.
    """
    if angle_threshold_deg <= 0 or angle_threshold_deg >= 180:
        raise ValueError("angle_threshold_deg must be between 0 and 180")

    high_rdp = tuple(sorted(base.high_pivots, key=lambda item: item.day))
    low_rdp = tuple(sorted(base.low_pivots, key=lambda item: item.day))
    high_candidates = tuple(sorted(base.high_candidates, key=lambda item: item.day))
    low_candidates = tuple(sorted(base.low_candidates, key=lambda item: item.day))
    added_high: dict[tuple[date, float], PivotPoint] = {}
    added_low: dict[tuple[date, float], PivotPoint] = {}
    spike_peaks: dict[tuple[date, float, str], SpikePeak] = {}

    def opposite_sideways_contains_spike(
        pivot: PivotPoint,
        direction: str,
    ) -> bool:
        opposite = low_rdp if direction == "up" else high_rdp
        prior_trend = "down" if direction == "up" else "up"
        for start, end in zip(opposite, opposite[1:]):
            sideways = classify_sideways_reference_line(
                start,
                end,
                prior_trend,
                geometry,
            )
            if sideways is not None and start.day < pivot.day < end.day:
                return True
        return False

    for index in range(1, len(high_rdp) - 1):
        left, pivot, right = high_rdp[index - 1], high_rdp[index], high_rdp[index + 1]
        if not (geometry.display_start <= pivot.day <= geometry.display_end):
            continue
        if not (pivot.value > left.value and pivot.value > right.value):
            continue
        angle = screen_angle_degrees(left, pivot, right, geometry)
        if angle >= angle_threshold_deg:
            continue
        opposite_inside = [
            item for item in low_rdp
            if left.day <= item.day <= right.day
        ]
        if any(item.value > max(left.value, right.value) for item in opposite_inside):
            continue
        if _first_pivot_after(low_rdp, right.day) is None:
            continue

        marker_only = opposite_sideways_contains_spike(pivot, "up")
        if marker_only:
            entry = None
        else:
            existing_entries = [
                item for item in low_rdp
                if left.day < item.day < pivot.day
            ]
            if existing_entries:
                entry = min(existing_entries, key=lambda item: item.value)
            else:
                entry_candidates = [
                    item for item in low_candidates
                    if left.day < item.day < pivot.day
                ]
                if not entry_candidates:
                    continue
                entry = min(entry_candidates, key=lambda item: item.value)
                added_low[(entry.day, entry.value)] = entry

        spike_peaks[(pivot.day, pivot.value, "up")] = SpikePeak(
            point=pivot,
            direction="up",
            angle_deg=angle,
            entry=entry,
            marker_only=marker_only,
        )

    for index in range(1, len(low_rdp) - 1):
        left, pivot, right = low_rdp[index - 1], low_rdp[index], low_rdp[index + 1]
        if not (geometry.display_start <= pivot.day <= geometry.display_end):
            continue
        if not (pivot.value < left.value and pivot.value < right.value):
            continue
        angle = screen_angle_degrees(left, pivot, right, geometry)
        if angle >= angle_threshold_deg:
            continue
        opposite_inside = [
            item for item in high_rdp
            if left.day <= item.day <= right.day
        ]
        if any(item.value < min(left.value, right.value) for item in opposite_inside):
            continue
        if _first_pivot_after(high_rdp, right.day) is None:
            continue

        marker_only = opposite_sideways_contains_spike(pivot, "down")
        if marker_only:
            entry = None
        else:
            existing_entries = [
                item for item in high_rdp
                if left.day < item.day < pivot.day
            ]
            if existing_entries:
                entry = max(existing_entries, key=lambda item: item.value)
            else:
                entry_candidates = [
                    item for item in high_candidates
                    if left.day < item.day < pivot.day
                ]
                if not entry_candidates:
                    continue
                entry = max(entry_candidates, key=lambda item: item.value)
                added_high[(entry.day, entry.value)] = entry

        spike_peaks[(pivot.day, pivot.value, "down")] = SpikePeak(
            point=pivot,
            direction="down",
            angle_deg=angle,
            entry=entry,
            marker_only=marker_only,
        )

    result = SpikeAugmentedPivotResult(
        base=base,
        added_high_pivots=tuple(sorted(added_high.values(), key=lambda item: item.day)),
        added_low_pivots=tuple(sorted(added_low.values(), key=lambda item: item.day)),
        spike_peaks=tuple(sorted(spike_peaks.values(), key=lambda item: item.point.day)),
    )

    return result



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


def prune_same_trend_extremes(
    result: SimplifiedLineResult,
    geometry: ChartGeometry,
    *,
    angle_threshold_deg: float = SAME_TREND_ANGLE_THRESHOLD_DEG,
) -> SimplifiedLineResult:
    """Additional deletion-only pass over the already simplified path.

    The existing simplification is left intact first. This pass only collapses a
    confirmed directional run between an existing transition extreme and a later
    same-trend extreme.

    Uptrend:
      - anchor = an existing low that is lower than the adjacent surviving lows
      - inspect later highs only
      - lower/equal highs are ignored
      - only strict new highs participate
      - compare consecutive record highs from the fixed low anchor
      - every comparison must be <= 10 degrees
      - at least TWO successful record-high updates are required before collapsing
        anything (H1->H2 and H2->H3)
      - when a new record high exceeds 10 degrees, stop before that high

    Downtrend is the exact mirror using a local high anchor and strict new lows.

    Sideways and spike segments are hard trend boundaries. The scan stops before
    them. Once an extreme is confirmed, every ordinary segment/marker strictly
    between anchor and extreme is removed and replaced by one direct trend segment.
    Spike peaks and sideways structure are never deleted.
    """
    if angle_threshold_deg <= 0 or angle_threshold_deg >= 180:
        raise ValueError("angle_threshold_deg must be between 0 and 180")

    def key(point: PivotPoint) -> tuple[date, float, str]:
        return point.day, point.value, point.pivot_type

    # This stage may only inspect points that survived the immediately previous
    # stage. Never re-inject points from augmented/base/raw candidate sets.
    point_map = {key(marker): marker for marker in result.markers}
    points = tuple(sorted(
        point_map.values(),
        key=lambda item: (item.day, item.pivot_type),
    ))

    segment_endpoint_keys = {
        key(point)
        for segment in result.segments
        for point in (segment.start, segment.end)
    }
    # Standalone markers are points intentionally emitted by the previous stage
    # without a connecting segment (for example marker-only sideways spikes).
    # They remain protected, but protection never prevents them from acting as
    # an anchor if they are otherwise a valid surviving point.
    standalone_marker_keys = {
        key(marker)
        for marker in result.markers
        if key(marker) not in segment_endpoint_keys
    }
    highs = tuple(item for item in points if item.pivot_type == "high")
    lows = tuple(item for item in points if item.pivot_type == "low")

    # A spike still stops the active trend at its start. A sideways segment is
    # different: both boundary pivots remain part of the just-finished trend, so
    # the 10-degree scan may inspect through the sideways END but never beyond it.
    hard_boundaries = tuple(sorted(
        (
            segment.start.day
            if segment.kind == "spike"
            else segment.end.day
        )
        for segment in result.segments
        if segment.kind in {"sideways", "spike"}
    ))

    def first_boundary_after(day: date) -> date | None:
        return next((item for item in hard_boundaries if item > day), None)

    # Only a fully confirmed trend reversal may create a NEW anchor.
    # The chart's first surviving point is the initial anchor. After that:
    #   down -> up: a low survives a rebound, the next low stays above it,
    #               and a later high exceeds the rebound high.
    #   up -> down: exact mirror.
    # A failed reversal candidate is discarded as soon as the old trend makes
    # a new extreme. Sideways may sit between the old and new trends; when a
    # reversal is confirmed across sideways, the sideways END is the new anchor.
    ordered_points = list(points)
    sideways_segments_sorted = tuple(sorted(
        result.sideways_segments,
        key=lambda item: (item.start.day, item.end.day),
    ))

    def sideways_end_between(start_day: date, confirm_day: date) -> PivotPoint | None:
        matches = [
            item.end
            for item in sideways_segments_sorted
            if start_day <= item.start.day
            and item.end.day <= confirm_day
        ]
        return matches[-1] if matches else None

    def initial_direction() -> str | None:
        if len(ordered_points) < 2:
            return None
        first = ordered_points[0]
        for item in ordered_points[1:]:
            if item.value > first.value:
                return "up"
            if item.value < first.value:
                return "down"
        return None

    run_starts: list[tuple[PivotPoint, str]] = []
    direction = initial_direction()
    if ordered_points and direction is not None:
        run_starts.append((ordered_points[0], direction))

        anchor_index = 0
        scan_index = 1
        while scan_index < len(ordered_points):
            if direction == "down":
                trend_low: PivotPoint | None = None
                rebound_high: PivotPoint | None = None
                higher_low_seen = False
                confirmed = False

                while scan_index < len(ordered_points):
                    item = ordered_points[scan_index]
                    if item.pivot_type == "low":
                        if trend_low is None or item.value < trend_low.value:
                            trend_low = item
                            rebound_high = None
                            higher_low_seen = False
                        elif rebound_high is not None and item.value > trend_low.value:
                            higher_low_seen = True
                    else:
                        if trend_low is not None:
                            if rebound_high is None:
                                rebound_high = item
                            elif higher_low_seen and item.value > rebound_high.value:
                                new_anchor = sideways_end_between(
                                    trend_low.day,
                                    item.day,
                                ) or trend_low
                                run_starts.append((new_anchor, "up"))
                                direction = "up"
                                anchor_index = ordered_points.index(new_anchor)
                                scan_index = anchor_index + 1
                                confirmed = True
                                break
                            elif item.value > rebound_high.value:
                                rebound_high = item
                    scan_index += 1

                if not confirmed:
                    break
                continue

            trend_high: PivotPoint | None = None
            pullback_low: PivotPoint | None = None
            lower_high_seen = False
            confirmed = False

            while scan_index < len(ordered_points):
                item = ordered_points[scan_index]
                if item.pivot_type == "high":
                    if trend_high is None or item.value > trend_high.value:
                        trend_high = item
                        pullback_low = None
                        lower_high_seen = False
                    elif pullback_low is not None and item.value < trend_high.value:
                        lower_high_seen = True
                else:
                    if trend_high is not None:
                        if pullback_low is None:
                            pullback_low = item
                        elif lower_high_seen and item.value < pullback_low.value:
                            new_anchor = sideways_end_between(
                                trend_high.day,
                                item.day,
                            ) or trend_high
                            run_starts.append((new_anchor, "down"))
                            direction = "down"
                            anchor_index = ordered_points.index(new_anchor)
                            scan_index = anchor_index + 1
                            confirmed = True
                            break
                        elif item.value < pullback_low.value:
                            pullback_low = item
                scan_index += 1

            if not confirmed:
                break

    # A point may be encountered again when a sideways END becomes the confirmed
    # reversal anchor. Keep only its latest confirmed direction.
    run_start_map = {
        key(anchor): (anchor, run_direction)
        for anchor, run_direction in run_starts
    }
    run_starts = sorted(
        run_start_map.values(),
        key=lambda item: item[0].day,
    )

    replacement_intervals: list[tuple[PivotPoint, PivotPoint]] = []

    def already_inside(day: date) -> bool:
        return any(start.day < day < end.day for start, end in replacement_intervals)

    for anchor, direction in run_starts:
        if already_inside(anchor.day):
            continue

        boundary = first_boundary_after(anchor.day)
        same_side = highs if direction == "up" else lows
        candidates = [
            item for item in same_side
            if item.day > anchor.day
            and (boundary is None or item.day <= boundary)
        ]
        if not candidates:
            continue

        # Connection candidates and angle ordinals are intentionally separate.
        #
        # A lower high during an up-run (or a higher low during a down-run) is
        # NOT a new connection extreme, but it still consumes an angle ordinal.
        # Therefore the first visible angle is ignored regardless of size, while
        # the second and later visible angles are subject to the 10-degree rule.
        #
        # Example (up-run): H1 -> lower H2 -> higher H3
        #   - H2 is skipped as a connection extreme
        #   - angle H1-anchor-H2 is still angle #1 and is ignored
        #   - angle H1-anchor-H3 is angle #2 and must pass the threshold
        #
        # Existing simplify_pivot_lines() already handles provisional 100%+
        # retracement/reversal structure by following the later same-side extreme;
        # do not duplicate that state machine here.
        extreme = candidates[0]
        angle_ordinal = 0
        for candidate in candidates[1:]:
            angle_ordinal += 1
            angle = screen_origin_angle_degrees(
                anchor,
                extreme,
                candidate,
                geometry,
            )

            # Angle #1 never stops the run. Angle #2 onward does.
            if angle_ordinal >= 2 and angle > angle_threshold_deg:
                break

            improves = (
                candidate.value > extreme.value
                if direction == "up"
                else candidate.value < extreme.value
            )
            if improves:
                extreme = candidate

        # No later same-side candidate improved on the first extreme, so there is
        # nothing to collapse to. The first angle never blocks simplification:
        # when the next candidate improves the extreme, collapse through it
        # immediately. The 10-degree rule starts with angle #2.
        if extreme == candidates[0]:
            continue

        sideways_keys = {
            key(point)
            for sideways in result.sideways_segments
            for point in sideways.pivot_points
        }
        structure_protected_keys = standalone_marker_keys | sideways_keys

        # The first connection extreme disappears when we collapse to the final
        # extreme. Preserve it when it is a protected spike/sideways boundary.
        # The final extreme itself may be protected because it remains the endpoint.
        if key(candidates[0]) in structure_protected_keys:
            continue

        if anchor.day >= extreme.day:
            continue
        if boundary is not None and extreme.day > boundary:
            continue

        # Spike peaks and both sideways boundary pivots are always preserved.
        # A collapse may not cross any of those protected structure points.
        protected_structure_points = [
            item for item in points
            if key(item) in structure_protected_keys
        ]
        if any(
            anchor.day < marker.day < extreme.day
            for marker in protected_structure_points
        ):
            continue

        interior_points = [
            item for item in points
            if anchor.day < item.day < extreme.day
            and key(item) not in structure_protected_keys
        ]
        if not interior_points:
            continue

        replacement_intervals.append((anchor, extreme))

    if not replacement_intervals:
        return result

    replacement_intervals.sort(key=lambda item: (item[0].day, item[1].day))
    non_overlapping: list[tuple[PivotPoint, PivotPoint]] = []
    for start, end in replacement_intervals:
        if non_overlapping and start.day < non_overlapping[-1][1].day:
            continue
        non_overlapping.append((start, end))
    replacement_intervals = non_overlapping

    def inside_interval(segment: SimplifiedLineSegment) -> bool:
        return any(
            start.day <= segment.start.day
            and segment.end.day <= end.day
            for start, end in replacement_intervals
        )

    kept_segments = [
        segment for segment in result.segments
        if not inside_interval(segment)
    ]
    for start, end in replacement_intervals:
        kept_segments.append(
            SimplifiedLineSegment(start=start, end=end, kind="trend")
        )
    kept_segments.sort(key=lambda item: (item.start.day, item.end.day, item.kind))

    marker_map: dict[tuple[date, float, str], PivotPoint] = {}
    for segment in kept_segments:
        marker_map[key(segment.start)] = segment.start
        marker_map[key(segment.end)] = segment.end
    for marker in result.markers:
        if key(marker) in standalone_marker_keys:
            marker_map[key(marker)] = marker

    # Deletion-only invariant: this stage may never resurrect a point that the
    # previous stage did not return.
    if not set(marker_map).issubset(point_map):
        raise RuntimeError("prune_same_trend_extremes resurrected a prior-stage point")

    return SimplifiedLineResult(
        markers=tuple(sorted(
            marker_map.values(),
            key=lambda item: (item.day, item.pivot_type),
        )),
        segments=tuple(kept_segments),
        sideways_segments=result.sideways_segments,
    )



def prune_unconfirmed_retracements(
    result: SimplifiedLineResult,
) -> SimplifiedLineResult:
    """Final deletion-only cleanup over the completed 10-degree result.

    This stage sees ONLY points that survived the previous stage. Confirmed trend
    reversal anchors from that result are protected; cleanup is limited to the
    interiors between those anchors.

    Repeatedly inside each anchor interval:
      - consecutive lows keep only the lower low
      - consecutive highs keep only the higher high
      - low -> high -> lower low removes the failed high reversal
      - high -> low -> higher high removes the failed low reversal

    Confirmed sideways/spike structure and standalone marker-only points are also
    protected. No prior-stage point can ever be restored.
    """
    def key(point: PivotPoint) -> tuple[date, float, str]:
        return point.day, point.value, point.pivot_type

    input_map = {key(point): point for point in result.markers}
    original = list(sorted(
        result.markers,
        key=lambda item: (item.day, item.pivot_type),
    ))
    if len(original) < 2:
        return result

    standalone_keys = {
        key(marker)
        for marker in result.markers
        if not any(
            key(marker) in {key(segment.start), key(segment.end)}
            for segment in result.segments
        )
    }
    sideways_keys = {
        key(point)
        for sideways in result.sideways_segments
        for point in sideways.pivot_points
    }
    spike_keys = {
        key(point)
        for segment in result.segments
        if segment.kind == "spike"
        for point in (segment.start, segment.end)
    }

    def confirmed_reversal_keys(points: Sequence[PivotPoint]) -> set[tuple[date, float, str]]:
        """Find only fully confirmed reversals in the current-stage point set."""
        confirmed: set[tuple[date, float, str]] = set()
        if points:
            confirmed.add(key(points[0]))

        for index, pivot in enumerate(points[:-1]):
            if pivot.pivot_type == "low":
                rebound_high: PivotPoint | None = None
                higher_low_seen = False
                for item in points[index + 1:]:
                    if item.pivot_type == "low":
                        if item.value < pivot.value:
                            break
                        if rebound_high is not None and item.value > pivot.value:
                            higher_low_seen = True
                        continue

                    if rebound_high is None:
                        rebound_high = item
                        continue
                    if higher_low_seen and item.value > rebound_high.value:
                        confirmed.add(key(pivot))
                        break
                    if not higher_low_seen and item.value > rebound_high.value:
                        rebound_high = item
                continue

            pullback_low: PivotPoint | None = None
            lower_high_seen = False
            for item in points[index + 1:]:
                if item.pivot_type == "high":
                    if item.value > pivot.value:
                        break
                    if pullback_low is not None and item.value < pivot.value:
                        lower_high_seen = True
                    continue

                if pullback_low is None:
                    pullback_low = item
                    continue
                if lower_high_seen and item.value < pullback_low.value:
                    confirmed.add(key(pivot))
                    break
                if not lower_high_seen and item.value < pullback_low.value:
                    pullback_low = item

        return confirmed

    confirmed_anchor_keys = confirmed_reversal_keys(original)
    protected_keys = (
        standalone_keys
        | sideways_keys
        | spike_keys
        | confirmed_anchor_keys
    )

    ordered = list(original)

    # One extra normalization pass only. Do not recursively re-simplify the newly
    # shortened path; the previous stages already own those broader decisions.
    same_side_delete: set[tuple[date, float, str]] = set()
    for left, right in zip(original, original[1:]):
        if left.pivot_type != right.pivot_type:
            continue

        if left.pivot_type == "low":
            candidate = left if right.value < left.value else right
        else:
            candidate = left if right.value > left.value else right

        if key(candidate) not in protected_keys:
            same_side_delete.add(key(candidate))

    ordered = [
        point for point in ordered
        if key(point) not in same_side_delete
    ]

    failed_reversal_delete: set[tuple[date, float, str]] = set()
    for left, middle, right in zip(ordered, ordered[1:], ordered[2:]):
        if left.pivot_type != right.pivot_type or middle.pivot_type == left.pivot_type:
            continue
        if key(middle) in protected_keys:
            continue
        if left.pivot_type == "low" and right.value < left.value:
            failed_reversal_delete.add(key(middle))
        elif left.pivot_type == "high" and right.value > left.value:
            failed_reversal_delete.add(key(middle))

    ordered = [
        point for point in ordered
        if key(point) not in failed_reversal_delete
    ]

    surviving_keys = {key(point) for point in ordered}
    if not surviving_keys.issubset(input_map):
        raise RuntimeError("final cleanup resurrected a prior-stage point")

    exact_kind = {
        (key(segment.start), key(segment.end)): segment.kind
        for segment in result.segments
    }
    line_points = [point for point in ordered if key(point) not in standalone_keys]
    rebuilt_segments: list[SimplifiedLineSegment] = []
    for start_point, end_point in zip(line_points, line_points[1:]):
        kind = exact_kind.get((key(start_point), key(end_point)), "trend")
        rebuilt_segments.append(
            SimplifiedLineSegment(
                start=start_point,
                end=end_point,
                kind=kind,
            )
        )

    marker_map = {key(point): point for point in ordered}
    surviving_sideways = tuple(
        segment
        for segment in result.sideways_segments
        if key(segment.start) in marker_map and key(segment.end) in marker_map
    )

    return SimplifiedLineResult(
        markers=tuple(sorted(
            marker_map.values(),
            key=lambda item: (item.day, item.pivot_type),
        )),
        segments=tuple(rebuilt_segments),
        sideways_segments=surviving_sideways,
    )


def simplify_pivot_lines(
    augmented: SpikeAugmentedPivotResult,
    geometry: ChartGeometry,
) -> SimplifiedLineResult:
    """Simplify the line path in chronological order.

    Rules:
      - uptrend starts low -> high, then continues high -> high
      - when the next high is lower, the PREVIOUS high owns the down reversal
      - that previous high connects to the first later low that is NOT itself
        a low-side up->down reversal point
      - downtrend is the exact mirror
      - sideways keeps the same side as the prior trend
      - overlapping opposite-side sideways pivots are removed before later links
      - spike entry interrupts the current line, entry -> peak is drawn, then restart
    """
    original_highs = tuple(sorted(augmented.high_pivots, key=lambda item: item.day))
    original_lows = tuple(sorted(augmented.low_pivots, key=lambda item: item.day))
    if not original_highs or not original_lows:
        return SimplifiedLineResult(markers=(), segments=(), sideways_segments=())

    marker_only_spikes = tuple(
        item for item in augmented.spike_peaks if item.marker_only
    )
    marker_only_keys = {
        (item.point.day, item.point.value, item.point.pivot_type)
        for item in marker_only_spikes
    }
    line_highs = tuple(
        item for item in original_highs
        if (item.day, item.value, item.pivot_type) not in marker_only_keys
    )
    line_lows = tuple(
        item for item in original_lows
        if (item.day, item.value, item.pivot_type) not in marker_only_keys
    )

    spikes = tuple(sorted(
        (
            item for item in augmented.spike_peaks
            if not item.marker_only and item.entry is not None
        ),
        key=lambda item: item.entry.day,
    ))

    def point_key(point: PivotPoint) -> tuple[date, float, str]:
        return point.day, point.value, point.pivot_type

    def sideways_pairs(
        points: Sequence[PivotPoint],
        prior_trend: str,
    ) -> tuple[SidewaysSegment, ...]:
        found: list[SidewaysSegment] = []
        for left, right in zip(points, points[1:]):
            segment = classify_sideways_reference_line(
                left, right, prior_trend, geometry,
            )
            if segment is not None:
                found.append(segment)
        return tuple(found)

    high_sideways = sideways_pairs(line_highs, "up")
    low_sideways = sideways_pairs(line_lows, "down")
    removed_keys: set[tuple[date, float, str]] = set()

    def overlaps(left: SidewaysSegment, right: SidewaysSegment) -> bool:
        return left.start.day <= right.end.day and right.start.day <= left.end.day

    def remove_opposite_sideways(selected: SidewaysSegment) -> None:
        opposite = high_sideways if selected.reference_side == "low" else low_sideways
        for other in opposite:
            if overlaps(selected, other):
                removed_keys.add(point_key(other.start))
                removed_keys.add(point_key(other.end))

    highs = list(original_highs)
    lows = list(original_lows)
    consumed_spikes: set[tuple[date, float, str]] = set()
    segments: list[SimplifiedLineSegment] = []
    sideways_segments: list[SidewaysSegment] = []

    def refresh_points() -> None:
        nonlocal highs, lows
        highs = [item for item in line_highs if point_key(item) not in removed_keys]
        lows = [item for item in line_lows if point_key(item) not in removed_keys]

    def add_segment(start: PivotPoint, end: PivotPoint, kind: str) -> None:
        if start.day >= end.day:
            return
        key = (
            start.day, start.value, start.pivot_type,
            end.day, end.value, end.pivot_type, kind,
        )
        if any(
            (
                item.start.day, item.start.value, item.start.pivot_type,
                item.end.day, item.end.value, item.end.pivot_type, item.kind,
            ) == key
            for item in segments
        ):
            return
        segments.append(SimplifiedLineSegment(start=start, end=end, kind=kind))

    def next_after(points: Sequence[PivotPoint], after: date) -> PivotPoint | None:
        return next((item for item in points if item.day > after), None)

    def previous_before(points: Sequence[PivotPoint], before: date) -> PivotPoint | None:
        previous = [item for item in points if item.day < before]
        return previous[-1] if previous else None

    def is_same_direction_turn(
        candidate: PivotPoint,
        points: Sequence[PivotPoint],
        wanted_direction: str,
    ) -> bool:
        """Candidate is the actual same-side turning vertex into wanted_direction."""
        ordered = [item for item in points if item.day <= candidate.day]
        if len(ordered) < 3 or ordered[-1] != candidate:
            return False
        left, pivot, right = ordered[-3], ordered[-2], ordered[-1]

        # The TURN lives at the middle point. We are testing whether candidate
        # is that turning point, so candidate needs a point AFTER it.
        after = next_after(points, candidate.day)
        before = previous_before(points, candidate.day)
        if before is None or after is None:
            return False

        if candidate.pivot_type == "low":
            if wanted_direction == "down":
                return before.value < candidate.value and after.value < candidate.value
            return before.value > candidate.value and after.value > candidate.value

        if wanted_direction == "down":
            return before.value < candidate.value and after.value < candidate.value
        return before.value > candidate.value and after.value > candidate.value

    def next_valid_opposite(
        points: Sequence[PivotPoint],
        after: date,
        wanted_direction: str,
    ) -> PivotPoint | None:
        candidate = next_after(points, after)
        while candidate is not None and is_same_direction_turn(
            candidate,
            points,
            wanted_direction,
        ):
            candidate = next_after(points, candidate.day)
        return candidate

    def next_spike_before(after: date, before: date | None) -> SpikePeak | None:
        for spike in spikes:
            key = (spike.point.day, spike.point.value, spike.direction)
            if key in consumed_spikes or spike.entry is None:
                continue
            if spike.entry.day <= after:
                continue
            if before is not None and spike.entry.day > before:
                continue
            if point_key(spike.entry) in removed_keys or point_key(spike.point) in removed_keys:
                continue
            return spike
        return None

    refresh_points()
    first_high = highs[0]
    first_low = lows[0]

    if first_low.day < first_high.day:
        direction = "up"
        anchor = first_low
        current_same_side = next_after(highs, anchor.day)
    else:
        direction = "down"
        anchor = first_high
        current_same_side = next_after(lows, anchor.day)

    if current_same_side is None:
        return SimplifiedLineResult(markers=(), segments=(), sideways_segments=())

    # Initial cross-side leg.
    add_segment(anchor, current_same_side, "trend")
    anchor = current_same_side

    while True:
        refresh_points()

        if direction == "up":
            next_high = next_after(highs, anchor.day)
            if next_high is None:
                break

            # A spike/restart can leave the current anchor on the opposite side.
            # Complete only that first cross-side leg, then resume high -> high.
            if anchor.pivot_type == "low":
                add_segment(anchor, next_high, "trend")
                anchor = next_high
                continue

            spike = next_spike_before(anchor.day, next_high.day)
            if spike is not None and spike.entry is not None:
                add_segment(anchor, spike.entry, "trend")
                add_segment(spike.entry, spike.point, "spike")
                consumed_spikes.add((spike.point.day, spike.point.value, spike.direction))
                anchor = spike.point
                direction = "down" if spike.point.pivot_type == "high" else "up"
                continue

            sideways = classify_sideways_reference_line(anchor, next_high, "up", geometry)
            if sideways is not None:
                remove_opposite_sideways(sideways)
                refresh_points()
                add_segment(anchor, next_high, "sideways")
                sideways_segments.append(sideways)
                anchor = next_high
                continue

            if next_high.value > anchor.value:
                add_segment(anchor, next_high, "trend")
                anchor = next_high
                continue

            # next_high is lower: anchor is the actual high-side reversal owner.
            reversal_owner = anchor
            low_candidate = next_valid_opposite(lows, reversal_owner.day, "down")
            if low_candidate is None:
                break

            spike = next_spike_before(reversal_owner.day, low_candidate.day)
            if spike is not None and spike.entry is not None:
                add_segment(reversal_owner, spike.entry, "trend")
                add_segment(spike.entry, spike.point, "spike")
                consumed_spikes.add((spike.point.day, spike.point.value, spike.direction))
                anchor = spike.point
                direction = "down" if spike.point.pivot_type == "high" else "up"
                continue

            add_segment(reversal_owner, low_candidate, "trend")
            anchor = low_candidate
            direction = "down"
            continue

        next_low = next_after(lows, anchor.day)
        if next_low is None:
            break

        # A spike/restart can leave the current anchor on the opposite side.
        # Complete only that first cross-side leg, then resume low -> low.
        if anchor.pivot_type == "high":
            add_segment(anchor, next_low, "trend")
            anchor = next_low
            continue

        spike = next_spike_before(anchor.day, next_low.day)
        if spike is not None and spike.entry is not None:
            add_segment(anchor, spike.entry, "trend")
            add_segment(spike.entry, spike.point, "spike")
            consumed_spikes.add((spike.point.day, spike.point.value, spike.direction))
            anchor = spike.point
            direction = "down" if spike.point.pivot_type == "high" else "up"
            continue

        sideways = classify_sideways_reference_line(anchor, next_low, "down", geometry)
        if sideways is not None:
            remove_opposite_sideways(sideways)
            refresh_points()
            add_segment(anchor, next_low, "sideways")
            sideways_segments.append(sideways)
            anchor = next_low
            continue

        if next_low.value < anchor.value:
            add_segment(anchor, next_low, "trend")
            anchor = next_low
            continue

        # next_low is higher: anchor is the actual low-side reversal owner.
        reversal_owner = anchor
        high_candidate = next_valid_opposite(highs, reversal_owner.day, "up")
        if high_candidate is None:
            break

        spike = next_spike_before(reversal_owner.day, high_candidate.day)
        if spike is not None and spike.entry is not None:
            add_segment(reversal_owner, spike.entry, "trend")
            add_segment(spike.entry, spike.point, "spike")
            consumed_spikes.add((spike.point.day, spike.point.value, spike.direction))
            anchor = spike.point
            direction = "down" if spike.point.pivot_type == "high" else "up"
            continue

        add_segment(reversal_owner, high_candidate, "trend")
        anchor = high_candidate
        direction = "up"

    segments.sort(key=lambda item: (item.start.day, item.end.day, item.kind))
    sideways_segments.sort(key=lambda item: (item.start.day, item.end.day))

    used_markers: dict[tuple[date, float, str], PivotPoint] = {}
    for segment in segments:
        used_markers[point_key(segment.start)] = segment.start
        used_markers[point_key(segment.end)] = segment.end
    for spike in marker_only_spikes:
        used_markers[point_key(spike.point)] = spike.point

    simplified = SimplifiedLineResult(
        markers=tuple(sorted(
            used_markers.values(),
            key=lambda item: (item.day, item.pivot_type),
        )),
        segments=tuple(segments),
        sideways_segments=tuple(sideways_segments),
    )
    angle_pruned = prune_same_trend_extremes(
        simplified,
        geometry,
    )
    return prune_unconfirmed_retracements(angle_pruned)

def _single_row(
    db: SupabaseRest,
    table: str,
    params: dict[str, str],
) -> dict[str, Any]:
    rows = db.request("GET", table, params=params) or []
    if len(rows) != 1:
        raise RuntimeError(f"expected exactly one {table} row, got {len(rows)}")
    return rows[0]


def _get_rows_paginated(
    db: SupabaseRest,
    table: str,
    params: dict[str, str],
    *,
    page_size: int = SUPABASE_REST_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """Read every REST row without relying on Supabase's per-request row cap."""
    if page_size <= 0:
        raise ValueError("page_size must be positive")

    base_params = dict(params)
    base_params.pop("limit", None)
    base_params.pop("offset", None)
    rows: list[dict[str, Any]] = []
    offset = 0

    while True:
        page_params = {
            **base_params,
            "limit": str(page_size),
            "offset": str(offset),
        }
        page = db.request("GET", table, params=page_params) or []
        if not isinstance(page, list):
            raise RuntimeError(f"expected a list from {table}, got {type(page).__name__}")
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += len(page)


def load_case_series(
    db: SupabaseRest,
    *,
    case_code: str,
    index_code: str,
    series_code: str,
) -> tuple[tuple[dict[str, Any], ...], str, date, date]:
    cycle = _single_row(db, "historical_case_market_cycles", {
        "select": "start_date,trough_date",
        "case_code": f"eq.{case_code}",
        "index_code": f"eq.{index_code}",
        "limit": "2",
    })
    if not cycle.get("start_date") or not cycle.get("trough_date"):
        raise RuntimeError("Historical cycle requires both START and TROUGH")
    cycle_start = date.fromisoformat(str(cycle["start_date"])[:10])
    cycle_trough = date.fromisoformat(str(cycle["trough_date"])[:10])
    buffer_start, buffer_end = buffer_bounds(cycle_start, cycle_trough)

    rows = _get_rows_paginated(db, "economic_chart_points", {
        "select": "observation_date,value,frequency",
        "series_code": f"eq.{series_code}",
        "observation_date": f"gte.{buffer_start.isoformat()}",
        "and": f"(observation_date.lte.{buffer_end.isoformat()})",
        "order": "observation_date.asc",
    })
    if not rows:
        raise RuntimeError(f"No economic-chart rows for {series_code} in buffer range")
    frequencies = {str(row.get("frequency") or "") for row in rows}
    if len(frequencies) != 1:
        raise RuntimeError(
            f"{series_code} has inconsistent frequencies in buffer range: {sorted(frequencies)}"
        )
    frequency = next(iter(frequencies))
    if frequency not in PIVOT_POLICIES:
        raise RuntimeError(
            f"{series_code} frequency {frequency} is not part of this base pipeline"
        )
    return tuple(rows), frequency, buffer_start, buffer_end


def calculate_case_series(
    db: SupabaseRest,
    *,
    case_code: str,
    index_code: str,
    series_code: str,
) -> tuple[BasePivotResult, date, date]:
    rows, frequency, buffer_start, buffer_end = load_case_series(
        db,
        case_code=case_code,
        index_code=index_code,
        series_code=series_code,
    )
    return calculate_base_pivots(rows, frequency), buffer_start, buffer_end


def frontend_payload(
    result: BasePivotResult,
    *,
    case_code: str,
    index_code: str,
    series_code: str,
    buffer_start: date,
    buffer_end: date,
) -> dict[str, Any]:
    """Serialize marker-only output; raw chart points remain owned by the frontend data path."""
    return {
        "case_code": case_code,
        "index_code": index_code,
        "series_code": series_code,
        "frequency": result.frequency,
        "buffer_start": buffer_start.isoformat(),
        "buffer_end": buffer_end.isoformat(),
        "markers": [
            {
                "pivot_date": marker.day.isoformat(),
                "pivot_value": marker.value,
                "pivot_type": marker.pivot_type,
            }
            for marker in result.display_markers
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calculate base Historical Insight pivot markers without changing source rows."
    )
    parser.add_argument("--case-code", required=True)
    parser.add_argument("--index-code", required=True)
    parser.add_argument("--series-code", required=True)
    args = parser.parse_args()

    result, buffer_start, buffer_end = calculate_case_series(
        SupabaseRest(),
        case_code=args.case_code,
        index_code=args.index_code,
        series_code=args.series_code,
    )
    print(json.dumps(frontend_payload(
        result,
        case_code=args.case_code,
        index_code=args.index_code,
        series_code=args.series_code,
        buffer_start=buffer_start,
        buffer_end=buffer_end,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
