from __future__ import annotations
from common import SupabaseRest
import historical_pivot_base as h

db=SupabaseRest()
cases=[
    ("US10Y_REAL","tightening_2022","NASDAQ_COMPOSITE"),
    ("NFCI","tightening_2022","NASDAQ_COMPOSITE"),
    ("US_COMMERCIAL_CH11","tightening_2022","NASDAQ_COMPOSITE"),
]

for code,case,index in cases:
    rows,freq,start,end=h.load_case_series(db,case_code=case,index_code=index,series_code=code)
    points=h.normalize_rows(rows)
    vals=[p.value for p in points]
    lo,hi=min(vals),max(vals)
    pad=(hi-lo)*0.08 if hi!=lo else 1.0
    g=h.ChartGeometry(start,end,lo-pad,hi+pad,1200.0,600.0)

    base=h.calculate_base_pivots(rows,freq)
    spike=h.augment_spike_entry_points(base,g)

    base_keys={(p.day,p.value,p.pivot_type) for p in base.display_markers}
    spike_keys={(p.day,p.value,p.pivot_type) for p in spike.display_markers}
    assert spike_keys <= base_keys, (code,"spike resurrected",spike_keys-base_keys)

    orig=h.prune_same_trend_extremes
    h.prune_same_trend_extremes=lambda result,geometry,**kwargs: result
    simplified=h.simplify_pivot_lines(spike,g)
    h.prune_same_trend_extremes=orig

    simp_keys={(p.day,p.value,p.pivot_type) for p in simplified.markers}
    assert simp_keys <= spike_keys, (code,"simplify resurrected",simp_keys-spike_keys)

    pruned=orig(simplified,g)
    prune_keys={(p.day,p.value,p.pivot_type) for p in pruned.markers}
    assert prune_keys <= simp_keys, (code,"prune resurrected",prune_keys-simp_keys)

    print(code, freq, len(base_keys), len(spike_keys), len(simp_keys), len(prune_keys))

print("PIPELINE_SUBSET_OK")
