"""Stage 1: build the finished upper/lower RDP pivot sets.

Stage 1 is intentionally limited to:
- envelope construction
- plateau extrema extraction
- fixed-count RDP selection

No spike, rapid-move, sideways, protection, or wave-merging logic belongs here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Sequence

from historical_pivot_shared import PivotPoint, SeriesPoint, normalize_rows


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
class EnvelopePoint:
    day: date
    value: float
    upper: float
    lower: float


@dataclass(frozen=True)
class BasePivotResult:
    """Sealed Stage-1 result: completed upper/lower RDP pivots."""

    frequency: str
    policy: PivotPolicy
    high_candidates: tuple[PivotPoint, ...]
    low_candidates: tuple[PivotPoint, ...]
    high_pivots: tuple[PivotPoint, ...]
    low_pivots: tuple[PivotPoint, ...]

    @property
    def display_markers(self) -> tuple[PivotPoint, ...]:
        return tuple(sorted(
            (*self.high_pivots, *self.low_pivots),
            key=lambda item: (item.day, item.pivot_type),
        ))


def build_envelope(
    points: Sequence[SeriesPoint],
    frequency: str,
) -> tuple[EnvelopePoint, ...]:
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
            result.append(
                EnvelopePoint(point.day, point.value, max(values), min(values))
            )
        return tuple(result)

    radius = policy.envelope_points // 2
    for index, point in enumerate(points):
        window = points[
            max(0, index - radius):
            min(len(points), index + radius + 1)
        ]
        values = [item.value for item in window]
        result.append(
            EnvelopePoint(point.day, point.value, max(values), min(values))
        )
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
        while (
            run_end < len(envelope)
            and getattr(envelope[run_end], attribute) == level
        ):
            run_end += 1

        run = envelope[run_start:run_end]
        if len(run) >= 2:
            chosen = (
                max(run, key=lambda item: item.value)
                if pivot_type == "high"
                else min(run, key=lambda item: item.value)
            )
            candidates.append(
                PivotPoint(chosen.day, chosen.value, pivot_type)
            )
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
    return abs(
        (y2 - y1) * px
        - (x2 - x1) * py
        + x2 * y1
        - y2 * x1
    ) / denominator


def fixed_count_rdp(
    points: Sequence[PivotPoint],
    target_count: int,
) -> tuple[PivotPoint, ...]:
    """Greedy fixed-count RDP-style simplification."""
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
                    ordered[index],
                    ordered[left_index],
                    ordered[right_index],
                    origin,
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
