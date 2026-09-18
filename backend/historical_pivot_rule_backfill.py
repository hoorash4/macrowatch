"""Historical Insight deterministic indicator structure detector — clean v7 rebuild.

User contract enforced by code:
- raw values are used only to find the exact raw extrema and to persist display values;
- after raw extrema discovery, every comparison is performed on the frozen chart
  coordinates x/y, where the Historical Case fixes X and the visible indicator
  min/max fix Y;
- "large/small/long/short" is never inferred from raw units or raw percent change;
- market-index prices never enter indicator structure detection;
- +/-24 months of the same indicator are edge context only and never rescale axes.

Order:
1) exact raw extrema
2) frozen-axis coordinates
3) structural-flow discovery (HH/HL, LH/LL)
4) axis-share validation / pruning
5) sideways and spike exceptions
6) persist exact raw dates/values
"""
from __future__ import annotations

import argparse
import calendar
from dataclasses import dataclass
from datetime import date
from typing import Any

from common import SupabaseRest

INDEX_CODES = {"SP500", "NASDAQ_COMPOSITE", "KOSPI"}
SCHEMA_VERSION = "rule-structure-v7"
ENGINE_VERSION = "historical-rules-20260918-v7"
BUFFER_MONTHS = 24

# These constants are used only where the rule itself is about chart geometry.
# They are shares of the already-frozen chart axes, never raw indicator units.
SPIKE_MIN_Y_SHARE = 0.50
SPIKE_MAX_X_SHARE = 0.16
FLAT_MAX_Y_TO_X = 0.35
FLAT_MIN_X_SHARE = 0.10
LONG_BOX_X_SHARE = 0.20
MICRO_MAX_Y_SHARE = 0.06
DOMINATED_LEG_RATIO = 0.34


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
    return [
        Point(
            date=str(row["date"]),
            value=float(row["value"]),
            x=days_between(visible_start, str(row["date"])) / x_days,
            y=(float(row["value"]) - y_min) / y_span,
        )
        for row in rows
    ], y_min, y_max


def visible_indices(points: list[Point]) -> list[int]:
    return [i for i, p in enumerate(points) if 0.0 <= p.x <= 1.0]


def dx(points: list[Point], a: int, b: int) -> float:
    return abs(points[b].x - points[a].x)


def dy(points: list[Point], a: int, b: int) -> float:
    return abs(points[b].y - points[a].y)


# ---------------------------------------------------------------------------
# RAW SECTION. Raw values are allowed only to locate exact extrema.
# ---------------------------------------------------------------------------

def exact_raw_turns(points: list[Point]) -> list[Turn]:
    if len(points) < 3:
        return []
    out: list[Turn] = []
    last_nonflat = 0
    previous_direction = 0

    for i in range(1, len(points)):
        if points[i].value == points[last_nonflat].value:
            continue
        direction = 1 if points[i].value > points[last_nonflat].value else -1
        if previous_direction and direction != previous_direction:
            out.append(Turn(last_nonflat, "high" if previous_direction > 0 else "low"))
        previous_direction = direction
        last_nonflat = i
    return collapse_same_kind(points, out)


def collapse_same_kind(points: list[Point], turns: list[Turn]) -> list[Turn]:
    out: list[Turn] = []
    for turn in sorted(turns, key=lambda t: t.index):
        if out and out[-1].kind == turn.kind:
            prior = out[-1]
            if turn.kind == "high":
                if points[turn.index].y > points[prior.index].y:
                    out[-1] = turn
            else:
                if points[turn.index].y < points[prior.index].y:
                    out[-1] = turn
        else:
            out.append(turn)
    return out


def exact_visible_extrema(points: list[Point], v0: int, v1: int) -> list[Turn]:
    turns = [t for t in exact_raw_turns(points) if v0 <= t.index <= v1]

    # Global visible high/low must always be present as candidates.
    hi = max(range(v0, v1 + 1), key=lambda i: points[i].value)
    lo = min(range(v0, v1 + 1), key=lambda i: points[i].value)
    for t in (Turn(hi, "high"), Turn(lo, "low")):
        if all(existing.index != t.index for existing in turns):
            turns.append(t)
    return collapse_same_kind(points, turns)


# ---------------------------------------------------------------------------
# From here on: normalized fixed-axis geometry only.
# ---------------------------------------------------------------------------

def visible_sequence(points: list[Point], extrema: list[Turn], v0: int, v1: int) -> list[Turn]:
    seq = list(extrema)
    if not seq or seq[0].index != v0:
        first_kind = "low" if (seq and seq[0].kind == "high") else "high"
        seq.insert(0, Turn(v0, first_kind))
    if not seq or seq[-1].index != v1:
        last_kind = "low" if (seq and seq[-1].kind == "high") else "high"
        seq.append(Turn(v1, last_kind))
    return collapse_same_kind(points, seq)


def continuation(points: list[Point], four: list[Turn]) -> str | None:
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


def merge_directional_continuations(points: list[Point], turns: list[Turn], protected: set[int]) -> list[Turn]:
    """Discover HH/HL and LH/LL, then validate the internal correction on Y-axis share."""
    out = list(turns)
    changed = True
    while changed and len(out) >= 4:
        changed = False
        for i in range(len(out) - 3):
            four = out[i:i + 4]
            if not continuation(points, four):
                continue
            b, c = four[1], four[2]
            if b.index in protected or c.index in protected:
                continue
            correction = dy(points, b.index, c.index)
            left_impulse = dy(points, four[0].index, b.index)
            right_impulse = dy(points, c.index, four[3].index)
            # Only discard when the counter-wave is chart-small compared with
            # the directional impulses around it.
            if correction <= min(left_impulse, right_impulse):
                del out[i + 1:i + 3]
                out = collapse_same_kind(points, out)
                changed = True
                break
    return out


def prune_dominated_zigzags(points: list[Point], turns: list[Turn], protected: set[int]) -> list[Turn]:
    """Remove tiny chart-height reversals, not raw-unit reversals."""
    out = list(turns)
    changed = True
    while changed and len(out) >= 3:
        changed = False
        for i in range(1, len(out) - 1):
            left, mid, right = out[i - 1:i + 2]
            if mid.index in protected:
                continue
            left_leg = dy(points, left.index, mid.index)
            right_leg = dy(points, mid.index, right.index)
            small = min(left_leg, right_leg)
            large = max(left_leg, right_leg)
            # A turn is disposable only when the smaller side is both visually
            # tiny on the fixed Y axis and clearly dominated by the other side.
            if small > MICRO_MAX_Y_SHARE or small > large * DOMINATED_LEG_RATIO:
                continue

            # Remove the tiny excursion as a pair with the adjacent same-kind
            # anchor, keeping the more extreme anchor in frozen-Y coordinates.
            if left.kind == right.kind:
                if left.index in protected and right.index in protected:
                    continue
                if left.index in protected:
                    survivor = left
                elif right.index in protected:
                    survivor = right
                elif left.kind == "high":
                    survivor = left if points[left.index].y >= points[right.index].y else right
                else:
                    survivor = left if points[left.index].y <= points[right.index].y else right
                out[i - 1:i + 2] = [survivor]
                out = collapse_same_kind(points, out)
                changed = True
                break
    return out


def find_spikes(points: list[Point], extrema: list[Turn]) -> list[tuple[Turn, Turn, Turn]]:
    """Find structural entry -> exact spike extreme -> structural retracement.

    Spike classification is the one place where large-Y + short-X is explicit.
    The entry/retracement anchors need not be immediately adjacent raw extrema.
    """
    spikes: list[tuple[Turn, Turn, Turn]] = []
    for k, extreme in enumerate(extrema):
        # Search several extrema outward so a tiny wiggle cannot hide the real
        # structural spike entry/retracement anchor.
        lefts = extrema[max(0, k - 6):k]
        rights = extrema[k + 1:k + 7]
        best: tuple[float, Turn, Turn] | None = None
        for left in lefts:
            if left.kind == extreme.kind:
                continue
            for right in rights:
                if right.kind != left.kind:
                    continue
                width = dx(points, left.index, right.index)
                if width <= 0 or width > SPIKE_MAX_X_SHARE:
                    continue
                left_move = dy(points, left.index, extreme.index)
                right_move = dy(points, extreme.index, right.index)
                excursion = min(left_move, right_move)
                if excursion < SPIKE_MIN_Y_SHARE:
                    continue
                score = excursion - width
                if best is None or score > best[0]:
                    best = (score, left, right)
        if best is not None:
            _, left, right = best
            spikes.append((left, extreme, right))

    # keep non-overlapping strongest structures
    unique: list[tuple[Turn, Turn, Turn]] = []
    used: set[int] = set()
    for triple in sorted(
        spikes,
        key=lambda t: min(dy(points, t[0].index, t[1].index), dy(points, t[1].index, t[2].index)),
        reverse=True,
    ):
        if any(t.index in used for t in triple):
            continue
        unique.append(triple)
        used.update(t.index for t in triple)
    return sorted(unique, key=lambda t: t[1].index)


def flat_boxes(points: list[Point], v0: int, v1: int) -> list[Box]:
    """Detect obvious long flat/stalled regimes directly from frozen X/Y geometry."""
    boxes: list[Box] = []
    # Consider every sufficiently separated pair. Keep windows whose total
    # vertical envelope is small relative to their horizontal chart width.
    for start in range(v0, v1):
        best_end: int | None = None
        ymin = ymax = points[start].y
        for end in range(start + 1, v1 + 1):
            ymin = min(ymin, points[end].y)
            ymax = max(ymax, points[end].y)
            width = dx(points, start, end)
            if width < FLAT_MIN_X_SHARE:
                continue
            yspan = ymax - ymin
            if yspan <= width * FLAT_MAX_Y_TO_X:
                best_end = end
            elif best_end is not None:
                break
        if best_end is not None:
            boxes.append(Box(start, best_end, "flat"))

    return maximal_boxes(boxes)


def oscillatory_boxes(points: list[Point], extrema: list[Turn]) -> list[Box]:
    """Large-amplitude boxes are allowed if highs/lows fail to progress directionally."""
    boxes: list[Box] = []
    for i in range(len(extrema)):
        for j in range(len(extrema) - 1, i + 3, -1):
            window = extrema[i:j + 1]
            if len(window) < 5:
                continue
            start, end = window[0].index, window[-1].index
            width = dx(points, start, end)
            if width < FLAT_MIN_X_SHARE:
                continue

            highs = [points[t.index].y for t in window if t.kind == "high"]
            lows = [points[t.index].y for t in window if t.kind == "low"]
            if len(highs) < 2 or len(lows) < 2:
                continue

            highs_up = all(b >= a for a, b in zip(highs, highs[1:]))
            lows_up = all(b >= a for a, b in zip(lows, lows[1:]))
            highs_down = all(b <= a for a, b in zip(highs, highs[1:]))
            lows_down = all(b <= a for a, b in zip(lows, lows[1:]))
            if (highs_up and lows_up) or (highs_down and lows_down):
                continue

            # No sustained directional progression. Net displacement must be
            # smaller than the internal vertical envelope; amplitude itself can
            # be large and therefore does not disqualify a box.
            ys = [points[t.index].y for t in window]
            envelope = max(ys) - min(ys)
            net = dy(points, start, end)
            if envelope <= 1e-12 or net > envelope * 0.45:
                continue
            boxes.append(Box(start, end, "oscillatory"))
            break
    return maximal_boxes(boxes)


def maximal_boxes(boxes: list[Box]) -> list[Box]:
    selected: list[Box] = []
    for box in sorted(boxes, key=lambda b: (b.end_index - b.start_index), reverse=True):
        if any(not (box.end_index < other.start_index or box.start_index > other.end_index) for other in selected):
            continue
        selected.append(box)
    return sorted(selected, key=lambda b: b.start_index)


def nearest_turn_before(turns: list[Turn], index: int) -> Turn | None:
    return next((t for t in reversed(turns) if t.index < index), None)


def nearest_turn_after(turns: list[Turn], index: int) -> Turn | None:
    return next((t for t in turns if t.index > index), None)


def box_survives(points: list[Point], box: Box, skeleton: list[Turn]) -> tuple[bool, dict[str, Any]]:
    before = nearest_turn_before(skeleton, box.start_index)
    after = nearest_turn_after(skeleton, box.end_index)
    if not before or not after:
        return False, {}

    width = dx(points, box.start_index, box.end_index)
    left_x = dx(points, before.index, box.start_index)
    right_x = dx(points, box.end_index, after.index)
    entry_delta = points[box.start_index].y - points[before.index].y
    exit_delta = points[after.index].y - points[box.end_index].y
    reversal = entry_delta != 0 and exit_delta != 0 and (entry_delta > 0) != (exit_delta > 0)
    long_box = width >= LONG_BOX_X_SHARE or width >= max(left_x, right_x)

    # short continuation pause disappears; long box or reversal box survives
    keep = long_box or reversal
    return keep, {
        "width": width,
        "left_x": left_x,
        "right_x": right_x,
        "long": long_box,
        "reversal": reversal,
    }


def boundary_kind(points: list[Point], index: int, side: str) -> str:
    if side == "entry" and index > 0:
        return "high" if points[index].y >= points[index - 1].y else "low"
    if side == "exit" and index + 1 < len(points):
        return "low" if points[index + 1].y >= points[index].y else "high"
    return "low"


def edge_reversal(points: list[Point], turn: Turn, v0: int, v1: int) -> bool:
    """Only genuine edge turns use the +/-24m same-indicator buffer."""
    if turn.index not in {v0, v1}:
        return True
    value = points[turn.index].y
    if turn.index == v0:
        before = [p.y for p in points[:v0]]
        after = [p.y for p in points[v0 + 1:v1 + 1]]
    else:
        before = [p.y for p in points[v0: v1]]
        after = [p.y for p in points[v1 + 1:]]
    if not before or not after:
        return False
    if turn.kind == "high":
        return max(before) < value and max(after) < value
    return min(before) > value and min(after) > value


def structure(points: list[Point]) -> dict[str, Any]:
    visible = visible_indices(points)
    if len(visible) < 3:
        return {"regimes": [], "pivots": [], "sideways_boundaries": [], "turning_points": []}

    v0, v1 = visible[0], visible[-1]
    extrema = exact_visible_extrema(points, v0, v1)
    sequence = visible_sequence(points, extrema, v0, v1)

    spikes = find_spikes(points, extrema)
    spike_indices = {t.index for triple in spikes for t in triple}
    global_high = max(range(v0, v1 + 1), key=lambda i: points[i].y)
    global_low = min(range(v0, v1 + 1), key=lambda i: points[i].y)

    # Global exact extrema stay candidates; they still need axis-share validation
    # before being persisted as ordinary reversals.
    protected = spike_indices | {global_high, global_low}

    skeleton = merge_directional_continuations(points, sequence, protected)
    skeleton = prune_dominated_zigzags(points, skeleton, protected)
    skeleton = merge_directional_continuations(points, skeleton, protected)
    skeleton = prune_dominated_zigzags(points, skeleton, protected)

    boxes = maximal_boxes([
        *flat_boxes(points, v0, v1),
        *oscillatory_boxes(points, extrema),
    ])

    accepted: dict[int, dict[str, Any]] = {}
    extrema_indices = {t.index for t in extrema}

    # Ordinary reversals: candidate first, then validate incoming/outgoing chart
    # shares. Never call a leg "large" here; report its actual fixed-Y share.
    for i in range(1, len(skeleton) - 1):
        turn = skeleton[i]
        if turn.index not in extrema_indices:
            continue
        incoming = dy(points, skeleton[i - 1].index, turn.index)
        outgoing = dy(points, turn.index, skeleton[i + 1].index)

        # Reject an isolated tiny opposite leg. This is precisely an axis-share
        # validation, not a raw-number or raw-percent check.
        small = min(incoming, outgoing)
        large = max(incoming, outgoing)
        if small <= MICRO_MAX_Y_SHARE and small <= large * DOMINATED_LEG_RATIO:
            continue
        if not edge_reversal(points, turn, v0, v1):
            continue

        accepted[turn.index] = {
            "turn": turn,
            "type": "major_reversal",
            "grade": "A",
            "reason": (
                f"A: {'상승→하락' if turn.kind == 'high' else '하락→상승'} 구조적 반전 후보를 "
                f"고정축으로 검증했다. 전·후 이동은 각각 Y축의 {incoming*100:.1f}%/{outgoing*100:.1f}%다. "
                "원시 수치가 아니라 이 축 비중 검증을 통과해 유지."
            ),
        }

    kept_boxes: list[Box] = []
    sideways_boundaries: list[dict[str, Any]] = []
    for n, box in enumerate(boxes, 1):
        keep, context = box_survives(points, box, skeleton)
        if not keep:
            continue
        kept_boxes.append(box)
        ys = [points[i].y for i in range(box.start_index, box.end_index + 1)]
        yspan = max(ys) - min(ys)
        width = context["width"]

        entry = Turn(box.start_index, boundary_kind(points, box.start_index, "entry"))
        exit_ = Turn(box.end_index, boundary_kind(points, box.end_index, "exit"))
        sideways_boundaries.append({
            "id": f"box-{n}",
            "start_date": points[box.start_index].date,
            "end_date": points[box.end_index].date,
            "mode": box.mode,
            "x_share": round(width, 6),
            "y_span": round(yspan, 6),
            "long": bool(context["long"]),
        })
        accepted[entry.index] = {
            "turn": entry,
            "type": "sideways_entry",
            "grade": "B",
            "reason": (
                f"B: 횡보 진입. 구간은 고정 X축의 {width*100:.1f}%를 차지하고 "
                f"전체 Y폭은 고정 Y축의 {yspan*100:.1f}%다. "
                f"{'장기 횡보' if context['long'] else '반전형 단기 횡보'}로 검증되어 경계를 유지."
            ),
        }
        accepted[exit_.index] = {
            "turn": exit_,
            "type": "sideways_exit",
            "grade": "B",
            "reason": (
                f"B: 횡보 이탈. 같은 횡보가 X축 {width*100:.1f}%, Y축 {yspan*100:.1f}%를 차지하며 "
                "이 지점 이후 방향 진행이 재개되어 경계를 유지."
            ),
        }

    # Accepted box suppresses internal ordinary reversals.
    for box in kept_boxes:
        for idx in list(accepted):
            if box.start_index < idx < box.end_index and idx not in spike_indices:
                del accepted[idx]

    # Spikes override ordinary/box labels and preserve all three anchors.
    for left, extreme, right in spikes:
        left_y = dy(points, left.index, extreme.index)
        right_y = dy(points, extreme.index, right.index)
        width = dx(points, left.index, right.index)
        accepted[left.index] = {
            "turn": left, "type": "spike_entry", "grade": "A",
            "reason": (
                f"A: 스파이크 시작점. 극점까지 Y축 {left_y*100:.1f}% 이동하고 "
                f"전체 왕복은 X축 {width*100:.1f}% 안에서 일어나 스파이크 구조의 시작 앵커로 유지."
            ),
        }
        accepted[extreme.index] = {
            "turn": extreme, "type": "spike_extreme", "grade": "A",
            "reason": (
                f"A: 정확한 스파이크 극점. 양쪽 이동은 Y축 {left_y*100:.1f}%/{right_y*100:.1f}%, "
                f"왕복 X폭은 {width*100:.1f}%로 검증."
            ),
        }
        accepted[right.index] = {
            "turn": right, "type": "spike_retracement", "grade": "A",
            "reason": (
                f"A: 스파이크 복귀점. 극점에서 Y축 {right_y*100:.1f}% 되돌린 뒤 "
                "스파이크 왕복 구조가 끝나는 반대편 앵커라 유지."
            ),
        }

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
        if any(box.start_index <= left and right <= box.end_index for box in kept_boxes):
            kind = "sideways"
        else:
            kind = "uptrend" if points[right].y > points[left].y else "downtrend" if points[right].y < points[left].y else "sideways"
        if regimes and regimes[-1]["type"] == kind:
            regimes[-1]["end_date"] = points[right].date
        else:
            regimes.append({"type": kind, "start_date": points[left].date, "end_date": points[right].date, "confidence": 1.0})

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
    rows = load_indicator_rows(db, series_code, shift_months(start, -BUFFER_MONTHS), shift_months(end, BUFFER_MONTHS))
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
        "anomaly_validation_version": "rule-spike-axis-v7",
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
        raise RuntimeError("Rule pivot failures: " + "; ".join(f"{code}={message}" for code, message in failures[:10]))


if __name__ == "__main__":
    main()
