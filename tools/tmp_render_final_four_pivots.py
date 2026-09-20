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

OUT = Path("tmp_pivot_charts_final")
OUT.mkdir(exist_ok=True)

WIDTH = 1200.0
HEIGHT = 600.0

def derive_us10y2y(db: SupabaseRest):
    _, _, buffer_start, buffer_end = load_case_series(
        db,
        case_code="global_financial_crisis",
        index_code="SP500",
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
    vals = [p.value for p in pts if start <= p.day <= end]
    lo, hi = min(vals), max(vals)
    span = hi - lo
    pad = span * 0.08 if span else max(abs(hi) * 0.08, 1.0)
    return ChartGeometry(start, end, lo - pad, hi + pad, WIDTH, HEIGHT)

def render(code: str, rows, freq: str, start: date, end: date):
    raw = normalize_rows(rows)
    base = calculate_base_pivots(rows, freq)
    geometry = geometry_for(rows, start, end)
    augmented = augment_spike_entry_points(base, geometry)
    final = simplify_pivot_lines(augmented, geometry)
    env = build_envelope(raw, freq)

    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot([p.day for p in raw], [p.value for p in raw], linewidth=1.0, label="raw")
    ax.plot([p.day for p in env], [p.upper for p in env], linewidth=0.7, alpha=0.45, label="upper envelope")
    ax.plot([p.day for p in env], [p.lower for p in env], linewidth=0.7, alpha=0.45, label="lower envelope")
    ax.scatter([p.day for p in base.high_pivots], [p.value for p in base.high_pivots], s=28, marker="^", label="RDP high")
    ax.scatter([p.day for p in base.low_pivots], [p.value for p in base.low_pivots], s=28, marker="v", label="RDP low")

    for seg in final.segments:
        ax.plot([seg.start.day, seg.end.day], [seg.start.value, seg.end.value], linewidth=2.2)

    ax.set_xlim(start, end)
    ax.set_ylim(geometry.y_min, geometry.y_max)
    ax.set_title(f"{code} | {freq} | full ±24M buffer | sealed-stage pipeline")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="best", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    path = OUT / f"{code}.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)

    print(code,
          "range=", start, end,
          "rows=", len(rows),
          "RDP=", len(base.display_markers),
          "stage=", len(augmented.display_markers),
          "final=", len(final.markers))

def main():
    db = SupabaseRest()

    for code in ("US10Y_REAL", "NFCI", "US_COMMERCIAL_CH11"):
        rows, freq, start, end = load_case_series(
            db,
            case_code="tightening_2022",
            index_code="NASDAQ_COMPOSITE",
            series_code=code,
        )
        render(code, rows, freq, start, end)

    rows, freq, start, end = derive_us10y2y(db)
    render("US10Y2Y", rows, freq, start, end)

if __name__ == "__main__":
    main()
