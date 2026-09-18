"""Historical Insight deterministic pivot detector — v3.

This implementation is intentionally rebuilt from scratch around the agreed rules.

Core contract
-------------
1. Structure detection receives ONE indicator only.
2. Historical Case start/end define the frozen X axis.
3. The indicator's visible min/max define the frozen Y axis.
4. Raw values are used to locate exact extrema and to store/display the value.
5. Every judgment that says a move is large/small, long/short, spike-like, or
   visually flat is validated in normalized fixed-axis coordinates.
6. +/-24 months of the SAME indicator are context only for visible-edge turns.
7. Market-index prices and market START/PEAK/TROUGH never enter detection.
8. Merge is the default.  HH/HL and LH/LL continuation waves are compressed.
9. Sideways and spike rules use X/Y math only because those rules explicitly
   require duration/amplitude validation.
"""
from __future__ import annotations

import argparse
import calendar
from dataclasses import dataclass
from datetime import date
from typing import Any

from common import SupabaseRest

INDEX_CODES = {"SP500", "NASDAQ_COMPOSITE", "KOSPI"}
SCHEMA_VERSION = "rule-structure-v3"
ENGINE_VERSION = "historical-rules-20260918-v3"
BUFFER_MONTHS = 24

# Axis-relative validation thresholds.  These are used only for the named task.
# Ordinary reversal: both adjacent legs must be visually meaningful on the
# frozen Y axis before the turn can be kept as an A pivot.
REVERSAL_MIN_Y_SHARE = 0.08
REVERSAL_REVIEW_Y_SHARE = 0.04

# Spike: deliberately strict.  A spike is an exceptional excursion, not merely
# a large ordinary wave.
SPIKE_MIN_Y_SHARE = 0.50
SPIKE_MAX_TOTAL_X_SHARE = 0.18

# Sideways: X is used only to decide whether the regime lasts long enough to
# matter; Y is used only for the special "nearly straight flat" case.
SIDEWAYS_MIN_X_SHARE = 0.10
LONG_SIDEWAYS_X_SHARE = 0.20
FLAT_SIDEWAYS_MAX_Y_SHARE = 0.06

# Minimum extrema count for an oscillatory (potentially large-amplitude) box.
OSCILLATORY_SIDEWAYS_MIN_TURNS = 5


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
class Sideways:
    start_index: int
    end_index: int
    mode: str  # flat | oscillatory
    long: bool


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
    return [i for i, point in enumerate(points) if 0.0 <= point.x <= 1.0]


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def exact_turns(points: list[Point], start_index: int, end_index: int) -> list[Turn]:
    """Find exact raw extrema, including flat-top/flat-bottom reversals.

    This stage deliberately uses raw values because its only job is to locate
    the exact high/low observation.  It does not decide importance.
    """
    if end_index - start_index < 2:
        return []

    turns: list[Turn] = []
    i = start_index
    last_nonflat = start_index
    previous_sign = 0

    while i < end_index:
        j = i + 1
        while j <= end_index and points[j].value == points[i].value:
            j += 1
        if j > end_index:
            break

        sign = _sign(points[j].value - points[i].value)
        if previous_sign and sign != previous_sign:
            # If a flat run sits at the reversal, choose the point at the end
            # of the run nearest the outgoing move.  The raw extreme value is
            # identical across the run, so this preserves the actual boundary.
            turn_index = i
            turns.append(Turn(turn_index, "high" if previous_sign > 0 else "low"))

        previous_sign = sign
        last_nonflat = j
        i = j

    # Deduplicate same-kind adjacent turns by keeping the more extreme raw value.
    collapsed: list[Turn] = []
    for turn in turns:
        if collapsed and collapsed[-1].kind == turn.kind:
            prior = collapsed[-1]
            if (turn.kind == "high" and points[turn.index].value > points[prior.index].value) or (
                turn.kind == "low" and points[turn.index].value < points[prior.index].value
            ):
                collapsed[-1] = turn
        else:
            collapsed.append(turn)
    return collapsed


def edge_turns_from_buffer(points: list[Point], visible_start: int, visible_end: int) -> list[Turn]:
    """Add a visible-edge turn only when buffer context proves a sign reversal."""
    found: list[Turn] = []

    def direction_between(left: int, right: int) -> int:
        if right <= left:
            return 0
        return _sign(points[right].value - points[left].value)

    # First visible point.
    if visible_start > 0 and visible_start < len(points) - 1:
        pre = direction_between(visible_start - 1, visible_start)
        post = direction_between(visible_start, min(visible_start + 1, visible_end))
        if pre and post and pre != post:
            found.append(Turn(visible_start, "high" if pre > 0 else "low"))

    # Last visible point.
    if 0 < visible_end < len(points) - 1:
        pre = direction_between(max(visible_start, visible_end - 1), visible_end)
        post = direction_between(visible_end, visible_end + 1)
        if pre and post and pre != post:
            found.append(Turn(visible_end, "high" if pre > 0 else "low"))

    return found


def ordered_turns(points: list[Point], visible_start: int, visible_end: int) -> list[Turn]:
    raw = exact_turns(points, visible_start, visible_end)
    for turn in edge_turns_from_buffer(points, visible_start, visible_end):
        if all(existing.index != turn.index for existing in raw):
            raw.append(turn)
    return sorted(raw, key=lambda item: item.index)


def leg_y(points: list[Point], left: Turn, right: Turn) -> float:
    return abs(points[right.index].y - points[left.index].y)


def leg_x(points: list[Point], left: Turn, right: Turn) -> float:
    return abs(points[right.index].x - points[left.index].x)


def continuation_direction(points: list[Point], four: list[Turn]) -> str | None:
    """HH/HL or LH/LL candidate discovery.  No size judgment here."""
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


def compress_continuations(points: list[Point], turns: list[Turn]) -> list[Turn]:
    """Merge HH/HL and LH/LL internal waves before reversal validation."""
    out = list(turns)
    changed = True
    while changed and len(out) >= 4:
        changed = False
        for i in range(len(out) - 3):
            if continuation_direction(points, out[i:i + 4]):
                del out[i + 1:i + 3]
                changed = True
                break
    return out


def validate_reversals(points: list[Point], turns: list[Turn]) -> tuple[list[Turn], list[Turn]]:
    """Validate every candidate reversal on the frozen Y axis.

    This is where 'large/small enough to matter' is decided.  Candidate finding
    itself does not use the threshold.
    """
    accepted = list(turns)
    review: list[Turn] = []

    changed = True
    while changed and len(accepted) >= 3:
        changed = False
        for i in range(1, len(accepted) - 1):
            left, current, right = accepted[i - 1:i + 2]
            incoming = leg_y(points, left, current)
            outgoing = leg_y(points, current, right)
            visual_reversal = min(incoming, outgoing)

            if visual_reversal < REVERSAL_REVIEW_Y_SHARE:
                # Visually tiny on the frozen Y axis: merge it away.
                del accepted[i]
                changed = True
                break
            if visual_reversal < REVERSAL_MIN_Y_SHARE:
                review.append(current)
                del accepted[i]
                changed = True
                break

    return accepted, review


def find_spikes(points: list[Point], turns: list[Turn]) -> list[tuple[Turn, Turn, Turn]]:
    """Find exceptional spike triplets using only fixed-axis shares."""
    found: list[tuple[Turn, Turn, Turn]] = []

    for center_pos in range(len(turns)):
        extreme = turns[center_pos]

        best: tuple[Turn, Turn, Turn] | None = None
        best_span = None

        # Search outward rather than requiring immediate adjacent extrema so
        # small wiggles cannot hide the true structural entry/retracement point.
        for left_pos in range(center_pos - 1, -1, -1):
            left = turns[left_pos]
            if left.kind == extreme.kind:
                continue
            for right_pos in range(center_pos + 1, len(turns)):
                right = turns[right_pos]
                if right.kind != left.kind:
                    continue

                total_x = points[right.index].x - points[left.index].x
                if total_x <= 0 or total_x > SPIKE_MAX_TOTAL_X_SHARE:
                    continue

                left_y = leg_y(points, left, extreme)
                right_y = leg_y(points, extreme, right)
                if left_y < SPIKE_MIN_Y_SHARE or right_y < SPIKE_MIN_Y_SHARE:
                    continue

                if best is None or total_x < best_span:
                    best = (left, extreme, right)
                    best_span = total_x

        if best and all(best[1].index != item[1].index for item in found):
            found.append(best)

    return found


def flat_sideways_candidates(points: list[Point], visible_start: int, visible_end: int) -> list[Sideways]:
    """Detect visually flat long stretches, including near-straight lines."""
    candidates: list[Sideways] = []
    n = visible_end - visible_start + 1
    if n < 4:
        return candidates

    # Use candidate boundaries only at observations.  X/Y shares validate the
    # 'long' and 'visually flat' claims; raw values are never compared here.
    for start in range(visible_start, visible_end - 2):
        for end in range(visible_end, start + 2, -1):
            x_share = points[end].x - points[start].x
            if x_share < SIDEWAYS_MIN_X_SHARE:
                break
            segment_y = [points[i].y for i in range(start, end + 1)]
            y_span = max(segment_y) - min(segment_y)
            if y_span <= FLAT_SIDEWAYS_MAX_Y_SHARE:
                candidates.append(Sideways(
                    start_index=start,
                    end_index=end,
                    mode="flat",
                    long=x_share >= LONG_SIDEWAYS_X_SHARE,
                ))
                break

    # Keep only maximal non-contained flat intervals.
    maximal: list[Sideways] = []
    for candidate in sorted(candidates, key=lambda box: points[box.end_index].x - points[box.start_index].x, reverse=True):
        if any(candidate.start_index >= box.start_index and candidate.end_index <= box.end_index for box in maximal):
            continue
        maximal.append(candidate)
    return sorted(maximal, key=lambda box: box.start_index)


def mixed_progression(values: list[float]) -> bool:
    directions = set()
    for left, right in zip(values, values[1:]):
        if right > left:
            directions.add(1)
        elif right < left:
            directions.add(-1)
    return directions == {1, -1} or len(directions) == 0


def oscillatory_sideways_candidates(points: list[Point], turns: list[Turn]) -> list[Sideways]:
    """Detect large-amplitude boxes by absence of sustained extrema progress."""
    found: list[Sideways] = []
    for i in range(len(turns)):
        for j in range(len(turns) - 1, i + OSCILLATORY_SIDEWAYS_MIN_TURNS - 2, -1):
            window = turns[i:j + 1]
            x_share = points[window[-1].index].x - points[window[0].index].x
            if x_share < SIDEWAYS_MIN_X_SHARE:
                continue

            highs = [points[item.index].y for item in window if item.kind == "high"]
            lows = [points[item.index].y for item in window if item.kind == "low"]
            if len(highs) < 2 or len(lows) < 2:
                continue

            # A box may have huge amplitude.  What matters is that highs/lows
            # do not sustain one-direction progress at the structural scale.
            if not mixed_progression(highs) or not mixed_progression(lows):
                continue

            # Do not call it sideways if the whole window is clean HH/HL or LH/LL.
            has_clean_direction = any(
                continuation_direction(points, window[k:k + 4])
                for k in range(max(0, len(window) - 3))
            )
            if has_clean_direction:
                continue

            found.append(Sideways(
                start_index=window[0].index,
                end_index=window[-1].index,
                mode="oscillatory",
                long=x_share >= LONG_SIDEWAYS_X_SHARE,
            ))
            break

    maximal: list[Sideways] = []
    for candidate in sorted(found, key=lambda box: points[box.end_index].x - points[box.start_index].x, reverse=True):
        if any(candidate.start_index >= box.start_index and candidate.end_index <= box.end_index for box in maximal):
            continue
        maximal.append(candidate)
    return sorted(maximal, key=lambda box: box.start_index)


def merge_sideways(points: list[Point], boxes: list[Sideways]) -> list[Sideways]:
    selected: list[Sideways] = []
    for box in sorted(boxes, key=lambda item: points[item.end_index].x - points[item.start_index].x, reverse=True):
        if any(not (box.end_index < kept.start_index or box.start_index > kept.end_index) for kept in selected):
            continue
        selected.append(box)
    return sorted(selected, key=lambda item: item.start_index)


def nearest_turn_before(turns: list[Turn], index: int) -> Turn | None:
    return next((turn for turn in reversed(turns) if turn.index <= index), None)


def nearest_turn_after(turns: list[Turn], index: int) -> Turn | None:
    return next((turn for turn in turns if turn.index >= index), None)


def structure(points: list[Point]) -> dict[str, Any]:
    visible = visible_indices(points)
    if len(visible) < 3:
        return {"regimes": [], "pivots": [], "sideways_boundaries": [], "turning_points": []}

    v0, v1 = visible[0], visible[-1]
    raw = ordered_turns(points, v0, v1)

    # 1) Exact extrema first.
    # 2) Discover big directional flow via HH/HL and LH/LL.
    # 3) Validate reversal magnitude on fixed Y axis.
    compressed = compress_continuations(points, raw)
    validated, review_turns = validate_reversals(points, compressed)

    # Sideways is a regime hypothesis, not a by-product of local-turn count.
    flat_boxes = flat_sideways_candidates(points, v0, v1)
    oscillatory_boxes = oscillatory_sideways_candidates(points, raw)
    boxes = merge_sideways(points, [*flat_boxes, *oscillatory_boxes])

    # Spike is an explicit exceptional-shape rule and therefore uses X/Y shares.
    spikes = find_spikes(points, raw)
    spike_indices = {turn.index for triple in spikes for turn in triple}

    accepted: dict[int, dict[str, Any]] = {}

    # Ordinary validated reversals.
    for i, turn in enumerate(validated):
        if i == 0 or i == len(validated) - 1:
            continue
        left, right = validated[i - 1], validated[i + 1]
        incoming = leg_y(points, left, turn)
        outgoing = leg_y(points, turn, right)
        transition = "상승→하락" if turn.kind == "high" else "하락→상승"
        accepted[turn.index] = {
            "turn": turn,
            "type": "major_reversal",
            "grade": "A",
            "reason": (
                f"A: {transition} 구조적 반전. 후보 극점 자체는 원시값으로 정확히 찾았고, "
                f"반전 전·후 움직임이 고정 Y축의 {incoming*100:.1f}%와 {outgoing*100:.1f}%를 차지해 "
                "시각적으로 의미 있는 반전으로 검증되어 유지."
            ),
        }

    # D only for genuine reversal candidates that narrowly fail the Y-axis check.
    for turn in review_turns:
        if turn.index in accepted:
            continue
        accepted[turn.index] = {
            "turn": turn,
            "type": "review_required",
            "grade": "D",
            "reason": (
                f"D: 방향 반전 후보는 맞지만 인접 움직임 중 작은 쪽이 고정 Y축의 "
                f"{min(REVERSAL_MIN_Y_SHARE, max(REVERSAL_REVIEW_Y_SHARE, 0))*100:.0f}% 전후 구간이라 "
                "구조적 반전으로 자동 확정하지 않고 보류."
            ),
        }

    # Sideways anchors: use nearest exact extrema around the actual regime.
    sideways_boundaries: list[dict[str, Any]] = []
    for number, box in enumerate(boxes, 1):
        entry = nearest_turn_before(raw, box.start_index)
        exit_ = nearest_turn_after(raw, box.end_index)
        if not entry or not exit_ or entry.index >= exit_.index:
            continue

        x_share = points[exit_.index].x - points[entry.index].x
        segment_y = [points[i].y for i in range(entry.index, exit_.index + 1)]
        y_span = max(segment_y) - min(segment_y)

        sideways_boundaries.append({
            "id": f"box-{number}",
            "start_date": points[entry.index].date,
            "end_date": points[exit_.index].date,
            "mode": box.mode,
            "x_share": round(x_share, 6),
            "y_span": round(y_span, 6),
            "long": box.long,
        })

        if box.long:
            keep_box = True
        else:
            before = nearest_turn_before(validated, entry.index - 1)
            after = nearest_turn_after(validated, exit_.index + 1)
            if before and after:
                in_dir = "up" if points[entry.index].y > points[before.index].y else "down"
                out_dir = "up" if points[after.index].y > points[exit_.index].y else "down"
                keep_box = in_dir != out_dir
            else:
                keep_box = False

        if keep_box:
            accepted[entry.index] = {
                "turn": entry,
                "type": "sideways_entry",
                "grade": "B",
                "reason": (
                    f"B: 횡보 진입 구조점. 해당 비방향성 구간이 고정 X축의 {x_share*100:.1f}%를 차지"
                    + (f"하고 고정 Y축 내 변동폭은 {y_span*100:.1f}%인 직선형 횡보." if box.mode == "flat"
                       else "하며 고점·저점 진행 방향이 혼재되어 지속적인 상승/하락 진행이 없는 횡보.")
                ),
            }
            accepted[exit_.index] = {
                "turn": exit_,
                "type": "sideways_exit",
                "grade": "B",
                "reason": (
                    f"B: 횡보 이탈 구조점. 같은 횡보 구간이 고정 X축의 {x_share*100:.1f}%를 차지하며 "
                    "이 지점 이후 비방향성 구조가 끝나 방향 진행이 시작되어 유지."
                ),
            }

    # Spike triplets override ordinary labels because their structural reason is special.
    for left, extreme, right in spikes:
        left_y = leg_y(points, left, extreme)
        right_y = leg_y(points, extreme, right)
        total_x = points[right.index].x - points[left.index].x

        accepted[left.index] = {
            "turn": left,
            "type": "spike_entry",
            "grade": "A",
            "reason": (
                f"A: 스파이크 진입점. 이 점에서 스파이크 극점까지 고정 Y축의 {left_y*100:.1f}%를 "
                f"이동하고 전체 왕복이 고정 X축의 {total_x*100:.1f}% 안에서 발생해 구조적 시작점으로 유지."
            ),
        }
        accepted[extreme.index] = {
            "turn": extreme,
            "type": "spike_extreme",
            "grade": "A",
            "reason": (
                f"A: 스파이크 극점. 진입/복귀 양쪽 이동이 고정 Y축의 {left_y*100:.1f}%와 "
                f"{right_y*100:.1f}%이고 전체 폭이 고정 X축의 {total_x*100:.1f}%라 일반 파동이 아닌 특이점으로 유지."
            ),
        }
        accepted[right.index] = {
            "turn": right,
            "type": "spike_retracement",
            "grade": "A",
            "reason": (
                f"A: 스파이크 복귀점. 극점에서 이 점까지 고정 Y축의 {right_y*100:.1f}%를 되돌렸고 "
                "이후의 큰 구조를 판단하기 위한 반대편 구조적 극점이라 스파이크와 짝으로 유지."
            ),
        }

    # Accepted sideways suppresses internal ordinary A/D points. Spike points survive.
    for boundary in sideways_boundaries:
        start_date, end_date = boundary["start_date"], boundary["end_date"]
        start_idx = next((i for i in visible if points[i].date == start_date), None)
        end_idx = next((i for i in visible if points[i].date == end_date), None)
        if start_idx is None or end_idx is None:
            continue
        for idx in list(accepted):
            if start_idx < idx < end_idx and idx not in spike_indices:
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

    structural = [item for item in pivots if item["grade"] in {"A", "B"}]
    date_to_index = {points[i].date: i for i in visible}
    structural_dates = [item["date"] for item in structural]

    for pivot in pivots:
        if pivot["grade"] not in {"A", "B"}:
            continue
        idx = date_to_index.get(pivot["date"])
        if idx is None:
            continue
        later = [value for value in structural_dates if value > pivot["date"]]
        end_date = later[0] if later else points[v1].date
        end_idx = date_to_index.get(end_date, v1)
        if pivot["type"] == "sideways_entry":
            post = "sideways"
        elif points[end_idx].y > points[idx].y:
            post = "up"
        elif points[end_idx].y < points[idx].y:
            post = "down"
        else:
            post = "sideways"
        pivot["post_trend"] = {"direction": post, "end_date": end_date}

    regimes: list[dict[str, Any]] = []
    boundaries = sorted(set([v0, *[date_to_index[p["date"]] for p in structural if p["date"] in date_to_index], v1]))
    for left, right in zip(boundaries, boundaries[1:]):
        if left == right:
            continue
        in_box = any(
            boundary["start_date"] <= points[left].date and points[right].date <= boundary["end_date"]
            for boundary in sideways_boundaries
        )
        if in_box:
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
        "source_point_count": len([point for point in points if 0 <= point.x <= 1]),
        "chart_sha256": None,
        "anomaly_validation_version": "rule-spike-v3",
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
