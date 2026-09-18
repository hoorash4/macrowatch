"""Historical Insight deterministic indicator-structure detector.

This file intentionally implements only the user's agreed structural rules.

Hard boundary:
- The detector receives one indicator series only.
- Historical Case start/end fix the X axis.
- The indicator's visible min/max fix the Y axis.
- +/-24 months of the same indicator are used only to validate a pivot that
  sits exactly on a visible boundary.
- Market-index prices and market START/PEAK/TROUGH never enter structure()
  or any helper used by it.

Design rule:
- Big flow first, merge by default.
- Ordinary reversals are decided by structural direction changes, not generic
  amplitude/prominence scores.
- X/Y ratios are used only where the rule itself requires them:
  spike shape and long/short sideways context.
"""
from __future__ import annotations

import argparse
import calendar
from dataclasses import dataclass
from datetime import date
from typing import Any

from common import SupabaseRest

INDEX_CODES = {"SP500", "NASDAQ_COMPOSITE", "KOSPI"}
SCHEMA_VERSION = "rule-structure-v2"
ENGINE_VERSION = "historical-rules-20260918-v2"
BUFFER_MONTHS = 24

# Numeric criteria exist only where the agreed rule explicitly needs numbers.
# Spike = very large Y excursion in a short X interval.
SPIKE_MIN_Y_SHARE = 0.50
SPIKE_MAX_X_SHARE = 0.15

# A sideways interval must have enough alternating turns to establish that it
# is a regime rather than one ordinary correction.
SIDEWAYS_MIN_EXTREMA = 6
LONG_SIDEWAYS_MIN_X_SHARE = 0.20


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
    long: bool
    entry_direction: str | None
    exit_direction: str | None


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
        raise RuntimeError("Rule backfill requires a completed Historical Case search_end.")
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
    x_span_days = max(1, days_between(visible_start, visible_end))
    points = [
        Point(
            date=str(row["date"]),
            value=float(row["value"]),
            x=days_between(visible_start, str(row["date"])) / x_span_days,
            y=(float(row["value"]) - y_min) / y_span,
        )
        for row in rows
    ]
    return points, y_min, y_max


def visible_indices(points: list[Point]) -> list[int]:
    return [i for i, point in enumerate(points) if 0.0 <= point.x <= 1.0]


def raw_turns(points: list[Point], start_index: int, end_index: int) -> list[Turn]:
    """Return exact raw sign-change extrema plus segment endpoints.

    No radius, prominence, smoothing, or amplitude threshold is used.
    """
    if end_index <= start_index:
        return []
    turns: list[Turn] = []
    previous_sign = 0
    previous_nonflat_index = start_index
    for index in range(start_index + 1, end_index + 1):
        delta = points[index].value - points[previous_nonflat_index].value
        if delta == 0:
            continue
        sign = 1 if delta > 0 else -1
        if previous_sign and sign != previous_sign:
            turning_index = previous_nonflat_index
            turns.append(Turn(turning_index, "high" if previous_sign > 0 else "low"))
        previous_sign = sign
        previous_nonflat_index = index

    if turns:
        first_kind = "low" if turns[0].kind == "high" else "high"
        last_kind = "low" if turns[-1].kind == "high" else "high"
    else:
        first_kind = "low" if points[end_index].value >= points[start_index].value else "high"
        last_kind = "high" if first_kind == "low" else "low"
    return [Turn(start_index, first_kind), *turns, Turn(end_index, last_kind)]


def directional_four(points: list[Point], four: list[Turn]) -> str | None:
    """Recognize HH/HL or LH/LL progression using order only."""
    if len(four) != 4:
        return None
    a, b, c, d = four
    kinds = [item.kind for item in four]
    if kinds == ["low", "high", "low", "high"]:
        if points[c.index].value >= points[a.index].value and points[d.index].value >= points[b.index].value:
            return "up"
    if kinds == ["high", "low", "high", "low"]:
        if points[c.index].value <= points[a.index].value and points[d.index].value <= points[b.index].value:
            return "down"
    return None


def compress_big_flow(points: list[Point], turns: list[Turn]) -> list[Turn]:
    """Merge internal waves whenever HH/HL or LH/LL preserves one big trend."""
    out = list(turns)
    changed = True
    while changed and len(out) >= 4:
        changed = False
        for index in range(len(out) - 3):
            four = out[index:index + 4]
            if directional_four(points, four):
                del out[index + 1:index + 3]
                changed = True
                break
    return out


def spike_triplets(points: list[Point], turns: list[Turn]) -> list[tuple[Turn, Turn, Turn]]:
    """Preserve entry -> spike extreme -> retracement anchor.

    Y and X ratios are used here because spike is explicitly a size/time concept.
    """
    found: list[tuple[Turn, Turn, Turn]] = []
    for i in range(1, len(turns) - 1):
        left, extreme, right = turns[i - 1:i + 2]
        if left.kind != right.kind or extreme.kind == left.kind:
            continue
        y_left = abs(points[extreme.index].y - points[left.index].y)
        y_right = abs(points[extreme.index].y - points[right.index].y)
        x_span = points[right.index].x - points[left.index].x
        if y_left >= SPIKE_MIN_Y_SHARE and y_right >= SPIKE_MIN_Y_SHARE and 0 < x_span <= SPIKE_MAX_X_SHARE:
            found.append((left, extreme, right))
    return found


def progression_sign(values: list[float]) -> set[int]:
    signs: set[int] = set()
    for left, right in zip(values, values[1:]):
        if right > left:
            signs.add(1)
        elif right < left:
            signs.add(-1)
    return signs


def clear_sideways_window(points: list[Point], turns: list[Turn]) -> bool:
    """A clear box has repeated oscillation without sustained HH/HL or LH/LL.

    This uses ordering only.  Amplitude is deliberately irrelevant.
    """
    if len(turns) < SIDEWAYS_MIN_EXTREMA:
        return False
    highs = [points[item.index].value for item in turns if item.kind == "high"]
    lows = [points[item.index].value for item in turns if item.kind == "low"]
    if len(highs) < 3 or len(lows) < 3:
        return False
    # Both high sequence and low sequence must actually change direction.
    if progression_sign(highs) != {1, -1} or progression_sign(lows) != {1, -1}:
        return False
    # A box must not contain a clean 4-extrema directional run.  This is a
    # conservative rule: better to miss an ambiguous box than erase a real trend.
    for i in range(len(turns) - 3):
        if directional_four(points, turns[i:i + 4]):
            return False
    return True


def nearest_direction(points: list[Point], left: Turn | None, right: Turn | None) -> str | None:
    if not left or not right or left.index == right.index:
        return None
    return "up" if points[right.index].value > points[left.index].value else "down" if points[right.index].value < points[left.index].value else None


def detect_sideways(points: list[Point], raw: list[Turn], compressed: list[Turn], spike_indices: set[int]) -> list[Sideways]:
    """Find only clear sideways regimes, then use X solely for short/long context."""
    candidates: list[tuple[int, int]] = []
    i = 0
    while i + SIDEWAYS_MIN_EXTREMA <= len(raw):
        accepted: tuple[int, int] | None = None
        for j in range(len(raw), i + SIDEWAYS_MIN_EXTREMA - 1, -1):
            window = raw[i:j]
            if any(item.index in spike_indices for item in window):
                continue
            if clear_sideways_window(points, window):
                accepted = (i, j - 1)
                break
        if accepted:
            candidates.append(accepted)
            i = accepted[1] + 1
        else:
            i += 1

    out: list[Sideways] = []
    for start_pos, end_pos in candidates:
        start_turn, end_turn = raw[start_pos], raw[end_pos]
        previous = next((item for item in reversed(compressed) if item.index < start_turn.index), None)
        following = next((item for item in compressed if item.index > end_turn.index), None)
        entry_direction = nearest_direction(points, previous, start_turn)
        exit_direction = nearest_direction(points, end_turn, following)

        # X is used only now, because this is specifically the agreed
        # short-versus-long sideways rule.
        box_x = points[end_turn.index].x - points[start_turn.index].x
        left_x = points[start_turn.index].x - points[previous.index].x if previous else None
        right_x = points[following.index].x - points[end_turn.index].x if following else None
        long_box = bool(
            box_x >= LONG_SIDEWAYS_MIN_X_SHARE
            or (
                left_x is not None and right_x is not None
                and box_x >= left_x and box_x >= right_x
            )
        )
        reversal_box = bool(
            entry_direction in {"up", "down"} and exit_direction in {"up", "down"}
            and entry_direction != exit_direction
        )
        if reversal_box or long_box:
            out.append(Sideways(
                start_index=start_turn.index,
                end_index=end_turn.index,
                long=long_box,
                entry_direction=entry_direction,
                exit_direction=exit_direction,
            ))
    return out


def edge_verified(points: list[Point], turn: Turn, visible_start_index: int, visible_end_index: int) -> bool:
    """Use +/-24m indicator context only when the structural point is on an edge."""
    if turn.index not in {visible_start_index, visible_end_index}:
        return True
    if turn.index == visible_start_index:
        before = [point.value for point in points[:turn.index]]
        after = [point.value for point in points[turn.index + 1:visible_end_index + 1]]
    else:
        before = [point.value for point in points[visible_start_index:turn.index]]
        after = [point.value for point in points[turn.index + 1:]]
    if not before or not after:
        return False
    value = points[turn.index].value
    if turn.kind == "high":
        return max(before) < value and max(after) < value
    return min(before) > value and min(after) > value


def structural_reason(previous: Turn | None, current: Turn, following: Turn | None) -> str:
    if current.kind == "high":
        return "A: 큰 상승 흐름이 이 고점에서 종료되고 이후 큰 하락 흐름으로 전환되어 유지한 구조적 고점."
    return "A: 큰 하락 흐름이 이 저점에서 종료되고 이후 큰 상승 흐름으로 전환되어 유지한 구조적 저점."


def structure(points: list[Point]) -> dict[str, Any]:
    visible = visible_indices(points)
    if len(visible) < 3:
        return {"regimes": [], "pivots": [], "sideways_boundaries": [], "turning_points": []}
    v0, v1 = visible[0], visible[-1]

    raw = raw_turns(points, v0, v1)
    compressed = compress_big_flow(points, raw)

    spikes = spike_triplets(points, raw)
    spike_indices = {item.index for triple in spikes for item in triple}
    spike_extreme_indices = {triple[1].index for triple in spikes}
    spike_entry_exit_indices = {triple[0].index for triple in spikes} | {triple[2].index for triple in spikes}

    sideways = detect_sideways(points, raw, compressed, spike_indices)
    sideways_internal: set[int] = set()
    sideways_boundaries: list[dict[str, Any]] = []
    sideways_anchor_map: dict[int, str] = {}
    for number, box in enumerate(sideways, 1):
        for idx in range(box.start_index + 1, box.end_index):
            sideways_internal.add(idx)
        sideways_anchor_map[box.start_index] = "entry"
        sideways_anchor_map[box.end_index] = "exit"
        sideways_boundaries.append({
            "id": f"box-{number}",
            "start_date": points[box.start_index].date,
            "end_date": points[box.end_index].date,
            "long": box.long,
            "entry_direction": box.entry_direction,
            "exit_direction": box.exit_direction,
        })

    accepted: dict[int, dict[str, Any]] = {}

    # Ordinary structural reversals come only from the compressed big-flow path.
    for pos, turn in enumerate(compressed[1:-1], 1):
        if turn.index in sideways_internal and turn.index not in sideways_anchor_map:
            continue
        if not edge_verified(points, turn, v0, v1):
            continue
        accepted[turn.index] = {
            "turn": turn,
            "type": "major_reversal",
            "grade": "A",
            "reason": structural_reason(compressed[pos - 1], turn, compressed[pos + 1]),
        }

    # Sideways anchors override ordinary reversal labels.
    for box in sideways:
        for idx, ptype in ((box.start_index, "sideways_entry"), (box.end_index, "sideways_exit")):
            turn = next((item for item in raw if item.index == idx), None)
            if not turn:
                continue
            label = "진입" if ptype == "sideways_entry" else "이탈"
            why = (
                f"B: 반복 고점·저점이 한 방향으로 지속 진행하지 않아 횡보로 본 구간의 {label} 구조점. "
                + ("진입·이탈 방향이 같지만 주변 추세 구간보다 횡보 지속기간이 길어 구조적으로 유지." if box.long and box.entry_direction == box.exit_direction
                   else "횡보 전후의 큰 방향이 달라지는 구조라 진입·이탈점을 유지.")
            )
            accepted[idx] = {"turn": turn, "type": ptype, "grade": "B", "reason": why}

    # Spike structure preserves entry, exact raw extreme, and retracement anchor.
    for left, extreme, right in spikes:
        accepted[left.index] = {
            "turn": left,
            "type": "spike_entry",
            "grade": "A",
            "reason": "A: 고정 Y축의 50% 이상을 짧은 X구간에서 이탈하는 스파이크가 시작된 직전 구조적 극점이라 스파이크 진입점으로 유지.",
        }
        accepted[extreme.index] = {
            "turn": extreme,
            "type": "spike_extreme",
            "grade": "A",
            "reason": (
                f"A: 스파이크 극점. 진입점과 복귀점 양쪽으로 각각 고정 Y축의 {SPIKE_MIN_Y_SHARE*100:.0f}% 이상 이동했고 "
                f"왕복 구간이 고정 X축의 {SPIKE_MAX_X_SHARE*100:.0f}% 이내라 일반 파동과 분리해 유지."
            ),
        }
        accepted[right.index] = {
            "turn": right,
            "type": "spike_retracement",
            "grade": "A",
            "reason": "A: 스파이크 이후 급격한 복귀가 끝난 첫 구조적 반대 극점이라 스파이크 극점과 짝으로 유지.",
        }

    # Visible endpoints are context anchors, not automatic pivots.  The +/-24m
    # buffer is consulted only when a genuine structural turn lands on the
    # boundary; synthetic first/last points are never emitted as D by default.

    pivots: list[dict[str, Any]] = []
    for idx in sorted(accepted):
        item = accepted[idx]
        turn: Turn = item["turn"]
        point = points[idx]
        pivots.append({
            "date": point.date,
            "value": point.value,
            "type": item["type"],
            "grade": item["grade"],
            "direction": turn.kind,
            "reason": item["reason"],
            "confidence": 1.0 if item["grade"] in {"A", "B"} else 0.0,
            "post_trend": None,
        })

    # Build regimes only from accepted A/B structure. No generic magnitude math.
    structural = [item for item in pivots if item["grade"] in {"A", "B"}]
    date_to_index = {points[i].date: i for i in visible}
    boundaries = [v0, *[date_to_index[item["date"]] for item in structural if item["date"] in date_to_index], v1]
    boundaries = sorted(set(boundaries))
    regimes: list[dict[str, Any]] = []
    box_ranges = [(box.start_index, box.end_index) for box in sideways]
    for left, right in zip(boundaries, boundaries[1:]):
        if left == right:
            continue
        is_box = any(left >= start and right <= end for start, end in box_ranges)
        if is_box:
            kind = "sideways"
        else:
            kind = "uptrend" if points[right].value > points[left].value else "downtrend" if points[right].value < points[left].value else "sideways"
        if regimes and regimes[-1]["type"] == kind:
            regimes[-1]["end_date"] = points[right].date
        else:
            regimes.append({"type": kind, "start_date": points[left].date, "end_date": points[right].date, "confidence": 1.0})

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
            post_direction = "sideways"
        elif points[end_idx].value > points[idx].value:
            post_direction = "up"
        elif points[end_idx].value < points[idx].value:
            post_direction = "down"
        else:
            post_direction = "sideways"
        pivot["post_trend"] = {"direction": post_direction, "end_date": end_date}

    turning_points = [
        {
            "id": f"tp-{i+1}",
            "date": item["date"],
            "value": item["value"],
            "type": item["type"],
            "grade": item["grade"],
            "direction": item["direction"],
            "reason": item["reason"],
        }
        for i, item in enumerate(pivots)
    ]
    return {
        "regimes": regimes,
        "pivots": pivots,
        "sideways_boundaries": sideways_boundaries,
        "turning_points": turning_points,
    }


def analyze_indicator_only(db: SupabaseRest, case_code: str, series_code: str) -> tuple[dict[str, Any], dict[str, Any], list[Point], float, float]:
    case = load_case(db, case_code)
    fixed_start = str(case["search_start"])
    fixed_end = str(case["search_end"])
    raw_rows = load_indicator_rows(
        db,
        series_code,
        shift_months(fixed_start, -BUFFER_MONTHS),
        shift_months(fixed_end, BUFFER_MONTHS),
    )
    points, y_min, y_max = normalize_points(raw_rows, fixed_start, fixed_end)
    result = structure(points)
    return case, result, points, y_min, y_max


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
    # Market metadata is attached only after indicator structure is finalized.
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
        "anomaly_validation_version": "rule-spike-v2",
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

    fixed_start, fixed_end = str(case["search_start"]), str(case["search_end"])
    series_codes = [args.series] if args.series != "all" else eligible_series(db, fixed_start, fixed_end)
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
