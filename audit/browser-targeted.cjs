const {chromium}=require('./runtime/node_modules/playwright');
const fs=require('fs');
const results=[];
async function api(route,method='GET',data,token){const r=await fetch('http://127.0.0.1:8000'+route,{method,headers:{...(data?{'Content-Type':'application/json'}:{}),...(token?{Authorization:`Bearer ${token}`}:{})},body:data?JSON.stringify(data):undefined});return{status:r.status,data:await r.json().catch(()=>null)}}
async function main(){
const login=await api('/api/auth/login','POST',{email:'teacher1@lnhs.deped.gov.ph',password:'Trace@2026',device_id:'targeted-browser-device'});
let auth=login.data;if(auth.requires_2fa)auth=(await api('/api/auth/verify-otp','POST',{user_id:auth.user_id,code:auth.debug_otp,device_id:'targeted-browser-device'})).data;
const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});
try{
const context=await browser.newContext({viewport:{width:375,height:900}});const page=await context.newPage();await page.goto('http://127.0.0.1:5173/login');
await page.evaluate(a=>{localStorage.setItem('trace.access_token',a.access_token);localStorage.setItem('trace.refresh_token',a.refresh_token);localStorage.setItem('trace.user',JSON.stringify(a.user))},auth);
await page.goto('http://127.0.0.1:5173/account');await page.waitForTimeout(900);await page.screenshot({path:'audit/account-mobile-375.png',fullPage:true});
results.push({name:'account375',grid:await page.locator('section.grid').evaluate(e=>({columns:getComputedStyle(e).gridTemplateColumns,width:e.getBoundingClientRect().width,inputs:[...e.querySelectorAll('input:not([type=file])')].map(i=>({width:i.getBoundingClientRect().width,placeholder:i.placeholder}))}))});
await page.setViewportSize({width:320,height:900});await page.goto('http://127.0.0.1:5173/dashboard');await page.waitForTimeout(1000);await page.screenshot({path:'audit/dashboard-mobile-320.png',fullPage:true});results.push({name:'passing chart line',charts:await page.locator('.recharts-wrapper').count(),referenceLines:await page.locator('.recharts-reference-line').count()});
await page.setViewportSize({width:1440,height:1000});await page.goto('http://127.0.0.1:5173/class-record');await page.waitForTimeout(1000);
await page.getByRole('button',{name:'Set up record'}).click();
const highest=page.locator('thead input').first();const original=await highest.inputValue();await highest.fill('37');
await page.getByLabel('Quarter',{exact:true}).selectOption('2');await page.waitForTimeout(350);
let captured;
await page.route('**/api/class-record/setup*',async route=>{if(route.request().method()==='PUT'){captured={url:route.request().url(),payload:route.request().postDataJSON()};return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(captured.payload)})}return route.continue()});
await page.getByRole('button',{name:'Save setup'}).click();await page.waitForTimeout(500);
results.push({name:'setup draft survives term change',original,edited:'37',selectedTerm:'2',captured:captured?{url:captured.url,firstHighest:captured.payload.ww_highest[0]}:null});await page.unroute('**/api/class-record/setup*');
// Force old section response to finish after the newer selection.
const options=await page.getByLabel('Section',{exact:true}).locator('option').evaluateAll(es=>es.map(e=>({value:e.value,label:e.textContent})));
if(options.length>=2){
const a=options[0],b=options[1];await page.getByLabel('Section',{exact:true}).selectOption(b.value);await page.waitForTimeout(400);
let delay=false;
await page.route('**/api/enrollments?*',async route=>{const isOld=new URL(route.request().url()).searchParams.get('section_id')===a.value;if(delay&&isOld){const response=await route.fetch();await new Promise(r=>setTimeout(r,1700));await route.fulfill({response})}else await route.continue()});
await page.route('**/api/paces?*',async route=>{const isOld=new URL(route.request().url()).searchParams.get('section_id')===a.value;if(delay&&isOld){const response=await route.fetch();await new Promise(r=>setTimeout(r,1700));await route.fulfill({response})}else await route.continue()});
delay=true;await page.getByLabel('Section',{exact:true}).selectOption(a.value);await page.waitForTimeout(100);await page.getByLabel('Section',{exact:true}).selectOption(b.value);await page.waitForTimeout(2200);
const rows=await page.locator('tbody th.cr-name').allTextContents();const expectedA=(await api('/api/enrollments?section_id='+a.value,'GET',undefined,auth.access_token)).data;
results.push({name:'reordered roster responses',selected:b.label,staleSection:a.label,displayedNames:rows,staleSectionStudentNames:expectedA.map(e=>e.student_name),staleRendered:rows.some(n=>expectedA.some(e=>n.includes(e.student_name)))});
}
}finally{await browser.close();fs.writeFileSync('audit/browser-targeted-results.json',JSON.stringify(results,null,2));console.log(JSON.stringify(results,null,2))}
}
main().catch(e=>{console.error(e);process.exitCode=1});
