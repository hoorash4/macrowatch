from __future__ import annotations
from datetime import date
from common import SupabaseRest
import historical_pivot_base as h

db=h.SupabaseRest()
rows,freq,start,end=h.load_case_series(
    db,case_code="tightening_2022",index_code="NASDAQ_COMPOSITE",series_code="US10Y_REAL"
)
pts=h.normalize_rows(rows)
vals=[p.value for p in pts if start<=p.day<=end]
lo,hi=min(vals),max(vals); pad=(hi-lo)*0.08 if hi!=lo else 1.0
g=h.ChartGeometry(start,end,lo-pad,hi+pad,1200,600)
base=h.calculate_base_pivots(rows,freq)
s1=h.augment_spike_entry_points(base,g)

# Reconstruct stage outputs explicitly.
orig10=h.prune_same_trend_extremes
origfinal=h.prune_unconfirmed_retracements
h.prune_same_trend_extremes=lambda result,geometry,**kwargs: result
h.prune_unconfirmed_retracements=lambda result: result
simp=h.simplify_pivot_lines(s1,g)
h.prune_same_trend_extremes=orig10
h.prune_unconfirmed_retracements=origfinal
ten=orig10(simp,g)
final=origfinal(ten)

target_start=date(2022,5,1); target_end=date(2023,1,31)
for name,res in [("STAGE1",s1),("SIMPLIFIED",simp),("TEN",ten),("FINAL",final)]:
    print("\n"+name)
    markers = res.display_markers if hasattr(res,"display_markers") else res.markers
    for p in markers:
        if target_start<=p.day<=target_end:
            print("P",p.day,p.value,p.pivot_type)
    if hasattr(res,"segments"):
        for s in res.segments:
            if s.end.day>=target_start and s.start.day<=target_end:
                print("S",s.kind,s.start.day,s.start.value,s.start.pivot_type,"->",s.end.day,s.end.value,s.end.pivot_type)
