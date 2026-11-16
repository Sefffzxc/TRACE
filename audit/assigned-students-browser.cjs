/* Synthetic assigned-roster UI checks. No production service or student data. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const esbuild = require('../frontend/node_modules/esbuild');
const { chromium } = require('./runtime/node_modules/playwright');

const frontend = path.resolve(__dirname, '../frontend');
const bundle = esbuild.buildSync({
  stdin: { contents: `
    import React from 'react';
    import { createRoot } from 'react-dom/client';
    import { MemoryRouter, Routes, Route } from 'react-router-dom';
    import { AuthProvider } from './src/context/AuthContext';
    import { ThemeProvider } from './src/context/ThemeContext';
    import { ToastProvider } from './src/context/ToastContext';
    import StudentsPage from './src/pages/app/StudentsPage';
    import StudentFormModal from './src/components/common/StudentFormModal';
    import ImportClassRecord from './src/components/common/ImportClassRecord';
    const grades = [{grade_level_id: 1, name:'Grade 1'}, {grade_level_id: 2, name:'Grade 2'}, {grade_level_id:3,name:'Grade 3'}];
    const sections = [{section_id: 10, grade_level_id:1, name:'Lily',capacity:40,enrolled_count:0},
      {section_id: 11, grade_level_id:2, name:'Rose',capacity:40,enrolled_count:0},
      {section_id:12,grade_level_id:1,name:'Unrelated',capacity:40,enrolled_count:0}];
    createRoot(document.getElementById('root')).render(
      <React.StrictMode><ThemeProvider><AuthProvider><ToastProvider>
        <MemoryRouter initialEntries={[location.pathname]}><Routes>
          <Route path="/students" element={<StudentsPage />} />
          <Route path="/direct-add" element={<StudentFormModal gradeLevels={grades} sections={sections} onClose={()=>{}} onSaved={()=>{}} />} />
          <Route path="/score-import" element={<ImportClassRecord section={sections[0]} subject={{subject_id:8,subject_name:'Math'}} periodLabel="Term" onClose={()=>{}} onDone={()=>{ document.body.dataset.importDone='true'; }} />} />
        </Routes></MemoryRouter>
      </ToastProvider></AuthProvider></ThemeProvider></React.StrictMode>
    );
  `, resolveDir: frontend, loader:'jsx' },
  bundle:true,write:false,platform:'browser',format:'iife',jsx:'automatic',
  define:{'import.meta.env':'{}','process.env.NODE_ENV':'"development"'},
});
const css = ['tokens.css','base.css','layout.css','pages.css'].map((name)=>fs.readFileSync(path.join(frontend,'src/styles',name))).join('\n');
const server=http.createServer((req,res)=>{
  if(req.url==='/bundle.js'){res.setHeader('Content-Type','text/javascript');res.end(bundle.outputFiles[0].text);}
  else if(req.url==='/styles.css'){res.setHeader('Content-Type','text/css');res.end(css);}
  else{res.setHeader('Content-Type','text/html');res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/styles.css"></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>');}
});
const grades=[{grade_level_id:1,name:'Grade 1'},{grade_level_id:2,name:'Grade 2'},{grade_level_id:3,name:'Grade 3'}];
const sections=[{section_id:10,grade_level_id:1,name:'Lily',capacity:40,enrolled_count:0},{section_id:11,grade_level_id:2,name:'Rose',capacity:40,enrolled_count:0},{section_id:12,grade_level_id:1,name:'Unrelated',capacity:40,enrolled_count:0}];
const optionValues=(locator)=>locator.locator('option').evaluateAll((options)=>options.map((option)=>option.value));
const file={name:'synthetic-class.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:Buffer.from('Synthetic workbook; parser mocked locally')};

async function run(){
  await new Promise((resolve)=>server.listen(0,'127.0.0.1',resolve));
  const origin=`http://127.0.0.1:${server.address().port}`;
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try{
    for(const width of [375,1440]){
      const context=await browser.newContext({viewport:{width,height:1000}});
      await context.addInitScript(()=>{if(!localStorage.getItem('trace.user'))localStorage.setItem('trace.user',JSON.stringify({role:'teacher',id:'synthetic',first_name:'Test',last_name:'Teacher'}));});
      let access={unrestricted:false,grade:{10:['Math']},advisory:[11]};
      let failAccess=false,blocked=false;
      const creates=[],imports=[],errors=[];
      await context.route('**/api/**',async(route)=>{
        const request=route.request(),url=new URL(request.url());
        const reply=(body,status=200)=>route.fulfill({status,json:body,headers:{'access-control-allow-origin':'*'}});
        if(request.method()==='OPTIONS')return reply({});
        if(url.pathname==='/api/schools/me')return reply({school_id:1,tracks_units:false});
        if(url.pathname==='/api/my-classes')return failAccess?reply({detail:'Assigned sections unavailable.'},503):reply(access);
        if(url.pathname==='/api/grade-levels')return reply(grades);
        if(url.pathname==='/api/sections')return reply(sections);
        if(url.pathname==='/api/students'){
          if(request.method()==='POST'){creates.push(request.postDataJSON());return reply({student_id:1});}
          return reply([]);
        }
        if(url.pathname==='/api/class-record/import'){
          const body=request.postDataBuffer().toString('utf8');
          const roster=/name="roster_only"\r\n\r\ntrue/.test(body),save=/name="save"\r\n\r\ntrue/.test(body);
          imports.push({body,roster,save});
          return reply({roster_only:roster,can_add_students:true,can_import:!blocked,blocking_reason:blocked?'Only an assigned teacher or adviser may add missing learners.':null,
            counts:{matched:0,new:1,enrol:0,elsewhere:0},warnings:[],learners:[{name:'DOE, ALICE',gender:'Female',status:'new'}],
            terms:roster?[]:[{term:1,learners:1,graded:1,written_works:1,performance_tasks:1,exams:1}],
            saved:save?{added:1,enrolled:0,skipped:0,terms:roster?[]:[1],ai:null}:null});
        }
        throw new Error('Unexpected synthetic request '+url.pathname);
      });
      const page=await context.newPage();
      page.on('pageerror',(error)=>errors.push(error.message));
      await page.goto(origin+'/students');
      const add=page.getByRole('button',{name:'Add student',exact:true});
      await add.waitFor();await page.waitForFunction(()=>![...document.querySelectorAll('button')].find((button)=>button.textContent.trim()==='Add student').disabled);
      assert.deepEqual(await optionValues(page.getByLabel('Filter by grade level')),['','1','2']);
      assert.deepEqual(await optionValues(page.getByLabel('Filter by section')),['','10','11']);
      await add.click();
      await page.locator('#s_grade option[value="2"]').waitFor({state:'attached'});
      assert.deepEqual(await optionValues(page.locator('#s_grade')),['','1','2']);
      await page.locator('#s_grade').selectOption('1');
      await page.waitForFunction(()=>document.querySelector('#s_section').value==='10');
      assert.deepEqual(await optionValues(page.locator('#s_section')),['','10']);
      await page.locator('#s_grade').selectOption('2');
      await page.waitForFunction(()=>document.querySelector('#s_section').value==='11');
      assert.deepEqual(await optionValues(page.locator('#s_section')),['','11']);
      await page.locator('#s_first').fill('Alice');await page.locator('#s_last').fill('Doe');
      await page.locator('button[form="student-form"]').click();
      await page.getByText('Student added and enrolled.',{exact:true}).waitFor();
      assert.equal(creates.at(-1).section_id,11,'Adviser can add to advisory section');

      await page.getByRole('button',{name:'Import students',exact:true}).click();
      await page.locator('#roster_grade option[value="1"]').waitFor({state:'attached'});
      assert.deepEqual(await optionValues(page.locator('#roster_grade')),['','1','2']);
      await page.locator('#roster_grade').selectOption('2');
      await page.waitForFunction(()=>document.querySelector('#roster_section').value==='11');
      await page.getByRole('button',{name:'Choose workbook',exact:true}).click();
      await page.locator('input[type="file"]').setInputFiles(file);
      await page.getByText('1 learners in the file',{exact:false}).waitFor();
      assert(imports.at(-1).roster,'Roster preview uses roster_only=true');
      assert(!imports.at(-1).body.includes('name="subject_id"'),'Roster has no subject');
      assert.equal(imports.at(-1).save,false,'File choice only previews');
      await page.getByRole('button',{name:'Import students',exact:true}).last().click();
      await page.getByText('Students imported: 1 added, 0 enrolled.',{exact:true}).waitFor();
      assert(imports.at(-1).roster && imports.at(-1).save,'Roster save retains mode');

      access={unrestricted:false,grade:{10:['Math']},advisory:[]};
      await page.goto(origin+'/direct-add');
      await page.waitForFunction(()=>document.querySelector('#s_section').value==='10');
      assert.deepEqual(await optionValues(page.locator('#s_grade')),['','1']);
      assert.deepEqual(await optionValues(page.locator('#s_section')),['','10']);
      access={unrestricted:false,grade:{},advisory:[]};
      await page.goto(origin+'/students');
      await page.getByText('You need a teaching or adviser assignment before adding or importing students. Ask your principal to assign your section.',{exact:true}).waitFor();
      assert(await page.getByRole('button',{name:'Add student',exact:true}).isDisabled());
      assert(await page.getByRole('button',{name:'Import students',exact:true}).isDisabled());
      failAccess=true;
      await page.goto(origin+'/students');
      await page.getByRole('alert').filter({hasText:'Assigned sections unavailable.'}).waitFor();
      assert.deepEqual(await optionValues(page.getByLabel('Filter by section')),['']);
      assert(await page.getByRole('button',{name:'Add student',exact:true}).isDisabled());
      await page.goto(origin+'/direct-add');
      await page.getByRole('alert').filter({hasText:'Assigned sections unavailable.'}).waitFor();
      assert(await page.locator('button[form="student-form"]').isDisabled());
      failAccess=false;
      await page.evaluate(()=>localStorage.setItem('trace.user',JSON.stringify({role:'admin',first_name:'Test',last_name:'Principal'})));
      await page.goto(origin+'/students');
      await page.getByLabel('Filter by section').locator('option[value="12"]').waitFor({state:'attached'});
      assert.equal(await page.getByRole('button',{name:'Add student',exact:true}).count(),0);
      assert.equal(await page.getByRole('button',{name:'Import students',exact:true}).count(),0);
      await page.goto(origin+'/direct-add');
      await page.getByText('Only a section’s assigned teachers or adviser can add students.',{exact:true}).waitFor();
      assert(await page.locator('button[form="student-form"]').isDisabled());

      blocked=true;
      await page.goto(origin+'/score-import');
      await page.locator('input[type="file"]').setInputFiles(file);
      await page.getByRole('alert').filter({hasText:'Only an assigned teacher or adviser may add missing learners.'}).waitFor();
      assert(await page.getByRole('button',{name:'Import and save',exact:true}).isDisabled());
      blocked=false;
      await page.evaluate(()=>localStorage.setItem('trace.user',JSON.stringify({role:'teacher',first_name:'Test',last_name:'Teacher'})));
      await page.goto(origin+'/score-import');
      await page.locator('input[type="file"]').setInputFiles(file);
      await page.getByRole('button',{name:'Import and save',exact:true}).waitFor();
      await page.waitForFunction(()=>![...document.querySelectorAll('button')].find((button)=>button.textContent.trim()==='Import and save').disabled);
      await page.getByRole('button',{name:'Import and save',exact:true}).click();
      await page.waitForFunction(()=>document.body.dataset.importDone==='true');
      assert(!imports.at(-1).roster && imports.at(-1).save);
      assert(imports.at(-1).body.includes('name="subject_id"\r\n\r\n8'));
      assert.deepEqual(errors,[]);
      await context.close();
      console.log(`PASS @${width}: assigned/advisory grades and sections, own-access modal, roster preview/save with no scores, principal blocked, no assignments, fail-closed lookup, score import permissions`);
    }
  }finally{await browser.close();}
}
run().catch((error)=>{console.error(error);process.exitCode=1;}).finally(()=>server.close());
