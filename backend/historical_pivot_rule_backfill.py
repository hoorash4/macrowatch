"""Historical Insight deterministic pivot detector — v4 clean rebuild.

The detector follows one strict coordinate contract:

- Raw indicator values are used only to locate exact extrema and to persist the
  actual displayed value.
- Historical Case start/end freeze the X axis.
- Visible indicator min/max freeze the Y axis.
- Every judgment about size, duration, height, distance, "large/small", or
  "long/short" is made only from normalized fixed-axis X/Y shares.
- +/-24 months of the SAME indicator are context for edge-extrema validation
  only.  They never rescale X or Y.
- Market-index prices never enter structure detection.

Processing order:
1) find exact raw extrema;
2) convert/compare only in frozen-axis coordinates;
3) discover HH/HL or LH/LL continuation structure;
4) validate candidate turns by their Y-axis share and discard visual micro-waves;
5) detect sideways regimes directly from axis geometry;
6) detect exceptional spikes as a separate X/Y-shape rule;
7) persist only the validated structural boundaries.
"""
from __future__ import annotations

import argparse
import calendar
from dataclasses import dataclass
from datetime import date
from typing import Any

from common import SupabaseRest

INDEX_CODES = {"SP500", "NASDAQ_COMPOSITE", "KOSPI"}
SCHEMA_VERSION = "rule-structure-v4"
ENGINE_VERSION = "historical-rules-20260918-v4"
BUFFER_MONTHS = 24

# Fixed-axis validation rules.
# These do NOT discover extrema. They only decide whether a discovered move is
# visually meaningful on the already-frozen chart.
MIN_STRUCTURAL_Y_SHARE = 0.08
REVIEW_Y_SHARE = 0.05

# Sideways geometry.
FLAT_BOX_MAX_Y_SHARE = 0.06
BOX_MIN_X_SHARE = 0.12
LONG_BOX_X_SHARE = 0.25
OSC_BOX_MAX_CENTER_DRIFT_Y = 0.12
OSC_BOX_MIN_TURNS = 5

# Spike geometry. User explicitly required a very large excursion; 50% is used
# only for spike classification, never for ordinary reversals.
SPIKE_MIN_Y_SHARE = 0.50
SPIKE_HALF_WINDOW_X = 0.09
SPIKE_MAX_TOTAL_X = 0.18
SPIKE_MAX_BASE_GAP_Y = 0.20


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
class Box:
    start_index: int
    end_index: int
    mode: str  # flat | oscillatory

    @property
    def key(self) -> tuple[int, int]:
        return (self.start_index, self.end_index)


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


def sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


# ---------------------------------------------------------------------------
# 1. Exact extrema discovery: raw values are allowed ONLY in this section.
# ---------------------------------------------------------------------------

def exact_raw_turns(points: list[Point]) -> list[Turn]:
    """Locate exact raw sign-change extrema across visible+buffer data.

    Flat tops/bottoms are treated as one extreme; the last equal observation
    before the outgoing move is used as the boundary.
    """
    if len(points) < 3:
        return []

    turns: list[Turn] = []
    previous_direction = 0
    i = 0
    while i < len(points) - 1:
        j = i + 1
        while j < len(points) and points[j].value == points[i].value:
            j += 1
        if j >= len(points):
            break

        direction = sign(points[j].value - points[i].value)
        if previous_direction and direction != previous_direction:
            turns.append(Turn(i, "high" if previous_direction > 0 else "low"))
        previous_direction = direction
        i = j

    return collapse_same_kind_raw(points, turns)


def collapse_same_kind_raw(points: list[Point], turns: list[Turn]) -> list[Turn]:
    out: list[Turn] = []
    for turn in sorted(turns, key=lambda item: item.index):
        if out and out[-1].kind == turn.kind:
            prior = out[-1]
            if (turn.kind == "high" and points[turn.index].value > points[prior.index].value) or (
                turn.kind == "low" and points[turn.index].value < points[prior.index].value
            ):
                out[-1] = turn
        else:
            out.append(turn)
    return out


def exact_visible_candidates(points: list[Point], v0: int, v1: int) -> list[Turn]:
    """Return visible exact turns and always include exact visible global max/min."""
    turns = [turn for turn in exact_raw_turns(points) if v0 <= turn.index <= v1]

    visible_slice = range(v0, v1 + 1)
    max_idx = max(visible_slice, key=lambda i: points[i].value)
    min_idx = min(visible_slice, key=lambda i: points[i].value)

    for turn in (Turn(max_idx, "high"), Turn(min_idx, "low")):
        if all(existing.index != turn.index for existing in turns):
            turns.append(turn)

    return collapse_same_kind_raw(points, sorted(turns, key=lambda item: item.index))


# ---------------------------------------------------------------------------
# 2. Fixed-axis geometry helpers. Raw values are forbidden below this line for
#    structural size/duration judgments.
# ---------------------------------------------------------------------------

def y_share(points: list[Point], left_index: int, right_index: int) -> float:
    return abs(points[right_index].y - points[left_index].y)


def x_share(points: list[Point], left_index: int, right_index: int) -> float:
    return abs(points[right_index].x - points[left_index].x)


def collapse_same_kind_axis(points: list[Point], turns: list[Turn]) -> list[Turn]:
    out: list[Turn] = []
    for turn in sorted(turns, key=lambda item: item.index):
        if out and out[-1].kind == turn.kind:
            prior = out[-1]
            if (turn.kind == "high" and points[turn.index].y >= points[prior.index].y) or (
                turn.kind == "low" and points[turn.index].y <= points[prior.index].y
            ):
                out[-1] = turn
        else:
            out.append(turn)
    return out


def add_context_anchors(points: list[Point], turns: list[Turn], v0: int, v1: int) -> list[Turn]:
    """Visible endpoints are context anchors, never automatic pivots."""
    sequence = list(turns)
    if not sequence or sequence[0].index != v0:
        first_kind = "low" if (not sequence or sequence[0].kind == "high") else "high"
        sequence.append(Turn(v0, first_kind))
    if not sequence or sequence[-1].index != v1:
        last_kind = "low" if (not sequence or sequence[-1].kind == "high") else "high"
        sequence.append(Turn(v1, last_kind))
    return collapse_same_kind_axis(points, sorted(sequence, key=lambda item: item.index))


def continuation_kind(points: list[Point], four: list[Turn]) -> str | None:
    """Discover HH/HL or LH/LL continuation using normalized Y ordering."""
    if len(four) != 4:
        return None
    a, b, c, d = four
    kinds = [item.kind for item in four]
    if kinds == ["low", "high", "low", "high"]:
        if points[c.index].y >= points[a.index].y and points[d.index].y >= points[b.index].y:
            return "up"
    if kinds == ["high", "low", "high", "low"]:
        if points[c.index].y <= points[a.index].y and points[d.index].y <= points[b.index].y:
            return "down"
    return None


def merge_continuation_waves(points: list[Point], turns: list[Turn], protected: set[int] | None = None) -> list[Turn]:
    """Merge internal HH/HL and LH/LL waves; protected structural anchors survive."""
    protected = protected or set()
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
            del out[i + 1:i + 3]
            out = collapse_same_kind_axis(points, out)
            changed = True
            break
    return out


def prune_visual_micro_turns(
    points: list[Point],
    turns: list[Turn],
    protected: set[int] | None = None,
) -> tuple[list[Turn], list[tuple[Turn, float, float]]]:
    """Collapse visually tiny zigzags in frozen-Y coordinates.

    For an alternating triple high-low-high or low-high-low, a tiny adjacent
    leg means the middle opposite turn is only a micro-wave.  The correct
    structural action is NOT to delete whichever pivot happens to own the
    small leg.  Instead, collapse the whole three-turn zigzag and keep the more
    extreme of the two same-kind outer turns.  This preserves the true high or
    low and removes its tiny neighboring wiggle.
    """
    protected = protected or set()
    out = list(turns)
    reviews: list[tuple[Turn, float, float]] = []

    while len(out) >= 3:
        changed = False
        for i in range(len(out) - 2):
            left, middle, right = out[i:i + 3]
            if left.kind != right.kind or middle.kind == left.kind:
                continue
            if middle.index in protected:
                continue

            left_leg = y_share(points, left.index, middle.index)
            right_leg = y_share(points, middle.index, right.index)
            weakest = min(left_leg, right_leg)
            if weakest >= MIN_STRUCTURAL_Y_SHARE:
                continue

            if REVIEW_Y_SHARE <= weakest < MIN_STRUCTURAL_Y_SHARE:
                reviews.append((middle, left_leg, right_leg))

            if left.kind == "high":
                survivor = left if points[left.index].y >= points[right.index].y else right
            else:
                survivor = left if points[left.index].y <= points[right.index].y else right

            # If one outer anchor is protected, it wins.  Two protected outer
            # anchors mean this zigzag belongs to another explicit rule and is
            # left untouched.
            left_protected = left.index in protected
            right_protected = right.index in protected
            if left_protected and right_protected:
                continue
            if left_protected:
                survivor = left
            elif right_protected:
                survivor = right

            out[i:i + 3] = [survivor]
            out = collapse_same_kind_axis(points, out)
            changed = True
            break

        if not changed:
            break

    return out, reviews


# ---------------------------------------------------------------------------
# 3. Sideways regimes: detect regime geometry directly, not by demanding lots
#    of local extrema.  Flat boxes and oscillatory boxes are separate cases.
# ---------------------------------------------------------------------------

def maximal_flat_boxes(points: list[Point], v0: int, v1: int) -> list[Box]:
    candidates: list[Box] = []

    # Candidate ranges are validated exclusively by fixed-axis X/Y shares.
    for start in range(v0, v1 - 1):
        min_y = max_y = points[start].y
        best_end: int | None = None
        for end in range(start + 1, v1 + 1):
            min_y = min(min_y, points[end].y)
            max_y = max(max_y, points[end].y)
            if max_y - min_y > FLAT_BOX_MAX_Y_SHARE:
                break
            if x_share(points, start, end) >= BOX_MIN_X_SHARE:
                best_end = end
        if best_end is not None:
            candidates.append(Box(start, best_end, "flat"))

    return keep_maximal_noncontained(points, candidates)


def progression_signs(values: list[float]) -> set[int]:
    signs: set[int] = set()
    for left, right in zip(values, values[1:]):
        if right > left:
            signs.add(1)
        elif right < left:
            signs.add(-1)
    return signs


def median(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("median requires data")
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def center_drift_share(points: list[Point], start_idx: int, end_idx: int) -> float | None:
    start_x = points[start_idx].x
    end_x = points[end_idx].x
    width = end_x - start_x
    if width <= 0:
        return None

    centers: list[float] = []
    for part in range(3):
        left = start_x + width * part / 3
        right = start_x + width * (part + 1) / 3
        ys = [
            point.y
            for point in points[start_idx:end_idx + 1]
            if (left <= point.x < right) or (part == 2 and left <= point.x <= right)
        ]
        if not ys:
            return None
        centers.append(median(ys))
    return max(centers) - min(centers)


def oscillatory_boxes(points: list[Point], turns: list[Turn]) -> list[Box]:
    candidates: list[Box] = []
    if len(turns) < OSC_BOX_MIN_TURNS:
        return candidates

    for i in range(len(turns)):
        for j in range(len(turns) - 1, i + OSC_BOX_MIN_TURNS - 2, -1):
            window = turns[i:j + 1]
            start_idx, end_idx = window[0].index, window[-1].index
            if x_share(points, start_idx, end_idx) < BOX_MIN_X_SHARE:
                continue

            highs = [points[item.index].y for item in window if item.kind == "high"]
            lows = [points[item.index].y for item in window if item.kind == "low"]
            if len(highs) < 2 or len(lows) < 2:
                continue

            high_signs = progression_signs(highs)
            low_signs = progression_signs(lows)
            sustained_up = high_signs in ({1}, set()) and low_signs in ({1}, set()) and (1 in high_signs or 1 in low_signs)
            sustained_down = high_signs in ({-1}, set()) and low_signs in ({-1}, set()) and (-1 in high_signs or -1 in low_signs)
            if sustained_up or sustained_down:
                continue

            drift = center_drift_share(points, start_idx, end_idx)
            if drift is None or drift > OSC_BOX_MAX_CENTER_DRIFT_Y:
                continue

            candidates.append(Box(start_idx, end_idx, "oscillatory"))
            break

    return keep_maximal_noncontained(points, candidates)


def keep_maximal_noncontained(points: list[Point], boxes: list[Box]) -> list[Box]:
    selected: list[Box] = []
    for box in sorted(boxes, key=lambda item: x_share(points, item.start_index, item.end_index), reverse=True):
        if any(box.start_index >= kept.start_index and box.end_index <= kept.end_index for kept in selected):
            continue
        selected.append(box)
    return sorted(selected, key=lambda item: item.start_index)


def merge_overlapping_boxes(points: list[Point], boxes: list[Box]) -> list[Box]:
    if not boxes:
        return []

    # A directly observed flat interval is the stronger description when an
    # oscillatory candidate sits entirely inside it.  Do not let incidental
    # tiny wiggles relabel an obvious straight sideways stretch.
    flat_boxes = [box for box in boxes if box.mode == "flat"]
    filtered: list[Box] = []
    for box in boxes:
        if box.mode == "oscillatory" and any(
            flat.start_index <= box.start_index and box.end_index <= flat.end_index
            for flat in flat_boxes
        ):
            continue
        filtered.append(box)

    boxes = sorted(filtered, key=lambda item: item.start_index)
    merged: list[Box] = []
    for box in boxes:
        if not merged or box.start_index > merged[-1].end_index:
            merged.append(box)
            continue

        prior = merged[-1]
        if prior.mode == box.mode == "flat":
            merged[-1] = Box(
                min(prior.start_index, box.start_index),
                max(prior.end_index, box.end_index),
                "flat",
            )
            continue

        # For mixed-mode overlaps, keep the interval that occupies more of the
        # fixed X axis rather than fabricating a new boundary.
        prior_width = x_share(points, prior.start_index, prior.end_index)
        box_width = x_share(points, box.start_index, box.end_index)
        if box_width > prior_width:
            merged[-1] = box

    return merged


def box_boundary_turn(points: list[Point], box: Box, kind: str) -> Turn:
    """Determine boundary direction from surrounding normalized geometry."""
    if kind == "entry":
        before_idx = max(0, box.start_index - 1)
        direction = sign(points[box.start_index].y - points[before_idx].y)
        turn_kind = "low" if direction < 0 else "high"
        return Turn(box.start_index, turn_kind)

    after_idx = min(len(points) - 1, box.end_index + 1)
    direction = sign(points[after_idx].y - points[box.end_index].y)
    turn_kind = "low" if direction > 0 else "high"
    return Turn(box.end_index, turn_kind)


def box_should_survive(points: list[Point], box: Box, skeleton: list[Turn]) -> bool:
    width = x_share(points, box.start_index, box.end_index)
    if width >= LONG_BOX_X_SHARE:
        return True

    before = next((turn for turn in reversed(skeleton) if turn.index < box.start_index), None)
    after = next((turn for turn in skeleton if turn.index > box.end_index), None)
    if not before or not after:
        return False

    entry_direction = sign(points[box.start_index].y - points[before.index].y)
    exit_direction = sign(points[after.index].y - points[box.end_index].y)
    return entry_direction != 0 and exit_direction != 0 and entry_direction != exit_direction


# ---------------------------------------------------------------------------
# 4. Spike: exceptional short-X / very-large-Y excursion.  Entry and recovery
#    anchors are searched directly in the local raw observations so a tiny
#    intervening wiggle cannot hide the true structural start/end.
# ---------------------------------------------------------------------------

def find_spikes(points: list[Point], extrema: list[Turn], v0: int, v1: int) -> list[tuple[Turn, Turn, Turn]]:
    candidates: list[tuple[float, float, Turn, Turn, Turn]] = []

    for extreme in extrema:
        center = extreme.index
        if not (v0 < center < v1):
            continue

        left_indices = [
            i for i in range(v0, center)
            if 0 < points[center].x - points[i].x <= SPIKE_HALF_WINDOW_X
        ]
        right_indices = [
            i for i in range(center + 1, v1 + 1)
            if 0 < points[i].x - points[center].x <= SPIKE_HALF_WINDOW_X
        ]
        if not left_indices or not right_indices:
            continue

        if extreme.kind == "high":
            left_idx = min(left_indices, key=lambda i: points[i].y)
            right_idx = min(right_indices, key=lambda i: points[i].y)
        else:
            left_idx = max(left_indices, key=lambda i: points[i].y)
            right_idx = max(right_indices, key=lambda i: points[i].y)

        left_move = y_share(points, left_idx, center)
        right_move = y_share(points, center, right_idx)
        total_x = x_share(points, left_idx, right_idx)
        base_gap = y_share(points, left_idx, right_idx)

        if left_move < SPIKE_MIN_Y_SHARE or right_move < SPIKE_MIN_Y_SHARE:
            continue
        if total_x > SPIKE_MAX_TOTAL_X:
            continue
        if base_gap > SPIKE_MAX_BASE_GAP_Y:
            continue

        anchor_kind = "low" if extreme.kind == "high" else "high"
        left = Turn(left_idx, anchor_kind)
        right = Turn(right_idx, anchor_kind)
        candidates.append((min(left_move, right_move), total_x, left, extreme, right))

    selected: list[tuple[Turn, Turn, Turn]] = []
    occupied: list[tuple[int, int]] = []
    for _, _, left, extreme, right in sorted(candidates, key=lambda item: (-item[0], item[1])):
        if any(not (right.index < start or left.index > end) for start, end in occupied):
            continue
        selected.append((left, extreme, right))
        occupied.append((left.index, right.index))

    return sorted(selected, key=lambda item: item[1].index)


# ---------------------------------------------------------------------------
# 5. Structure assembly.
# ---------------------------------------------------------------------------

def structure(points: list[Point]) -> dict[str, Any]:
    visible = visible_indices(points)
    if len(visible) < 3:
        return {"regimes": [], "pivots": [], "sideways_boundaries": [], "turning_points": []}

    v0, v1 = visible[0], visible[-1]

    # Exact extrema first. This guarantees exact visible global max/min are
    # always present as candidates before any structural filtering.
    extrema = exact_visible_candidates(points, v0, v1)

    # Sideways is detected directly from chart geometry, including straight
    # flat stretches that have almost no local extrema.
    flat = maximal_flat_boxes(points, v0, v1)
    oscillatory = oscillatory_boxes(points, extrema)
    boxes = merge_overlapping_boxes(points, [*flat, *oscillatory])

    # Spike anchors are found before ordinary pruning and are protected.
    spikes = find_spikes(points, extrema, v0, v1)
    spike_indices = {turn.index for triple in spikes for turn in triple}

    box_boundary_indices: set[int] = set()
    for box in boxes:
        box_boundary_indices.update({box.start_index, box.end_index})

    protected = spike_indices | box_boundary_indices

    # Discover flow, then validate it in fixed-axis geometry.
    skeleton = add_context_anchors(points, extrema, v0, v1)
    skeleton = merge_continuation_waves(points, skeleton, protected)
    skeleton, review_turns = prune_visual_micro_turns(points, skeleton, protected)
    skeleton = merge_continuation_waves(points, skeleton, protected)
    skeleton, more_reviews = prune_visual_micro_turns(points, skeleton, protected)
    review_turns.extend(more_reviews)

    accepted: dict[int, dict[str, Any]] = {}

    # Ordinary A pivots: only real extrema, never synthetic context endpoints.
    extrema_by_index = {turn.index: turn for turn in extrema}
    for i, turn in enumerate(skeleton):
        if turn.index not in extrema_by_index:
            continue
        if i == 0 or i == len(skeleton) - 1:
            continue
        incoming = y_share(points, skeleton[i - 1].index, turn.index)
        outgoing = y_share(points, turn.index, skeleton[i + 1].index)
        if min(incoming, outgoing) < MIN_STRUCTURAL_Y_SHARE:
            continue

        transition = "상승→하락" if turn.kind == "high" else "하락→상승"
        accepted[turn.index] = {
            "turn": turn,
            "type": "major_reversal",
            "grade": "A",
            "reason": (
                f"A: {transition} 구조적 반전. 극점은 원시 지표값에서 정확히 찾았고, "
                f"검증 시 반전 전·후 이동이 고정 Y축의 {incoming*100:.1f}%와 "
                f"{outgoing*100:.1f}%를 차지해 구조점으로 유지."
            ),
        }

    # Borderline reversals are D and hidden from the chart.
    seen_review: set[int] = set()
    for turn, incoming, outgoing in review_turns:
        if turn.index in accepted or turn.index in seen_review or turn.index in protected:
            continue
        seen_review.add(turn.index)
        accepted[turn.index] = {
            "turn": turn,
            "type": "review_required",
            "grade": "D",
            "reason": (
                f"D: 반전 후보이지만 고정 Y축 검증 결과 전·후 이동이 "
                f"{incoming*100:.1f}%와 {outgoing*100:.1f}%로 자동 구조점 기준에 못 미쳐 보류."
            ),
        }

    # Sideways boundaries override ordinary pivots only when the box itself
    # survives the short/long rule.
    sideways_boundaries: list[dict[str, Any]] = []
    kept_boxes: list[Box] = []
    for number, box in enumerate(boxes, 1):
        if not box_should_survive(points, box, skeleton):
            continue
        kept_boxes.append(box)

        width = x_share(points, box.start_index, box.end_index)
        y_values = [points[i].y for i in range(box.start_index, box.end_index + 1)]
        vertical_span = max(y_values) - min(y_values)

        entry = box_boundary_turn(points, box, "entry")
        exit_ = box_boundary_turn(points, box, "exit")

        sideways_boundaries.append({
            "id": f"box-{number}",
            "start_date": points[box.start_index].date,
            "end_date": points[box.end_index].date,
            "mode": box.mode,
            "x_share": round(width, 6),
            "y_span": round(vertical_span, 6),
            "long": width >= LONG_BOX_X_SHARE,
        })

        accepted[entry.index] = {
            "turn": entry,
            "type": "sideways_entry",
            "grade": "B",
            "reason": (
                f"B: 횡보 진입점. 이 비방향성 구간은 고정 X축의 {width*100:.1f}%를 차지하고 "
                f"고정 Y축 내 전체 높이는 {vertical_span*100:.1f}%로 검증됨."
            ),
        }
        accepted[exit_.index] = {
            "turn": exit_,
            "type": "sideways_exit",
            "grade": "B",
            "reason": (
                f"B: 횡보 이탈점. 같은 횡보 구간이 고정 X축의 {width*100:.1f}%를 차지하며 "
                "이 지점에서 비방향성 구간이 끝나 이후 방향 진행으로 전환됨."
            ),
        }

    # Spike triplet overrides ordinary labels.
    for left, extreme, right in spikes:
        left_move = y_share(points, left.index, extreme.index)
        right_move = y_share(points, extreme.index, right.index)
        width = x_share(points, left.index, right.index)

        accepted[left.index] = {
            "turn": left,
            "type": "spike_entry",
            "grade": "A",
            "reason": (
                f"A: 스파이크 진입점. 이 점부터 정확한 극점까지 고정 Y축의 {left_move*100:.1f}%를 "
                f"이동했고 전체 왕복 폭은 고정 X축의 {width*100:.1f}%라 구조적 시작점으로 유지."
            ),
        }
        accepted[extreme.index] = {
            "turn": extreme,
            "type": "spike_extreme",
            "grade": "A",
            "reason": (
                f"A: 스파이크 극점. 양쪽 이동이 고정 Y축의 {left_move*100:.1f}%와 "
                f"{right_move*100:.1f}%이고 전체 왕복이 고정 X축의 {width*100:.1f}% 안에서 발생."
            ),
        }
        accepted[right.index] = {
            "turn": right,
            "type": "spike_retracement",
            "grade": "A",
            "reason": (
                f"A: 스파이크 복귀점. 극점에서 이 점까지 고정 Y축의 {right_move*100:.1f}%를 "
                "되돌려 스파이크 구조의 반대편 앵커로 유지."
            ),
        }

    # Accepted boxes suppress their internal ordinary A/D noise. Spike points survive.
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
            "confidence": 1.0 if item["grade"] in {"A", "B"} else 0.5,
            "post_trend": None,
        })

    structural = [pivot for pivot in pivots if pivot["grade"] in {"A", "B"}]
    date_to_index = {points[i].date: i for i in visible}
    structural_dates = [pivot["date"] for pivot in structural]

    for pivot in structural:
        idx = date_to_index.get(pivot["date"])
        if idx is None:
            continue
        later = [value for value in structural_dates if value > pivot["date"]]
        end_date = later[0] if later else points[v1].date
        end_idx = date_to_index.get(end_date, v1)
        if pivot["type"] == "sideways_entry":
            post = "sideways"
        else:
            post = "up" if points[end_idx].y > points[idx].y else "down" if points[end_idx].y < points[idx].y else "sideways"
        pivot["post_trend"] = {"direction": post, "end_date": end_date}

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
            "date": pivot["date"],
            "value": pivot["value"],
            "type": pivot["type"],
            "grade": pivot["grade"],
            "direction": pivot["direction"],
            "reason": pivot["reason"],
        }
        for i, pivot in enumerate(pivots)
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
    # Market metadata is attached only AFTER indicator-only structure is final.
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
        "source_point_count": len([point for point in points if 0 <= point.x <= 1]),
        "chart_sha256": None,
        "anomaly_validation_version": "rule-spike-v4",
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
