"""Read-only HTTP probes against the root's isolated browser server."""
import asyncio
import json
import math
import pathlib
import statistics
import time
import httpx

ROOT = pathlib.Path(__file__).resolve().parent
RESULTS = {}

async def main():
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000', timeout=45) as client:
        spec = (await client.get('/openapi.json')).json()
        anonymous=[]
        for route, methods in spec['paths'].items():
            if 'get' not in methods:
                continue
            url = route
            for param in methods['get'].get('parameters', []):
                if param.get('in') == 'path':
                    value = '00000000-0000-0000-0000-000000000001' if param.get('schema',{}).get('format')=='uuid' else '1'
                    url=url.replace('{'+param['name']+'}',value)
            r=await client.get(url)
            anonymous.append({'method':'GET','path':route,'status':r.status_code})
        RESULTS['all_get_routes_without_auth']=anonymous
        login=await client.post('/api/auth/login', json={'email':'teacher4@lnhs.deped.gov.ph','password':'Trace@2026','device_id':'root-http-audit-device'})
        data=login.json()
        if data.get('requires_2fa'):
            verify=await client.post('/api/auth/verify-otp',json={'user_id':data['user_id'],'code':data['debug_otp'],'device_id':'root-http-audit-device'})
            data=verify.json()
        headers={'Authorization':'Bearer '+data['access_token']}
        RESULTS['http_login']={'login_status':login.status_code,'access_issued':bool(data.get('access_token'))}
        routes=['/api/dashboard','/api/students','/api/early-warnings','/api/paces','/api/sections','/api/grade-levels','/api/enrollments','/api/my-classes']
        semaphore=asyncio.Semaphore(25)
        async def one(i):
            async with semaphore:
                start=time.perf_counter()
                try:
                    r=await client.get(routes[i%len(routes)],headers=headers)
                    return {'route':routes[i%len(routes)],'status':r.status_code,'ms':round((time.perf_counter()-start)*1000,2)}
                except Exception as e:
                    return {'route':routes[i%len(routes)],'error':type(e).__name__,'ms':round((time.perf_counter()-start)*1000,2)}
        start=time.perf_counter()
        burst=await asyncio.gather(*(one(i) for i in range(100)))
        times=sorted(r['ms'] for r in burst)
        RESULTS['concurrent_reads']={'requests':100,'concurrency':25,'seconds':round(time.perf_counter()-start,3),'statuses':{str(s):sum(r.get('status')==s for r in burst) for s in {r.get('status') for r in burst}},'p50_ms':statistics.median(times),'p95_ms':times[math.ceil(len(times)*.95)-1],'max_ms':max(times),'errors':[r for r in burst if r.get('status')!=200]}
        cors=[]
        for origin in ['http://127.0.0.1:5173','https://evil.example.com']:
            r=await client.options('/api/students',headers={'Origin':origin,'Access-Control-Request-Method':'GET','Access-Control-Request-Headers':'authorization'})
            cors.append({'origin':origin,'status':r.status_code,'allow_origin':r.headers.get('access-control-allow-origin')})
        RESULTS['cors_preflight']=cors
        special=[]
        for query in ["' OR 1=1--",'<script>alert(1)</script>','%', 'a'*5000]:
            r=await client.get('/api/students',headers=headers,params={'search':query})
            special.append({'query':query[:50],'input_length':len(query),'status':r.status_code,'content_type':r.headers.get('content-type')})
        RESULTS['search_inputs']=special
        r=await client.get('/api/students',headers=headers)
        RESULTS['personal_data_response_headers']={h:r.headers.get(h) for h in ['cache-control','pragma','x-content-type-options','content-security-policy','referrer-policy','strict-transport-security']}
        public=pathlib.Path(__file__).resolve().parents[1]/'frontend'/'public'
        assets=list(public.rglob('*.json'))
        failures=[]
        for file in assets:
            try:json.loads(file.read_text(encoding='utf-8'))
            except Exception as e:failures.append({'file':str(file.relative_to(public)),'error':str(e)})
        RESULTS['static_address_json']={'files_parsed':len(assets),'invalid':failures}
    (ROOT/'http-runtime-results.json').write_text(json.dumps(RESULTS,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in RESULTS.items() if k!='all_get_routes_without_auth'},indent=2))
    print('Anonymous GET routes tested:',len(anonymous))

asyncio.run(main())
