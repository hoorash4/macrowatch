from __future__ import annotations

from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt

from common import SupabaseRest
from historical_pivot_base import (
    ChartGeometry,
    _get_rows_paginated,
    augment_spike_entry_points,
    build_envelope,
    calculate_base_pivots,
    load_case_series,
    normalize_rows,
    simplify_pivot_lines,
)

CASE_CODE = "tightening_2022"
INDEX_CODE = "NASDAQ_COMPOSITE"
TEN_TWO_CASE_CODE = "global_financial_crisis"
TEN_TWO_INDEX_CODE = "SP500"
WIDTH = 1200.0
HEIGHT = 600.0
OUT = Path("tmp_pivot_charts_buffer")
OUT.mkdir(exist_ok=True)


def derive_us10y2y(db: SupabaseRest):
    _, _, buffer_start, buffer_end = load_case_series(
        db,
        case_code=TEN_TWO_CASE_CODE,
        index_code=TEN_TWO_INDEX_CODE,
        series_code="US10Y",
    )

    def load(code: str):
        return _get_rows_paginated(db, "economic_chart_points", {
            "select": "observation_date,value,frequency",
            "series_code": f"eq.{code}",
            "observation_date": f"gte.{buffer_start.isoformat()}",
            "and": f"(observation_date.lte.{buffer_end.isoformat()})",
            "order": "observation_date.asc",
        })

    ten = {row["observation_date"]: float(row["value"]) for row in load("US10Y")}
    two = {row["observation_date"]: float(row["value"]) for row in load("US2Y")}
    rows = [
        {"observation_date": day, "value": ten[day] - two[day], "frequency": "D"}
        for day in sorted(ten.keys() & two.keys())
    ]
    return rows, "D", buffer_start, buffer_end


def geometry_for(rows, start: date, end: date):
    pts = normalize_rows(rows)
    visible = [p.value for p in pts if start <= p.day <= end]
    lo, hi = min(visible), max(visible)
    span = hi - lo
    pad = span * 0.08 if span else max(abs(hi) * 0.08, 1.0)
    return ChartGeometry(start, end, lo - pad, hi + pad, WIDTH, HEIGHT)


def draw(series_code: str, rows, frequency: str, start: date, end: date):
    raw = normalize_rows(rows)
    base = calculate_base_pivots(rows, frequency)
    geometry = geometry_for(rows, start, end)
    augmented = augment_spike_entry_points(base, geometry)
    simplified = simplify_pivot_lines(augmented, geometry)
    envelope = build_envelope(raw, frequency)

    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot([p.day for p in raw], [p.value for p in raw], linewidth=1.1, label="raw")
    ax.plot([p.day for p in envelope], [p.upper for p in envelope], linewidth=0.8, alpha=0.55, label="upper envelope")
    ax.plot([p.day for p in envelope], [p.lower for p in envelope], linewidth=0.8, alpha=0.55, label="lower envelope")
    if base.high_pivots:
        ax.scatter([p.day for p in base.high_pivots], [p.value for p in base.high_pivots], s=34, marker="^", label="high RDP")
    if base.low_pivots:
        ax.scatter([p.day for p in base.low_pivots], [p.value for p in base.low_pivots], s=34, marker="v", label="low RDP")
    for seg in simplified.segments:
        ax.plot([seg.start.day, seg.end.day], [seg.start.value, seg.end.value], linewidth=2.2)

    ax.set_xlim(start, end)
    ax.set_ylim(geometry.y_min, geometry.y_max)
    ax.set_title(f"{series_code} | {frequency} | full ±24M buffer")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="best", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    path = OUT / f"{series_code}.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    print(series_code, "rows=", len(rows), "range=", start, end, "raw=", raw[0].day, raw[-1].day)


def main():
    db = SupabaseRest()
    for code in ("US10Y_REAL", "NFCI", "US_COMMERCIAL_CH11"):
        rows, frequency, start, end = load_case_series(
            db,
            case_code=CASE_CODE,
            index_code=INDEX_CODE,
            series_code=code,
        )
        draw(code, rows, frequency, start, end)

    rows, frequency, start, end = derive_us10y2y(db)
    draw("US10Y2Y", rows, frequency, start, end)


if __name__ == "__main__":
    main()
