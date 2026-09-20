from __future__ import annotations

from datetime import date
from pathlib import Path
import ast
import matplotlib.pyplot as plt

from common import SupabaseRest
from historical_pivot_base import (
    ChartGeometry,
    _get_rows_paginated,
    build_envelope,
    load_case_series,
    normalize_rows,
)
from historical_pivot_stage1 import calculate_base_pivots, augment_spike_entry_points
from historical_pivot_stage2 import finalize_sideways_protection
from historical_pivot_stage3 import simplify_pivot_lines as run_stage3
from historical_pivot_stage4 import prune_same_trend_extremes
from historical_pivot_stage5 import prune_unconfirmed_retracements

OUT = Path("tmp_staged_four")
OUT.mkdir(exist_ok=True)

EXPECTED = {
    "US10Y_REAL": [
        ("2018-03-26", 0.77, "high"),
        ("2018-04-02", 0.68, "low"),
        ("2018-11-27", 1.15, "high"),
        ("2020-03-06", -0.57, "low"),
        ("2020-03-19", 0.62, "high"),
        ("2020-08-06", -1.08, "low"),
        ("2021-11-09", -1.17, "low"),
        ("2022-06-14", 0.89, "high"),
        ("2022-08-01", 0.09, "low"),
        ("2022-09-30", 1.68, "high"),
        ("2023-04-06", 1.06, "low"),
        ("2023-10-25", 2.52, "high"),
        ("2024-09-17", 1.53, "low"),
        ("2024-12-19", 2.28, "high"),
    ],
    "NFCI": [
        ("2018-03-30", -0.497, "high"),
        ("2018-05-25", -0.567, "low"),
        ("2019-04-19", -0.619, "low"),
        ("2019-12-20", -0.554, "high"),
        ("2020-01-24", -0.629, "low"),
        ("2020-04-03", 0.313, "high"),
        ("2021-06-04", -0.699, "low"),
        ("2021-12-10", -0.541, "high"),
        ("2022-10-07", -0.089, "high"),
        ("2023-09-01", -0.338, "low"),
        ("2024-03-15", -0.439, "low"),
        ("2024-08-02", -0.359, "high"),
    ],
    "US_COMMERCIAL_CH11": [
        ("2018-05-01", 447.0, "high"),
        ("2018-06-01", 306.0, "low"),
        ("2019-02-01", 681.0, "high"),
        ("2019-03-01", 434.0, "low"),
        ("2019-12-01", 391.0, "low"),
        ("2020-09-01", 747.0, "high"),
        ("2021-05-01", 246.0, "low"),
        ("2022-07-01", 212.0, "low"),
        ("2024-06-01", 989.0, "high"),
        ("2024-12-01", 553.0, "low"),
    ],
    "US10Y2Y": [
        ("2000-10-13", -0.11, "high"),
        ("2000-10-23", -0.23, "low"),
        ("2001-09-19", 1.88, "high"),
        ("2003-07-29", 2.75, "high"),
        ("2006-11-15", -0.19, "low"),
        ("2009-06-04", 2.76, "high"),
        ("2010-08-26", 1.99, "low"),
        ("2011-03-08", 2.83, "high"),
    ],
}

def key(p):
    return (p.day.isoformat(), round(p.value, 3), p.pivot_type)

def assert_subset(label, later, earlier):
    l={key(p) for p in later}
    e={key(p) for p in earlier}
    assert l <= e, f"{label}: stage resurrected points: {sorted(l-e)}"

def geometry(rows,start,end):
    pts=normalize_rows(rows)
    vals=[p.value for p in pts if start<=p.day<=end]
    lo,hi=min(vals),max(vals)
    span=hi-lo
    pad=span*.08 if span else 1.0
    return ChartGeometry(start,end,lo-pad,hi+pad,1200,600)

def derive_10y2y(db):
    _,_,start,end=load_case_series(
        db,case_code="global_financial_crisis",index_code="SP500",series_code="US10Y"
    )
    def load(code):
        return _get_rows_paginated(db,"economic_chart_points",{
            "select":"observation_date,value,frequency",
            "series_code":f"eq.{code}",
            "observation_date":f"gte.{start.isoformat()}",
            "and":f"(observation_date.lte.{end.isoformat()})",
            "order":"observation_date.asc",
        })
    ten={r["observation_date"]:float(r["value"]) for r in load("US10Y")}
    two={r["observation_date"]:float(r["value"]) for r in load("US2Y")}
    rows=[{"observation_date":d,"value":ten[d]-two[d],"frequency":"D"} for d in sorted(ten.keys() & two.keys())]
    return rows,"D",start,end

def render(code,rows,freq,start,end):
    raw=normalize_rows(rows)
    g=geometry(rows,start,end)

    base=calculate_base_pivots(rows,freq)
    s1=augment_spike_entry_points(base,g)
    s2=finalize_sideways_protection(s1,g)
    s3=run_stage3(s2,g)
    s4=prune_same_trend_extremes(s3,g)
    s5=prune_unconfirmed_retracements(s4)

    assert not hasattr(s1, "base")
    assert_subset(code+" S2<=S1", s2.display_markers, s1.display_markers)
    assert_subset(code+" S3<=S2", s3.markers, s2.display_markers)
    assert_subset(code+" S4<=S3", s4.markers, s3.markers)
    assert_subset(code+" S5<=S4", s5.markers, s4.markers)

    actual=[key(p) for p in s5.markers]
    expected=[(d,round(v,3),t) for d,v,t in EXPECTED[code]]
    assert actual == expected, f"{code}: refactor changed logic\nactual={actual}\nexpected={expected}"

    env=build_envelope(raw,freq)
    highs=[p for p in s5.markers if p.pivot_type=="high"]
    lows=[p for p in s5.markers if p.pivot_type=="low"]
    fig,ax=plt.subplots(figsize=(14,7))
    ax.plot([p.day for p in raw],[p.value for p in raw],linewidth=1.0,label="raw")
    ax.plot([p.day for p in env],[p.upper for p in env],linewidth=.7,alpha=.45,label="upper envelope")
    ax.plot([p.day for p in env],[p.lower for p in env],linewidth=.7,alpha=.45,label="lower envelope")
    for seg in s5.segments:
        ax.plot([seg.start.day,seg.end.day],[seg.start.value,seg.end.value],linewidth=2.2)
    if highs:
        ax.scatter([p.day for p in highs],[p.value for p in highs],s=32,marker="^",label="final high")
    if lows:
        ax.scatter([p.day for p in lows],[p.value for p in lows],s=32,marker="v",label="final low")
    ax.set_xlim(start,end); ax.set_ylim(g.y_min,g.y_max)
    ax.set_title(f"{code} | staged pipeline | FINAL ONLY")
    ax.grid(True,alpha=.2); ax.legend(loc="best",fontsize=8)
    fig.autofmt_xdate(); fig.tight_layout()
    fig.savefig(OUT/f"{code}.png",dpi=160); plt.close(fig)

    print(code, "S1",len(s1.display_markers),"S2",len(s2.display_markers),"S3",len(s3.markers),"S4",len(s4.markers),"S5",len(s5.markers))
    print(code,"FINAL",actual)

def main():
    db=SupabaseRest()
    for code in ("US10Y_REAL","NFCI","US_COMMERCIAL_CH11"):
        rows,freq,start,end=load_case_series(
            db,case_code="tightening_2022",index_code="NASDAQ_COMPOSITE",series_code=code
        )
        render(code,rows,freq,start,end)
    rows,freq,start,end=derive_10y2y(db)
    render("US10Y2Y",rows,freq,start,end)

if __name__=="__main__":
    main()
