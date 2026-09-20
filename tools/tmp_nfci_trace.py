from __future__ import annotations
from common import SupabaseRest
import historical_pivot_base as h

db=SupabaseRest()
rows,freq,start,end=h.load_case_series(db,case_code="tightening_2022",index_code="NASDAQ_COMPOSITE",series_code="NFCI")
pts=h.normalize_rows(rows)
vals=[p.value for p in pts]
lo,hi=min(vals),max(vals); pad=(hi-lo)*.08
g=h.ChartGeometry(start,end,lo-pad,hi+pad,1200,600)
base=h.calculate_base_pivots(rows,freq)
aug=h.augment_spike_entry_points(base,g)
orig=h.prune_same_trend_extremes
h.prune_same_trend_extremes=lambda result,geometry,**kwargs: result
pre=h.simplify_pivot_lines(aug,g)
h.prune_same_trend_extremes=orig

def key(p): return (p.day,p.value,p.pivot_type)
pm={}
for s in pre.segments:
    pm[key(s.start)]=s.start; pm[key(s.end)]=s.end
points=sorted(pm.values(),key=lambda p:(p.day,p.pivot_type))
highs=[p for p in points if p.pivot_type=="high"]
lows=[p for p in points if p.pivot_type=="low"]

hard=sorted((s.start.day if s.kind=="spike" else s.end.day) for s in pre.segments if s.kind in {"spike","sideways"})
def boundary_after(d): return next((x for x in hard if x>d),None)
def anchors(side,typ):
    out=[]
    for i in range(1,len(side)-1):
        a,b,c=side[i-1:i+2]
        if (typ=="low" and b.value<a.value and b.value<c.value) or (typ=="high" and b.value>a.value and b.value>c.value): out.append(b)
    return out
runs=[(p,"up") for p in anchors(lows,"low")]+[(p,"down") for p in anchors(highs,"high")]
if points:
    fp=points[0]
    if all(a!=fp for a,_ in runs): runs.append((fp,"up" if fp.pivot_type=="low" else "down"))

print("POINTS")
for p in points: print(p.day,p.value,p.pivot_type)
print("HARD",hard)
print("RUNS")
for anchor,d in sorted(runs,key=lambda x:x[0].day):
    b=boundary_after(anchor.day)
    same=highs if d=="up" else lows
    cand=[p for p in same if p.day>anchor.day and (b is None or p.day<=b)]
    if not cand: continue
    print("\nANCHOR",anchor.day,anchor.value,anchor.pivot_type,d,"boundary",b)
    extreme=cand[0]
    print(" first",extreme.day,extreme.value)
    ord=0
    for c in cand[1:]:
        ord+=1
        ang=h.screen_origin_angle_degrees(anchor,extreme,c,g)
        improves=c.value>extreme.value if d=="up" else c.value<extreme.value
        print(" ",ord,c.day,c.value,"angle",round(ang,3),"improves",improves)
        if ord>=2 and ang>10:
            print("  BREAK")
            break
        if improves: extreme=c
    print(" final extreme",extreme.day,extreme.value)
