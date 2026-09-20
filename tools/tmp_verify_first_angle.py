from __future__ import annotations
from datetime import date
from common import SupabaseRest
import historical_pivot_base as h

db=SupabaseRest()
rows,freq,start,end=h.load_case_series(
    db,case_code="tightening_2022",index_code="NASDAQ_COMPOSITE",series_code="US_COMMERCIAL_CH11"
)
pts=h.normalize_rows(rows)
vals=[p.value for p in pts]
lo,hi=min(vals),max(vals); pad=(hi-lo)*0.08
g=h.ChartGeometry(start,end,lo-pad,hi+pad,1200,600)
base=h.calculate_base_pivots(rows,freq)
rdp=h.augment_spike_entry_points(base,g)
orig=h.prune_same_trend_extremes
h.prune_same_trend_extremes=lambda result,geometry,**kwargs: result
simp=h.simplify_pivot_lines(rdp,g)
h.prune_same_trend_extremes=orig
final=orig(simp,g)

window=lambda p: date(2018,8,1)<=p.day<=date(2020,2,1)
print("SIMPLIFIED",[(str(p.day),p.value,p.pivot_type) for p in simp.markers if window(p)])
print("FINAL",[(str(p.day),p.value,p.pivot_type) for p in final.markers if window(p)])
assert not any(p.day==date(2019,3,1) and p.value==434.0 for p in final.markers)
print("CH11_FIRST_ANGLE_OK")
