"""Score stored Pivot AI structures for Historical Insight.

Only A/B pivots are eligible for normal market matching. C/D pivots remain stored
for audit only. Direct-window matches receive normal scores, expanded-window
near-misses receive half-weight scores, and A/B pivots outside the expanded
window remain display-only with no score. Anomalies remain separately preserved.
"""
from __future__ import annotations

import argparse
import calendar
from datetime import date
from typing import Any

from common import SupabaseRest

SCORING_VERSION = "pivot-ai-score-v5"
REFERENCE_ORDER = ("START", "PEAK", "TROUGH")
REFERENCE_WEIGHTS = {"structural": 0.45, "timing": 0.35, "duration": 0.20}
OVERALL_WEIGHTS = {"best": 0.70, "mean": 0.30}
COVERAGE_BONUS = {1: 0, 2: 12, 3: 25}
GRADE_STRUCTURAL = {"A": 100.0, "B": 88.0, "C": 72.0, "D": 58.0}
NEAR_MISS_SCORE_FACTOR = 0.50
MARKET_REFERENCE_ROLE = {"START": "up_start", "PEAK": "down_start", "TROUGH": "down_end"}
OPPOSITE_ROLE = {"up_start": "down_start", "down_start": "up_start", "down_end": "up_end", "up_end": "down_end"}
TRANSITION_ROLES = {
    ("falling", "rising"): ("up_start", "down_end"),
    ("sideways", "rising"): ("up_start",),
    ("rising", "falling"): ("down_start", "up_end"),
    ("sideways", "falling"): ("down_start",),
    ("rising", "sideways"): ("up_end",),
    ("falling", "sideways"): ("down_end",),
}
REGIME_MAP = {"uptrend": "rising", "downtrend": "falling", "sideways": "sideways"}


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


def timing_score(offset_days: int, before: int, after: int) -> float:
    if offset_days < -before or offset_days > after:
        return 0.0
    span = max(1, before + after)
    elapsed = offset_days + before
    return max(0.0, min(100.0, (1.0 - elapsed / span) * 100.0))


def duration_score(indicator_days: int, market_days: int | None) -> float:
    if not market_days or market_days <= 0:
        return 0.0
    return min(max(indicator_days, 0) / market_days, 1.0) * 100.0


def structural_score(pivot: dict[str, Any]) -> float:
    base = GRADE_STRUCTURAL.get(str(pivot.get("grade") or "D"), 58.0)
    confidence = max(0.0, min(1.0, float(pivot.get("confidence") or 0.0)))
    return min(100.0, base * 0.85 + confidence * 15.0)


def normalized_regimes(regimes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for item in regimes or []:
        kind = REGIME_MAP.get(str(item.get("type")))
        start = str(item.get("start_date") or "")[:10]
        end = str(item.get("end_date") or "")[:10] or None
        if kind and start:
            out.append({"type": kind, "start": start, "end": end})
    return sorted(out, key=lambda row: row["start"])


def transition_for(pivot: dict[str, Any], regimes: list[dict[str, Any]]) -> tuple[str, str]:
    pivot_date = str(pivot.get("date"))[:10]
    previous = next((row["type"] for row in regimes if row.get("end") == pivot_date), None)
    next_regime = next((row["type"] for row in regimes if row.get("start") == pivot_date), None)
    if previous and next_regime:
        return previous, next_regime
    ptype = str(pivot.get("type") or "")
    direction = str(pivot.get("direction") or "neutral")
    if ptype in {"major_reversal", "local_reversal", "anomaly"}:
        return ("rising", "falling") if direction == "high" else ("falling", "rising")
    if ptype == "sideways_entry":
        return ("rising", "sideways") if direction == "high" else ("falling", "sideways")
    if ptype == "sideways_exit":
        return ("sideways", "falling") if direction == "high" else ("sideways", "rising")
    return "sideways", "sideways"


def structural_relationship(reference_type: str, previous: str, next_regime: str) -> str:
    reference_role = MARKET_REFERENCE_ROLE[reference_type]
    roles = TRANSITION_ROLES.get((previous, next_regime), ())
    if reference_role in roles:
        return "positive"
    if OPPOSITE_ROLE[reference_role] in roles:
        return "inverse"
    return "unclear"


def persistence_days(pivot_date: str, next_regime: str, regimes: list[dict[str, Any]], analysis_end: str) -> int:
    if next_regime not in {"rising", "falling"}:
        current = next((row for row in regimes if row["start"] == pivot_date), None)
        end = current.get("end") if current else analysis_end
        return max(0, days_between(pivot_date, end or analysis_end))
    opposite = "falling" if next_regime == "rising" else "rising"
    later = [row for row in regimes if row["start"] > pivot_date]
    terminal = next((row for row in later if row["type"] == opposite), None)
    end = terminal["start"] if terminal else analysis_end
    return max(0, days_between(pivot_date, end))


def filter_pivots_for_scoring(
    pivots: list[dict[str, Any]],
    anomalies: list[dict[str, Any]],
    regimes: list[dict[str, Any]],
    analysis_end: str,
) -> list[dict[str, Any]]:
    """Return A/B pivots plus separately preserved anomaly candidates."""
    del regimes, analysis_end
    accepted = [dict(item) for item in pivots if str(item.get("grade") or "D").upper() in {"A", "B"}]
    existing_dates = {str(item.get("date"))[:10] for item in accepted}
    for anomaly in anomalies or []:
        anomaly_date = str(anomaly.get("date") or "")[:10]
        if not anomaly_date or anomaly_date in existing_dates:
            continue
        anomaly_type = str(anomaly.get("type") or "")
        direction = "low" if "down" in anomaly_type else "high" if "up" in anomaly_type else "neutral"
        accepted.append({
            "date": anomaly_date,
            "value": float(anomaly.get("value") or 0.0),
            "type": "anomaly",
            "grade": "B",
            "direction": direction,
            "confidence": float(anomaly.get("confidence") or 0.0),
            "anomaly_type": anomaly_type,
        })
    return sorted(accepted, key=lambda item: str(item.get("date") or ""))


def market_duration(reference_type: str, cycle: dict[str, Any]) -> int | None:
    start, peak, trough = cycle.get("start_date"), cycle.get("peak_date"), cycle.get("trough_date")
    if reference_type == "START" and start and peak:
        return max(1, days_between(str(start), str(peak)))
    if reference_type == "PEAK" and peak and trough:
        return max(1, days_between(str(peak), str(trough)))
    if reference_type == "TROUGH" and trough:
        return max(1, days_between(str(trough), shift_months(str(trough), 24)))
    return None


def post_trend_values(pivot: dict[str, Any]) -> tuple[str | None, str | None]:
    value = pivot.get("post_trend")
    if not isinstance(value, dict):
        return None, None
    direction = str(value.get("direction") or "")
    end_date = str(value.get("end_date") or "")[:10]
    if direction not in {"up", "down", "sideways"} or not end_date:
        return None, None
    try:
        if date.fromisoformat(end_date) < date.fromisoformat(str(pivot.get("date") or "")[:10]):
            return None, None
    except ValueError:
        return None, None
    return direction, end_date


def market_direction_for_reference(reference_type: str, cycle: dict[str, Any]) -> str | None:
    if reference_type == "START":
        return "up"
    if reference_type == "PEAK":
        return "down"
    if reference_type == "TROUGH":
        value = str(cycle.get("_post_trough_direction") or "")
        return value if value in {"up", "sideways"} else None
    return None


def relationship_from_post_trend(reference_type: str, post_direction: str, cycle: dict[str, Any]) -> str:
    market_direction = market_direction_for_reference(reference_type, cycle)
    if not market_direction:
        return "unclear"
    if post_direction == market_direction:
        return "positive"
    if post_direction == "down" and market_direction in {"up", "sideways"}:
        return "inverse"
    if market_direction == "down" and post_direction == "up":
        return "inverse"
    return "unclear"


def result_from_pivot(
    pivot: dict[str, Any],
    reference_type: str,
    reference_date: str,
    cycle: dict[str, Any],
    regimes: list[dict[str, Any]],
    analysis_end: str,
) -> dict[str, Any]:
    previous, next_regime = transition_for(pivot, regimes)
    offset = days_between(reference_date, str(pivot["date"]))
    window_from, window_to = shift_months(reference_date, -3), shift_months(reference_date, 1)
    before = max(1, days_between(window_from, reference_date))
    after = max(1, days_between(reference_date, window_to))
    structural = structural_score(pivot)
    timing = timing_score(offset, before, after)
    post_direction, post_end_date = post_trend_values(pivot)
    if post_direction and post_end_date:
        persistence = max(0, days_between(str(pivot["date"]), post_end_date))
        relationship = relationship_from_post_trend(reference_type, post_direction, cycle)
        relationship_source = "post_trend"
    else:
        # Legacy analyses remain scoreable until the updated prompt has been rerun.
        persistence = persistence_days(str(pivot["date"]), next_regime, regimes, analysis_end)
        relationship = structural_relationship(reference_type, previous, next_regime)
        relationship_source = "legacy_regime"
    duration = duration_score(persistence, market_duration(reference_type, cycle))
    base_score = min(100.0, structural * REFERENCE_WEIGHTS["structural"] + timing * REFERENCE_WEIGHTS["timing"] + duration * REFERENCE_WEIGHTS["duration"])
    return {
        "referenceType": reference_type,
        "referenceDate": reference_date,
        "pivotDate": str(pivot["date"]),
        "pivotValue": float(pivot["value"]),
        "pivotType": str(pivot.get("type") or ""),
        "pivotGrade": str(pivot.get("grade") or "D"),
        "pivotConfidence": float(pivot.get("confidence") or 0.0),
        "previousRegime": previous,
        "nextRegime": next_regime,
        "regimeBoundaryDate": str(pivot["date"]),
        "confirmationDate": str(pivot["date"]),
        "offsetDays": offset,
        "structuralScore": structural,
        "timingScore": timing,
        "durationScore": duration,
        "structuralPersistenceDays": persistence,
        "baseScore": base_score,
        "relationship": relationship,
        "nativeRelationship": relationship,
        "relationshipSource": relationship_source,
        "marketDirection": market_direction_for_reference(reference_type, cycle),
        "postTrendDirection": post_direction,
        "postTrendEndDate": post_end_date,
        "relationshipScore": duration,
        "relationshipBonus": 0.0,
        "relationshipConfidence": duration / 100.0,
        "pivotSelectionScore": structural,
        "pivotRole": "anomaly" if str(pivot.get("type")) == "anomaly" else "market-relevant",
        "markerStatus": "confirmed",
        "score": base_score,
    }


def same_pivot(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return left.get("pivotDate") == right.get("pivotDate")


def score_analysis(row: dict[str, Any], cycle: dict[str, Any]) -> dict[str, Any]:
    ai_pivots = list(row.get("pivots") or [])
    ai_anomalies = list(row.get("anomalies") or [])
    regimes = normalized_regimes(list(row.get("regimes") or []))
    analysis_end = str(row.get("display_end") or cycle.get("trough_date") or cycle.get("peak_date") or cycle.get("start_date"))
    pivots = filter_pivots_for_scoring(ai_pivots, ai_anomalies, regimes, analysis_end)

    candidates: dict[str, list[dict[str, Any]]] = {}
    near_miss_candidates: dict[str, list[dict[str, Any]]] = {}
    for reference_type in REFERENCE_ORDER:
        reference_date = cycle.get(f"{reference_type.lower()}_date")
        if not reference_date:
            candidates[reference_type], near_miss_candidates[reference_type] = [], []
            continue
        reference_date = str(reference_date)
        direct_from, direct_to = shift_months(reference_date, -3), shift_months(reference_date, 1)
        near_from, near_to = shift_months(reference_date, -6), shift_months(reference_date, 2)
        all_results = [result_from_pivot(pivot, reference_type, reference_date, cycle, regimes, analysis_end) for pivot in pivots]
        candidates[reference_type] = [item for item in all_results if direct_from <= item["pivotDate"] <= direct_to]
        near_miss_candidates[reference_type] = [
            item for item in all_results
            if near_from <= item["pivotDate"] <= near_to and not (direct_from <= item["pivotDate"] <= direct_to)
        ]

    assigned: dict[str, dict[str, Any] | None] = {}
    used: list[dict[str, Any]] = []
    for reference_type in REFERENCE_ORDER:
        available = [item for item in candidates[reference_type] if not any(same_pivot(item, previous) for previous in used)]
        available.sort(key=lambda item: (-item["structuralScore"], abs(item["offsetDays"]), -item["timingScore"], item["pivotDate"]))
        selected = available[0] if available else None
        assigned[reference_type] = selected
        if selected:
            used.append(selected)

    hypothesis = {"positive": 0.0, "inverse": 0.0}
    for result in assigned.values():
        if result and result["relationship"] in hypothesis:
            hypothesis[result["relationship"]] += max(0.0, float(result["pivotSelectionScore"]))
    total = hypothesis["positive"] + hypothesis["inverse"]
    winner = None if hypothesis["positive"] == hypothesis["inverse"] else ("positive" if hypothesis["positive"] > hypothesis["inverse"] else "inverse")
    dominance = hypothesis[winner] / total if winner and total else 0.0
    cycle_relationship = winner if winner and dominance >= 0.60 else "unresolved"

    normalized: dict[str, dict[str, Any] | None] = {}
    results: list[dict[str, Any]] = []
    for reference_type in REFERENCE_ORDER:
        result = assigned[reference_type]
        if not result:
            normalized[reference_type] = None
            continue
        native = result["relationship"]
        aligned = cycle_relationship != "unresolved" and native == cycle_relationship
        status = "unresolved" if cycle_relationship == "unresolved" else ("aligned" if aligned else "conflict")
        updated = dict(result)
        updated.update({
            "nativeRelationship": native,
            "relationship": native,
            "cycleRelationship": cycle_relationship,
            "relationshipStatus": status,
            "score": float(result["baseScore"]),
            "markerStatus": "confirmed",
        })
        normalized[reference_type] = updated
        results.append(updated)

    near_miss: list[dict[str, Any]] = []
    for reference_type in REFERENCE_ORDER:
        if normalized[reference_type]:
            continue
        available = [item for item in near_miss_candidates[reference_type] if not any(same_pivot(item, previous) for previous in used)]
        available.sort(key=lambda item: (abs(item["offsetDays"]), -item["structuralScore"], item["pivotDate"]))
        if not available:
            continue
        candidate = dict(available[0])
        candidate.update({
            "score": float(candidate["baseScore"]) * NEAR_MISS_SCORE_FACTOR,
            "markerStatus": "near_miss",
            "pivotRole": "near-miss",
            "scoreFactor": NEAR_MISS_SCORE_FACTOR,
        })
        near_miss.append(candidate)
        used.append(candidate)

    coverage_items = [item for item in results if item.get("relationshipStatus") == "aligned" and item.get("cycleRelationship") in {"positive", "inverse"}]
    coverage_count = len(coverage_items)
    coverage_bonus = float(COVERAGE_BONUS.get(coverage_count, 0))

    score_items = results + near_miss
    scores = [float(item["score"]) for item in score_items if item.get("score") is not None]
    if scores:
        best, mean = max(scores), sum(scores) / len(scores)
        overall = min(100.0, best * OVERALL_WEIGHTS["best"] + mean * OVERALL_WEIGHTS["mean"] + coverage_bonus)
        max_reference = best
    else:
        overall = 0.0
        max_reference = 0.0

    return {
        "case_code": row["case_code"],
        "index_code": row["index_code"],
        "series_code": row["series_code"],
        "overall_score": round(overall, 6),
        "meaningful_reference_count": len(score_items),
        "max_reference_score": round(max_reference, 6),
        "reference_coverage_count": coverage_count,
        "coverage_bonus": coverage_bonus,
        "cycle_relationship": cycle_relationship,
        "by_reference": normalized,
        "results": results,
        "near_miss_pivots": near_miss,
        "ai_pivots": ai_pivots,
        "filtered_pivots": pivots,
        "ai_regimes": row.get("regimes") or [],
        "ai_anomalies": ai_anomalies,
        "scoring_version": SCORING_VERSION,
        "source_analyzed_at": row["analyzed_at"],
    }


def post_trough_market_direction(db: SupabaseRest, index_code: str, trough_date: str | None) -> str | None:
    if not trough_date:
        return None
    end_date = shift_months(str(trough_date), 24)
    rows = fetch_all(db, "market_index_prices", {
        "select": "market_date,close",
        "index_code": f"eq.{index_code}",
        "market_date": f"gte.{trough_date}",
        "order": "market_date.asc",
    })
    usable = []
    for row in rows:
        market_date = str(row.get("market_date") or "")[:10]
        if not market_date or market_date > end_date:
            break
        try:
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        usable.append((market_date, close))
    if len(usable) < 2:
        return None
    # Product rule: after TROUGH, classify the market only as up or sideways.
    return "up" if usable[-1][1] > usable[0][1] else "sideways"


def score_rows(db: SupabaseRest, case_code: str | None = None, index_code: str | None = None, series_code: str | None = None) -> int:
    params = {"select": "case_code,index_code,series_code,display_end,regimes,pivots,anomalies,analyzed_at", "order": "case_code.asc,index_code.asc,series_code.asc"}
    if case_code:
        params["case_code"] = f"eq.{case_code}"
    if index_code:
        params["index_code"] = f"eq.{index_code}"
    if series_code and series_code != "all":
        params["series_code"] = f"eq.{series_code}"
    analyses = fetch_all(db, "historical_indicator_ai_analysis", params)
    if not analyses:
        raise RuntimeError("No Pivot AI analyses found for scoring.")

    cycle_cache: dict[tuple[str, str], dict[str, Any]] = {}
    scored = []
    for row in analyses:
        key = (str(row["case_code"]), str(row["index_code"]))
        if key not in cycle_cache:
            cycles = fetch_all(db, "historical_case_market_cycles", {
                "select": "case_code,index_code,start_date,peak_date,trough_date,cycle_status",
                "case_code": f"eq.{key[0]}",
                "index_code": f"eq.{key[1]}",
            })
            if not cycles:
                raise RuntimeError(f"Missing historical cycle: {key[0]}/{key[1]}")
            cycle = dict(cycles[0])
            cycle["_post_trough_direction"] = post_trough_market_direction(db, key[1], cycle.get("trough_date"))
            cycle_cache[key] = cycle
        scored.append(score_analysis(row, cycle_cache[key]))

    db.upsert("historical_indicator_ai_scores", scored, conflict="case_code,index_code,series_code")
    for row in scored:
        print(f"SCORED {row['case_code']}/{row['index_code']}/{row['series_code']}: {row['overall_score']:.1f} refs={row['meaningful_reference_count']} filtered={len(row['filtered_pivots'])}/{len(row['ai_pivots'])}")
    return len(scored)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case")
    parser.add_argument("--index")
    parser.add_argument("--series", default="all")
    args = parser.parse_args()
    count = score_rows(SupabaseRest(), args.case, args.index, args.series)
    print(f"Stored {count} AI pivot score row(s).")


if __name__ == "__main__":
    main()
