from __future__ import annotations
from datetime import date
from common import SupabaseRest
import historical_pivot_base as h

db=SupabaseRest()
rows,freq,start,end=h.load_case_series(
    db,case_code="tightening_2022",index_code="NASDAQ_COMPOSITE",series_code="NFCI"
)
pts=h.normalize_rows(rows)
vals=[p.value for p in pts]
lo,hi=min(vals),max(vals); pad=(hi-lo)*0.08 if hi!=lo else 1.0
g=h.ChartGeometry(start,end,lo-pad,hi+pad,1200,600)
base=h.calculate_base_pivots(rows,freq)
rdp=h.augment_spike_entry_points(base,g)
orig=h.prune_same_trend_extremes
h.prune_same_trend_extremes=lambda result,geometry,**kwargs: result
simp=h.simplify_pivot_lines(rdp,g)
h.prune_same_trend_extremes=orig
final=orig(simp,g)

def out(label, result):
    print("\n"+label)
    for p in result.markers:
        if p.day>=date(2022,7,1):
            print(str(p.day),p.value,p.pivot_type)
    print("SEGMENTS")
    for s in result.segments:
        if s.end.day>=date(2022,7,1):
            print(s.kind,str(s.start.day),s.start.value,s.start.pivot_type,"->",str(s.end.day),s.end.value,s.end.pivot_type)

out("SIMPLIFIED",simp)
out("FINAL",final)
