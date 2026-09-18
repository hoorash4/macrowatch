"""Historical Insight deterministic indicator-structure detector — v5 clean rebuild.

Contract:
- Raw indicator values are used only to locate the exact extrema and to persist
  the displayed value.
- Historical Case start/end freeze the X axis.
- Visible indicator min/max freeze the Y axis.
- After extrema discovery, every comparison of size/height/duration/long/short
  is made only with fixed-axis shares (normalized X/Y).
- +/-24 months of the SAME indicator are context for validating visible-edge
  extrema only. They never rescale X/Y.
- Market-index prices never enter structure detection.

Pipeline:
1. discover exact extrema from raw values;
2. map them to the frozen chart axes;
3. discover trend candidates with HH/HL and LH/LL structure;
4. validate every candidate against relative X/Y geometry before accepting it;
5. detect sideways as lack of directional progress, including straight flats;
6. detect spikes only as RELATIVE amplitude/duration anomalies versus the other
   swings on the same frozen chart;
7. persist only validated structural boundaries.
"""
from __future__ import annotations

import argparse
import calendar
from dataclasses import dataclass
from datetime import date
from typing import Any

from common import SupabaseRest

INDEX_CODES = {"SP500", "NASDAQ_COMPOSITE", "KOSPI"}
SCHEMA_VERSION = "rule-structure-v5"
ENGINE_VERSION = "historical-rules-20260918-v5"
BUFFER_MONTHS = 24


@dataclass(frozen=True)
class Point:
    date: str
    value: float
    x: float
    y: float


@dataclass(frozen=True)
class Turn:
    index: int
    kind: str  # high | low


@dataclass(frozen=True)
class Scale:
    typical_y: float
    material_y: float
    typical_x: float
    long_x: float
    spike_y_outlier: float
    short_roundtrip_x: float


@dataclass(frozen=True)
class Box:
    start_index: int
    end_index: int
    mode: str  # flat | oscillatory


def fetch_all(db: SupabaseRest, table: str, params: dict[str, str], page_size: int = 1000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = dict(params)
        query["limit"] = str(page_size)
        query["offset"] = str(offset)
        batch = db.request("GET", table, params=query) or []
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
        offset += page_size


def shift_months(value: str, amount: int) -> str:
    source = date.fromisoformat(value)
    month_index = source.year * 12 + source.month - 1 + amount
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    day = min(source.day, calendar.monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def days_between(left: str, right: str) -> int:
    return (date.fromisoformat(right) - date.fromisoformat(left)).days


def load_case(db: SupabaseRest, case_code: str) -> dict[str, Any]:
    rows = fetch_all(db, "historical_cases", {
        "select": "case_code,case_name,primary_index_code,search_start,search_end",
        "case_code": f"eq.{case_code}",
    })
    if not rows:
        raise RuntimeError(f"Historical case not found: {case_code}")
    case = rows[0]
    if not case.get("search_end"):
        raise RuntimeError("Historical Case search_end is required.")
    return case


def load_cycle(db: SupabaseRest, case_code: str, index_code: str) -> dict[str, Any]:
    rows = fetch_all(db, "historical_case_market_cycles", {
        "select": "case_code,index_code,start_date,peak_date,trough_date,cycle_status",
        "case_code": f"eq.{case_code}",
        "index_code": f"eq.{index_code}",
    })
    if not rows:
        raise RuntimeError(f"Historical market cycle not found: {case_code}/{index_code}")
    return rows[0]


def load_indicator_rows(db: SupabaseRest, series_code: str, start: str, end: str) -> list[dict[str, Any]]:
    raw = fetch_all(db, "economic_chart_series_points", {
        "select": "observation_date,value",
        "series_code": f"eq.{series_code}",
        "observation_date": f"gte.{start}",
        "and": f"(observation_date.lte.{end})",
        "order": "observation_date.asc",
    })
    return [
        {"date": str(row["observation_date"])[:10], "value": float(row["value"])}
        for row in raw if row.get("value") is not None
    ]


def eligible_series(db: SupabaseRest, start: str, end: str) -> list[str]:
    coverage = fetch_all(db, "economic_chart_series_coverage", {
        "select": "series_code,first_date,last_date,point_count",
        "first_date": f"lte.{end}",
        "last_date": f"gte.{start}",
        "order": "series_code.asc",
    })
    return [
        str(row["series_code"]) for row in coverage
        if str(row["series_code"]) not in INDEX_CODES and int(row.get("point_count") or 0) >= 3
    ]


def normalize_points(rows: list[dict[str, Any]], visible_start: str, visible_end: str) -> tuple[list[Point], float, float]:
    visible = [row for row in rows if visible_start <= row["date"] <= visible_end]
    if len(visible) < 3:
        raise RuntimeError("Not enough indicator points inside the fixed case window.")

    y_min = min(float(row["value"]) for row in visible)
    y_max = max(float(row["value"]) for row in visible)
    y_span = max(y_max - y_min, 1e-12)
    x_days = max(1, days_between(visible_start, visible_end))

    points = [
        Point(
            date=str(row["date"]),
            value=float(row["value"]),
            x=days_between(visible_start, str(row["date"])) / x_days,
            y=(float(row["value"]) - y_min) / y_span,
        )
        for row in rows
    ]
    return points, y_min, y_max


def visible_indices(points: list[Point]) -> list[int]:
    return [i for i, point in enumerate(points) if 0.0 <= point.x <= 1.0]


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def median(values: list[float]) -> float:
    return quantile(values, 0.5)


def y_share(points: list[Point], left: int, right: int) -> float:
    return abs(points[right].y - points[left].y)


def x_share(points: list[Point], left: int, right: int) -> float:
    return abs(points[right].x - points[left].x)


# ---------------------------------------------------------------------------
# 1) Exact extrema discovery. Raw values are allowed only in this section.
# ---------------------------------------------------------------------------

def exact_raw_turns(points: list[Point]) -> list[Turn]:
    if len(points) < 3:
        return []

    turns: list[Turn] = []
    last_nonflat = 0
    previous_direction = 0

    i = 1
    while i < len(points):
        if points[i].value == points[last_nonflat].value:
            i += 1
            continue

        direction = 1 if points[i].value > points[last_nonflat].value else -1
        if previous_direction and direction != previous_direction:
            turns.append(Turn(last_nonflat, "high" if previous_direction > 0 else "low"))
        previous_direction = direction
        last_nonflat = i
        i += 1

    return collapse_same_kind_raw(points, turns)


def collapse_same_kind_raw(points: list[Point], turns: list[Turn]) -> list[Turn]:
    out: list[Turn] = []
    for turn in sorted(turns, key=lambda t: t.index):
        if out and out[-1].kind == turn.kind:
            prior = out[-1]
            if turn.kind == "high":
                if points[turn.index].value > points[prior.index].value:
                    out[-1] = turn
            else:
                if points[turn.index].value < points[prior.index].value:
                    out[-1] = turn
        else:
            out.append(turn)
    return out


def exact_visible_candidates(points: list[Point], v0: int, v1: int) -> list[Turn]:
    turns = [t for t in exact_raw_turns(points) if v0 <= t.index <= v1]

    # Exact visible global high/low are never allowed to disappear before
    # validation. This fixes the prior failure to even candidate obvious extrema.
    max_idx = max(range(v0, v1 + 1), key=lambda i: points[i].value)
    min_idx = min(range(v0, v1 + 1), key=lambda i: points[i].value)
    for turn in (Turn(max_idx, "high"), Turn(min_idx, "low")):
        if all(existing.index != turn.index for existing in turns):
            turns.append(turn)

    return collapse_same_kind_raw(points, turns)


# ---------------------------------------------------------------------------
# 2) Frozen-axis scale. All structural comparisons below use only x/y shares.
# ---------------------------------------------------------------------------

def build_scale(points: list[Point], turns: list[Turn]) -> Scale:
    swing_y = [
        y_share(points, a.index, b.index)
        for a, b in zip(turns, turns[1:])
        if a.index != b.index
    ]
    swing_x = [
        x_share(points, a.index, b.index)
        for a, b in zip(turns, turns[1:])
        if a.index != b.index
    ]

    typical_y = median(swing_y) if swing_y else 1.0
    # With only a few structural legs there is no statistical basis for
    # declaring one of them "micro" merely because it is below the median.
    # In richer/noisier charts, use the lower quartile as the minimum material
    # same-chart Y share; pruning still uses the median typical swing.
    material_y = (
        min(swing_y) if 0 < len(swing_y) <= 4
        else quantile(swing_y, 0.25) if swing_y
        else 1.0
    )
    typical_x = median(swing_x) if swing_x else 1.0
    long_x = quantile(swing_x, 0.75) if swing_x else typical_x

    # Spike amplitude is RELATIVE to the other swing amplitudes.
    # Tukey's upper fence is used only as a same-chart outlier detector.
    if len(swing_y) >= 4:
        q1 = quantile(swing_y, 0.25)
        q3 = quantile(swing_y, 0.75)
        spike_y_outlier = q3 + 1.5 * (q3 - q1)
    else:
        spike_y_outlier = max(swing_y) if swing_y else 1.0

    roundtrip_x = [
        x_share(points, turns[i - 1].index, turns[i + 1].index)
        for i in range(1, len(turns) - 1)
    ]
    short_roundtrip_x = median(roundtrip_x) if roundtrip_x else typical_x * 2.0

    return Scale(
        typical_y=max(typical_y, 1e-12),
        material_y=max(material_y, 1e-12),
        typical_x=max(typical_x, 1e-12),
        long_x=max(long_x, 1e-12),
        spike_y_outlier=max(spike_y_outlier, 1e-12),
        short_roundtrip_x=max(short_roundtrip_x, 1e-12),
    )


def collapse_same_kind_axis(points: list[Point], turns: list[Turn]) -> list[Turn]:
    out: list[Turn] = []
    for turn in sorted(turns, key=lambda t: t.index):
        if out and out[-1].kind == turn.kind:
            prior = out[-1]
            if turn.kind == "high":
                if points[turn.index].y >= points[prior.index].y:
                    out[-1] = turn
            else:
                if points[turn.index].y <= points[prior.index].y:
                    out[-1] = turn
        else:
            out.append(turn)
    return out


def add_visible_anchors(points: list[Point], turns: list[Turn], v0: int, v1: int) -> list[Turn]:
    sequence = list(turns)
    if not sequence or sequence[0].index != v0:
        kind = "low" if (not sequence or sequence[0].kind == "high") else "high"
        sequence.append(Turn(v0, kind))
    sequence = collapse_same_kind_axis(points, sequence)

    if not sequence or sequence[-1].index != v1:
        kind = "low" if (not sequence or sequence[-1].kind == "high") else "high"
        sequence.append(Turn(v1, kind))
    return collapse_same_kind_axis(points, sequence)


def continuation_kind(points: list[Point], four: list[Turn]) -> str | None:
    if len(four) != 4:
        return None
    a, b, c, d = four
    kinds = [t.kind for t in four]
    if kinds == ["low", "high", "low", "high"]:
        if points[c.index].y >= points[a.index].y and points[d.index].y >= points[b.index].y:
            return "up"
    if kinds == ["high", "low", "high", "low"]:
        if points[c.index].y <= points[a.index].y and points[d.index].y <= points[b.index].y:
            return "down"
    return None


def merge_hh_hl_lh_ll(points: list[Point], turns: list[Turn], protected: set[int]) -> list[Turn]:
    out = list(turns)
    changed = True
    while changed and len(out) >= 4:
        changed = False
        for i in range(len(out) - 3):
            four = out[i:i + 4]
            if not continuation_kind(points, four):
                continue
            if four[1].index in protected or four[2].index in protected:
                continue

            # The flow pattern discovers a possible merge; frozen-Y shares
            # validate that the counter-wave is not larger than the impulses
            # around it. No raw value or absolute indicator unit is used.
            correction = y_share(points, four[1].index, four[2].index)
            impulse_left = y_share(points, four[0].index, four[1].index)
            impulse_right = y_share(points, four[2].index, four[3].index)
            if correction <= max(impulse_left, impulse_right):
                del out[i + 1:i + 3]
                out = collapse_same_kind_axis(points, out)
                changed = True
                break
    return out


def prune_relative_micro_waves(
    points: list[Point],
    turns: list[Turn],
    scale: Scale,
    protected: set[int],
) -> list[Turn]:
    """Remove zigzags whose chart-height is below the same-chart typical swing.

    The threshold is not an absolute number. It is the median Y-axis share of
    all discovered swings on this indicator chart.
    """
    out = list(turns)
    while len(out) >= 3:
        changed = False
        for i in range(len(out) - 2):
            left, middle, right = out[i:i + 3]
            if left.kind != right.kind or middle.kind == left.kind:
                continue
            if middle.index in protected:
                continue

            left_dy = y_share(points, left.index, middle.index)
            right_dy = y_share(points, middle.index, right.index)
            # A zigzag is micro only when BOTH of its legs are below the
            # same-chart typical swing.  One smaller leg beside a substantial
            # opposite leg is not enough to erase a real reversal.
            if max(left_dy, right_dy) >= scale.typical_y:
                continue

            left_protected = left.index in protected
            right_protected = right.index in protected
            if left_protected and right_protected:
                continue

            if left_protected:
                survivor = left
            elif right_protected:
                survivor = right
            elif left.kind == "high":
                survivor = left if points[left.index].y >= points[right.index].y else right
            else:
                survivor = left if points[left.index].y <= points[right.index].y else right

            out[i:i + 3] = [survivor]
            out = collapse_same_kind_axis(points, out)
            changed = True
            break

        if not changed:
            break
    return out


# ---------------------------------------------------------------------------
# 3) Sideways detection from axis geometry.
# ---------------------------------------------------------------------------

def flat_boxes(points: list[Point], v0: int, v1: int, scale: Scale) -> list[Box]:
    """Find long visually flat runs from normalized local slopes.

    A straight sideways stretch does not need local extrema.  Its defining
    feature is that normalized Y changes per normalized X are persistently in
    the low-slope tail of THIS chart, while the segment occupies a relatively
    long X share and makes little net directional progress.
    """
    step_slopes: list[float] = []
    for i in range(v0 + 1, v1 + 1):
        dx = x_share(points, i - 1, i)
        if dx <= 0:
            continue
        step_slopes.append(y_share(points, i - 1, i) / dx)
    if not step_slopes:
        return []

    low_slope = quantile(step_slopes, 0.25)
    candidates: list[Box] = []

    for start in range(v0, v1):
        for end in range(v1, start, -1):
            width = x_share(points, start, end)
            if width < scale.long_x:
                break

            local_slopes: list[float] = []
            for i in range(start + 1, end + 1):
                dx = x_share(points, i - 1, i)
                if dx > 0:
                    local_slopes.append(y_share(points, i - 1, i) / dx)
            if not local_slopes or quantile(local_slopes, 0.75) > low_slope:
                continue

            ys = [points[i].y for i in range(start, end + 1)]
            y_range = max(ys) - min(ys)
            if y_range >= scale.material_y:
                continue

            net = y_share(points, start, end)
            # A monotonic low-slope trend is still a trend.  A flat box spends
            # its Y range oscillating/stalling rather than progressing from one
            # edge of that range to the other.
            if y_range > 0 and net > y_range * 0.5:
                continue

            candidates.append(Box(start, end, "flat"))
            break

    return maximal_boxes(points, candidates)


def oscillatory_boxes(points: list[Point], turns: list[Turn], scale: Scale) -> list[Box]:
    candidates: list[Box] = []
    for i in range(len(turns)):
        for j in range(len(turns) - 1, i + 3, -1):
            window = turns[i:j + 1]
            if len(window) < 5:
                continue

            start, end = window[0].index, window[-1].index
            if x_share(points, start, end) < scale.typical_x:
                continue

            highs = [points[t.index].y for t in window if t.kind == "high"]
            lows = [points[t.index].y for t in window if t.kind == "low"]
            if len(highs) < 2 or len(lows) < 2:
                continue

            high_progress = all(b >= a for a, b in zip(highs, highs[1:])) or all(b <= a for a, b in zip(highs, highs[1:]))
            low_progress = all(b >= a for a, b in zip(lows, lows[1:])) or all(b <= a for a, b in zip(lows, lows[1:]))
            same_up = all(b >= a for a, b in zip(highs, highs[1:])) and all(b >= a for a, b in zip(lows, lows[1:]))
            same_down = all(b <= a for a, b in zip(highs, highs[1:])) and all(b <= a for a, b in zip(lows, lows[1:]))
            if high_progress and low_progress and (same_up or same_down):
                continue

            # Directionless if the net displacement is small relative to the
            # typical same-chart swing, regardless of the box's internal range.
            net = y_share(points, start, end)
            if net > scale.typical_y:
                continue

            candidates.append(Box(start, end, "oscillatory"))
            break

    return maximal_boxes(points, candidates)


def maximal_boxes(points: list[Point], boxes: list[Box]) -> list[Box]:
    selected: list[Box] = []
    for box in sorted(boxes, key=lambda b: x_share(points, b.start_index, b.end_index), reverse=True):
        if any(box.start_index >= kept.start_index and box.end_index <= kept.end_index for kept in selected):
            continue
        selected.append(box)
    return sorted(selected, key=lambda b: b.start_index)


def merge_boxes(points: list[Point], boxes: list[Box]) -> list[Box]:
    if not boxes:
        return []
    ordered = sorted(boxes, key=lambda b: b.start_index)
    out: list[Box] = []
    for box in ordered:
        if not out or box.start_index > out[-1].end_index:
            out.append(box)
            continue
        prior = out[-1]
        if box.mode == prior.mode == "flat":
            out[-1] = Box(min(prior.start_index, box.start_index), max(prior.end_index, box.end_index), "flat")
        elif prior.mode == "flat" and box.mode == "oscillatory":
            # A directly observed flat interval is stronger evidence than a
            # broad oscillatory interpretation that merely overlaps it.
            continue
        elif prior.mode == "oscillatory" and box.mode == "flat":
            out[-1] = box
        else:
            prior_x = x_share(points, prior.start_index, prior.end_index)
            box_x = x_share(points, box.start_index, box.end_index)
            if box_x > prior_x:
                out[-1] = box
    return out


def path_direction_changes(points: list[Point], start: int, end: int) -> int:
    previous = 0
    changes = 0
    for i in range(start + 1, end + 1):
        delta = points[i].y - points[i - 1].y
        direction = 1 if delta > 0 else -1 if delta < 0 else 0
        if direction == 0:
            continue
        if previous and direction != previous:
            changes += 1
        previous = direction
    return changes


def box_survives(points: list[Point], box: Box, skeleton: list[Turn], scale: Scale) -> bool:
    width = x_share(points, box.start_index, box.end_index)

    # Long/short is judged only by X-axis shares relative to the other swing
    # durations on this same chart.
    if width >= scale.long_x:
        return True

    # A short interval is not a "box" merely because a tiny top/bottom wiggle
    # happens to fit inside a narrow Y range. It needs repeated sideways
    # behavior before the short-box reversal rule is even considered.
    if path_direction_changes(points, box.start_index, box.end_index) < 3:
        return False

    before = next((t for t in reversed(skeleton) if t.index < box.start_index), None)
    after = next((t for t in skeleton if t.index > box.end_index), None)
    if not before or not after:
        return False

    entry = points[box.start_index].y - points[before.index].y
    exit_ = points[after.index].y - points[box.end_index].y
    return entry != 0 and exit_ != 0 and (entry > 0) != (exit_ > 0)


def boundary_kind(points: list[Point], index: int, side: str) -> str:
    if side == "entry":
        prior = max(0, index - 1)
        return "low" if points[index].y < points[prior].y else "high"
    after = min(len(points) - 1, index + 1)
    return "low" if points[after].y > points[index].y else "high"


# ---------------------------------------------------------------------------
# 4) Spike = RELATIVE amplitude outlier + RELATIVELY short X duration.
# ---------------------------------------------------------------------------

def find_spikes(points: list[Point], extrema: list[Turn], scale: Scale, v0: int, v1: int) -> list[tuple[Turn, Turn, Turn]]:
    if len(extrema) < 3:
        return []

    candidates: list[tuple[float, float, float, Turn, Turn, Turn]] = []

    for center_pos, extreme in enumerate(extrema):
        center = extreme.index
        if not (v0 < center < v1):
            continue

        # Search structural entry/recovery anchors among opposite extrema within
        # a relative X neighborhood derived from this chart's swing durations.
        left_pool = [
            t for t in extrema[:center_pos]
            if t.kind != extreme.kind and points[center].x - points[t.index].x <= scale.long_x
        ]
        right_pool = [
            t for t in extrema[center_pos + 1:]
            if t.kind != extreme.kind and points[t.index].x - points[center].x <= scale.long_x
        ]
        if not left_pool or not right_pool:
            continue

        best: tuple[float, float, float, Turn, Turn] | None = None
        for left in left_pool:
            for right in right_pool:
                left_dy = y_share(points, left.index, center)
                right_dy = y_share(points, center, right.index)
                excursion = min(left_dy, right_dy)
                width = x_share(points, left.index, right.index)
                base_gap = y_share(points, left.index, right.index)

                # No absolute 50% rule. The excursion must be an amplitude
                # outlier versus the other same-chart swings, and its duration
                # must be short relative to the same chart's round trips.
                if excursion <= scale.spike_y_outlier:
                    continue
                if width > scale.short_roundtrip_x:
                    continue

                score = excursion / max(width, 1e-12)
                item = (score, excursion, -base_gap, left, right)
                if best is None or item[:3] > best[:3]:
                    best = item

        if best:
            _, excursion, neg_base_gap, left, right = best
            candidates.append((excursion, -neg_base_gap, x_share(points, left.index, right.index), left, extreme, right))

    selected: list[tuple[Turn, Turn, Turn]] = []
    occupied: list[tuple[int, int]] = []
    for _, _, _, left, extreme, right in sorted(candidates, key=lambda item: (-item[0], item[2])):
        if any(not (right.index < a or left.index > b) for a, b in occupied):
            continue
        selected.append((left, extreme, right))
        occupied.append((left.index, right.index))
    return sorted(selected, key=lambda item: item[1].index)


# ---------------------------------------------------------------------------
# 5) Structure assembly.
# ---------------------------------------------------------------------------

def structure(points: list[Point]) -> dict[str, Any]:
    visible = visible_indices(points)
    if len(visible) < 3:
        return {"regimes": [], "pivots": [], "sideways_boundaries": [], "turning_points": []}

    v0, v1 = visible[0], visible[-1]
    extrema = exact_visible_candidates(points, v0, v1)
    if not extrema:
        return {"regimes": [], "pivots": [], "sideways_boundaries": [], "turning_points": []}

    scale_sequence = add_visible_anchors(points, extrema, v0, v1)
    scale = build_scale(points, scale_sequence)

    boxes = merge_boxes(points, [
        *flat_boxes(points, v0, v1, scale),
        *oscillatory_boxes(points, extrema, scale),
    ])
    spikes = find_spikes(points, extrema, scale, v0, v1)

    spike_indices = {t.index for triple in spikes for t in triple}
    box_indices = {idx for box in boxes for idx in (box.start_index, box.end_index)}
    global_high_index = max(range(v0, v1 + 1), key=lambda idx: points[idx].y)
    global_low_index = min(range(v0, v1 + 1), key=lambda idx: points[idx].y)
    protected = spike_indices | box_indices | {global_high_index, global_low_index}

    skeleton = list(scale_sequence)
    skeleton = merge_hh_hl_lh_ll(points, skeleton, protected)
    skeleton = prune_relative_micro_waves(points, skeleton, scale, protected)
    skeleton = merge_hh_hl_lh_ll(points, skeleton, protected)
    skeleton = prune_relative_micro_waves(points, skeleton, scale, protected)

    accepted: dict[int, dict[str, Any]] = {}
    extrema_map = {t.index: t for t in extrema}

    # Ordinary reversals must survive structure discovery AND same-chart Y-share
    # validation on both sides. No raw unit and no fixed absolute Y threshold.
    for i, turn in enumerate(skeleton):
        if turn.index not in extrema_map or i == 0 or i == len(skeleton) - 1:
            continue
        incoming = y_share(points, skeleton[i - 1].index, turn.index)
        outgoing = y_share(points, turn.index, skeleton[i + 1].index)
        if incoming < scale.material_y or outgoing < scale.material_y:
            continue

        accepted[turn.index] = {
            "turn": turn,
            "type": "major_reversal",
            "grade": "A",
            "reason": (
                f"A: {'상승→하락' if turn.kind == 'high' else '하락→상승'} 구조적 반전. "
                f"반전 전·후 이동은 고정 Y축의 {incoming*100:.1f}%와 {outgoing*100:.1f}%이고, "
                f"이 지표의 동일 차트 기준 구조 검증 하한은 {scale.material_y*100:.1f}%이고 전형적 스윙은 {scale.typical_y*100:.1f}%라 구조점으로 유지."
            ),
        }

    # Visible global high/low are exact-extrema candidates by construction.
    # If both sides occupy at least the same-chart typical Y swing, they are
    # structural even when continuation compression would otherwise hide them.
    global_high = max(range(v0, v1 + 1), key=lambda idx: points[idx].y)
    global_low = min(range(v0, v1 + 1), key=lambda idx: points[idx].y)
    skeleton_indices = [turn.index for turn in skeleton]
    for idx, kind in ((global_high, "high"), (global_low, "low")):
        if idx in accepted:
            continue
        left = next((turn for turn in reversed(skeleton) if turn.index < idx), None)
        right = next((turn for turn in skeleton if turn.index > idx), None)
        if not left or not right:
            continue
        incoming = y_share(points, left.index, idx)
        outgoing = y_share(points, idx, right.index)
        if incoming < scale.material_y or outgoing < scale.material_y:
            continue
        accepted[idx] = {
            "turn": Turn(idx, kind),
            "type": "major_reversal",
            "grade": "A",
            "reason": (
                f"A: {'상승→하락' if kind == 'high' else '하락→상승'} 구조적 반전. "
                f"정확한 가시구간 {'최고점' if kind == 'high' else '최저점'}이며, "
                f"반전 전·후 이동은 고정 Y축의 {incoming*100:.1f}%와 {outgoing*100:.1f}%로 "
                f"같은 차트의 구조 검증 하한 {scale.material_y*100:.1f}%를 모두 넘어 유지."
            ),
        }

    # Sideways boundaries.
    kept_boxes: list[Box] = []
    sideways_boundaries: list[dict[str, Any]] = []
    for n, box in enumerate(boxes, 1):
        if not box_survives(points, box, skeleton, scale):
            continue
        kept_boxes.append(box)

        width = x_share(points, box.start_index, box.end_index)
        ys = [points[i].y for i in range(box.start_index, box.end_index + 1)]
        y_span = max(ys) - min(ys)

        entry = Turn(box.start_index, boundary_kind(points, box.start_index, "entry"))
        exit_ = Turn(box.end_index, boundary_kind(points, box.end_index, "exit"))

        sideways_boundaries.append({
            "id": f"box-{n}",
            "start_date": points[box.start_index].date,
            "end_date": points[box.end_index].date,
            "mode": box.mode,
            "x_share": round(width, 6),
            "y_span": round(y_span, 6),
            "long": width >= scale.long_x,
        })

        accepted[entry.index] = {
            "turn": entry,
            "type": "sideways_entry",
            "grade": "B",
            "reason": (
                f"B: 횡보 진입점. 횡보 구간은 고정 X축의 {width*100:.1f}%를 차지하며, "
                f"같은 차트의 전형적 스윙 기간 {scale.typical_x*100:.1f}%보다 {'길다' if width >= scale.typical_x else '짧다'}."
            ),
        }
        accepted[exit_.index] = {
            "turn": exit_,
            "type": "sideways_exit",
            "grade": "B",
            "reason": (
                f"B: 횡보 이탈점. 구간 전체 Y폭은 고정 Y축의 {y_span*100:.1f}%이고, "
                "이 지점 이후 다시 방향 진행이 시작되어 경계점으로 유지."
            ),
        }

    # Spike triplets override ordinary labels.
    for left, extreme, right in spikes:
        left_dy = y_share(points, left.index, extreme.index)
        right_dy = y_share(points, extreme.index, right.index)
        width = x_share(points, left.index, right.index)
        excursion = min(left_dy, right_dy)

        accepted[left.index] = {
            "turn": left,
            "type": "spike_entry",
            "grade": "A",
            "reason": (
                f"A: 스파이크 진입점. 극점 왕복 진폭 {excursion*100:.1f}%는 같은 차트 다른 스윙들의 "
                f"상대적 이상치 기준 {scale.spike_y_outlier*100:.1f}%를 넘고, 왕복 X폭 {width*100:.1f}%는 상대적으로 짧아 시작점으로 유지."
            ),
        }
        accepted[extreme.index] = {
            "turn": extreme,
            "type": "spike_extreme",
            "grade": "A",
            "reason": (
                f"A: 스파이크 극점. 양쪽 Y이동은 {left_dy*100:.1f}%/{right_dy*100:.1f}%이며 "
                f"다른 스윙 대비 상대적 진폭 이상치이고, X폭은 {width*100:.1f}%로 짧아 일반 파동과 분리."
            ),
        }
        accepted[right.index] = {
            "turn": right,
            "type": "spike_retracement",
            "grade": "A",
            "reason": (
                f"A: 스파이크 복귀점. 극점에서 이 점까지 고정 Y축의 {right_dy*100:.1f}%를 되돌려 "
                "상대적 이상치 왕복 구조의 반대편 앵커로 유지."
            ),
        }

    # Accepted sideways suppresses internal ordinary points, but not spikes.
    for box in kept_boxes:
        for idx in list(accepted):
            if box.start_index < idx < box.end_index and idx not in spike_indices:
                del accepted[idx]

    pivots: list[dict[str, Any]] = []
    for idx in sorted(accepted):
        item = accepted[idx]
        turn: Turn = item["turn"]
        pivots.append({
            "date": points[idx].date,
            "value": points[idx].value,
            "type": item["type"],
            "grade": item["grade"],
            "direction": turn.kind,
            "reason": item["reason"],
            "confidence": 1.0,
            "post_trend": None,
        })

    structural = [p for p in pivots if p["grade"] in {"A", "B"}]
    date_to_index = {points[i].date: i for i in visible}
    structural_dates = [p["date"] for p in structural]

    for pivot in structural:
        idx = date_to_index.get(pivot["date"])
        if idx is None:
            continue
        later = [d for d in structural_dates if d > pivot["date"]]
        end_date = later[0] if later else points[v1].date
        end_idx = date_to_index.get(end_date, v1)
        if pivot["type"] == "sideways_entry":
            direction = "sideways"
        else:
            direction = "up" if points[end_idx].y > points[idx].y else "down" if points[end_idx].y < points[idx].y else "sideways"
        pivot["post_trend"] = {"direction": direction, "end_date": end_date}

    regimes: list[dict[str, Any]] = []
    boundaries = sorted(set([v0, *[date_to_index[p["date"]] for p in structural if p["date"] in date_to_index], v1]))
    for left, right in zip(boundaries, boundaries[1:]):
        if left == right:
            continue
        in_box = any(box.start_index <= left and right <= box.end_index for box in kept_boxes)
        if in_box:
            kind = "sideways"
        else:
            kind = "uptrend" if points[right].y > points[left].y else "downtrend" if points[right].y < points[left].y else "sideways"
        if regimes and regimes[-1]["type"] == kind:
            regimes[-1]["end_date"] = points[right].date
        else:
            regimes.append({
                "type": kind,
                "start_date": points[left].date,
                "end_date": points[right].date,
                "confidence": 1.0,
            })

    turning_points = [
        {
            "id": f"tp-{i+1}",
            "date": p["date"],
            "value": p["value"],
            "type": p["type"],
            "grade": p["grade"],
            "direction": p["direction"],
            "reason": p["reason"],
        }
        for i, p in enumerate(pivots)
    ]

    return {
        "regimes": regimes,
        "pivots": pivots,
        "sideways_boundaries": sideways_boundaries,
        "turning_points": turning_points,
    }


def analyze_indicator_only(db: SupabaseRest, case_code: str, series_code: str) -> tuple[dict[str, Any], dict[str, Any], list[Point], float, float]:
    case = load_case(db, case_code)
    start, end = str(case["search_start"]), str(case["search_end"])
    rows = load_indicator_rows(
        db,
        series_code,
        shift_months(start, -BUFFER_MONTHS),
        shift_months(end, BUFFER_MONTHS),
    )
    points, y_min, y_max = normalize_points(rows, start, end)
    return case, structure(points), points, y_min, y_max


def persist_indicator_structure(
    db: SupabaseRest,
    case: dict[str, Any],
    index_code: str,
    series_code: str,
    result: dict[str, Any],
    points: list[Point],
    y_min: float,
    y_max: float,
) -> dict[str, Any]:
    # Market metadata is attached only after indicator-only structure is final.
    cycle = load_cycle(db, str(case["case_code"]), index_code)
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    row = {
        "case_code": str(case["case_code"]),
        "index_code": index_code,
        "series_code": series_code,
        "display_start": str(case["search_start"]),
        "display_end": str(case["search_end"]),
        "cycle_start": cycle.get("start_date"),
        "cycle_peak": cycle.get("peak_date"),
        "cycle_trough": cycle.get("trough_date"),
        "model": "python-rule-engine",
        "prompt_version": ENGINE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "regimes": result["regimes"],
        "pivots": result["pivots"],
        "anomalies": [],
        "source_point_count": len([p for p in points if 0 <= p.x <= 1]),
        "chart_sha256": None,
        "anomaly_validation_version": "rule-spike-relative-v5",
        "skeleton_trends": [],
        "sub_trends": [],
        "turning_points": result["turning_points"],
        "sideways_boundaries": result["sideways_boundaries"],
        "analyzed_at": now,
        "updated_at": now,
    }
    db.upsert("historical_indicator_ai_analysis", [row], conflict="case_code,index_code,series_code")
    print(
        f"{case['case_code']}/{index_code}/{series_code}: "
        f"A={sum(p['grade']=='A' for p in result['pivots'])} "
        f"B={sum(p['grade']=='B' for p in result['pivots'])} "
        f"D={sum(p['grade']=='D' for p in result['pivots'])} "
        f"boxes={len(result['sideways_boundaries'])} y=[{y_min:g},{y_max:g}]"
    )
    return row


def analyze_one(db: SupabaseRest, case_code: str, index_code: str, series_code: str) -> dict[str, Any]:
    case, result, points, y_min, y_max = analyze_indicator_only(db, case_code, series_code)
    return persist_indicator_structure(db, case, index_code, series_code, result, points, y_min, y_max)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--index")
    parser.add_argument("--series", default="all")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    db = SupabaseRest()
    case = load_case(db, args.case)
    index_code = args.index or str(case["primary_index_code"])
    load_cycle(db, args.case, index_code)

    start, end = str(case["search_start"]), str(case["search_end"])
    series_codes = [args.series] if args.series != "all" else eligible_series(db, start, end)
    if args.limit > 0:
        series_codes = series_codes[:args.limit]
    if not series_codes:
        raise RuntimeError("No overlapping indicator series found.")

    failures: list[tuple[str, str]] = []
    for series_code in series_codes:
        try:
            analyze_one(db, args.case, index_code, series_code)
        except Exception as exc:
            failures.append((series_code, str(exc)))
            print(f"ERROR {series_code}: {exc}")

    if failures:
        raise RuntimeError(
            "Rule pivot failures: "
            + "; ".join(f"{code}={message}" for code, message in failures[:10])
        )


if __name__ == "__main__":
    main()
