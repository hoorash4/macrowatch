"""Stage 2: sideways classification/protection boundary.

Input is only the untouched raw-graph geometry plus Stage 1's sealed points. This
stage does not know plateau candidates or deleted RDP points.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from historical_pivot_shared import (
    ChartGeometry,
    PivotPoint,
    SidewaysSegment,
    SpikePeak,
    classify_sideways_reference_line,
)
from historical_pivot_stage1 import SpikeAugmentedPivotResult

@dataclass(frozen=True)
class Stage2Result:
    high_pivots: tuple[PivotPoint, ...]
    low_pivots: tuple[PivotPoint, ...]
    spike_peaks: tuple[SpikePeak, ...]
    high_sideways_segments: tuple[SidewaysSegment, ...]
    low_sideways_segments: tuple[SidewaysSegment, ...]

    @property
    def display_markers(self) -> tuple[PivotPoint, ...]:
        return tuple(sorted(
            (*self.high_pivots, *self.low_pivots),
            key=lambda item: (item.day, item.pivot_type),
        ))

def finalize_sideways_protection(
    stage1: SpikeAugmentedPivotResult,
    geometry: ChartGeometry,
) -> Stage2Result:
    """Seal Stage 2 using only Stage 1's points and protection metadata."""
    marker_only_keys = {
        (item.point.day, item.point.value, item.point.pivot_type)
        for item in stage1.spike_peaks
        if item.marker_only
    }
    line_highs = tuple(
        item for item in stage1.high_pivots
        if (item.day, item.value, item.pivot_type) not in marker_only_keys
    )
    line_lows = tuple(
        item for item in stage1.low_pivots
        if (item.day, item.value, item.pivot_type) not in marker_only_keys
    )

    def sideways_pairs(
        points: Sequence[PivotPoint],
        prior_trend: str,
    ) -> tuple[SidewaysSegment, ...]:
        """Keep a flat same-side pair only when that side arrived from the
        matching trend direction.

        Sideways is not defined by a flat high/high or low/low pair alone.
        The pair must follow an already progressing same-side trend:
        - uptrend  -> rising highs, then high/high <= 6 degrees
        - downtrend -> falling lows, then low/low <= 6 degrees

        Once a sideways run has been established, adjacent flat pairs may
        continue that same run.
        """
        found: list[SidewaysSegment] = []
        ordered = tuple(sorted(points, key=lambda item: item.day))

        for index, (left, right) in enumerate(zip(ordered, ordered[1:])):
            segment = classify_sideways_reference_line(
                left, right, prior_trend, geometry,
            )
            if segment is None:
                continue

            continues_sideways = bool(
                found
                and found[-1].end == left
                and found[-1].prior_trend == prior_trend
            )
            if continues_sideways:
                found.append(segment)
                continue

            if index == 0:
                continue

            previous = ordered[index - 1]
            arrived_from_trend = (
                left.value > previous.value
                if prior_trend == "up"
                else left.value < previous.value
            )
            if arrived_from_trend:
                found.append(segment)

        return tuple(found)

    return Stage2Result(
        high_pivots=tuple(stage1.high_pivots),
        low_pivots=tuple(stage1.low_pivots),
        spike_peaks=tuple(stage1.spike_peaks),
        high_sideways_segments=sideways_pairs(line_highs, "up"),
        low_sideways_segments=sideways_pairs(line_lows, "down"),
    )
