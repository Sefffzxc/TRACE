/* Run: node audit/advisory-class-record-browser.cjs
   Real Class Record page/hook, synthetic fixtures and mocked API only. */
const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');
const esbuild = require('../frontend/node_modules/esbuild');
const { chromium } = require('./runtime/node_modules/playwright');
const frontend = path.resolve(__dirname, '../frontend');
const built = esbuild.buildSync({ stdin: { contents: `
  import React,{useState} from 'react'; import {createRoot} from 'react-dom/client';
  import {createBrowserRouter,RouterProvider,Link} from 'react-router-dom';
  import {AuthProvider} from './src/context/AuthContext'; import {ToastProvider} from './src/context/ToastContext';
  import ClassRecordPage from './src/pages/app/ClassRecordPage';
  import useClassRecordAutosave from './src/hooks/useClassRecordAutosave';
  function Probe(){const [editable,setEditable]=useState(true);const [subject,setSubject]=useState(1);
    const record=useClassRecordAutosave({section_id:1,subject_id:subject,subject_name:subject===1?'Mathematics':'Science',grading_period:1},0,{canEdit:editable});
    window.probe=record;window.probeEdit=setEditable;window.probeSubject=setSubject;
    return <div>{record.ready?'Probe ready':'Probe loading'}</div>}
  const router=createBrowserRouter([{path:'/class-record',element:<><Link to='/dashboard'>Dashboard</Link><ClassRecordPage/></>},
    {path:'/dashboard',element:<h1>Dashboard</h1>},{path:'/probe',element:<Probe/>}]);
  createRoot(document.getElementById('root')).render(<ToastProvider><AuthProvider><RouterProvider router={router}/></AuthProvider></ToastProvider>);
`, resolveDir: frontend, loader: 'jsx' }, bundle: true, write: false, jsx: 'automatic', platform: 'browser', format: 'iife', define: { 'import.meta.env': '{"VITE_API_URL":"http://api.invalid"}', 'process.env.NODE_ENV': '"development"' } });
const server = http.createServer((req, res) => {
  if (req.url === '/bundle.js') { res.setHeader('Content-Type', 'text/javascript'); res.end(built.outputFiles[0].text); }
  else { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><div id="root"></div><script src="/bundle.js"></script>'); }
});
const setup = { template: 'core', ww_highest: [20], pt_highest: [20], exam_highest: [20, 20, 20], ww_weight: 20, pt_weight: 50, ex_weight: 30, exam_subweights: [30, 30, 40] };
async function run() {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1200, height: 900 } });
    await context.addInitScript(() => { localStorage.setItem('trace.user', JSON.stringify({ id: 'adviser', role: 'teacher' })); localStorage.setItem('trace.access_token', 'synthetic-access'); });
    const writes = [], errors = [];
    let teaching = false, bulkFailure = false, setupGate = null, scienceFailure = false;
    let mathScore = 7;
    await context.route('**/api/**', async (route) => {
      const req = route.request(), url = new URL(req.url()), pathname = url.pathname;
      const headers = { 'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Headers': 'Content-Type,Authorization', 'Access-Control-Allow-Methods': 'GET,POST,PUT,OPTIONS' };
      if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
      let body = {}, status = 200;
      if (pathname === '/api/auth/me') body = { id: 'adviser', role: 'teacher' };
      else if (pathname === '/api/schools/me') body = { school_id: 1, grading_periods: 3, grading_period_label: 'Term', tracks_units: false };
      else if (pathname === '/api/sections') body = [{ section_id: 1, name: 'Faith', grade_level_id: 7 }, { section_id: 2, name: 'Hope', grade_level_id: 7 }];
      else if (pathname === '/api/subjects') body = [{ subject_id: 1, subject_name: 'Mathematics', grade_level_id: 7 }, { subject_id: 2, subject_name: 'Science', grade_level_id: 7 }, { subject_id: 3, subject_name: 'English', grade_level_id: 7 }, { subject_id: 4, subject_name: 'Other grade', grade_level_id: 8 }];
      else if (pathname === '/api/my-classes') body = { unrestricted: false, grade: teaching ? { '1': ['Mathematics'] } : {}, advisory: [1] };
      else if (pathname === '/api/ai/status') body = { available: true, ready: true };
      else if (pathname === '/api/enrollments') body = [{ enrollment_id: 201, student_id: 101, student_name: 'Alice Learner', section_id: 1 }];
      else if (pathname === '/api/paces') body = ['Mathematics', 'Science'].map((subject) => ({ enrollment_id: 201, student_id: 101, subject, grading_period: 1, ww_scores: [subject === 'Mathematics' ? mathScore : 9], pt_scores: [10], exam_scores: [10, 10, 10] }));
      else if (pathname === '/api/class-record/setup') {
        if (req.method() === 'PUT') { const payload = req.postDataJSON(); writes.push({ path: pathname, payload, subject: Number(url.searchParams.get('subject_id')) }); if (setupGate) await setupGate; body = payload; }
        else if (scienceFailure && url.searchParams.get('subject_id') === '2') { status = 503; body = { detail: 'Synthetic read failure' }; }
        else body = setup;
      } else if (pathname === '/api/paces/bulk') {
        const payload = req.postDataJSON(); writes.push({ path: pathname, payload, subject: payload.subject_id });
        if (bulkFailure) { status = 503; body = { detail: 'Synthetic write failure' }; }
        else { mathScore = payload.entries[0].ww_scores[0]; body = { saved: payload.entries.length, skipped: [] }; }
      } else if (pathname === '/api/class-record/import') throw new Error('Advisory records must not import.');
      else throw new Error('Unexpected request ' + req.method() + ' ' + pathname);
      await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
    });
    const page = await context.newPage(); page.setDefaultTimeout(8000); page.on('pageerror', (error) => errors.push(error.message));
    const score = () => page.getByLabel('Alice Learner written work 1', { exact: true });
    const waitReadonly = async () => { await page.locator('.cr-save-message strong').filter({ hasText: 'Read only' }).waitFor(); await score().waitFor(); };
    await page.goto(origin + '/class-record'); await waitReadonly();
    assert.deepEqual(await page.getByLabel('Section', { exact: true }).locator('option').allTextContents(), ['Faith']);
    assert.deepEqual(await page.getByLabel('Subject', { exact: true }).locator('option').allTextContents(), ['Mathematics', 'Science', 'English']);
    assert.equal(await score().innerText(), '7');
    assert.equal(await page.locator('tbody input, .cr-hps input').count(), 0);
    for (const name of ['Set up record', 'Import from Excel', 'Save now', 'Retry save']) assert.equal(await page.getByRole('button', { name, exact: true }).count(), 0);
    await page.getByLabel('Subject', { exact: true }).selectOption('2'); await waitReadonly(); assert.equal(await score().innerText(), '9');
    await page.getByLabel('Term', { exact: true }).selectOption('2'); await waitReadonly();
    assert.equal(await page.evaluate(() => window.dispatchEvent(new Event('beforeunload', { cancelable: true }))), true);
    await page.waitForTimeout(1400); assert.equal(writes.length, 0);
    await page.getByRole('link', { name: 'Dashboard', exact: true }).click(); await page.getByRole('heading', { name: 'Dashboard', exact: true }).waitFor();
    console.log('PASS: adviser-only records show every advisory subject and stored scores; no editing, setup, import, save, writes or unsaved warnings.');

    teaching = true; await page.goto(origin + '/class-record'); await score().waitFor(); assert.equal(await score().isEditable(), true);
    await score().fill('8'); await page.getByLabel('Subject', { exact: true }).selectOption('2'); await waitReadonly();
    assert.equal(writes.length, 1); assert.equal(writes[0].subject, 1); assert.equal(writes[0].payload.entries[0].ww_scores[0], 8);
    await page.waitForTimeout(1400); assert.equal(writes.length, 1);
    await page.getByLabel('Subject', { exact: true }).selectOption('1'); await score().waitFor(); assert.equal(await score().inputValue(), '8');
    bulkFailure = true; await score().fill('6'); await page.getByLabel('Subject', { exact: true }).selectOption('2');
    await page.getByRole('button', { name: 'Retry save', exact: true }).waitFor(); assert.equal(await page.getByLabel('Subject', { exact: true }).inputValue(), '1'); assert.equal(await score().inputValue(), '6');
    bulkFailure = false; await page.getByRole('button', { name: 'Retry save', exact: true }).click(); await page.waitForFunction(() => document.querySelector('.cr-save-message strong')?.textContent === 'Saved');
    scienceFailure = true; await page.getByLabel('Subject', { exact: true }).selectOption('2'); await page.getByRole('button', { name: 'Retry loading record', exact: true }).waitFor();
    const count = writes.length; await page.waitForTimeout(1400); assert.equal(writes.length, count); assert.equal(await page.getByRole('button', { name: 'Save now', exact: true }).count(), 0);
    scienceFailure = false; await page.getByRole('button', { name: 'Retry loading record', exact: true }).click(); await waitReadonly(); assert.equal(await score().innerText(), '9');
    console.log('PASS: taught subject autosaves before switching to advisory read-only; failed saves retain original sheet; read-only load failures never write stale scores.');

    await page.goto(origin + '/probe'); await page.waitForFunction(() => window.probe?.ready);
    let release; setupGate = new Promise((resolve) => { release = resolve; });
    const before = writes.length;
    await page.evaluate(() => {
      window.staleProbe = window.probe;
      window.probe.setCell(201, 'ww_scores', 0, '5');
      window.probe.setSetupDraft((head) => ({ ...head, ww_highest: [25] }));
      window.probe.save();
    });
    await page.waitForFunction(() => window.probe.saving); await new Promise((resolve) => setTimeout(resolve, 80));
    assert.equal(writes.length, before + 1); assert.equal(writes.at(-1).path, '/api/class-record/setup');
    await page.evaluate(() => window.probeEdit(false)); await page.waitForFunction(() => window.probe.ready && !window.probe.pending);
    setupGate = null; release(); await page.waitForTimeout(100);
    await page.evaluate(async () => {
      window.staleProbe.setCell(201, 'ww_scores', 0, '12');
      window.staleProbe.setSetupDraft((head) => ({ ...head, ww_highest: [30] }));
      await window.staleProbe.save(); await window.staleProbe.flush(); window.staleProbe.saveOnBlur();
      window.probe.setCell(201, 'ww_scores', 0, '13'); window.probe.setSetupDraft((head) => ({ ...head, ww_highest: [40] }));
      await window.probe.save(); await window.probe.flush(); window.probe.saveOnBlur();
    });
    await page.waitForTimeout(1400); assert.equal(writes.length, before + 1, 'Privilege loss between setup and score API calls stops the score batch and all stale callbacks');
    assert.equal(await page.evaluate(() => window.probe.pending || window.probe.dirtyCount || window.probe.setupDirty), false);
    await page.evaluate(() => window.probeSubject(2)); await page.waitForFunction(() => window.probe.ready && window.probe.context.subject_id === 2);
    await page.evaluate(async () => { window.staleProbe.setCell(201, 'ww_scores', 0, '19'); await window.staleProbe.save(); });
    await page.waitForTimeout(1300); assert.equal(writes.length, before + 1); assert.equal(await page.evaluate(() => window.probe.allRows[0].ww[0]), '9');
    assert.equal(await page.evaluate(() => window.dispatchEvent(new Event('beforeunload', { cancelable: true }))), true);
    assert.deepEqual(errors, []);
    console.log('PASS: hook independently blocks current/stale edits, setup, save, flush and blur in read-only contexts, including permission loss during a setup request.');
    await context.close();
  } finally { await browser.close(); server.close(); }
}
run().catch((error) => { console.error(error); server.close(); process.exitCode = 1; });
