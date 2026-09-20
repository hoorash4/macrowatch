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

def fpts(label, pts):
    print(label)
    for p in pts:
        if date(2018,8,1)<=p.day<=date(2020,2,1):
            print(str(p.day),p.value,p.pivot_type)

fpts("BASE",base.display_markers)
fpts("FINAL_RDP",rdp.display_markers)
fpts("SIMPLIFIED",simp.markers)
fpts("FINAL",final.markers)
print("SIMPLIFIED_SEGMENTS")
for s in simp.segments:
    if s.end.day>=date(2018,8,1) and s.start.day<=date(2020,2,1):
        print(s.kind,str(s.start.day),s.start.value,s.start.pivot_type,"->",str(s.end.day),s.end.value,s.end.pivot_type)
print("FINAL_SEGMENTS")
for s in final.segments:
    if s.end.day>=date(2018,8,1) and s.start.day<=date(2020,2,1):
        print(s.kind,str(s.start.day),s.start.value,s.start.pivot_type,"->",str(s.end.day),s.end.value,s.end.pivot_type)
