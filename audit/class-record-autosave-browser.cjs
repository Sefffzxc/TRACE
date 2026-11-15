/* Run: node audit/class-record-autosave-browser.cjs
   Real React page + DataRouter, synthetic class/learner data, mocked API only.
   Checks acknowledgement races, validation, filtering, import and navigation. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const esbuild = require('../frontend/node_modules/esbuild');
const { chromium } = require('./runtime/node_modules/playwright');
const frontend = path.resolve(__dirname, '../frontend');
const built = esbuild.buildSync({ stdin: { contents: `
  import React from 'react'; import {createRoot} from 'react-dom/client';
  import {createBrowserRouter,RouterProvider,Link,Outlet,useNavigate} from 'react-router-dom';
  import {AuthProvider} from './src/context/AuthContext'; import {ToastProvider} from './src/context/ToastContext';
  import ClassRecordPage from './src/pages/app/ClassRecordPage';
  function Frame(){const navigate=useNavigate();return <><nav><Link to="/dashboard">Dashboard sidebar</Link> <button onClick={()=>navigate('/account')}>Account menu</button></nav><Outlet/></>}
  const router=createBrowserRouter([{element:<Frame/>,children:[
    {path:'/class-record',element:<ClassRecordPage/>},
    {path:'/dashboard',element:<><h1>Dashboard</h1><Link to="/class-record">Open record</Link></>},
    {path:'/account',element:<><h1>Account</h1><Link to="/class-record">Open record</Link></>}
  ]}]);
  createRoot(document.getElementById('root')).render(<React.StrictMode><ToastProvider><AuthProvider><RouterProvider router={router}/></AuthProvider></ToastProvider></React.StrictMode>);
`, resolveDir: frontend, loader: 'jsx' }, bundle: true, write: false, jsx: 'automatic', platform: 'browser', format: 'iife', define: { 'import.meta.env': '{"VITE_API_URL":"http://api.invalid"}', 'process.env.NODE_ENV': '"development"' } });
const css = ['tokens.css','base.css','pages.css'].map((name)=>fs.readFileSync(path.join(frontend,'src/styles',name))).join('\n');
const server=http.createServer((req,res)=>{
  if(req.url==='/bundle.js'){res.setHeader('Content-Type','text/javascript');res.end(built.outputFiles[0].text)}
  else if(req.url==='/styles.css'){res.setHeader('Content-Type','text/css');res.end(css)}
  else{res.setHeader('Content-Type','text/html');res.end('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/styles.css"><div id="root"></div><script src="/bundle.js"></script>')}
});
const freshSetup=()=>({template:'core',ww_highest:[],pt_highest:[],exam_highest:[],ww_weight:20,pt_weight:50,ex_weight:30,exam_subweights:[30,30,40]});
const rows=[{enrollment_id:201,student_id:101,student_name:'Alice Learner',section_id:1},{enrollment_id:202,student_id:102,student_name:'Bob Learner',section_id:1},{enrollment_id:203,student_id:103,student_name:'Charlie Learner',section_id:2}];
async function run(){
 await new Promise((resolve)=>server.listen(0,'127.0.0.1',resolve)); const origin='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 await context.addInitScript(()=>{localStorage.setItem('trace.user',JSON.stringify({id:'synthetic-teacher',role:'teacher',first_name:'Test',last_name:'Teacher'}));localStorage.setItem('trace.access_token','synthetic-access')});
 const requests=[], errors=[], setups=new Map(), paces=new Map();
 let mode='ok', holdBulk=null, holdSetup=null;
 const bulkRequests=()=>requests.filter((r)=>r.path==='/api/paces/bulk');
 const setupKey=(params)=>[params.section_id,params.subject_id,params.grading_period].join(':');
 await context.route('**/api/**',async(route)=>{
  const req=route.request(), url=new URL(req.url()), pathname=url.pathname;
  const headers={'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Headers':'Content-Type,Authorization','Access-Control-Allow-Methods':'GET,POST,PUT,OPTIONS'};
  if(req.method()==='OPTIONS')return route.fulfill({status:204,headers});
  let body={},status=200;
  if(pathname==='/api/auth/me')body={id:'synthetic-teacher',role:'teacher',first_name:'Test',last_name:'Teacher'};
  else if(pathname==='/api/schools/me')body={school_id:1,grading_periods:3,grading_period_label:'Term',tracks_units:false};
  else if(pathname==='/api/sections')body=[{section_id:1,name:'Section A',grade_level_id:1},{section_id:2,name:'Section B',grade_level_id:1}];
  else if(pathname==='/api/subjects')body=[{subject_id:1,subject_name:'Mathematics',grade_level_id:1},{subject_id:2,subject_name:'Science',grade_level_id:1}];
  else if(pathname==='/api/my-classes')body={grade:{'1':['Mathematics','Science'],'2':['Mathematics']}};
  else if(pathname==='/api/ai/status')body={available:true,ready:true};
  else if(pathname==='/api/enrollments')body=rows.filter((row)=>row.section_id===Number(url.searchParams.get('section_id')));
  else if(pathname==='/api/paces')body=[...paces.values()].filter((row)=>rows.find((e)=>e.enrollment_id===row.enrollment_id)?.section_id===Number(url.searchParams.get('section_id')));
  else if(pathname==='/api/class-record/setup'){
   const params=Object.fromEntries(url.searchParams);
   if(req.method()==='PUT'){
    const payload=req.postDataJSON(), gate=holdSetup; requests.push({path:pathname,params,payload});
    if(gate)await gate;
    setups.set(setupKey(params),payload);body={...payload,...params};
   } else body=setups.get(setupKey(params))||freshSetup();
  }else if(pathname==='/api/paces/bulk'){
   const payload=req.postDataJSON(), gate=holdBulk, currentMode=mode;requests.push({path:pathname,payload});if(gate)await gate;
   if(currentMode==='fail'){status=503;body={detail:'Synthetic network/service failure.'}}
   else{
    const entries=currentMode==='partial'?payload.entries.slice(0,1):payload.entries;
    for(const entry of entries)paces.set([entry.enrollment_id,entry.subject,payload.grading_period].join(':'),{...entry,grading_period:payload.grading_period});
    body={saved:entries.length,skipped:currentMode==='partial'?['One learner could not be saved.']:[],ai:{fallback:0}};
   }
  }else if(pathname==='/api/class-record/import'){
   const saving=/name="save"\r\n\r\ntrue/.test(req.postData());requests.push({path:pathname,saving});
   const preview={counts:{matched:2,new:0,enrol:0,elsewhere:0},learners:rows.slice(0,2).map((r)=>({name:r.student_name,gender:'F',status:'matched'})),warnings:[],terms:[{term:1,learners:2,graded:2,written_works:5,performance_tasks:3,exams:3}]};
   if(saving){setups.set('1:1:1',{...setups.get('1:1:1'),ww_highest:[20,null,null,null,null]});paces.set('201:Mathematics:1',{enrollment_id:201,student_id:101,subject:'Mathematics',grading_period:1,ww_scores:[9,null,null,null,null],pt_scores:[null,null,null],exam_scores:[null,null,null]});body={...preview,saved:{terms:[1],added:0,enrolled:0}}}
   else body=preview;
  }else throw new Error('Unexpected API request '+req.method()+' '+pathname);
  await route.fulfill({status,headers,contentType:'application/json',body:JSON.stringify(body)});
 });
 const page=await context.newPage();page.setDefaultTimeout(8000);page.on('pageerror',(e)=>errors.push(e.message));
 const cell=(learner='Alice',group='written work',item=1)=>page.getByLabel(`${learner} Learner ${group} ${item}`,{exact:true});
 const status=()=>page.locator('.cr-save-message');
 const saved=()=>page.waitForFunction(()=>document.querySelector('.cr-save-message strong')?.textContent==='Saved');
 const waitCount=async(getter,count)=>{const until=Date.now()+8000;while(getter()<count){assert(Date.now()<until,'Expected request count '+count);await page.waitForTimeout(30)}};
 const setupInputs=()=>page.locator('.cr-hps input');
 await page.goto(origin+'/class-record');await cell().waitFor();
 assert.equal(await page.getByLabel('Alice Learner written work 5',{exact:true}).count(),1,'Real fresh empty arrays get default assessment columns');
 assert((await status().innerText()).includes('Ready to enter scores'));
 await cell().fill('7');await page.waitForTimeout(1400);
 assert.equal(bulkRequests().length,0,'Scores without HPS cannot persist');assert.equal(await cell().inputValue(),'7');
 await page.getByRole('button',{name:'Set up record',exact:true}).click();
 await setupInputs().nth(0).fill('10');await cell().fill('8');
 assert.equal(await page.locator('tbody tr').first().locator('td').nth(6).innerText(),'80','Preview uses unsaved maximum');
 await setupInputs().nth(0).fill('20');
 assert.equal(await page.locator('tbody tr').first().locator('td').nth(6).innerText(),'40');
 await setupInputs().nth(5).fill('20');await setupInputs().nth(8).fill('20');
 await cell('Alice','performance task').fill('10');await cell('Alice','examination').fill('10');await cell('Bob').fill('6');
 await saved();assert.equal(bulkRequests().length,1,'Rapid edits batch once');
 assert.equal(requests.find((r)=>r.path==='/api/class-record/setup').payload.ww_highest[0],20);
 assert.equal(bulkRequests()[0].payload.entries.length,2);
 assert.equal(await cell().inputValue(),'8','Saving setup preserves learner edits');
 console.log('PASS: real empty setup, combined setup/score entry, live maxima preview and batched autosave.');

 let release;holdBulk=new Promise((resolve)=>{release=resolve});let count=bulkRequests().length;
 await cell().fill('9');await waitCount(()=>bulkRequests().length,count+1);
 await cell().fill('7');await cell('Bob').fill('5');holdBulk=null;release();
 await saved();assert.equal(await cell().inputValue(),'7');assert.equal(bulkRequests().at(-1).payload.entries.find((e)=>e.enrollment_id===201).ww_scores[0],7);
 await page.getByRole('button',{name:'Done setup',exact:true}).click();await page.getByRole('button',{name:'Set up record',exact:true}).waitFor();assert.equal(await cell().inputValue(),'7');
 await cell().fill('4');await cell('Bob').fill('3');await page.getByRole('searchbox').fill('Alice');await saved();
 assert.deepEqual(bulkRequests().at(-1).payload.entries.map((e)=>e.enrollment_id).sort(),[201,202],'Filtered-out dirty rows save too');
 await page.getByRole('searchbox').fill('nobody');assert((await status().innerText()).includes('Saved'));await page.getByRole('searchbox').fill('');
 console.log('PASS: in-flight acknowledgements retain newer typing, Done setup preserves scores, hidden rows save.');

 for(const invalid of ['abc','-1','Infinity','21']){count=bulkRequests().length;await cell().fill(invalid);await page.waitForTimeout(1300);assert.equal(bulkRequests().length,count);assert.equal(await cell().inputValue(),invalid);assert.equal(await cell().getAttribute('aria-invalid'),'true')}
 await page.getByLabel('Term',{exact:true}).selectOption('2');await page.waitForTimeout(100);assert.equal(await page.getByLabel('Term',{exact:true}).inputValue(),'1','Invalid sheet cannot switch period');assert.equal(await cell().inputValue(),'21');
 await cell().fill('0');await saved();assert.equal(bulkRequests().at(-1).payload.entries[0].ww_scores[0],0);
 await cell().fill('');await saved();assert.equal(bulkRequests().at(-1).payload.entries[0].ww_scores[0],null);
 mode='fail';count=bulkRequests().length;await cell().fill('6');await waitCount(()=>bulkRequests().length,count+1);await page.getByRole('button',{name:'Retry save',exact:true}).waitFor();
 await page.waitForTimeout(1500);assert.equal(bulkRequests().length,count+1,'Failed saves do not loop');assert.equal(await cell().inputValue(),'6');
 mode='ok';await page.getByRole('button',{name:'Retry save',exact:true}).click();await saved();
 mode='partial';await cell().fill('5');await cell('Bob').fill('4');await page.getByRole('button',{name:'Retry save',exact:true}).waitFor();
 count=bulkRequests().length;await page.waitForTimeout(1500);assert.equal(bulkRequests().length,count);assert((await status().innerText()).includes('2 learners'));
 mode='ok';await page.getByRole('button',{name:'Retry save',exact:true}).click();await saved();
 console.log('PASS: malformed/negative/non-finite/above-max text retained; zero/blank preserved; errors and partial acknowledgements stop retry loops.');

 holdBulk=new Promise((resolve)=>{release=resolve});count=bulkRequests().length;await cell().fill('8');await waitCount(()=>bulkRequests().length,count+1);
 await page.getByLabel('Subject',{exact:true}).selectOption('2');assert.equal(await cell().isDisabled(),true);holdBulk=null;release();
 await page.waitForFunction(()=>document.querySelector('[aria-label="Subject"]').value==='2' && document.querySelector('tbody input')?.value==='');
 assert.equal(bulkRequests().at(-1).payload.subject_id,1);await page.getByLabel('Subject',{exact:true}).selectOption('1');await cell().waitFor();assert.equal(await cell().inputValue(),'8');
 await page.getByRole('button',{name:'Set up record',exact:true}).click();holdSetup=new Promise((resolve)=>{release=resolve});let setupCount=requests.filter((r)=>r.path==='/api/class-record/setup').length;
 await setupInputs().nth(0).fill('15');await waitCount(()=>requests.filter((r)=>r.path==='/api/class-record/setup').length,setupCount+1);
 await setupInputs().nth(0).fill('18');await cell().fill('9');holdSetup=null;release();await saved();assert.equal(await setupInputs().nth(0).inputValue(),'18');assert.equal(await cell().inputValue(),'9');
 await page.getByRole('button',{name:'Done setup',exact:true}).click();await page.getByRole('button',{name:'Set up record',exact:true}).waitFor();
 console.log('PASS: in-flight context changes flush original sheet; newer maxima and scores survive setup acknowledgements.');

 await page.getByRole('button',{name:'Set up record',exact:true}).click();setupCount=requests.filter((r)=>r.path==='/api/class-record/setup').length;
 await setupInputs().nth(0).fill('8');await page.getByRole('button',{name:'Retry save',exact:true}).waitFor();
 assert.equal(requests.filter((r)=>r.path==='/api/class-record/setup').length,setupCount,'Reducing HPS below an already-stored score cannot commit');
 assert.equal(await setupInputs().nth(0).inputValue(),'8');assert.equal(await cell().inputValue(),'9');
 await cell().fill('7');await saved();assert.equal(setups.get('1:1:1').ww_highest[0],8);assert.equal(await cell().inputValue(),'7','Combined maximum and learner correction saves safely');
 await page.getByRole('button',{name:'Done setup',exact:true}).click();await page.getByRole('button',{name:'Set up record',exact:true}).waitFor();
 console.log('PASS: HPS reductions validate every stored learner; combined header and learner corrections save together.');

 await page.getByRole('button',{name:'Import from Excel',exact:true}).click();
 assert.equal(await cell().isDisabled(),true,'Modal prevents background score edits');
 await page.locator('input[type="file"]').setInputFiles({name:'synthetic.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:Buffer.from('synthetic preview fixture')});
 await page.getByRole('button',{name:'Import and save',exact:true}).waitFor();
 await page.waitForFunction(()=>!document.querySelector('.modal-footer .btn-primary').disabled);
 assert.equal(requests.filter((r)=>r.path==='/api/class-record/import'&&r.saving).length,0,'File selection only previews');count=bulkRequests().length;
 await page.getByRole('button',{name:'Import and save',exact:true}).click();await page.getByRole('dialog').waitFor({state:'detached'});await cell().waitFor();
 assert.equal(requests.filter((r)=>r.path==='/api/class-record/import'&&r.saving).length,1);assert.equal(bulkRequests().length,count,'Import does not need second bulk save');
 assert((await page.locator('.toast-stack').innerText()).includes('No second save is needed.'));
 console.log('PASS: Excel preview remains read-only, background editing disabled, confirm imports and saves once.');

 // DataRouter protects every route entry point, including header useNavigate.
 mode='fail';await cell().fill('5');await page.getByRole('link',{name:'Dashboard sidebar',exact:true}).click();await page.getByRole('button',{name:'Retry save',exact:true}).waitFor();assert(new URL(page.url()).pathname==='/class-record');assert.equal(await cell().inputValue(),'5');
 mode='ok';await page.getByRole('link',{name:'Dashboard sidebar',exact:true}).click();await page.getByRole('heading',{name:'Dashboard',exact:true}).waitFor();
 await page.getByRole('link',{name:'Open record',exact:true}).click();await cell().waitFor();mode='fail';await cell().fill('6');await page.getByRole('button',{name:'Account menu',exact:true}).click();await page.getByRole('button',{name:'Retry save',exact:true}).waitFor();assert(new URL(page.url()).pathname==='/class-record');assert.equal(await cell().inputValue(),'6');
 mode='ok';await page.getByRole('button',{name:'Account menu',exact:true}).click();await page.getByRole('heading',{name:'Account',exact:true}).waitFor();
 await page.getByRole('link',{name:'Open record',exact:true}).click();await cell().waitFor();mode='fail';await cell().fill('7');await page.evaluate(()=>history.back());await page.getByRole('button',{name:'Retry save',exact:true}).waitFor();assert(new URL(page.url()).pathname==='/class-record');assert.equal(await cell().inputValue(),'7');
 mode='ok';await page.evaluate(()=>history.back());await page.getByRole('heading',{name:'Account',exact:true}).waitFor();
 assert.deepEqual(errors,[]);console.log('PASS: sidebar, programmatic account navigation and Back flush before leaving; failures retain sheet and drafts.');
 await page.getByRole('link',{name:'Open record',exact:true}).click();await cell().waitFor();
 await page.setViewportSize({width:375,height:1000});
 await page.getByRole('button',{name:'Set up record',exact:true}).click();
 assert.equal(await page.getByRole('rowheader',{name:'Highest possible score',exact:true}).count(),1);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'375px page stays within viewport; grid scrolls separately');
 const toolbar=await page.locator('.cr-setup-actions').boundingBox();assert(toolbar.width<=375,'Setup actions wrap within phone width');
 while(await page.getByRole('button',{name:'Dismiss',exact:true}).count())await page.getByRole('button',{name:'Dismiss',exact:true}).first().click();
 await page.screenshot({path:path.join(__dirname,'runtime/class-record-mobile375.png'),fullPage:true});
 console.log('PASS: mobile375 setup controls, explicit HPS label and save status wrap without page overflow.');
 await context.close();
 }finally{await browser.close();server.close()}
}
run().catch((error)=>{console.error(error);server.close();process.exitCode=1});
