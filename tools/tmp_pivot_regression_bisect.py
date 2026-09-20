from __future__ import annotations
import importlib.util, subprocess, sys
from datetime import date
from common import SupabaseRest

REFS=[
("b4629fe722a8d3344fa6db4cdacf513ef8170efd","spike-marker-only"),
("dd1d4700213f94a6bf8122735d12ba8926ff9bad","add-10deg"),
("ae7c28e339b011c0ceb532ce81bd2b0aa75e46a8","preserve-boundaries"),
("a7326ab4b8574808b054d095980db2aa2a224258","true-anchors"),
("ab8a32142467b2b25e38983d42cec0c53262ca58","two-record-updates"),
("32102b6a4837bb2c62bcb67065d9ab698b87e1d3","restore-angle-method"),
("1564cd0c273bd2264ba99f16c5f4486b972d111f","initial-anchor-sideways-end"),
("b7e167e47e2c71cab9821bdba5371f39a73b7103","ignore-first-angle"),
("822dfd349dc405dcafd8392b9d0d33cf44ac8c4a","protect-first-extreme"),
("8efe59822ee2430023e6aa3424102ae5117116c5","count-skipped-angle"),
("06853483864d828c721ed76ae1b6a5136ad7d978","current"),
]

db=SupabaseRest()
START=date(2018,3,23); END=date(2024,12,28)

def rows(code):
    all=[]; off=0
    while True:
        page=db.request("GET","economic_chart_points",params={
            "select":"observation_date,value,frequency",
            "series_code":f"eq.{code}",
            "observation_date":f"gte.{START.isoformat()}",
            "and":f"(observation_date.lte.{END.isoformat()})",
            "order":"observation_date.asc",
            "limit":"1000","offset":str(off),
        }) or []
        all.extend(page)
        if len(page)<1000:return all
        off+=len(page)

DATA={"US10Y_REAL":rows("US10Y_REAL"),"NFCI":rows("NFCI")}

def load(ref):
    p=f"/tmp/hpb_{ref[:7]}.py"
    data=subprocess.check_output(["git","show",f"{ref}:backend/historical_pivot_base.py"],text=True)
    open(p,"w").write(data)
    spec=importlib.util.spec_from_file_location(f"hpb_{ref[:7]}",p)
    m=importlib.util.module_from_spec(spec); sys.modules[spec.name]=m; spec.loader.exec_module(m)
    return m

def run(m,code):
    rs=DATA[code]; freq=rs[0]["frequency"]
    pts=m.normalize_rows(rs)
    vals=[p.value for p in pts]
    lo,hi=min(vals),max(vals); pad=(hi-lo)*.08
    g=m.ChartGeometry(START,END,lo-pad,hi+pad,1200,600)
    base=m.calculate_base_pivots(rs,freq)
    aug=m.augment_spike_entry_points(base,g)
    if not hasattr(m,"simplify_pivot_lines"):
        return []
    result=m.simplify_pivot_lines(aug,g)
    return [(str(p.day),round(p.value,3),p.pivot_type) for p in result.markers]

for ref,label in REFS:
    try:
        m=load(ref)
        print("\nREF",ref[:7],label)
        for code in ("US10Y_REAL","NFCI"):
            out=run(m,code)
            if code=="US10Y_REAL":
                filt=[x for x in out if "2018-03-01"<=x[0]<="2020-04-01"]
            else:
                filt=[x for x in out if "2020-01-01"<=x[0]<="2021-08-01"]
            print(code,filt)
    except Exception as e:
        print("ERROR",ref[:7],type(e).__name__,e)
