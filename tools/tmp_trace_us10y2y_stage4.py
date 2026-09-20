from __future__ import annotations
from common import SupabaseRest
from historical_pivot_base import _get_rows_paginated, load_case_series, ChartGeometry, normalize_rows
from historical_pivot_stage1 import calculate_base_pivots, augment_spike_entry_points
from historical_pivot_stage2 import finalize_sideways_protection
from historical_pivot_stage3 import simplify_pivot_lines as run_stage3
from historical_pivot_stage4 import prune_same_trend_extremes
from historical_pivot_shared import screen_origin_angle_degrees

def geometry(rows,start,end):
    raw=normalize_rows(rows)
    vals=[p.value for p in raw if start<=p.day<=end]
    lo,hi=min(vals),max(vals)
    pad=(hi-lo)*.08 if hi!=lo else 1.0
    return ChartGeometry(start,end,lo-pad,hi+pad,1200,600)

def derive_10y2y(db):
    _,_,start,end=load_case_series(db,case_code="global_financial_crisis",index_code="SP500",series_code="US10Y")
    def load(code):
        return _get_rows_paginated(db,"economic_chart_points",{
            "select":"observation_date,value,frequency",
            "series_code":f"eq.{code}",
            "observation_date":f"gte.{start.isoformat()}",
            "and":f"(observation_date.lte.{end.isoformat()})",
            "order":"observation_date.asc",
        })
    ten={r["observation_date"]:float(r["value"]) for r in load("US10Y")}
    two={r["observation_date"]:float(r["value"]) for r in load("US2Y")}
    rows=[{"observation_date":d,"value":ten[d]-two[d],"frequency":"D"} for d in sorted(ten.keys() & two.keys())]
    return rows,"D",start,end

db=SupabaseRest()
rows,freq,start,end=derive_10y2y(db)
g=geometry(rows,start,end)
s1=augment_spike_entry_points(calculate_base_pivots(rows,freq),g)
s2=finalize_sideways_protection(s1,g)
s3=run_stage3(s2,g)
s4=prune_same_trend_extremes(s3,g)

print("S3 MARKERS")
for p in s3.markers: print(p.day,p.value,p.pivot_type)
print("S3 SEGMENTS")
for s in s3.segments: print(s.kind,s.start.day,s.start.value,s.start.pivot_type,"->",s.end.day,s.end.value,s.end.pivot_type)

print("S4 MARKERS")
for p in s4.markers: print(p.day,p.value,p.pivot_type)
print("S4 SEGMENTS")
for s in s4.segments: print(s.kind,s.start.day,s.start.value,s.start.pivot_type,"->",s.end.day,s.end.value,s.end.pivot_type)

m=list(s3.markers)
print("ANGLES")
for i in range(len(m)):
    for j in range(i+1,len(m)):
        for k in range(j+1,len(m)):
            if m[i].pivot_type==m[k].pivot_type:
                try:
                    a=screen_origin_angle_degrees(m[j],m[i],m[k],g)
                except Exception:
                    continue
                if a <= 30:
                    print("A",m[j].day,"anchor?",m[i].day,m[k].day,a)

print("EXPECTED_STAGE4_ANGLES")
by_date={str(p.day):p for p in s3.markers}
checks=[
    ("2006-11-15","2008-03-06","2008-11-13"),
    ("2006-11-15","2008-11-13","2009-06-04"),
    ("2006-11-15","2009-06-04","2011-03-08"),
    ("2008-12-26","2009-06-04","2011-03-08"),
]
for a,b,cdate in checks:
    if a in by_date and b in by_date and cdate in by_date:
        print(a,b,cdate,screen_origin_angle_degrees(by_date[a],by_date[b],by_date[cdate],g))

print("RECONSTRUCTED_RUN_STARTS")
ordered_points=list(s3.markers)
def init_dir():
    if len(ordered_points)<2: return None
    first=ordered_points[0]
    for item in ordered_points[1:]:
        if item.value>first.value: return "up"
        if item.value<first.value: return "down"
    return None
run_starts=[]
direction=init_dir()
current_anchor=ordered_points[0]
scan_index=1
while direction is not None:
    if direction=="down":
        trend_low=None; rebound_high=None; higher_low_seen=False; confirmed=False
        while scan_index<len(ordered_points):
            item=ordered_points[scan_index]
            if item.pivot_type=="high" and current_anchor.pivot_type=="high" and item.value>current_anchor.value:
                current_anchor=item; trend_low=None; rebound_high=None; higher_low_seen=False; scan_index+=1; continue
            if item.pivot_type=="low":
                if trend_low is None or item.value<trend_low.value:
                    trend_low=item; rebound_high=None; higher_low_seen=False
                elif rebound_high is not None and item.value>trend_low.value:
                    higher_low_seen=True
            else:
                if trend_low is not None:
                    if rebound_high is None:
                        rebound_high=item
                    elif higher_low_seen and item.value>rebound_high.value:
                        run_starts.append((current_anchor,"down"))
                        current_anchor=trend_low; direction="up"; scan_index=ordered_points.index(current_anchor)+1; confirmed=True; break
                    elif item.value>rebound_high.value:
                        rebound_high=item
            scan_index+=1
        if not confirmed:
            run_starts.append((current_anchor,"down")); break
        continue
    trend_high=None; pullback_low=None; lower_high_seen=False; confirmed=False
    while scan_index<len(ordered_points):
        item=ordered_points[scan_index]
        if item.pivot_type=="low" and current_anchor.pivot_type=="low" and item.value<current_anchor.value:
            current_anchor=item; trend_high=None; pullback_low=None; lower_high_seen=False; scan_index+=1; continue
        if item.pivot_type=="high":
            if trend_high is None or item.value>trend_high.value:
                trend_high=item; pullback_low=None; lower_high_seen=False
            elif pullback_low is not None and item.value<trend_high.value:
                lower_high_seen=True
        else:
            if trend_high is not None:
                if pullback_low is None:
                    pullback_low=item
                elif lower_high_seen and item.value<pullback_low.value:
                    run_starts.append((current_anchor,"up"))
                    current_anchor=trend_high; direction="down"; scan_index=ordered_points.index(current_anchor)+1; confirmed=True; break
                elif item.value<pullback_low.value:
                    pullback_low=item
        scan_index+=1
    if not confirmed:
        run_starts.append((current_anchor,"up")); break
print([(str(a.day),a.value,a.pivot_type,d) for a,d in run_starts])
