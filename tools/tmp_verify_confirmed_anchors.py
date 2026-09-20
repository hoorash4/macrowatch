from __future__ import annotations
from datetime import date
from common import SupabaseRest
import historical_pivot_base as h

def run(code):
    db=SupabaseRest()
    rows,freq,start,end=h.load_case_series(
        db,case_code="tightening_2022",index_code="NASDAQ_COMPOSITE",series_code=code
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
    print(code,"simp",[(str(p.day),p.value,p.pivot_type) for p in simp.markers])
    print(code,"final",[(str(p.day),p.value,p.pivot_type) for p in final.markers])
    before={(p.day,p.value,p.pivot_type) for p in simp.markers}
    after={(p.day,p.value,p.pivot_type) for p in final.markers}
    assert after <= before

run("US_COMMERCIAL_CH11")
run("US10Y_REAL")
print("ANCHOR_LIVE_OK")
