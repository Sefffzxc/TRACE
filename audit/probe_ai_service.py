"""AI inference contract audit using the installed ML environment."""
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parents[1]
os.environ["MODEL_DIR"]=str(ROOT/"ai-service"/"model")
os.environ["AI_SERVICE_TOKEN"]="audit-token"
spec=importlib.util.spec_from_file_location("audit_ai_main",ROOT/"ai-service"/"main.py")
ai=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=ai
spec.loader.exec_module(ai)
import httpx

async def main():
    rows=[]
    def note(name,**kw):
        row={"probe":name,**kw};rows.append(row);print(json.dumps(row,default=str))
    ai.load_model()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ai.app,raise_app_exceptions=False),base_url="http://audit-ai") as c:
        async def req(method,path,body=None,headers=None):
            r=await c.request(method,path,json=body,headers=headers)
            try: data=r.json()
            except ValueError:data=r.text
            return r.status_code,data
        h={"X-Service-Token":"audit-token"}
        note("health",result=await req("GET","/health"))
        note("unauth_predict",result=await req("POST","/predict",{}))
        note("unauth_reload",result=await req("POST","/reload"))
        note("predict_missing_features",result=await req("POST","/predict",{},h))
        good={"q1_term_grade":85,"q1_initial_grade":82,"q1_ww_pct":80,"q1_pt_pct":85,"q1_exam_pct":80,"q1_component_gap":5,"grade_level":7}
        note("predict_valid",result=await req("POST","/predict",good,h))
        note("unknown_feature",result=await req("POST","/predict",{"attendance":90},h))
        note("out_of_range",result=await req("POST","/predict",{"q1_term_grade":101},h))
        note("empty_batch",result=await req("POST","/predict/batch",{"records":[]},h))
        note("oversize_batch",result=await req("POST","/predict/batch",{"records":[good]*501},h))
        started=time.perf_counter()
        status,data=await req("POST","/predict/batch",{"records":[{**good,"reference":str(n)} for n in range(500)]},h)
        note("max_batch",status=status,count=data.get("count"),seconds=round(time.perf_counter()-started,3),reference_order_correct=[x["reference"] for x in data.get("predictions",[])]==[str(n) for n in range(500)])
        started=time.perf_counter()
        concurrent=await asyncio.gather(*(req("POST","/predict",good,h) for _ in range(10)))
        note("concurrent_inference",statuses=[x[0] for x in concurrent],seconds=round(time.perf_counter()-started,3))
        status,card=await req("GET","/model-card")
        note("model_card",status=status,algorithm=card.get("algorithm"),model_version=card.get("model_version"),metrics=card.get("reported_metrics"),limitations=card.get("limitations"))
    (ROOT/"audit"/"ai-service-evidence.json").write_text(json.dumps(rows,indent=2),encoding="utf-8")

asyncio.run(main())
