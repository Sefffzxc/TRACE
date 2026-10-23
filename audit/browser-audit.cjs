const {chromium} = require('./runtime/node_modules/playwright');
const AxeBuilder = require('./runtime/node_modules/@axe-core/playwright').default;
const fs = require('fs');
const path = require('path');
const base = 'http://127.0.0.1:5173';
const apiBase = 'http://127.0.0.1:8000';
const results = {date:'2026-10-20', browser:'installed Chrome, headless', routes:[], scenarios:[], errors:[]};
const sessions = {};
let browser;
function record(name, value) {results.scenarios.push({name, ...value}); console.log(name, JSON.stringify(value));}
async function call(route, token, method='GET', body) {
  const res = await fetch(apiBase+route, {method, headers:{...(token?{Authorization:`Bearer ${token}`} : {}), ...(body?{'Content-Type':'application/json'}:{})}, body:body?JSON.stringify(body):undefined});
  return {status:res.status, data:await res.json().catch(()=>null)};
}
async function login(role, email) {
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e=>errors.push(e.message));
  page.on('response', r=>{if(r.status()>=400 && r.url().startsWith(apiBase)) errors.push(`${r.status()} ${new URL(r.url()).pathname}`)});
  await page.goto(base+'/login');
  await page.locator('#email').fill(email);
  await page.locator('#password').fill('Trace@2026');
  const waitLogin = page.waitForResponse(r=>r.url().endsWith('/api/auth/login'));
  await page.getByRole('button',{name:'Sign in', exact:true}).click();
  const response = await (await waitLogin).json();
  if(response.requires_2fa) {
    await page.waitForURL('**/verify');
    for (let i=0; i<6; i++) await page.getByLabel(`Digit ${i+1}`).fill(response.debug_otp[i]);
  }
  await page.waitForURL(role==='operator'?'**/schools':'**/dashboard');
  await page.waitForTimeout(600);
  const auth = await page.evaluate(()=>({access:localStorage.getItem('trace.access_token'),refresh:localStorage.getItem('trace.refresh_token'),user:localStorage.getItem('trace.user')}));
  sessions[role] = {context,page,auth,errors};
  record(`real UI login ${role}`, {landed:new URL(page.url()).pathname, issuedAccess:!!auth.access, errors});
  return sessions[role];
}
async function inspect(role, route, page, axe=true) {
  if(process.env.AUDIT_SCENARIOS==='1') return;
  await page.goto(base+route, {waitUntil:'domcontentloaded'});
  await page.waitForTimeout(800);
  const result = {role,route,landed:new URL(page.url()).pathname, heading:await page.locator('h1,h2').allTextContents(), widths:[], accessibility:[]};
  for(const width of [1440,768,375,320]) {
    await page.setViewportSize({width,height:1000});
    await page.waitForTimeout(90);
    result.widths.push(await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,overflows:document.documentElement.scrollWidth>innerWidth+2, offenders:[...document.querySelectorAll('input,button,.card,.grid,.toolbar,table')].filter(e=>{const r=e.getBoundingClientRect();return r.width&&r.right>innerWidth+2&&!e.closest('.cr-scroll,.table-wrap,.table-scroll')}).slice(0,10).map(e=>({tag:e.tagName,class:e.className,text:(e.innerText||'').slice(0,65),right:Math.round(e.getBoundingClientRect().right)}))})));
  }
  await page.setViewportSize({width:1440,height:1000});
  if(axe) {
    const a = await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();
    result.accessibility=a.violations.map(v=>({id:v.id,impact:v.impact,description:v.description,count:v.nodes.length,examples:v.nodes.slice(0,3).map(n=>({target:n.target,failure:n.failureSummary}))}));
  }
  result.errorNotes=await page.locator('.error-note,.alert-danger').allTextContents();
  result.bodyEmpty=(await page.locator('body').innerText()).trim().length===0;
  results.routes.push(result);
  console.log('route',role,route,'->',result.landed,'overflow',result.widths.filter(w=>w.overflows).map(w=>w.width),'axe',result.accessibility.map(a=>a.id));
}
async function main(){
  browser = await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  const pubContext = await browser.newContext();
  const pub = await pubContext.newPage();
  for(const route of ['/login','/register','/forgot','/reset','/verify','/not-a-page','/dashboard']) await inspect('guest',route,pub);
  const teacher=await login('teacher','teacher1@lnhs.deped.gov.ph');
  const admin=await login('admin','principal@lnhs.deped.gov.ph');
  const operator=await login('operator','superadmin@example.com');
  await call('/api/schools/me',admin.auth.access,'PATCH',{tracks_units:false});
  const students=await call('/api/students',teacher.auth.access);
  const student=(Array.isArray(students.data)?students.data:students.data?.items||[])[0];
  for(const route of ['/dashboard','/students',`/students/${student?.student_id||1}`,'/students/999999','/class-record','/pace','/early-warning','/ai-risk','/account','/teachers','/approvals','/class-management','/school-settings','/schools','/schedule','/not-a-page']) await inspect('teacher',route,teacher.page);
  for(const route of ['/dashboard','/teachers','/approvals','/class-management','/school-settings','/account','/students','/schools']) await inspect('admin',route,admin.page);
  for(const route of ['/schools','/account','/dashboard','/students','/teachers']) await inspect('operator',route,operator.page);
  // Expired access + independently valid refresh at a cold page load.
  const fresh=await call('/api/auth/refresh',teacher.auth.refresh,'POST');
  const coldContext=await browser.newContext();
  const cold=await coldContext.newPage();
  await cold.goto(base+'/login');
  await cold.evaluate(a=>{localStorage.setItem('trace.access_token','invalid-expired-test-token');localStorage.setItem('trace.refresh_token',a.refresh);localStorage.removeItem('trace.user')},teacher.auth);
  let refreshCalls=0;
  const onReq = r=>{if(r.url().endsWith('/api/auth/refresh'))refreshCalls++};
  cold.on('request',onReq);
  await cold.goto(base+'/dashboard');
  await cold.waitForTimeout(800);
  record('cold reload with invalid access and valid refresh',{refreshEndpointStatus:fresh.status,landed:new URL(cold.url()).pathname,refreshCalls,refreshCleared:await cold.evaluate(()=>!localStorage.getItem('trace.refresh_token'))});
  await coldContext.close();
  await teacher.page.evaluate(a=>{localStorage.setItem('trace.access_token',a.access);localStorage.setItem('trace.refresh_token',a.refresh);localStorage.setItem('trace.user',a.user)},teacher.auth);
  // Missing school settings must surface an error rather than permanent skeletons.
  await teacher.page.route('**/api/schools/me',r=>r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Audit settings outage'})}));
  await teacher.page.goto(base+'/early-warning');
  await teacher.page.waitForTimeout(1400);
  record('school settings outage on early warning',{skeletons:await teacher.page.locator('.skeleton').count(),messageShown:(await teacher.page.locator('body').innerText()).includes('Audit settings outage'),body:(await teacher.page.locator('body').innerText()).slice(-550)});
  await teacher.page.unroute('**/api/schools/me');
  // Offline data API is an actual browser network failure.
  await teacher.page.route('**/api/students?*',r=>r.abort('internetdisconnected'));
  await teacher.page.route('**/api/students',r=>r.abort('internetdisconnected'));
  await teacher.page.goto(base+'/students');
  await teacher.page.waitForTimeout(800);
  record('students offline API',{errorShown:(await teacher.page.locator('body').innerText()).includes('Cannot reach the server'),body:(await teacher.page.locator('body').innerText()).slice(-650)});
  await teacher.page.unroute('**/api/students?*');await teacher.page.unroute('**/api/students');
  // Hung me request: observe recovery or lack thereof, then release it.
  let hungRoute;
  await teacher.page.route('**/api/auth/me',r=>{hungRoute=r});
  await teacher.page.goto(base+'/dashboard');
  await teacher.page.waitForTimeout(2200);
  record('stalled session request',{observedMs:2200,body:(await teacher.page.locator('body').innerText()).slice(0,350)});
  await hungRoute?.abort();await teacher.page.unroute('**/api/auth/me');
  await teacher.page.evaluate(a=>{localStorage.setItem('trace.access_token',a.access);localStorage.setItem('trace.refresh_token',a.refresh);localStorage.setItem('trace.user',a.user)},teacher.auth);
  // Class record's search-filter/save behavior; intercept save, leave data intact.
  await teacher.page.goto(base+'/class-record');await teacher.page.waitForTimeout(1000);
  let captured=[];
  await teacher.page.route('**/api/paces/bulk',r=>{captured.push(r.request().postDataJSON());return r.fulfill({status:200,contentType:'application/json',body:'{"saved":1,"skipped":[],"ai":{}}'})});
  const crRows=teacher.page.locator('tbody tr');
  const rowA=crRows.nth(0), rowB=crRows.nth(1);
  if(await rowA.locator('input').count() && await rowB.locator('input').count()){
    const getName=r=>r.locator('th.cr-name').evaluate(e=>e.childNodes[e.childNodes.length-1].textContent.trim());
    const nameA=await getName(rowA);
    const nameB=await getName(rowB);
    await rowA.locator('input').first().fill('3');await rowB.locator('input').first().fill('4');
    await teacher.page.getByPlaceholder('Find a learner…').fill(nameA);
    const saveButton=teacher.page.getByRole('button',{name:/Save.*record/i});
    if(await saveButton.count()){
      await saveButton.first().click();await teacher.page.waitForTimeout(700);
      await teacher.page.getByPlaceholder('Find a learner…').fill('');
      record('class record saving with hidden drafts',{nameA,nameB,capturedEntryCount:captured[0]?.entries?.length||captured[0]?.records?.length||null,payloadKeys:captured[0]?Object.keys(captured[0]):[],rowBValueAfterSave:await teacher.page.locator('tbody tr').nth(1).locator('input').first().inputValue(),expectedDraft:'4'});
    }
  }
  await teacher.page.unroute('**/api/paces/bulk');
  // Render PACE screen using actual server school settings, restoring afterwards.
  const school=await call('/api/schools/me',admin.auth.access);
  await call('/api/schools/me',admin.auth.access,'PATCH',{tracks_units:true});
  await inspect('teacher-pace-school','/pace',teacher.page);
  if(process.env.AUDIT_SCENARIOS==='1'){await teacher.page.goto(base+'/pace');await teacher.page.waitForTimeout(900)}
  const selectSubject=async name=>{
    const value=await teacher.page.getByLabel('Subject').locator('option').evaluateAll((options,n)=>options.find(x=>x.textContent.startsWith(n)).value,name);
    await teacher.page.getByLabel('Subject').selectOption(value);
  };
  await selectSubject('English');
  await teacher.page.waitForTimeout(200);
  const paceInput=teacher.page.locator('tbody tr').first().locator('input').first();
  if(await paceInput.count()){
    await paceInput.fill('7');
    await selectSubject('Mathematics');
    record('PACE switching subjects after draft',{selectedSubject:await teacher.page.getByLabel('Subject').locator('option:checked').textContent(),value:await teacher.page.locator('tbody tr').first().locator('input').first().inputValue(),draftRetained:await teacher.page.locator('tbody tr').first().locator('input').first().inputValue()==='7'});
  }
  await call('/api/schools/me',admin.auth.access,'PATCH',{tracks_units:school.data.tracks_units});
  // Storage unavailable is an explicitly simulated browser policy failure.
  const denied=await browser.newContext();await denied.addInitScript(()=>{Storage.prototype.getItem=function(){throw new DOMException('Audit storage denied','SecurityError')}});
  const deniedPage=await denied.newPage();const deniedErrors=[];deniedPage.on('pageerror',e=>deniedErrors.push(e.message));
  await deniedPage.goto(base+'/login');await deniedPage.waitForTimeout(600);
  record('browser storage denied',{bodyEmpty:(await deniedPage.locator('body').innerText()).trim().length===0,errors:deniedErrors});
  // Ordinary delayed APIs (1.2s) should settle.
  const delayContext=await browser.newContext();const delayPage=await delayContext.newPage();
  await delayPage.route('**/api/schools/directory*',async r=>{await new Promise(res=>setTimeout(res,1200));await r.continue()});
  await delayPage.goto(base+'/register');await delayPage.waitForTimeout(1800);
  record('registration directory delayed 1200ms',{pageVisible:await delayPage.getByText(/Join your school|Request.*account|Create.*account/i).count()>0,errors:await delayPage.locator('.error-note').allTextContents()});
  for(const [role,s] of Object.entries(sessions))results.errors.push({role,errors:s.errors});
}
main().catch(e=>{results.fatal={message:e.message,stack:e.stack};console.error(e)}).finally(async()=>{fs.writeFileSync(path.join(__dirname,process.env.AUDIT_SCENARIOS==='1'?'browser-scenarios-results.json':'browser-results.json'),JSON.stringify(results,null,2));await browser?.close()});
