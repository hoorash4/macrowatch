"""Rule-based Historical Insight pivot detector.

This is the primary deterministic detector.  It deliberately does not call AI.
The visual-first Pivot AI runner remains in historical_pivot_ai_backfill.py as a
fallback candidate pipeline.

Core invariants:
- the Historical Case search range is the fixed X axis;
- the indicator's visible-range min/max is the fixed Y axis;
- +/-24 month data is context only and never changes either axis;
- market-index prices are not an input to indicator structure detection;
- merge/large-structure interpretation is preferred over local segmentation;
- sideways means lack of meaningful directional progression, not zero slope or
  small amplitude;
- spikes are distinguished from broad waves by large Y excursion over short X.
"""
from __future__ import annotations

import argparse
import calendar
import math
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from common import SupabaseRest

INDEX_CODES = {"SP500", "NASDAQ_COMPOSITE", "KOSPI"}
SCHEMA_VERSION = "rule-structure-v1"
ENGINE_VERSION = "historical-rules-20260918-v1"
BUFFER_MONTHS = 24

# Initial values only.  These are intentionally centralized so later tuning can
# change numbers without changing structural rules.
MAJOR_TURN_Y = 0.09
UNCERTAIN_TURN_Y = 0.055
CONTINUATION_TOLERANCE_Y = 0.035
PLATEAU_MIN_X = 0.08
PLATEAU_LONG_X = 0.12
PLATEAU_NET_DRIFT_Y = 0.11
PLATEAU_HALF_DRIFT_Y = 0.11
PLATEAU_DIRECTIONAL_EFFICIENCY = 0.42
SPIKE_MIN_Y = 0.13
SPIKE_MAX_X = 0.09
EDGE_ZONE_X = 0.04
MAX_D_PIVOTS = 5


@dataclass(frozen=True)
class Point:
    date: str
    value: float
    x: float = 0.0
    y: float = 0.0
    smooth: float = 0.0


@dataclass(frozen=True)
class Extreme:
    index: int
    kind: str  # high | low
    prominence: float


@dataclass(frozen=True)
class Plateau:
    start_index: int
    end_index: int
    start_extreme_index: int
    end_extreme_index: int
    x_span: float
    efficiency: float
    band_low: float
    band_high: float
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


def day_number(value: str) -> int:
    return date.fromisoformat(value).toordinal()


def days_between(left: str, right: str) -> int:
    return day_number(right) - day_number(left)


def median(values: Iterable[float]) -> float:
    seq = list(values)
    return statistics.median(seq) if seq else 0.0


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    items = sorted(values)
    if len(items) == 1:
        return items[0]
    pos = (len(items) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return items[lo]
    return items[lo] * (hi - pos) + items[hi] * (pos - lo)


def load_case(db: SupabaseRest, case_code: str, index_code: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    cases = fetch_all(db, "historical_cases", {
        "select": "case_code,case_name,primary_index_code,search_start,search_end",
        "case_code": f"eq.{case_code}",
    })
    if not cases:
        raise RuntimeError(f"Historical case not found: {case_code}")
    case = cases[0]
    if not case.get("search_end"):
        raise RuntimeError("Rule backfill currently requires a completed Historical Case search_end.")
    selected_index = index_code or str(case["primary_index_code"])
    cycles = fetch_all(db, "historical_case_market_cycles", {
        "select": "case_code,index_code,start_date,peak_date,trough_date,cycle_status",
        "case_code": f"eq.{case_code}",
        "index_code": f"eq.{selected_index}",
    })
    if not cycles:
        raise RuntimeError(f"Historical market cycle not found: {case_code}/{selected_index}")
    return case, cycles[0]


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
    # "all indicators" means every stored non-index series that overlaps the
    # fixed case window.  Full-window coverage is not required; missing early or
    # late history stays missing on the fixed X axis rather than being stretched.
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


def infer_smoothing_width(rows: list[dict[str, Any]]) -> int:
    if len(rows) < 4:
        return 1
    gaps = [
        max(1, days_between(str(rows[i - 1]["date"]), str(rows[i]["date"])))
        for i in range(1, len(rows))
    ]
    typical = median(gaps)
    if typical <= 3:
        return 7
    if typical <= 10:
        return 3
    return 1


def normalize_points(rows: list[dict[str, Any]], visible_start: str, visible_end: str) -> tuple[list[Point], float, float]:
    visible_rows = [row for row in rows if visible_start <= row["date"] <= visible_end]
    if len(visible_rows) < 3:
        raise RuntimeError("Not enough points inside the fixed case window.")
    y_min = min(float(row["value"]) for row in visible_rows)
    y_max = max(float(row["value"]) for row in visible_rows)
    y_span = max(y_max - y_min, 1e-12)
    x_span_days = max(1, days_between(visible_start, visible_end))
    width = infer_smoothing_width(visible_rows)

    raw_points: list[Point] = []
    for row in rows:
        x = days_between(visible_start, row["date"]) / x_span_days
        y = (float(row["value"]) - y_min) / y_span
        raw_points.append(Point(str(row["date"]), float(row["value"]), x=x, y=y, smooth=y))

    smooth_values: list[float] = []
    radius = width // 2
    for i, point in enumerate(raw_points):
        lo, hi = max(0, i - radius), min(len(raw_points), i + radius + 1)
        smooth_values.append(median(p.y for p in raw_points[lo:hi]))
    points = [
        Point(p.date, p.value, p.x, p.y, smooth_values[i])
        for i, p in enumerate(raw_points)
    ]
    return points, y_min, y_max


def visible_indices(points: list[Point]) -> list[int]:
    return [i for i, p in enumerate(points) if 0.0 <= p.x <= 1.0]


def local_extrema(points: list[Point]) -> list[Extreme]:
    visible = visible_indices(points)
    if len(visible) < 3:
        return []
    visible_days = max(1, days_between(points[visible[0]].date, points[visible[-1]].date))
    visible_gaps = [
        max(1, days_between(points[visible[i - 1]].date, points[visible[i]].date))
        for i in range(1, len(visible))
    ]
    radius_days = max(int(median(visible_gaps) * 2.5), int(visible_days * 0.018), 1)

    candidates: list[tuple[int, str]] = []
    for idx in visible:
        center = points[idx]
        window = [
            j for j, p in enumerate(points)
            if abs(days_between(center.date, p.date)) <= radius_days
        ]
        if len(window) < 2:
            continue
        values = [points[j].smooth for j in window]
        high = center.smooth >= max(values) - 1e-12
        low = center.smooth <= min(values) + 1e-12
        if high and not low:
            candidates.append((idx, "high"))
        elif low and not high:
            candidates.append((idx, "low"))

    # collapse adjacent candidates of the same kind to the more extreme point
    collapsed: list[tuple[int, str]] = []
    for idx, kind in candidates:
        if collapsed and collapsed[-1][1] == kind:
            prior_idx = collapsed[-1][0]
            better = points[idx].smooth > points[prior_idx].smooth if kind == "high" else points[idx].smooth < points[prior_idx].smooth
            if better:
                collapsed[-1] = (idx, kind)
        else:
            collapsed.append((idx, kind))

    extremes: list[Extreme] = []
    for idx, kind in collapsed:
        center = points[idx]
        # Prominence must not depend on already-detected opposite extrema.  A
        # clean V or a one-off spike can have only one interior local extreme,
        # while the meaningful shoulders are monotonic paths rather than local
        # extrema themselves.  Measure the candidate against the actual fixed-
        # axis path on each side.
        left_values = [
            p.smooth for p in points[:idx]
            if 0 < center.x - p.x <= 0.22
        ]
        right_values = [
            p.smooth for p in points[idx + 1:]
            if 0 < p.x - center.x <= 0.22
        ]
        if kind == "high":
            left_prom = center.smooth - min(left_values) if left_values else 0.0
            right_prom = center.smooth - min(right_values) if right_values else 0.0
        else:
            left_prom = max(left_values) - center.smooth if left_values else 0.0
            right_prom = max(right_values) - center.smooth if right_values else 0.0
        available = [v for v in (left_prom, right_prom) if v > 0]
        prominence = min(available) if len(available) == 2 else (available[0] if available else 0.0)
        extremes.append(Extreme(idx, kind, prominence))
    return extremes


def path_metrics(points: list[Point], start_idx: int, end_idx: int) -> tuple[float, float]:
    if end_idx <= start_idx:
        return 0.0, 0.0
    values = [p.smooth for p in points[start_idx:end_idx + 1]]
    net = values[-1] - values[0]
    travel = sum(abs(values[i] - values[i - 1]) for i in range(1, len(values)))
    efficiency = abs(net) / travel if travel > 1e-12 else 0.0
    return net, efficiency


def direction(points: list[Point], start_idx: int, end_idx: int, threshold: float = 0.045) -> str:
    net, efficiency = path_metrics(points, start_idx, end_idx)
    if abs(net) < threshold or efficiency < 0.22:
        return "sideways"
    return "up" if net > 0 else "down"


def entry_exit_direction(points: list[Point], start_idx: int, end_idx: int) -> tuple[str, str]:
    span = max(2, end_idx - start_idx)
    look = max(2, int(span * 0.55))
    before_start = max(0, start_idx - look)
    after_end = min(len(points) - 1, end_idx + look)
    return direction(points, before_start, start_idx), direction(points, end_idx, after_end)


def detect_plateaus(points: list[Point], extremes: list[Extreme]) -> list[Plateau]:
    strongish = [e for e in extremes if e.prominence >= UNCERTAIN_TURN_Y * 0.75]
    candidates: list[Plateau] = []
    for i in range(len(strongish)):
        for j in range(i + 3, len(strongish)):
            first, last = strongish[i], strongish[j]
            x_span = points[last.index].x - points[first.index].x
            if x_span < PLATEAU_MIN_X:
                continue
            segment = points[first.index:last.index + 1]
            if len(segment) < 4:
                continue
            highs = [e for e in strongish[i:j + 1] if e.kind == "high"]
            lows = [e for e in strongish[i:j + 1] if e.kind == "low"]
            if len(highs) < 2 or len(lows) < 2:
                continue
            net, efficiency = path_metrics(points, first.index, last.index)
            half = max(1, len(segment) // 2)
            half_drift = abs(median(p.smooth for p in segment[:half]) - median(p.smooth for p in segment[half:]))
            high_drift = points[highs[-1].index].smooth - points[highs[0].index].smooth
            low_drift = points[lows[-1].index].smooth - points[lows[0].index].smooth
            same_progression = (
                high_drift * low_drift > 0
                and abs(high_drift) >= PLATEAU_NET_DRIFT_Y * 0.75
                and abs(low_drift) >= PLATEAU_NET_DRIFT_Y * 0.75
            )
            directional = (
                same_progression
                or (abs(net) >= PLATEAU_NET_DRIFT_Y and efficiency >= PLATEAU_DIRECTIONAL_EFFICIENCY)
                or half_drift >= PLATEAU_HALF_DRIFT_Y
            )
            if directional:
                continue
            long = x_span >= PLATEAU_LONG_X
            entry_dir, exit_dir = entry_exit_direction(points, first.index, last.index)
            reversal_box = entry_dir in {"up", "down"} and exit_dir in {"up", "down"} and entry_dir != exit_dir
            if not long and not reversal_box:
                continue
            ys = [p.smooth for p in segment]
            candidates.append(Plateau(
                start_index=first.index,
                end_index=last.index,
                start_extreme_index=first.index,
                end_extreme_index=last.index,
                x_span=x_span,
                efficiency=efficiency,
                band_low=quantile(ys, 0.08),
                band_high=quantile(ys, 0.92),
                long=long,
            ))

    # Prefer the longest coherent box.  Overlapping smaller boxes are internal
    # descriptions of the same structure and should not survive.
    selected: list[Plateau] = []
    for candidate in sorted(candidates, key=lambda p: (p.x_span, -p.efficiency), reverse=True):
        if any(not (candidate.end_index < p.start_index or candidate.start_index > p.end_index) for p in selected):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda p: p.start_index)


def detect_spikes(points: list[Point], extremes: list[Extreme]) -> dict[int, dict[str, float]]:
    spikes: dict[int, dict[str, float]] = {}
    for extreme in extremes:
        idx = extreme.index
        p = points[idx]
        if not (0.02 <= p.x <= 0.98):
            continue
        pre = [q.smooth for q in points if 0.015 <= p.x - q.x <= 0.075]
        post = [q.smooth for q in points if 0.015 <= q.x - p.x <= 0.075]
        if len(pre) < 1 or len(post) < 1:
            continue
        pre_base, post_base = median(pre), median(post)
        baseline = (pre_base + post_base) / 2
        deviation = abs(p.smooth - baseline)
        if deviation < SPIKE_MIN_Y:
            continue
        if abs(pre_base - post_base) > max(0.08, deviation * 0.70):
            continue
        sign = 1 if p.smooth >= baseline else -1
        left_candidates = [
            q for q in points[:idx]
            if p.x - q.x <= SPIKE_MAX_X and sign * (q.smooth - baseline) <= deviation * 0.45
        ]
        right_candidates = [
            q for q in points[idx + 1:]
            if q.x - p.x <= SPIKE_MAX_X and sign * (q.smooth - baseline) <= deviation * 0.45
        ]
        if not left_candidates or not right_candidates:
            continue
        left = left_candidates[-1]
        right = right_candidates[0]
        width = right.x - left.x
        if width <= 0 or width > SPIKE_MAX_X:
            continue
        spikes[idx] = {"deviation": deviation, "width": width}
    return spikes


def edge_reversal_valid(points: list[Point], idx: int, kind: str) -> bool:
    p = points[idx]
    if EDGE_ZONE_X <= p.x <= 1.0 - EDGE_ZONE_X:
        return True
    if p.x < EDGE_ZONE_X:
        before = [i for i, q in enumerate(points) if q.x < 0 and p.x - q.x <= 0.20]
        after = [i for i, q in enumerate(points) if idx < i and q.x <= p.x + 0.12]
        if not before or not after:
            return False
        pre_dir = direction(points, before[0], idx)
        post_dir = direction(points, idx, after[-1])
    else:
        before = [i for i, q in enumerate(points) if i < idx and q.x >= p.x - 0.12]
        after = [i for i, q in enumerate(points) if q.x > 1 and q.x - p.x <= 0.20]
        if not before or not after:
            return False
        pre_dir = direction(points, before[0], idx)
        post_dir = direction(points, idx, after[-1])
    return (kind == "high" and pre_dir == "up" and post_dir == "down") or (kind == "low" and pre_dir == "down" and post_dir == "up")


def prune_continuation(points: list[Point], extremes: list[Extreme], locked: set[int]) -> list[Extreme]:
    out = list(extremes)
    changed = True
    while changed and len(out) >= 4:
        changed = False
        for i in range(len(out) - 3):
            a, b, c, d = out[i:i + 4]
            if b.index in locked or c.index in locked:
                continue
            ay, by, cy, dy = (points[e.index].smooth for e in (a, b, c, d))
            rising = (
                a.kind == "low" and b.kind == "high" and c.kind == "low" and d.kind == "high"
                and cy >= ay - CONTINUATION_TOLERANCE_Y
                and dy >= by + CONTINUATION_TOLERANCE_Y * 0.25
                and dy - ay >= MAJOR_TURN_Y
            )
            falling = (
                a.kind == "high" and b.kind == "low" and c.kind == "high" and d.kind == "low"
                and cy <= ay + CONTINUATION_TOLERANCE_Y
                and dy <= by - CONTINUATION_TOLERANCE_Y * 0.25
                and ay - dy >= MAJOR_TURN_Y
            )
            if rising or falling:
                del out[i + 1:i + 3]
                changed = True
                break
    return out


def point_reason(prefix: str, point: Point, prominence: float, extra: str = "") -> str:
    base = f"{prefix} 고정 Y축 대비 돌출도 {prominence * 100:.1f}%"
    return f"{base}. {extra}".strip()


def structure(points: list[Point]) -> dict[str, Any]:
    extremes = local_extrema(points)
    plateaus = detect_plateaus(points, extremes)
    spikes = detect_spikes(points, extremes)

    plateau_internal: set[int] = set()
    plateau_anchors: dict[int, tuple[str, Plateau]] = {}
    sideways_boundaries: list[dict[str, Any]] = []
    for n, plateau in enumerate(plateaus, 1):
        for i in range(plateau.start_index + 1, plateau.end_index):
            plateau_internal.add(i)
        plateau_anchors[plateau.start_extreme_index] = ("entry", plateau)
        plateau_anchors[plateau.end_extreme_index] = ("exit", plateau)
        sideways_boundaries.append({
            "id": f"box-{n}",
            "start_date": points[plateau.start_index].date,
            "end_date": points[plateau.end_index].date,
            "band_low_normalized": round(plateau.band_low, 6),
            "band_high_normalized": round(plateau.band_high, 6),
            "x_span_ratio": round(plateau.x_span, 6),
            "directional_efficiency": round(plateau.efficiency, 6),
            "long": plateau.long,
        })

    locked = set(plateau_anchors) | set(spikes)
    strong = [
        e for e in extremes
        if e.prominence >= MAJOR_TURN_Y
        and (e.index not in plateau_internal or e.index in spikes)
        and edge_reversal_valid(points, e.index, e.kind)
    ]
    # Box anchors are structural even when local prominence is modest.
    by_index = {e.index: e for e in strong}
    for idx in plateau_anchors:
        source = next((e for e in extremes if e.index == idx), None)
        if source:
            by_index[idx] = source
    strong = sorted(by_index.values(), key=lambda e: e.index)
    strong = prune_continuation(points, strong, locked)

    pivots: list[dict[str, Any]] = []
    accepted_indices = {e.index for e in strong}
    for e in strong:
        point = points[e.index]
        if e.index in plateau_anchors:
            boundary, plateau = plateau_anchors[e.index]
            ptype = "sideways_entry" if boundary == "entry" else "sideways_exit"
            reason = (
                f"B: {'횡보 진입' if boundary == 'entry' else '횡보 이탈'} 구조점. "
                f"해당 횡보는 고정 X축의 {plateau.x_span * 100:.1f}%를 차지하고 "
                f"방향효율은 {plateau.efficiency * 100:.1f}%로, 진폭 크기와 무관하게 "
                "고점·저점이 한 방향으로 유의미하게 진행하지 않는 구간이다. "
                "박스 내부 파동은 구조점에서 제외했다."
            )
            grade = "B"
        elif e.index in spikes:
            info = spikes[e.index]
            ptype = "spike_reversal"
            reason = (
                f"A: 스파이크형 구조적 극점. 고정 Y축의 {info['deviation'] * 100:.1f}%를 "
                f"고정 X축의 {info['width'] * 100:.1f}% 안에서 급격히 이탈·복귀했다. "
                "넓은 기간에 걸친 큰 파동과 달리 짧고 뾰족한 excursion이라 부모 추세의 "
                "전후 방향이 같더라도 극점을 보존한다."
            )
            grade = "A"
        else:
            ptype = "major_reversal"
            reason = (
                f"A: 큰 흐름의 방향을 바꾸는 반전 극점. 고정 Y축 대비 돌출도 "
                f"{e.prominence * 100:.1f}%이며, 연속된 상위 상승/하락 흐름을 병합한 뒤에도 "
                "이 점을 제거하면 구조 방향이 달라지므로 유지했다."
            )
            grade = "A"
        pivots.append({
            "date": point.date,
            "value": point.value,
            "type": ptype,
            "grade": grade,
            "direction": e.kind,
            "reason": reason,
            "confidence": round(min(1.0, max(0.5, e.prominence / max(MAJOR_TURN_Y, 1e-9))), 4),
            "post_trend": None,
        })

    # D = borderline structural candidates that are intentionally withheld.
    uncertain = [
        e for e in extremes
        if UNCERTAIN_TURN_Y <= e.prominence < MAJOR_TURN_Y
        and e.index not in accepted_indices
        and e.index not in plateau_internal
        and edge_reversal_valid(points, e.index, e.kind)
    ]
    uncertain = sorted(uncertain, key=lambda e: e.prominence, reverse=True)[:MAX_D_PIVOTS]
    for e in sorted(uncertain, key=lambda e: e.index):
        point = points[e.index]
        pivots.append({
            "date": point.date,
            "value": point.value,
            "type": "review_required",
            "grade": "D",
            "direction": e.kind,
            "reason": (
                f"D: 구조 후보지만 자동 확정을 보류했다. 고정 Y축 대비 돌출도 "
                f"{e.prominence * 100:.1f}%로 현재 A 기준({MAJOR_TURN_Y * 100:.1f}%)에는 "
                "못 미치지만 완전한 내부 노이즈로 단정하기도 어려워 관리자 판정이 필요하다."
            ),
            "confidence": round(e.prominence / MAJOR_TURN_Y, 4),
            "post_trend": None,
        })

    pivots.sort(key=lambda row: row["date"])
    structural = [p for p in pivots if p["grade"] in {"A", "B"}]
    plateau_ranges = [(p.start_index, p.end_index) for p in plateaus]

    # Build a simple, non-overlapping regime sequence from accepted structure.
    visible = visible_indices(points)
    if not visible:
        regimes: list[dict[str, Any]] = []
    else:
        boundaries = [visible[0]]
        pivot_index_by_date = {points[i].date: i for i in visible}
        boundaries.extend(pivot_index_by_date[p["date"]] for p in structural if p["date"] in pivot_index_by_date)
        boundaries.append(visible[-1])
        boundaries = sorted(set(boundaries))
        regimes = []
        for left, right in zip(boundaries, boundaries[1:]):
            is_box = any(left >= a and right <= b for a, b in plateau_ranges)
            if is_box:
                kind = "sideways"
            else:
                kind = {"up": "uptrend", "down": "downtrend", "sideways": "sideways"}[direction(points, left, right)]
            if regimes and regimes[-1]["type"] == kind:
                regimes[-1]["end_date"] = points[right].date
            else:
                regimes.append({
                    "type": kind,
                    "start_date": points[left].date,
                    "end_date": points[right].date,
                    "confidence": 1.0,
                })

    # post_trend is needed by the existing historical-reference relationship
    # layer.  It describes only the accepted structure after each A/B point.
    structural_dates = [p["date"] for p in structural]
    visible_end_date = points[visible[-1]].date if visible else (points[-1].date if points else "")
    for pivot in pivots:
        if pivot["grade"] not in {"A", "B"}:
            continue
        idx = pivot_index_by_date.get(pivot["date"])
        if idx is None:
            continue
        next_dates = [d for d in structural_dates if d > pivot["date"]]
        end_date = next_dates[0] if next_dates else visible_end_date
        end_idx = pivot_index_by_date.get(end_date, visible[-1] if visible else idx)
        if pivot["type"] == "sideways_entry":
            post = "sideways"
        else:
            post = direction(points, idx, end_idx)
        pivot["post_trend"] = {
            "direction": {"up": "up", "down": "down", "sideways": "sideways"}[post],
            "end_date": end_date,
        }

    turning_points = [
        {
            "id": f"tp-{i + 1}",
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


def analyze_one(db: SupabaseRest, case_code: str, index_code: str | None, series_code: str) -> dict[str, Any]:
    case, cycle = load_case(db, case_code, index_code)
    fixed_start = str(case["search_start"])
    fixed_end = str(case["search_end"])
    buffer_start = shift_months(fixed_start, -BUFFER_MONTHS)
    buffer_end = shift_months(fixed_end, BUFFER_MONTHS)
    raw_rows = load_indicator_rows(db, series_code, buffer_start, buffer_end)
    points, y_min, y_max = normalize_points(raw_rows, fixed_start, fixed_end)
    result = structure(points)
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    row = {
        "case_code": case_code,
        "index_code": str(cycle["index_code"]),
        "series_code": series_code,
        "display_start": fixed_start,
        "display_end": fixed_end,
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
        "anomaly_validation_version": "rule-spike-v1",
        "skeleton_trends": [],
        "sub_trends": [],
        "turning_points": result["turning_points"],
        "sideways_boundaries": result["sideways_boundaries"],
        "analyzed_at": now,
        "updated_at": now,
    }
    db.upsert("historical_indicator_ai_analysis", [row], conflict="case_code,index_code,series_code")
    print(
        f"{case_code}/{cycle['index_code']}/{series_code}: "
        f"A={sum(p['grade']=='A' for p in result['pivots'])} "
        f"B={sum(p['grade']=='B' for p in result['pivots'])} "
        f"D={sum(p['grade']=='D' for p in result['pivots'])} "
        f"boxes={len(result['sideways_boundaries'])} y=[{y_min:g},{y_max:g}]"
    )
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--index")
    parser.add_argument("--series", default="all")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    db = SupabaseRest()
    case, _ = load_case(db, args.case, args.index)
    fixed_start, fixed_end = str(case["search_start"]), str(case["search_end"])
    series_codes = [args.series] if args.series != "all" else eligible_series(db, fixed_start, fixed_end)
    if args.limit > 0:
        series_codes = series_codes[:args.limit]
    if not series_codes:
        raise RuntimeError("No overlapping indicator series found.")

    failures: list[tuple[str, str]] = []
    for series_code in series_codes:
        try:
            analyze_one(db, args.case, args.index, series_code)
        except Exception as exc:
            failures.append((series_code, str(exc)))
            print(f"ERROR {series_code}: {exc}")
    if failures:
        raise RuntimeError("Rule pivot failures: " + "; ".join(f"{code}={message}" for code, message in failures[:10]))


if __name__ == "__main__":
    main()
