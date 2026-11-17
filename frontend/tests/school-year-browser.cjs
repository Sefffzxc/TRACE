/* Real React screens with synthetic API responses; no production data is used.
   Run from the repository: node frontend/tests/school-year-browser.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { chromium } = require('../../audit/runtime/node_modules/playwright');
const frontend = path.resolve(__dirname, '..');
const entry = `
import React from 'react'; import {createRoot} from 'react-dom/client';
import {createBrowserRouter,RouterProvider} from 'react-router-dom';
import {AuthProvider} from './src/context/AuthContext';
import {ThemeProvider} from './src/context/ThemeContext';
import {ToastProvider} from './src/context/ToastContext';
import AppShell from './src/components/layout/AppShell';
import ClassManagementPage from './src/pages/app/ClassManagementPage';
import ClassRecordPage from './src/pages/app/ClassRecordPage';
import StudentsPage from './src/pages/app/StudentsPage';
import {paceApi} from './src/api/endpoints';
window.syntheticOldSave=()=>paceApi.bulkSave([],{school_year_id:1}).catch(e=>e.status);
const router=createBrowserRouter([{element:<AppShell/>,children:[
{path:'/class-management',element:<ClassManagementPage/>},
{path:'/class-record',element:<ClassRecordPage/>},
{path:'/students',element:<StudentsPage/>},
{path:'/dashboard',element:<h1>Dashboard</h1>}
]}]);
createRoot(document.getElementById('root')).render(<React.StrictMode><ThemeProvider><ToastProvider><AuthProvider><RouterProvider router={router}/></AuthProvider></ToastProvider></ThemeProvider></React.StrictMode>);
`;
let bundle = '';
const css = ['tokens.css', 'base.css', 'layout.css', 'pages.css'].map((name) => fs.readFileSync(path.join(frontend, 'src/styles', name), 'utf8')).join('\n');
const server = http.createServer((req, res) => {
  if (req.url === '/bundle.js') { res.setHeader('Content-Type', 'text/javascript'); res.end(bundle); }
  else if (req.url === '/styles.css') { res.setHeader('Content-Type', 'text/css'); res.end(css); }
  else { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><link rel="stylesheet" href="/styles.css"><div id="root"></div><script type="module" src="/bundle.js"></script>'); }
});

const years = [{ school_year_id: 1, year: '2026-2027' }, { school_year_id: 2, year: '2027-2028' }];
const grade = { grade_level_id: 1, name: 'Grade 1' };
const section = { section_id: 1, grade_level_id: 1, name: 'Lily', capacity: 40, enrolled_count: 1 };
const subject = { subject_id: 1, grade_level_id: 1, subject_name: 'Mathematics', is_active: true };
const learner = { student_id: 101, login_id: 'STUDENT-101', first_name: 'Alice', last_name: 'Learner', full_name: 'Alice Learner', section_id: 1, grade_level_id: 1, risk_level: 'low', profile_missing: [] };
const setup = { template: 'core', ww_highest: [10, null, null, null, null], pt_highest: [10, null, null], exam_highest: [10, null, null], ww_weight: 20, pt_weight: 50, ex_weight: 30, exam_subweights: [30, 30, 40] };

async function fixture(browser, origin, role) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const profile = { id: 'teacher-1', role, first_name: 'Test', last_name: role, school_id: 1 };
  await context.addInitScript((user) => { localStorage.setItem('trace.user', JSON.stringify(user)); localStorage.setItem('trace.access_token', 'synthetic-token'); }, profile);
  const state = { current: 1, assigned: new Set([1]), enrolled: new Set([1]), requests: [], errors: [] };
  await context.route('**/api/**', async (route) => {
    const request = route.request(), url = new URL(request.url()), pathname = url.pathname;
    const headers = { 'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Headers': 'Content-Type,Authorization,X-School-Year-Id', 'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    state.requests.push({ pathname, method: request.method(), year: request.headers()['x-school-year-id'], body: request.postData() });
    const receivedYear = Number(request.headers()['x-school-year-id']);
    let body = null, status = 200;
    if (pathname === '/api/auth/me') body = profile;
    else if (pathname === '/api/schools/me') body = { school_id: 1, name: 'Synthetic School', tracks_units: false, grading_periods: 3, grading_period_label: 'Term' };
    else if (pathname === '/api/school-years/current') body = { ...years.find((row) => row.school_year_id === state.current), is_current: true };
    else if (pathname === '/api/school-years') body = years.map((row) => ({ ...row, is_current: row.school_year_id === state.current }));
    else if (pathname.startsWith('/api/school-years/') && request.method() === 'PATCH') {
      state.current = Number(pathname.split('/').at(-1)); body = { ...years.find((row) => row.school_year_id === state.current), is_current: true };
    } else if (receivedYear && receivedYear !== state.current && !pathname.startsWith('/api/staff')) {
      status = 409; body = { detail: { code: 'school_year_changed', message: 'The current school year has changed.', current_school_year_id: state.current } };
    } else if (pathname === '/api/staff/pending') body = [];
    else if (pathname === '/api/staff') body = [{ ...profile, role: 'teacher', account_status: 'approved', is_active: true }];
    else if (pathname === '/api/grade-levels') body = [grade];
    else if (pathname === '/api/sections') body = [{ ...section, enrolled_count: state.enrolled.has(state.current) ? 1 : 0 }];
    else if (pathname === '/api/subjects') body = [subject];
    else if (pathname === '/api/teacher-assignments') body = state.assigned.has(state.current) ? [{ assignment_id: 1, teacher_id: profile.id, section_id: 1, subject_id: 1, teacher_name: 'Test teacher', subject_name: 'Mathematics', section_name: 'Lily', school_year_id: state.current }] : [];
    else if (pathname === '/api/section-advisers') body = state.assigned.has(state.current) ? [{ adviser_id: 1, section_id: 1, teacher_id: profile.id, teacher_name: 'Test teacher', section_name: 'Lily', school_year_id: state.current }] : [];
    else if (pathname === '/api/my-classes') body = { school_year_id: state.current, grade: state.assigned.has(state.current) ? { '1': ['Mathematics'] } : {}, advisory: state.assigned.has(state.current) ? [1] : [], unrestricted: false };
    else if (pathname === '/api/early-warnings') body = [];
    else if (pathname === '/api/ai/status') body = { reachable: false, model_loaded: false };
    else if (pathname === '/api/class-record/setup') body = setup;
    else if (pathname === '/api/enrollments' && request.method() === 'POST') { state.enrolled.add(state.current); body = { enrollment_id: 302, ...request.postDataJSON() }; }
    else if (pathname === '/api/enrollments') body = state.enrolled.has(state.current) ? [{ enrollment_id: state.current === 1 ? 201 : 302, student_id: 101, section_id: 1, student_name: 'Alice Learner', school_year_id: state.current }] : [];
    else if (pathname === '/api/paces/bulk') body = { saved: 0, skipped: [] };
    else if (pathname === '/api/paces') body = state.current === 1 ? [{ enrollment_id: 201, student_id: 101, subject: 'Mathematics', grading_period: 1, ww_scores: [8], pt_scores: [7], exam_scores: [6] }] : [];
    else if (pathname === '/api/students/returning') body = state.enrolled.has(state.current) ? [] : [learner];
    else if (pathname === '/api/students') body = state.enrolled.has(state.current) ? [learner] : [];
    else if (pathname === '/api/class-record/import') body = { counts: { matched: 1, new: 0, enrol: 0, elsewhere: 0 }, learners: [{ name: 'Alice Learner', gender: 'Female', status: 'matched' }], warnings: [], terms: [{ term: 1, learners: 1, graded: 1, written_works: 5, performance_tasks: 3, exams: 3 }] };
    else throw new Error(`Unexpected synthetic API call: ${request.method()} ${pathname}`);
    return route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
  });
  const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', (error) => state.errors.push(error.message));
  return { page, context, state };
}

async function run() {
  const { build } = await import(pathToFileURL(path.join(frontend, 'node_modules/vite/dist/node/index.js')).href);
  const virtualEntry = path.join(frontend, 'school-year-browser-harness.jsx').replace(/\\/g, '/');
  const result = await build({ configFile: false, root: frontend, logLevel: 'error', esbuild: { jsx: 'automatic' },
    define: { 'import.meta.env.VITE_API_URL': JSON.stringify('http://api.invalid') },
    plugins: [{ name: 'synthetic-browser-harness', resolveId(id) { if (id === virtualEntry) return id; }, load(id) { if (id === virtualEntry) return entry; } }],
    build: { write: false, minify: false, rollupOptions: { input: virtualEntry, output: { inlineDynamicImports: true } } } });
  bundle = result.output.find((item) => item.type === 'chunk').code;
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const passed = [];
  try {
    {
      const { page, context, state } = await fixture(browser, origin, 'admin');
      await page.goto(`${origin}/class-management`);
      await page.getByText('Showing assignments for S.Y. 2026-2027.', { exact: false }).waitFor();
      await page.getByRole('row').filter({ hasText: '2026-2027' }).getByText('Current', { exact: true }).waitFor();
      assert.equal(await page.locator('.setup-nav button').filter({ hasText: 'Subject teachers' }).locator('.count').innerText(), '1');
      const activate = async (year) => {
        await page.getByRole('row').filter({ hasText: year }).getByTitle('Edit', { exact: true }).click();
        await page.getByLabel('Make this the current school year').check();
        await page.getByRole('button', { name: 'Save changes', exact: true }).click();
        await page.locator('.header').getByText(`S.Y. ${year}`).waitFor();
        await page.getByText(`Showing assignments for S.Y. ${year}.`, { exact: false }).waitFor();
        await page.getByRole('row').filter({ hasText: year }).getByText('Current', { exact: true }).waitFor();
      };
      await activate('2027-2028');
      assert.equal(state.current, 2);
      assert.equal(await page.locator('.setup-nav button').filter({ hasText: 'Subject teachers' }).locator('.count').innerText(), '0');
      assert.equal(await page.locator('.setup-nav button').filter({ hasText: 'Section advisers' }).locator('.count').innerText(), '0');
      assert.equal(await page.locator('.setup-nav button').filter({ hasText: 'Grades & sections' }).locator('.count').innerText(), '1');
      assert.equal(await page.locator('.setup-nav button').filter({ hasText: 'Subjects' }).locator('.count').innerText(), '1');
      await activate('2026-2027');
      assert.equal(await page.locator('.setup-nav button').filter({ hasText: 'Subject teachers' }).locator('.count').innerText(), '1');
      assert.deepEqual(state.errors, []); passed.push('Principal switches year immediately; annual assignments empty, master setup persists, old assignments restore.');
      await context.close();
    }
    {
      const { page, context, state } = await fixture(browser, origin, 'teacher');
      await page.goto(`${origin}/class-record`);
      const cell = page.getByLabel('Alice Learner written work 1', { exact: true });
      await cell.waitFor(); assert.equal(await cell.inputValue(), '8');
      await cell.fill('9');
      state.current = 2;
      await page.evaluate(() => window.dispatchEvent(new Event('focus')));
      await page.getByText('No assigned classes', { exact: true }).waitFor();
      await page.waitForTimeout(1400);
      assert.equal(state.requests.filter((request) => request.pathname === '/api/paces/bulk').length, 0);
      assert.equal(await page.evaluate(() => window.syntheticOldSave()), 409);
      const blocked = state.requests.filter((request) => request.pathname === '/api/paces/bulk').at(-1);
      assert.equal(blocked.year, '1');
      assert.deepEqual(state.errors, []); passed.push('External year switch clears unsaved sheet and cancels autosave; old operation keeps its captured header and is rejected.');
      await context.close();
    }
    {
      const { page, context, state } = await fixture(browser, origin, 'teacher');
      await page.goto(`${origin}/class-record`);
      await page.getByRole('button', { name: 'Import from Excel' }).click();
      await page.locator('input[type="file"]').setInputFiles({ name: 'synthetic.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('synthetic mocked workbook') });
      await page.getByRole('button', { name: 'Import and save', exact: true }).waitFor();
      state.current = 2; state.assigned.add(2);
      await page.evaluate(() => window.dispatchEvent(new Event('focus')));
      await page.getByText('No learners here yet', { exact: true }).waitFor();
      assert.equal(await page.getByRole('dialog').count(), 0);
      assert.equal(state.requests.filter((request) => request.pathname === '/api/class-record/import').length, 1);
      await page.goto(`${origin}/students`);
      await page.getByText('No students enrolled this school year', { exact: true }).waitFor();
      await page.getByRole('button', { name: 'Enroll returning student', exact: true }).click();
      await page.getByLabel('Find an existing student').fill('Alice');
      await page.getByRole('radio').waitFor(); await page.getByRole('radio').check();
      await page.getByRole('button', { name: 'Enroll student', exact: true }).click();
      await page.getByText('Alice Learner', { exact: true }).waitFor();
      const enrollment = state.requests.filter((request) => request.pathname === '/api/enrollments' && request.method === 'POST').at(-1);
      assert.deepEqual(JSON.parse(enrollment.body), { student_id: 101, grade_level_id: 1, section_id: 1, school_year_id: 2 });
      assert.equal(enrollment.year, '2');
      assert.equal(state.requests.filter((request) => request.pathname === '/api/students' && request.method === 'POST').length, 0);
      assert.deepEqual(state.errors, []); passed.push('Import preview is discarded on year switch; returning-student UI reuses the existing student ID for new-year enrollment.');
      await context.close();
    }
    process.stdout.write(JSON.stringify({ passed }, null, 2) + '\n');
  } finally { await browser.close(); await new Promise((resolve) => server.close(resolve)); }
}
run().catch((error) => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; server.close(); });
