from __future__ import annotations
from datetime import date
from common import SupabaseRest
import historical_pivot_base as h

db=h.SupabaseRest() if hasattr(h,"SupabaseRest") else SupabaseRest()
_,_,start,end=h.load_case_series(db,case_code="global_financial_crisis",index_code="SP500",series_code="US10Y")

def load(code):
    return h._get_rows_paginated(db,"economic_chart_points",{
        "select":"observation_date,value,frequency",
        "series_code":f"eq.{code}",
        "observation_date":f"gte.{start.isoformat()}",
        "and":f"(observation_date.lte.{end.isoformat()})",
        "order":"observation_date.asc",
    })

ten={r["observation_date"]:float(r["value"]) for r in load("US10Y")}
two={r["observation_date"]:float(r["value"]) for r in load("US2Y")}
rows=[{"observation_date":d,"value":ten[d]-two[d],"frequency":"D"} for d in sorted(ten.keys() & two.keys())]

pts=h.normalize_rows(rows)
vals=[p.value for p in pts]
lo,hi=min(vals),max(vals); pad=(hi-lo)*.08
g=h.ChartGeometry(start,end,lo-pad,hi+pad,1200,600)

base=h.calculate_base_pivots(rows,"D")
rdp=h.augment_spike_entry_points(base,g)

orig10=h.prune_same_trend_extremes
origfinal=h.prune_unconfirmed_retracements
h.prune_same_trend_extremes=lambda result,geometry,**kwargs: result
h.prune_unconfirmed_retracements=lambda result: result
simp=h.simplify_pivot_lines(rdp,g)
h.prune_same_trend_extremes=orig10
h.prune_unconfirmed_retracements=origfinal
ten_result=orig10(simp,g)
final=origfinal(ten_result)

def show(label,res):
    print("\n"+label)
    if hasattr(res,"markers"):
        points=res.markers
    else:
        points=res.display_markers
    for p in points:
        if date(2007,1,1)<=p.day<=date(2010,1,1):
            print("P",p.day,round(p.value,4),p.pivot_type)
    if hasattr(res,"segments"):
        print("SEG")
        for s in res.segments:
            if s.end.day>=date(2007,1,1) and s.start.day<=date(2010,1,1):
                print(s.kind,s.start.day,round(s.start.value,4),s.start.pivot_type,"->",s.end.day,round(s.end.value,4),s.end.pivot_type)

show("FINAL_RDP",rdp)
show("SIMPLIFIED",simp)
show("TEN_DEG",ten_result)
show("FINAL_CLEANUP",final)

print("\nSIDEWAYS TEN",[(x.start.day,x.start.value,x.end.day,x.end.value,x.reference_side) for x in ten_result.sideways_segments if x.end.day>=date(2007,1,1) and x.start.day<=date(2010,1,1)])
