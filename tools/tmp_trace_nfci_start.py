from __future__ import annotations
from datetime import date
from common import SupabaseRest
from historical_pivot_base import load_case_series, ChartGeometry, normalize_rows
from historical_pivot_stage1 import calculate_base_pivots, augment_spike_entry_points
from historical_pivot_stage2 import finalize_sideways_protection
from historical_pivot_stage3 import simplify_pivot_lines as run_stage3
from historical_pivot_stage4 import prune_same_trend_extremes
from historical_pivot_stage5 import prune_unconfirmed_retracements

def geometry(rows,start,end):
    pts=normalize_rows(rows)
    vals=[p.value for p in pts if start<=p.day<=end]
    lo,hi=min(vals),max(vals)
    pad=(hi-lo)*.08 if hi!=lo else 1.0
    return ChartGeometry(start,end,lo-pad,hi+pad,1200,600)

db=SupabaseRest()
rows,freq,start,end=load_case_series(
    db,case_code="tightening_2022",index_code="NASDAQ_COMPOSITE",series_code="NFCI"
)
g=geometry(rows,start,end)
s1=augment_spike_entry_points(calculate_base_pivots(rows,freq),g)
s2=finalize_sideways_protection(s1,g)
s3=run_stage3(s2,g)
s4=prune_same_trend_extremes(s3,g)
s5=prune_unconfirmed_retracements(s4)

for name,res in [("STAGE1",s1),("STAGE2",s2),("STAGE3",s3),("STAGE4",s4),("STAGE5",s5)]:
    print("\n"+name)
    markers=res.display_markers if hasattr(res,"display_markers") else res.markers
    for p in markers[:8]:
        print("P",p.day,p.value,p.pivot_type)
    if hasattr(res,"segments"):
        for seg in res.segments[:8]:
            print("S",seg.kind,seg.start.day,seg.start.value,seg.start.pivot_type,"->",seg.end.day,seg.end.value,seg.end.pivot_type)
