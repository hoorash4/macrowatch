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
aug=h.augment_spike_entry_points(base,g)

orig=h.prune_same_trend_extremes
h.prune_same_trend_extremes=lambda result,geometry,**kwargs: result
simp=h.simplify_pivot_lines(aug,g)
h.prune_same_trend_extremes=orig
final=orig(simp,g)

def fmt(points):
    return [(str(p.day),round(p.value,4),p.pivot_type) for p in points]

print("RDP",fmt(base.display_markers))
print("SPIKES",[(str(s.point.day),round(s.point.value,4),s.point.pivot_type,s.direction,
                 None if s.entry is None else (str(s.entry.day),round(s.entry.value,4),s.entry.pivot_type),
                 s.marker_only,round(s.angle_deg,3)) for s in aug.spike_peaks])
print("AUG",fmt(aug.display_markers))
print("SIMPLIFIED",fmt(simp.markers))
print("SIMPLIFIED_SEGMENTS",[(s.kind,str(s.start.day),round(s.start.value,4),s.start.pivot_type,
                              str(s.end.day),round(s.end.value,4),s.end.pivot_type) for s in simp.segments])
print("FINAL",fmt(final.markers))
print("FINAL_SEGMENTS",[(s.kind,str(s.start.day),round(s.start.value,4),s.start.pivot_type,
                         str(s.end.day),round(s.end.value,4),s.end.pivot_type) for s in final.segments])
