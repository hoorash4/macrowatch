from __future__ import annotations
from common import SupabaseRest
from historical_pivot_base import load_case_series, ChartGeometry, normalize_rows
from historical_pivot_stage1 import calculate_base_pivots, augment_spike_entry_points
from historical_pivot_stage2 import finalize_sideways_protection
from historical_pivot_stage3 import simplify_pivot_lines as run_stage3
from historical_pivot_stage4 import prune_same_trend_extremes, _process_window, _key

def geometry(rows,start,end):
    raw=normalize_rows(rows)
    vals=[p.value for p in raw if start<=p.day<=end]
    lo,hi=min(vals),max(vals); pad=(hi-lo)*.08 if hi!=lo else 1.0
    return ChartGeometry(start,end,lo-pad,hi+pad,1200,600)

db=SupabaseRest()
rows,freq,start,end=load_case_series(db,case_code="tightening_2022",index_code="NASDAQ_COMPOSITE",series_code="NFCI")
g=geometry(rows,start,end)
s1=augment_spike_entry_points(calculate_base_pivots(rows,freq),g)
s2=finalize_sideways_protection(s1,g)
s3=run_stage3(s2,g)
pts=[p for p in s3.markers if p.day.year>=2022]
print("PTS",[(str(p.day),p.value,p.pivot_type) for p in pts])
print("SEGS",[(x.kind,str(x.start.day),x.start.value,str(x.end.day),x.end.value) for x in s3.segments if x.end.day.year>=2022])
print("INTERVALS_FULL",[(str(a.day),a.value,str(b.day),b.value) for a,b in _process_window(list(s3.markers),g,10.0)])
print("INTERVALS_TAIL",[(str(a.day),a.value,str(b.day),b.value) for a,b in _process_window(pts,g,10.0)])
s4=prune_same_trend_extremes(s3,g)
print("S4",[(str(p.day),p.value,p.pivot_type) for p in s4.markers if p.day.year>=2022])
print("S4SEG",[(x.kind,str(x.start.day),x.start.value,str(x.end.day),x.end.value) for x in s4.segments if x.end.day.year>=2022])
