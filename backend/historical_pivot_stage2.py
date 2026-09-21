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
        """Classify and merge consecutive same-side sideways segments.

        Each adjacent same-side pair is sideways when its absolute on-screen
        angle from the x-axis is <= 6 degrees. Direction is irrelevant.

        Consecutive sideways pairs are merged only while the direct line from
        the current run's start to the new end also remains <= 6 degrees.
        When that combined angle exceeds 6 degrees, the existing run is sealed
        and the new adjacent sideways pair starts a separate run.
        """
        found: list[SidewaysSegment] = []
        ordered = tuple(sorted(points, key=lambda item: item.day))
        active: SidewaysSegment | None = None

        for left, right in zip(ordered, ordered[1:]):
            pair = classify_sideways_reference_line(
                left, right, prior_trend, geometry,
            )

            if pair is None:
                if active is not None:
                    found.append(active)
                    active = None
                continue

            if active is None:
                active = pair
                continue

            if active.end != left:
                found.append(active)
                active = pair
                continue

            merged = classify_sideways_reference_line(
                active.start, right, prior_trend, geometry,
            )
            if merged is not None:
                active = merged
            else:
                found.append(active)
                active = pair

        if active is not None:
            found.append(active)

        return tuple(found)

    return Stage2Result(
        high_pivots=tuple(stage1.high_pivots),
        low_pivots=tuple(stage1.low_pivots),
        spike_peaks=tuple(stage1.spike_peaks),
        high_sideways_segments=sideways_pairs(line_highs, "up"),
        low_sideways_segments=sideways_pairs(line_lows, "down"),
    )
