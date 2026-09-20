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
    SpikeAugmentedPivotResult,
    SpikePeak,
    classify_sideways_reference_line,
)

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
        found: list[SidewaysSegment] = []
        for left, right in zip(points, points[1:]):
            segment = classify_sideways_reference_line(
                left, right, prior_trend, geometry,
            )
            if segment is not None:
                found.append(segment)
        return tuple(found)

    return Stage2Result(
        high_pivots=tuple(stage1.high_pivots),
        low_pivots=tuple(stage1.low_pivots),
        spike_peaks=tuple(stage1.spike_peaks),
        high_sideways_segments=sideways_pairs(line_highs, "up"),
        low_sideways_segments=sideways_pairs(line_lows, "down"),
    )
