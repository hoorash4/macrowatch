from __future__ import annotations
from datetime import date
import historical_pivot_base as h
from common import SupabaseRest

CASE="tightening_2022"; INDEX="NASDAQ_COMPOSITE"
W=1200.0; H=600.0

def geom(rows,start,end):
    pts=h.normalize_rows(rows)
    vals=[p.value for p in pts if start<=p.day<=end]
    lo,hi=min(vals),max(vals); span=hi-lo; pad=span*.08 if span else 1.0
    return h.ChartGeometry(start,end,lo-pad,hi+pad,W,H)

def trace(code, window_start, window_end):
    db=SupabaseRest()
    rows,freq,start,end=h.load_case_series(db,case_code=CASE,index_code=INDEX,series_code=code)
    g=geom(rows,start,end)
    base=h.calculate_base_pivots(rows,freq)
    aug=h.augment_spike_entry_points(base,g)

    orig_prune=h.prune_same_trend_extremes
    h.prune_same_trend_extremes=lambda result, geometry, **kwargs: result
    pre=h.simplify_pivot_lines(aug,g)
    h.prune_same_trend_extremes=orig_prune
    post=orig_prune(pre,g,protected_markers=tuple(s.point for s in aug.spike_peaks))

    print("\n===",code,"geometry",start,end,g.y_min,g.y_max,"===")
    def show(label, pts):
        print(label)
        for p in pts:
            if window_start<=p.day<=window_end:
                print(" ",p.day,round(p.value,5),p.pivot_type)
    show("BASE HIGH",base.high_pivots); show("BASE LOW",base.low_pivots)
    show("PRE MARKERS",pre.markers); show("POST MARKERS",post.markers)
    print("PRE SEGMENTS")
    for s in pre.segments:
        if s.end.day>=window_start and s.start.day<=window_end:
            print(" ",s.kind,s.start.day,round(s.start.value,4),s.start.pivot_type,"->",s.end.day,round(s.end.value,4),s.end.pivot_type)
    print("POST SEGMENTS")
    for s in post.segments:
        if s.end.day>=window_start and s.start.day<=window_end:
            print(" ",s.kind,s.start.day,round(s.start.value,4),s.start.pivot_type,"->",s.end.day,round(s.end.value,4),s.end.pivot_type)

    # print same-side origin angles around each local transition anchor in window
    points=sorted({(p.day,p.value,p.pivot_type):p for s in pre.segments for p in (s.start,s.end)}.values(), key=lambda p:(p.day,p.pivot_type))
    highs=[p for p in points if p.pivot_type=="high"]; lows=[p for p in points if p.pivot_type=="low"]
    def anchors(side, typ):
        out=[]
        for i in range(1,len(side)-1):
            a,b,c=side[i-1:i+2]
            if (typ=="low" and b.value<a.value and b.value<c.value) or (typ=="high" and b.value>a.value and b.value>c.value): out.append(b)
        return out
    starts=[(p,"up") for p in anchors(lows,"low")]+[(p,"down") for p in anchors(highs,"high")]
    if points:
        fp=points[0]; starts.append((fp,"up" if fp.pivot_type=="low" else "down"))
    print("ANGLE RUNS")
    for anchor,direction in sorted(starts,key=lambda x:x[0].day):
        if not (window_start<=anchor.day<=window_end): continue
        same=highs if direction=="up" else lows
        cand=[p for p in same if p.day>anchor.day]
        if not cand: continue
        extreme=cand[0]; print(" anchor",anchor.day,anchor.value,anchor.pivot_type,direction,"first",extreme.day,extreme.value)
        ord=0
        for c in cand[1:6]:
            ord+=1
            ang=h.screen_origin_angle_degrees(anchor,extreme,c,g)
            improves=c.value>extreme.value if direction=="up" else c.value<extreme.value
            print("   #",ord,c.day,c.value,"angle",round(ang,3),"improves",improves)
            if ord>=2 and ang>h.SAME_TREND_ANGLE_THRESHOLD_DEG:
                print("    BREAK")
                break
            if improves: extreme=c

trace("US10Y_REAL",date(2018,3,1),date(2019,8,1))
trace("NFCI",date(2020,5,1),date(2021,5,1))
