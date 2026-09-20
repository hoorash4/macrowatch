from __future__ import annotations
from datetime import date
from common import SupabaseRest
import historical_pivot_base as h

CASE="tightening_2022"; INDEX="NASDAQ_COMPOSITE"
W=1200.0; H=600.0

def geom(rows,start,end):
    pts=h.normalize_rows(rows)
    vals=[p.value for p in pts if start<=p.day<=end]
    lo,hi=min(vals),max(vals); span=hi-lo; pad=span*.08 if span else 1.0
    return h.ChartGeometry(start,end,lo-pad,hi+pad,W,H)

def inspect(code):
    db=SupabaseRest()
    rows,freq,start,end=h.load_case_series(db,case_code=CASE,index_code=INDEX,series_code=code)
    g=geom(rows,start,end)
    base=h.calculate_base_pivots(rows,freq)
    aug=h.augment_spike_entry_points(base,g)

    orig=h.prune_same_trend_extremes
    h.prune_same_trend_extremes=lambda result, geometry, **kwargs: result
    pre=h.simplify_pivot_lines(aug,g)
    h.prune_same_trend_extremes=orig
    post=orig(pre,g)

    pre_keys={(p.day,p.value,p.pivot_type) for p in pre.markers}
    post_keys={(p.day,p.value,p.pivot_type) for p in post.markers}
    assert post_keys <= pre_keys, (code, "resurrection", sorted(post_keys-pre_keys))

    print("\n",code)
    print("pre",[(str(p.day),p.value,p.pivot_type) for p in pre.markers if date(2018,1,1)<=p.day<=date(2021,8,1)])
    print("post",[(str(p.day),p.value,p.pivot_type) for p in post.markers if date(2018,1,1)<=p.day<=date(2021,8,1)])
    return post

us=inspect("US10Y_REAL")
nf=inspect("NFCI")

us_keys={(str(p.day),round(p.value,3),p.pivot_type) for p in us.markers}
nf_keys={(str(p.day),round(p.value,3),p.pivot_type) for p in nf.markers}

assert ("2018-04-02",0.68,"low") in us_keys
assert ("2018-11-27",1.15,"high") in us_keys
assert ("2020-10-02",-0.492,"low") not in nf_keys
assert ("2021-01-15",-0.621,"low") not in nf_keys
assert ("2020-04-03",0.313,"high") in nf_keys
assert ("2021-06-04",-0.699,"low") in nf_keys
print("\nOK")
