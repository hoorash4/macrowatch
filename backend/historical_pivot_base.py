"""Base Historical Insight pivot extraction.

The approved base pipeline is:
raw economic-chart points -> centered envelope -> plateau extrema -> fixed-count RDP.

Optional spike entry-point augmentation is a separate post-processing step. It never
replaces or removes the base RDP pivots.

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
    """Base RDP pivots plus additive spike-entry markers only."""

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
    """Add only missing spike entry points while preserving every base RDP pivot.

    Upward spike:
      - consecutive upper-RDP A-P-C with P above A/C
      - P is inside the visible case range and its downward-facing angle is < threshold
      - lower-RDP pivots may exist from A through C, but none may sit above
        max(A, C); such a lower pivot means the lower boundary followed the peak upward
      - D, the first lower-RDP pivot after C, exists
      - reuse the lowest existing lower-RDP point strictly between A and P when present;
        otherwise add the lowest lower-plateau candidate there

    Downward spike is the exact high/low mirror.
    D is already a base RDP pivot, so it is used as the spike exit and is not added again.
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
        )

    return SpikeAugmentedPivotResult(
        base=base,
        added_high_pivots=tuple(sorted(added_high.values(), key=lambda item: item.day)),
        added_low_pivots=tuple(sorted(added_low.values(), key=lambda item: item.day)),
        spike_peaks=tuple(sorted(spike_peaks.values(), key=lambda item: item.point.day)),
    )



def simplify_pivot_lines(
    augmented: SpikeAugmentedPivotResult,
    geometry: ChartGeometry,
) -> SimplifiedLineResult:
    """Simplify the line path after removing the unused side of confirmed sideways.

    Rules:
      - uptrend starts low -> high, then continues high -> high
      - downtrend starts high -> low, then continues low -> low
      - sideways keeps the same side as the prior trend:
        uptrend => high -> high, downtrend => low -> low
      - when one sideways side is selected, the overlapping opposite-side sideways
        pair is removed first, so those unused pivots cannot be used by later lines
      - a spike interrupts the current line at its entry, draws entry -> peak,
        then restarts from the peak
    """
    original_highs = tuple(sorted(augmented.high_pivots, key=lambda item: item.day))
    original_lows = tuple(sorted(augmented.low_pivots, key=lambda item: item.day))
    if not original_highs or not original_lows:
        return SimplifiedLineResult(markers=(), segments=(), sideways_segments=())

    spikes = tuple(sorted(
        (item for item in augmented.spike_peaks if item.entry is not None),
        key=lambda item: item.entry.day,
    ))

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

    high_sideways = sideways_pairs(original_highs, "up")
    low_sideways = sideways_pairs(original_lows, "down")

    removed_keys: set[tuple[date, float, str]] = set()

    def point_key(point: PivotPoint) -> tuple[date, float, str]:
        return point.day, point.value, point.pivot_type

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
        highs = [item for item in original_highs if point_key(item) not in removed_keys]
        lows = [item for item in original_lows if point_key(item) not in removed_keys]

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

    def turns_into_same_direction(
        candidate: PivotPoint,
        points: Sequence[PivotPoint],
        direction_to_check: str,
    ) -> bool:
        """Return whether candidate itself is a same-side reversal into the given direction."""
        ordered = [item for item in points if item.day <= candidate.day]
        if len(ordered) < 3 or ordered[-1] != candidate:
            return False
        before_previous, previous, current = ordered[-3], ordered[-2], ordered[-1]
        previous_delta = previous.value - before_previous.value
        current_delta = current.value - previous.value
        if previous_delta == 0 or current_delta == 0:
            return False
        previous_direction = "up" if previous_delta > 0 else "down"
        current_direction = "up" if current_delta > 0 else "down"
        return (
            previous_direction != current_direction
            and current_direction == direction_to_check
        )

    def next_valid_opposite(
        points: Sequence[PivotPoint],
        after: date,
        direction_to_check: str,
    ) -> PivotPoint | None:
        candidate = next_after(points, after)
        while candidate is not None and turns_into_same_direction(
            candidate,
            points,
            direction_to_check,
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
        target = next_after(highs, anchor.day)
    else:
        direction = "down"
        anchor = first_high
        target = next_after(lows, anchor.day)

    if target is None:
        return SimplifiedLineResult(markers=(), segments=(), sideways_segments=())

    while target is not None:
        spike = next_spike_before(anchor.day, target.day)
        if spike is not None and spike.entry is not None:
            add_segment(anchor, spike.entry, "trend")
            add_segment(spike.entry, spike.point, "spike")
            consumed_spikes.add((spike.point.day, spike.point.value, spike.direction))
            anchor = spike.point
            direction = "down" if spike.point.pivot_type == "high" else "up"
            refresh_points()
            target = (
                next_after(lows, anchor.day)
                if direction == "down"
                else next_after(highs, anchor.day)
            )
            continue

        if direction == "up":
            if anchor.pivot_type == "low":
                add_segment(anchor, target, "trend")
                anchor = target
                target = next_after(highs, anchor.day)
                continue

            sideways = classify_sideways_reference_line(anchor, target, "up", geometry)
            if sideways is not None:
                remove_opposite_sideways(sideways)
                refresh_points()
                add_segment(anchor, target, "sideways")
                sideways_segments.append(sideways)
                anchor = target
                target = next_after(highs, anchor.day)
                continue

            if target.value > anchor.value:
                add_segment(anchor, target, "trend")
                anchor = target
                target = next_after(highs, anchor.day)
                continue

            reversal_low = next_valid_opposite(lows, anchor.day, "down")
            if reversal_low is None:
                break
            spike = next_spike_before(anchor.day, reversal_low.day)
            if spike is not None and spike.entry is not None:
                add_segment(anchor, spike.entry, "trend")
                add_segment(spike.entry, spike.point, "spike")
                consumed_spikes.add((spike.point.day, spike.point.value, spike.direction))
                anchor = spike.point
                direction = "down" if spike.point.pivot_type == "high" else "up"
                refresh_points()
                target = (
                    next_after(lows, anchor.day)
                    if direction == "down"
                    else next_after(highs, anchor.day)
                )
                continue
            add_segment(anchor, reversal_low, "trend")
            anchor = reversal_low
            direction = "down"
            target = next_after(lows, anchor.day)
            continue

        if anchor.pivot_type == "high":
            add_segment(anchor, target, "trend")
            anchor = target
            target = next_after(lows, anchor.day)
            continue

        sideways = classify_sideways_reference_line(anchor, target, "down", geometry)
        if sideways is not None:
            remove_opposite_sideways(sideways)
            refresh_points()
            add_segment(anchor, target, "sideways")
            sideways_segments.append(sideways)
            anchor = target
            target = next_after(lows, anchor.day)
            continue

        if target.value < anchor.value:
            add_segment(anchor, target, "trend")
            anchor = target
            target = next_after(lows, anchor.day)
            continue

        reversal_high = next_valid_opposite(highs, anchor.day, "up")
        if reversal_high is None:
            break
        spike = next_spike_before(anchor.day, reversal_high.day)
        if spike is not None and spike.entry is not None:
            add_segment(anchor, spike.entry, "trend")
            add_segment(spike.entry, spike.point, "spike")
            consumed_spikes.add((spike.point.day, spike.point.value, spike.direction))
            anchor = spike.point
            direction = "down" if spike.point.pivot_type == "high" else "up"
            refresh_points()
            target = (
                next_after(lows, anchor.day)
                if direction == "down"
                else next_after(highs, anchor.day)
            )
            continue
        add_segment(anchor, reversal_high, "trend")
        anchor = reversal_high
        direction = "up"
        target = next_after(highs, anchor.day)

    segments.sort(key=lambda item: (item.start.day, item.end.day, item.kind))
    sideways_segments.sort(key=lambda item: (item.start.day, item.end.day))

    used_markers: dict[tuple[date, float, str], PivotPoint] = {}
    for segment in segments:
        used_markers[point_key(segment.start)] = segment.start
        used_markers[point_key(segment.end)] = segment.end

    return SimplifiedLineResult(
        markers=tuple(sorted(
            used_markers.values(),
            key=lambda item: (item.day, item.pivot_type),
        )),
        segments=tuple(segments),
        sideways_segments=tuple(sideways_segments),
    )

def _single_row(
    db: SupabaseRest,
    table: str,
    params: dict[str, str],
) -> dict[str, Any]:
    rows = db.request("GET", table, params=params) or []
    if len(rows) != 1:
        raise RuntimeError(f"expected exactly one {table} row, got {len(rows)}")
    return rows[0]


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

    rows = db.request("GET", "economic_chart_points", params={
        "select": "observation_date,value,frequency",
        "series_code": f"eq.{series_code}",
        "observation_date": f"gte.{buffer_start.isoformat()}",
        "and": f"(observation_date.lte.{buffer_end.isoformat()})",
        "order": "observation_date.asc",
        "limit": "10000",
    }) or []
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
