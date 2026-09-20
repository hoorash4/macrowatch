from __future__ import annotations
from common import SupabaseRest
from historical_pivot_base import _get_rows_paginated, load_case_series, ChartGeometry, normalize_rows
from historical_pivot_stage1 import calculate_base_pivots, augment_spike_entry_points
from historical_pivot_stage2 import finalize_sideways_protection
from historical_pivot_stage3 import simplify_pivot_lines as run_stage3
from historical_pivot_stage4 import prune_same_trend_extremes
from historical_pivot_stage5 import prune_unconfirmed_retracements

def geometry(rows,start,end):
    raw=normalize_rows(rows)
    vals=[p.value for p in raw if start<=p.day<=end]
    lo,hi=min(vals),max(vals); pad=(hi-lo)*.08 if hi!=lo else 1.0
    return ChartGeometry(start,end,lo-pad,hi+pad,1200,600)

def derive(db):
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
    return ([{"observation_date":d,"value":ten[d]-two[d],"frequency":"D"} for d in sorted(ten.keys() & two.keys())],"D",start,end)

db=SupabaseRest()
rows,freq,start,end=derive(db)
g=geometry(rows,start,end)
s1=augment_spike_entry_points(calculate_base_pivots(rows,freq),g)
s2=finalize_sideways_protection(s1,g)
s3=run_stage3(s2,g)
s4=prune_same_trend_extremes(s3,g)
s5=prune_unconfirmed_retracements(s4)

for name,res in [("S2",s2),("S3",s3),("S4",s4),("S5",s5)]:
    print("\n"+name)
    markers=res.display_markers if hasattr(res,"display_markers") else res.markers
    for p in markers:
        if 2008 <= p.day.year <= 2011:
            print("P",p.day,p.value,p.pivot_type)
    if hasattr(res,"segments"):
        for seg in res.segments:
            if seg.end.day.year>=2008 and seg.start.day.year<=2011:
                print("S",seg.kind,seg.start.day,seg.start.value,seg.start.pivot_type,"->",seg.end.day,seg.end.value,seg.end.pivot_type)
