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

OUT=Path("tmp_final_four_charts")
OUT.mkdir(exist_ok=True)

def geometry(rows,start,end):
    pts=normalize_rows(rows)
    vals=[p.value for p in pts if start<=p.day<=end]
    lo,hi=min(vals),max(vals); span=hi-lo
    pad=span*.08 if span else 1.0
    return ChartGeometry(start,end,lo-pad,hi+pad,1200,600)

def derive_10y2y(db):
    _,_,start,end=load_case_series(
        db,
        case_code="global_financial_crisis",
        index_code="SP500",
        series_code="US10Y",
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
    rows=[
        {"observation_date":d,"value":ten[d]-two[d],"frequency":"D"}
        for d in sorted(ten.keys() & two.keys())
    ]
    return rows,"D",start,end

def pkey(p):
    return (p.day,p.value,p.pivot_type)

def render(code,rows,freq,start,end):
    raw=normalize_rows(rows)
    g=geometry(rows,start,end)
    base=calculate_base_pivots(rows,freq)
    stage1=augment_spike_entry_points(base,g)
    final=simplify_pivot_lines(stage1,g)
    env=build_envelope(raw,freq)

    stage1_keys={pkey(p) for p in stage1.display_markers}
    final_keys={pkey(p) for p in final.markers}
    assert final_keys <= stage1_keys, f"{code}: final contains non-stage1 point"
    for seg in final.segments:
        assert pkey(seg.start) in final_keys
        assert pkey(seg.end) in final_keys

    highs=[p for p in final.markers if p.pivot_type=="high"]
    lows=[p for p in final.markers if p.pivot_type=="low"]

    fig,ax=plt.subplots(figsize=(14,7))
    ax.plot([p.day for p in raw],[p.value for p in raw],linewidth=1.0,label="raw")
    ax.plot([p.day for p in env],[p.upper for p in env],linewidth=.7,alpha=.45,label="upper envelope")
    ax.plot([p.day for p in env],[p.lower for p in env],linewidth=.7,alpha=.45,label="lower envelope")
    for s in final.segments:
        ax.plot([s.start.day,s.end.day],[s.start.value,s.end.value],linewidth=2.2)
    if highs:
        ax.scatter([p.day for p in highs],[p.value for p in highs],s=32,marker="^",label="final high")
    if lows:
        ax.scatter([p.day for p in lows],[p.value for p in lows],s=32,marker="v",label="final low")
    ax.set_xlim(start,end)
    ax.set_ylim(g.y_min,g.y_max)
    ax.set_title(f"{code} | {freq} | full ±24M buffer | FINAL ONLY")
    ax.grid(True,alpha=.2)
    ax.legend(loc="best",fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(OUT/f"{code}.png",dpi=160)
    plt.close(fig)
    print(code,"STAGE1",len(stage1.display_markers),"FINAL",len(final.markers))
    print(code,"FINAL_MARKERS",[(str(p.day),p.value,p.pivot_type) for p in final.markers])

def main():
    db=SupabaseRest()
    for code in ("US10Y_REAL","NFCI","US_COMMERCIAL_CH11"):
        rows,freq,start,end=load_case_series(
            db,
            case_code="tightening_2022",
            index_code="NASDAQ_COMPOSITE",
            series_code=code,
        )
        render(code,rows,freq,start,end)
    rows,freq,start,end=derive_10y2y(db)
    render("US10Y2Y",rows,freq,start,end)

if __name__=="__main__":
    main()
