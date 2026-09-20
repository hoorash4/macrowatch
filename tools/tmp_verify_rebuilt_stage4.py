from __future__ import annotations
from datetime import date
from pathlib import Path
import matplotlib.pyplot as plt

from common import SupabaseRest
from historical_pivot_base import load_case_series, _get_rows_paginated, ChartGeometry, normalize_rows
from historical_pivot_stage1 import calculate_base_pivots, augment_spike_entry_points
from historical_pivot_stage2 import finalize_sideways_protection
from historical_pivot_stage3 import simplify_pivot_lines as run_stage3
from historical_pivot_stage4 import prune_same_trend_extremes
from historical_pivot_stage5 import prune_unconfirmed_retracements

OUT=Path("tmp_stage4_rebuilt_four")
OUT.mkdir(exist_ok=True)

def key(p): return (p.day,round(p.value,6),p.pivot_type)

def geometry(rows,start,end):
    raw=normalize_rows(rows)
    vals=[p.value for p in raw if start<=p.day<=end]
    lo,hi=min(vals),max(vals)
    pad=(hi-lo)*.08 if hi!=lo else 1.0
    return ChartGeometry(start,end,lo-pad,hi+pad,1200,600)

def derive_10y2y(db):
    _,_,start,end=load_case_series(db,case_code="global_financial_crisis",index_code="SP500",series_code="US10Y")
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

def run(code,rows,freq,start,end):
    raw=normalize_rows(rows); g=geometry(rows,start,end)
    s1=augment_spike_entry_points(calculate_base_pivots(rows,freq),g)
    s2=finalize_sideways_protection(s1,g)
    s3=run_stage3(s2,g)
    s4=prune_same_trend_extremes(s3,g)
    s5=prune_unconfirmed_retracements(s4)

    if code=="US10Y2Y":
        print("US10Y2Y_S3",[(str(p.day),p.value,p.pivot_type) for p in s3.markers])
        print("US10Y2Y_S4",[(str(p.day),p.value,p.pivot_type) for p in s4.markers])
        print("US10Y2Y_S4_SEG",[(str(x.start.day),x.start.value,x.start.pivot_type,str(x.end.day),x.end.value,x.end.pivot_type,x.kind) for x in s4.segments])
        s4keys={key(p) for p in s4.markers}
        assert (date(2006,11,15),-0.19,"low") in s4keys
        assert (date(2009,6,4),2.76,"high") in s4keys
        assert (date(2008,3,6),2.09,"high") not in s4keys
        assert (date(2008,11,13),2.6,"high") not in s4keys

    if code=="NFCI":
        final={key(p) for p in s5.markers}
        assert (date(2018,3,30),-0.497,"high") in final
        assert (date(2024,8,2),-0.359,"high") not in final
        assert (date(2024,12,27),-0.485,"low") in final

    if code=="US10Y_REAL":
        final={key(p) for p in s5.markers}
        for t in [
            (date(2022,9,30),1.68,"high"),
            (date(2023,4,6),1.06,"low"),
            (date(2023,10,25),2.52,"high"),
        ]:
            assert t in final, t

    fig,ax=plt.subplots(figsize=(14,7))
    ax.plot([p.day for p in raw],[p.value for p in raw],linewidth=1.0,label="raw")
    for seg in s5.segments:
        ax.plot([seg.start.day,seg.end.day],[seg.start.value,seg.end.value],linewidth=2.2)
    highs=[p for p in s5.markers if p.pivot_type=="high"]
    lows=[p for p in s5.markers if p.pivot_type=="low"]
    if highs:
        ax.scatter([p.day for p in highs],[p.value for p in highs],s=32,marker="^",label="final high")
    if lows:
        ax.scatter([p.day for p in lows],[p.value for p in lows],s=32,marker="v",label="final low")
    ax.set_xlim(start,end); ax.set_ylim(g.y_min,g.y_max)
    ax.set_title(f"{code} | rebuilt Stage4")
    ax.grid(True,alpha=.2); ax.legend(loc="best",fontsize=8)
    fig.autofmt_xdate(); fig.tight_layout()
    fig.savefig(OUT/f"{code}.png",dpi=160); plt.close(fig)

    print(code,"S3",[(str(p.day),p.value,p.pivot_type) for p in s3.markers])
    print(code,"S4",[(str(p.day),p.value,p.pivot_type) for p in s4.markers])
    print(code,"S5",[(str(p.day),p.value,p.pivot_type) for p in s5.markers])

def main():
    db=SupabaseRest()
    for code in ("US10Y_REAL","NFCI","US_COMMERCIAL_CH11"):
        rows,freq,start,end=load_case_series(db,case_code="tightening_2022",index_code="NASDAQ_COMPOSITE",series_code=code)
        run(code,rows,freq,start,end)
    rows,freq,start,end=derive_10y2y(db)
    run("US10Y2Y",rows,freq,start,end)

if __name__=="__main__": main()
