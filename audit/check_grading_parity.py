"""Compare frontend and backend arithmetic using synthetic fixtures only."""
import importlib.util
import json
from pathlib import Path
import random
import subprocess

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("audit_grading",ROOT/"backend/app/services/grading.py")
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
base={"ww_highest":[100],"pt_highest":[100],"exam_highest":[100,100,100]}
cases=[]
def add(name,row,setup=None):cases.append({"name":name,"row":row,"setup":setup or base})
add("all_blank",{})
add("all_zero",{"ww_scores":[0],"pt_scores":[0],"exam_scores":[0,0,0]})
add("all_full",{"ww_scores":[100],"pt_scores":[100],"exam_scores":[100,100,100]})
add("partial_exams",{"ww_scores":[100],"pt_scores":[100],"exam_scores":[100,None,None]})
add("partial_written_work",{"ww_scores":[100,None],"pt_scores":[90],"exam_scores":[90,90,90]},{**base,"ww_highest":[100,100]})
add("bonus_over_maximum",{"ww_scores":[105],"pt_scores":[110],"exam_scores":[105,102,110]})
add("mapeh",{"ww_scores":[90],"pt_scores":[85],"exam_scores":[80,90,95]},{**base,"template":"mapeh"})
add("missing_denominators",{"ww_scores":[90],"pt_scores":[85],"exam_scores":[80,90,95]},{"ww_highest":[],"pt_highest":[],"exam_highest":[]})
add("binary_tie_pass_boundary",{"ww_scores":[24.625],"pt_scores":[100],"exam_scores":[50.25,50.25,50.25]})
for value in [24.665,0.125,0.375,0.625,0.875,24.625,73.533,73.54,75.9,99.5,100]:
    add(f"decimal_edge_{value}",{"ww_scores":[value],"pt_scores":[value],"exam_scores":[value]*3})
for initial,_ in g.TRANSMUTATION:
    for delta in [-.001,0,.001]:
        value=max(0,initial+delta)
        add(f"band_{initial}_{delta}",{"ww_scores":[value],"pt_scores":[value],"exam_scores":[value]*3})
rng=random.Random(20261020)
for n in range(500):
    setup={"ww_highest":[rng.randint(10,100) for _ in range(5)],"pt_highest":[rng.randint(10,100) for _ in range(3)],"exam_highest":[rng.randint(10,100) for _ in range(3)],"template":"mapeh" if n%2 else "core"}
    row={field:[None if rng.random()<.1 else round(rng.uniform(0,cap*1.1),3) for cap in setup[caps]] for field,caps in [("ww_scores","ww_highest"),("pt_scores","pt_highest"),("exam_scores","exam_highest")]}
    add(f"random_{n}",row,setup)
node_script="""import fs from 'node:fs';import {computeRow} from './frontend/src/lib/grading.js'; const cases=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(cases.map(x=>computeRow(x.row,x.setup))));"""
js=json.loads(subprocess.run(["node","--input-type=module","-e",node_script],cwd=ROOT,input=json.dumps(cases),text=True,capture_output=True,check=True).stdout)
mapping={"ww_total":"ww_total_raw","ww_pct":"ww_percent","ww_ws":"ww_weighted","pt_total":"pt_total_raw","pt_pct":"pt_percent","pt_ws":"pt_weighted","exam_parts":"exam_parts","exam_pct":"exam_percent","exam_ws":"exam_weighted","initial_grade":"initial_grade","transmuted_grade":"transmuted_grade","descriptor":"descriptor","complete":"complete","pre_exam_weighted":"pre_exam_weighted"}
def equal(a,b):
    if isinstance(a,list) and isinstance(b,list):return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
    if isinstance(a,(int,float)) and not isinstance(a,bool) and isinstance(b,(int,float)) and not isinstance(b,bool):return abs(a-b)<=1e-9
    return a==b
mismatches=[]
for case,front in zip(cases,js):
    py=g.compute(**case["row"],**case["setup"])
    diff={name:{"frontend":front[name],"backend":py[target]} for name,target in mapping.items() if not equal(front[name],py[target])}
    if diff:mismatches.append({**case,"differences":diff})
result={"synthetic_cases":len(cases),"numeric_tolerance":1e-9,"mismatch_case_count":len(mismatches),"term_grade_mismatch_count":sum("transmuted_grade" in x["differences"] for x in mismatches),"mismatches":mismatches,"real_data_read":False}
http_evidence=ROOT/"audit/backend-jwt-crosscheck.json"
if http_evidence.exists():
    result["persisted_http_crosscheck"]=json.loads(http_evidence.read_text(encoding="utf-8")).get("BF03_persisted_grading_parity")
(ROOT/"audit/grading-parity-results.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
print(json.dumps({k:v for k,v in result.items() if k!="mismatches"},indent=2))
print(json.dumps([x for x in mismatches if "transmuted_grade" in x["differences"]][:3],indent=2))
