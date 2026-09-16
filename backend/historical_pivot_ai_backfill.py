"""Backfill one or more Historical Insight indicator structures through Pivot AI."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import os
from datetime import date, timedelta
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import requests

from common import SupabaseRest, require_env

INDEX_CODES = {"SP500", "NASDAQ_COMPOSITE", "KOSPI"}
LEFT_RATIO = 0.15
MAIN_RATIO = 0.70
RIGHT_RATIO = 0.15


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
            break
        offset += page_size
    return rows


def shift_days(value: str, days: int) -> str:
    return (date.fromisoformat(value) + timedelta(days=days)).isoformat()


def display_window(start_date: str, end_date: str) -> tuple[str, str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    core_days = max(1, (end - start).days)
    context_days = round(core_days * LEFT_RATIO / MAIN_RATIO)
    return (start - timedelta(days=context_days)).isoformat(), (end + timedelta(days=context_days)).isoformat()


def load_case(db: SupabaseRest, case_code: str, index_code: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    cases = fetch_all(db, "historical_cases", {
        "select": "case_code,case_name,primary_index_code,search_start,search_end",
        "case_code": f"eq.{case_code}",
    })
    if not cases:
        raise RuntimeError(f"Historical case not found: {case_code}")
    case = cases[0]
    index_code = index_code or str(case["primary_index_code"])
    cycles = fetch_all(db, "historical_case_market_cycles", {
        "select": "case_code,index_code,start_date,peak_date,trough_date,cycle_status",
        "case_code": f"eq.{case_code}",
        "index_code": f"eq.{index_code}",
    })
    if not cycles:
        raise RuntimeError(f"Historical market cycle not found: {case_code}/{index_code}")
    cycle = cycles[0]
    if cycle.get("cycle_status") != "confirmed" or not cycle.get("start_date") or not cycle.get("trough_date"):
        raise RuntimeError("Only confirmed historical cycles with START/TROUGH can be backfilled.")
    return case, cycle


def load_index_rows(db: SupabaseRest, index_code: str, start: str, end: str) -> list[dict[str, Any]]:
    raw = fetch_all(db, "market_index_prices", {
        "select": "market_date,close",
        "index_code": f"eq.{index_code}",
        "market_date": f"gte.{start}",
        "and": f"(market_date.lte.{end})",
        "order": "market_date.asc",
    })
    return [{"date": str(row["market_date"])[:10], "value": float(row["close"])} for row in raw if row.get("close") is not None]


def load_indicator_rows(db: SupabaseRest, series_code: str, start: str, end: str) -> list[dict[str, Any]]:
    raw = fetch_all(db, "economic_chart_series_points", {
        "select": "observation_date,value",
        "series_code": f"eq.{series_code}",
        "observation_date": f"gte.{start}",
        "and": f"(observation_date.lte.{end})",
        "order": "observation_date.asc",
    })
    return [{"date": str(row["observation_date"])[:10], "value": float(row["value"])} for row in raw if row.get("value") is not None]


def eligible_series(db: SupabaseRest, start: str, end: str) -> list[str]:
    coverage = fetch_all(db, "economic_chart_series_coverage", {
        "select": "series_code,first_date,last_date,point_count",
        "first_date": f"lte.{start}",
        "last_date": f"gte.{end}",
        "order": "series_code.asc",
    })
    return [str(row["series_code"]) for row in coverage if str(row["series_code"]) not in INDEX_CODES and int(row.get("point_count") or 0) >= 3]


def render_chart(index_rows: list[dict[str, Any]], indicator_rows: list[dict[str, Any]], cycle: dict[str, Any], display_start: str, display_end: str, index_code: str, series_code: str) -> bytes:
    if len(index_rows) < 2 or len(indicator_rows) < 2:
        raise RuntimeError("Not enough chart data to render.")
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True, gridspec_kw={"height_ratios": [1, 1.25], "hspace": 0.08})
    fig.patch.set_facecolor("#08111f")
    for ax in axes:
        ax.set_facecolor("#08111f")
        ax.tick_params(colors="#aeb9c8", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#243247")
        ax.grid(True, axis="y", alpha=0.15)
        ax.set_xlim(date.fromisoformat(display_start), date.fromisoformat(display_end))
    axes[0].plot([date.fromisoformat(row["date"]) for row in index_rows], [row["value"] for row in index_rows], linewidth=1.5, color="#70b7ff")
    axes[1].plot([date.fromisoformat(row["date"]) for row in indicator_rows], [row["value"] for row in indicator_rows], linewidth=1.6, color="#d946ef")
    axes[0].set_title(f"Market index · {index_code}", loc="left", color="#e7edf6", fontsize=11, fontweight="bold")
    axes[1].set_title(f"Indicator · {series_code}", loc="left", color="#e7edf6", fontsize=11, fontweight="bold")
    marker_styles = {"START": (cycle.get("start_date"), "#22c55e"), "PEAK": (cycle.get("peak_date"), "#f59e0b"), "TROUGH": (cycle.get("trough_date"), "#ef4444")}
    for label, (value, color) in marker_styles.items():
        if not value:
            continue
        x = date.fromisoformat(str(value))
        for ax in axes:
            ax.axvline(x, linewidth=1.0, linestyle="--", color=color, alpha=0.75)
        axes[0].text(x, axes[0].get_ylim()[1], f" {label}", color=color, fontsize=8, va="top")
    axes[1].xaxis.set_major_locator(mdates.AutoDateLocator(minticks=6, maxticks=12))
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    plt.setp(axes[1].get_xticklabels(), rotation=0, ha="center")
    fig.suptitle(f"Historical Pivot AI · shared X-axis · {display_start} ~ {display_end}", color="#f3f6fb", fontsize=12, y=0.985)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buffer.getvalue()


def invoke_pivot_ai(payload: dict[str, Any]) -> dict[str, Any]:
    url = require_env("SUPABASE_URL").rstrip("/") + "/functions/v1/pivot-ai"
    key = require_env("SUPABASE_SERVICE_ROLE_KEY")
    response = requests.post(url, headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=payload, timeout=180)
    if not response.ok:
        raise RuntimeError(f"pivot-ai {response.status_code}: {response.text[:1200]}")
    return response.json()


def analyze_one(db: SupabaseRest, case_code: str, index_code: str | None, series_code: str) -> dict[str, Any]:
    case, cycle = load_case(db, case_code, index_code)
    index_code = str(cycle["index_code"])
    core_end = str(cycle.get("trough_date") or cycle.get("peak_date"))
    display_start, display_end = display_window(str(cycle["start_date"]), core_end)
    index_rows = load_index_rows(db, index_code, display_start, display_end)
    indicator_rows = load_indicator_rows(db, series_code, display_start, display_end)
    image = render_chart(index_rows, indicator_rows, cycle, display_start, display_end, index_code, series_code)
    payload = {
        "case_code": case_code,
        "case_name": case.get("case_name"),
        "index_code": index_code,
        "series_code": series_code,
        "cycle": {"start_date": cycle["start_date"], "peak_date": cycle.get("peak_date"), "trough_date": cycle.get("trough_date")},
        "display_window": {"start_date": display_start, "end_date": display_end, "left_ratio": LEFT_RATIO, "main_ratio": MAIN_RATIO, "right_ratio": RIGHT_RATIO},
        "indicator_points": indicator_rows,
        "chart_image_data_url": "data:image/png;base64," + base64.b64encode(image).decode("ascii"),
        "chart_sha256": hashlib.sha256(image).hexdigest(),
    }
    result = invoke_pivot_ai(payload)
    print(f"{case_code}/{index_code}/{series_code}: pivots={len(result.get('analysis', {}).get('pivots', []))} regimes={len(result.get('analysis', {}).get('regimes', []))} anomalies={len(result.get('analysis', {}).get('anomalies', []))}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--index")
    parser.add_argument("--series", default="all")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    db = SupabaseRest()
    case, cycle = load_case(db, args.case, args.index)
    core_end = str(cycle.get("trough_date") or cycle.get("peak_date"))
    display_start, display_end = display_window(str(cycle["start_date"]), core_end)
    series_codes = [args.series] if args.series != "all" else eligible_series(db, display_start, display_end)
    if args.limit > 0:
        series_codes = series_codes[: args.limit]
    if not series_codes:
        raise RuntimeError("No eligible indicators found.")
    failures: list[tuple[str, str]] = []
    for series_code in series_codes:
        try:
            analyze_one(db, args.case, args.index, series_code)
        except Exception as exc:  # one bad series must not erase successful backfill rows
            failures.append((series_code, str(exc)))
            print(f"ERROR {series_code}: {exc}")
    if failures:
        raise RuntimeError("Pivot AI failures: " + "; ".join(f"{code}={message}" for code, message in failures[:10]))


if __name__ == "__main__":
    main()
