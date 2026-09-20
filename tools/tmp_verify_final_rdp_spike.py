from __future__ import annotations
from common import SupabaseRest
import historical_pivot_base as h

db=SupabaseRest()
rows,freq,start,end=h.load_case_series(
    db, case_code="tightening_2022", index_code="NASDAQ_COMPOSITE", series_code="US10Y_REAL"
)
pts=h.normalize_rows(rows)
vals=[p.value for p in pts]
lo,hi=min(vals),max(vals); pad=(hi-lo)*0.08 if hi!=lo else 1.0
g=h.ChartGeometry(start,end,lo-pad,hi+pad,1200,600)
base=h.calculate_base_pivots(rows,freq)
final_rdp=h.augment_spike_entry_points(base,g)
print("SPIKES",[(str(s.point.day),s.point.value,s.direction,
                 None if s.entry is None else (str(s.entry.day),s.entry.value,s.entry.pivot_type),
                 s.marker_only) for s in final_rdp.spike_peaks])
print("ADDED_HIGH",[(str(p.day),p.value) for p in final_rdp.added_high_pivots])
print("ADDED_LOW",[(str(p.day),p.value) for p in final_rdp.added_low_pivots])
assert final_rdp.spike_peaks, "US10Y_REAL spike disappeared before final stage-1 RDP"
for s in final_rdp.spike_peaks:
    if s.marker_only:
        assert s.entry is None
print("FINAL_RDP_OK",len(base.display_markers),len(final_rdp.display_markers))
