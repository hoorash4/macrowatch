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

orig10=h.prune_same_trend_extremes
origfinal=h.prune_unconfirmed_retracements
h.prune_same_trend_extremes=lambda result,geometry,**kwargs: result
h.prune_unconfirmed_retracements=lambda result: result
simp=h.simplify_pivot_lines(rdp,g)
h.prune_same_trend_extremes=orig10
h.prune_unconfirmed_retracements=origfinal
ten=orig10(simp,g)
final=origfinal(ten)

for label,res in [("SIMPLIFIED",simp),("TEN",ten),("FINAL",final)]:
    print("\n"+label)
    print("MARKERS",[(str(p.day),p.value,p.pivot_type) for p in res.markers if p.day>=date(2022,1,1)])
    print("SEGMENTS")
    for s in res.segments:
        if s.end.day>=date(2022,1,1):
            print(s.kind,str(s.start.day),s.start.value,s.start.pivot_type,"->",str(s.end.day),s.end.value,s.end.pivot_type)
