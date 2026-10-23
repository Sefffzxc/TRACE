"""AST-only endpoint and auth dependency inventory; reads no application DB."""
import ast
import csv
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
rows=[]
files=sorted((ROOT/"backend/app/api/routers").glob("*.py"))+[ROOT/"backend/app/main.py",ROOT/"ai-service/main.py"]
for file in files:
    tree=ast.parse(file.read_text(encoding="utf-8"))
    prefixes={"app":""}
    for node in ast.walk(tree):
        if isinstance(node,ast.Assign) and isinstance(node.value,ast.Call):
            fn=node.value.func
            if isinstance(fn,ast.Name) and fn.id=="APIRouter":
                for target in node.targets:
                    if isinstance(target,ast.Name):
                        prefixes[target.id]=next((ast.literal_eval(k.value) for k in node.value.keywords if k.arg=="prefix"),"")
    for fn in [n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]:
        dependencies=[]
        for default in list(fn.args.defaults)+[d for d in fn.args.kw_defaults if d]:
            if isinstance(default,ast.Call) and isinstance(default.func,ast.Name) and default.func.id=="Depends":
                dependencies.append(ast.unparse(default.args[0]) if default.args else "inferred Depends")
        for deco in fn.decorator_list:
            if not(isinstance(deco,ast.Call) and isinstance(deco.func,ast.Attribute) and isinstance(deco.func.value,ast.Name)):
                continue
            method=deco.func.attr.lower()
            if method not in {"get","post","put","patch","delete","options","head"}:continue
            route_dependencies=list(dependencies)
            for kw in deco.keywords:
                if kw.arg=="dependencies":
                    for n in ast.walk(kw.value):
                        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=="Depends" and n.args:
                            route_dependencies.append(ast.unparse(n.args[0]))
            deps=set(route_dependencies)
            if "require_token" in deps: guard="Shared AI service token; optional when AI_SERVICE_TOKEN empty"
            elif "get_super_admin" in deps: guard="Authenticated super_admin"
            elif "school_admin_scope" in deps or "get_current_admin" in deps: guard="Authenticated school admin"+("; school scope" if "school_scope" in deps or "school_admin_scope" in deps else "")
            elif "get_user_from_refresh_token" in deps:guard="Refresh JWT"
            elif "get_current_user" in deps:guard="Access JWT"+("; school scope" if "school_scope" in deps else "")
            elif "school_scope" in deps:guard="Access JWT; school scope"
            else:guard="Public (no auth dependency declared)"
            path=prefixes.get(deco.func.value.id,"")+str(ast.literal_eval(deco.args[0]))
            rows.append({"service":"ai-service" if file.parts[-2]=="ai-service" else "backend","method":method.upper(),"path":path,"router":file.relative_to(ROOT).as_posix(),"function":fn.name,"source_line":fn.lineno,"guard":guard,"direct_dependencies":"; ".join(route_dependencies)})
out=ROOT/"audit/api-inventory.csv"
with out.open("w",newline="",encoding="utf-8") as f:
    writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(sorted(rows,key=lambda r:(r["service"],r["path"],r["method"])))
tracked=subprocess.run(["git","ls-files","ml"],cwd=ROOT,check=True,capture_output=True,text=True).stdout.splitlines()
headers=[]
for filename in tracked:
    if not filename.endswith(".csv"):continue
    with (ROOT/filename).open(newline="",encoding="utf-8-sig") as f:
        header=next(csv.reader(f))
    headers.append({"path":filename,"columns":header})
(ROOT/"audit/ml-csv-header-inventory.json").write_text(json.dumps(headers,indent=2),encoding="utf-8")
print(json.dumps({"endpoints":len(rows),"backend":sum(r["service"]=="backend" for r in rows),"ai_service":sum(r["service"]=="ai-service" for r in rows),"csv_headers":headers},indent=2))
