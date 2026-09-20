from __future__ import annotations
from common import SupabaseRest
from historical_pivot_base import load_case_series, ChartGeometry, normalize_rows
from historical_pivot_stage1 import calculate_base_pivots, augment_spike_entry_points
from historical_pivot_stage2 import finalize_sideways_protection

def geometry(rows,start,end):
    pts=normalize_rows(rows); vals=[p.value for p in pts if start<=p.day<=end]
    lo,hi=min(vals),max(vals); pad=(hi-lo)*.08 if hi!=lo else 1.0
    return ChartGeometry(start,end,lo-pad,hi+pad,1200,600)

db=SupabaseRest()
for code in ("US10Y_REAL","NFCI","US_COMMERCIAL_CH11"):
    rows,freq,start,end=load_case_series(db,case_code="tightening_2022",index_code="NASDAQ_COMPOSITE",series_code=code)
    g=geometry(rows,start,end)
    s1=augment_spike_entry_points(calculate_base_pivots(rows,freq),g)
    s2=finalize_sideways_protection(s1,g)
    print("\n"+code)
    print("H",[(str(p.day),p.value) for p in s2.high_pivots])
    print("L",[(str(p.day),p.value) for p in s2.low_pivots])
    print("SPIKES",[(str(s.point.day),s.point.value,s.direction, str(s.entry.day) if s.entry else None, s.marker_only) for s in s2.spike_peaks])
