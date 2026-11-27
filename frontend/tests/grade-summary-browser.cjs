/* Exercise the real screens with invented grades and mocked API responses.
   Run: node frontend/tests/grade-summary-browser.cjs */
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
import ClassRecordPage from './src/pages/app/ClassRecordPage';
import GradeSummaryPage from './src/pages/app/GradeSummaryPage';
const router=createBrowserRouter([{element:<AppShell/>,children:[
 {path:'/grade-summary',element:<GradeSummaryPage/>},
 {path:'/class-record',element:<ClassRecordPage/>},
 {path:'/students/:id',element:<h1>Student profile</h1>},
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
const sections = [{ section_id: 10, grade_level_id: 1, name: 'Faith', grade_level_name: 'Grade 8' },
  { section_id: 11, grade_level_id: 2, name: 'Hope', grade_level_name: 'Grade 7' }];
const subjects = [{ subject_id: 100, grade_level_id: 1, subject_name: 'Mathematics' },
  { subject_id: 101, grade_level_id: 1, subject_name: 'English' },
  { subject_id: 200, grade_level_id: 2, subject_name: 'Science' }];
const levels = ['critical', 'high', 'moderate', 'low', null];
const names = ['Alexandra Gabrielle Dela Cruz Villanueva', 'Risk Learner', 'Watch Learner', 'Ready Learner', 'New Learner'];

function summary(state, sectionId, subjectId) {
  return { school_year_id: 1, section_id: sectionId, section_name: sectionId === 10 ? 'Faith' : 'Hope', subject_id: subjectId,
    subject: subjects.find((s) => s.subject_id === subjectId).subject_name,
    period_count: 3, period_label: 'Term', passing_mark: 75,
    rows: sectionId === 11 ? [] : names.map((student_name, i) => ({ student_id: i + 1, enrollment_id: i + 10,
      student_name, login_id: `S00${i + 1}`, term_grades: i === 4 ? [null, null, null] : i === 3 ? [80, 84, 88] : [70 + i * 5, 74 + i * 5, null],
      completed_periods: i === 4 ? 0 : i === 3 ? 3 : 2,
      current_average: i === 4 ? null : i === 3 ? 84 : 72 + i * 5,
      final_grade: i === 3 ? 84 : null, descriptor: i === 3 ? 'Benchmarking' : null,
      result: i === 3 ? 'Passed' : 'Pending', risk_level: state.saved && i === 0 ? 'low' : levels[i],
      ai: i === 4 ? null : { predicted_final: 74 + i * 5, range_low: 72 + i * 5, range_high: 76 + i * 5,
        source: i === 0 ? 'fallback' : 'model', model_version: '5.0.0-second-term',
        features: { q1_term_grade: 70 + i * 5, q2_term_grade: 74 + i * 5 }, created_at: '2026-11-26T12:00:00Z' },
    })) };
}

async function fixture(browser, origin) {
  const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
  const profile = { id: 'teacher-1', role: 'teacher', first_name: 'Test', last_name: 'Teacher', school_id: 1 };
  await context.addInitScript((user) => {
    localStorage.setItem('trace.user', JSON.stringify(user));
    localStorage.setItem('trace.access_token', 'synthetic-token');
    localStorage.setItem('trace.theme', 'light');
    localStorage.setItem('trace.class-record.v1:teacher-1:1:1', JSON.stringify({ sectionId: '10', subjectId: '100', term: 2 }));
  }, profile);
  const state = { requests: [], errors: [], saved: false, failSummary: false, summaryDelay: 0 };
  await context.route('**/api/**', async (route) => {
    const request = route.request(), url = new URL(request.url()), pathname = url.pathname;
    const headers = { 'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Headers': 'Content-Type,Authorization,X-School-Year-Id', 'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS' };
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
    state.requests.push({ pathname, method: request.method(), url: url.toString(), body: request.postData() });
    let body, status = 200;
    if (pathname === '/api/auth/me') body = profile;
    else if (pathname === '/api/schools/me') body = { school_id: 1, name: 'Synthetic School', tracks_units: false, grading_periods: 3, grading_period_label: 'Term' };
    else if (pathname === '/api/school-years/current') body = { school_year_id: 1, year: '2026-2027', is_current: true };
    else if (pathname === '/api/my-classes') body = { unrestricted: false, grade: { 10: ['Mathematics', 'English'], 11: ['Science'] }, advisory: [] };
    else if (pathname === '/api/sections') body = sections;
    else if (pathname === '/api/subjects') body = subjects;
    else if (pathname === '/api/grade-summary') {
      if (state.summaryDelay) await new Promise((resolve) => setTimeout(resolve, state.summaryDelay));
      if (state.failSummary) { status = 500; body = { detail: 'Synthetic summary unavailable' }; }
      else body = summary(state, Number(url.searchParams.get('section_id')), Number(url.searchParams.get('subject_id')));
    }
    else if (pathname === '/api/enrollments') body = Number(url.searchParams.get('section_id')) === 11 ? [] : names.map((student_name, i) => ({ student_id: i + 1, enrollment_id: i + 10, student_name, section_id: 10, school_year_id: 1 }));
    else if (pathname === '/api/paces') body = ['Mathematics', 'English'].flatMap((subject) => [1, 2].flatMap((grading_period) => names.map((student_name, i) => ({
      student_id: i + 1, enrollment_id: i + 10, student_name, subject, grading_period,
      ww_scores: [7], pt_scores: [7], exam_scores: [7, 7, 7], current_pace: 0, expected_pace: 0, total_paces: 0,
    }))));
    else if (pathname === '/api/class-record/setup') body = { template: 'core', ww_highest: [10], pt_highest: [10], exam_highest: [10, 10, 10], ww_weight: 20, pt_weight: 50, ex_weight: 30, exam_subweights: [30, 30, 40] };
    else if (pathname === '/api/paces/bulk') {
      await new Promise((resolve) => setTimeout(resolve, 200));
      state.saved = true;
      body = { saved: JSON.parse(request.postData()).entries.length, skipped: [] };
    }
    else if (pathname === '/api/ai/status') body = { enabled: false, reachable: false, model_loaded: false };
    else if (pathname === '/api/ai/rescore') body = { scored: 4, fallback: 1, source: 'mixed' };
    else if (['/api/early-warnings', '/api/staff/pending', '/api/staff', '/api/students'].includes(pathname)) body = [];
    else { status = 404; body = { detail: `Unexpected synthetic request ${pathname}` }; }
    await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
  });
  const page = await context.newPage();
  page.on('pageerror', (error) => state.errors.push(error.message));
  return { context, page, state };
}

async function run() {
  const { build } = await import(pathToFileURL(path.join(frontend, 'node_modules/vite/dist/node/index.js')).href);
  const virtualEntry = path.join(frontend, 'grade-summary-browser-harness.jsx').replace(/\\/g, '/');
  const result = await build({ configFile: false, root: frontend, logLevel: 'error', esbuild: { jsx: 'automatic' },
    define: { 'import.meta.env.VITE_API_URL': JSON.stringify('http://api.invalid') },
    plugins: [{ name: 'grade-summary-harness', resolveId(id) { if (id === virtualEntry) return id; }, load(id) { if (id === virtualEntry) return entry; } }],
    build: { write: false, minify: false, rollupOptions: { input: virtualEntry, output: { inlineDynamicImports: true } } } });
  bundle = result.output.find((item) => item.type === 'chunk').code;
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const passed = [];
  try {
    {
      const { context, page, state } = await fixture(browser, origin);
      await page.goto(`${origin}/grade-summary?section_id=10&subject_id=100`);
      await page.getByRole('heading', { name: 'Grade summary', exact: true }).waitFor();
      await page.locator('.gs-table tbody tr').last().waitFor();
      assert.equal(await page.locator('.gs-table tbody tr').count(), 5);
      assert.equal(await page.locator('.risk-legend').count(), 1);
      assert.deepEqual(await page.locator('.risk-legend .badge').allTextContents(), ['Critical', 'At risk', 'Watch', 'On track']);
      assert.equal(await page.locator('.gs-student .badge-compact').count(), 4);
      assert.equal(await page.locator('.gs-forecast-details[open]').count(), 0);
      const fallback = page.locator('.gs-table tbody tr').filter({ hasText: names[0] });
      const fallbackDetails = fallback.locator('.gs-forecast-details');
      assert.match(await fallback.locator('.gs-forecast-summary').innerText(), /74\.0.*72\.0.*76\.0/s);
      assert.match(await fallback.locator('.gs-forecast-summary').innerText(), /Rule-based/);
      assert.equal(await fallback.getByText(/^Rule-based estimate/).isVisible(), false);
      assert.equal(await fallback.getByText('Based on First and Second Term results', { exact: true }).isVisible(), false);
      assert.equal(await fallback.getByText(/^Forecast saved /).isVisible(), false);
      await fallback.locator('.gs-forecast-summary').focus();
      await page.keyboard.press('Enter');
      assert.equal(await fallbackDetails.getAttribute('open'), '');
      assert.equal(await fallback.getByText(/^Rule-based estimate/).isVisible(), true);
      assert.equal(await fallback.getByText('Based on First and Second Term results', { exact: true }).isVisible(), true);
      assert.equal(await fallback.getByText(/^Forecast saved /).isVisible(), true);
      await fallback.locator('.gs-forecast-summary').click();
      assert.equal(await fallbackDetails.getAttribute('open'), null);
      const finished = page.locator('.gs-table tbody tr').filter({ hasText: 'Ready Learner' });
      assert.equal(await finished.locator('.gs-final strong').innerText(), '84');
      assert.equal(await finished.getByText('Benchmarking', { exact: true }).count(), 0);
      assert.equal(await finished.getByText(/Earlier forecast/).isVisible(), false);
      await finished.locator('.gs-forecast-summary').click();
      assert.equal(await finished.getByText(/Earlier forecast/).isVisible(), true);
      assert.equal(await finished.getByText(/^AI prediction/).isVisible(), true);
      await finished.locator('.gs-forecast-summary').click();
      for (const name of names.slice(1)) {
        const height = await page.locator('.gs-table tbody tr').filter({ hasText: name }).evaluate((el) => el.getBoundingClientRect().height);
        assert.ok(height < 85, `${name} has a compact closed row (${height}px)`);
      }
      assert.equal(await page.locator('.gs-table').getByText(/\d\/3 complete/).count(), 0);
      assert.match(await fallback.locator('.gs-student a').getAttribute('title'), /S001/);
      assert.equal(await page.locator('.gs-student').getByText(/^S00\d$/).count(), 0);
      const ungraded = page.locator('.gs-table tbody tr').filter({ hasText: 'New Learner' });
      assert.equal(await ungraded.locator('.badge-compact').count(), 0);
      assert.equal(await ungraded.locator('.gs-student').evaluate((el) => {
        const copy = el.cloneNode(true); copy.querySelectorAll('.sr-only, .visually-hidden').forEach((node) => node.remove());
        return copy.textContent.trim();
      }), 'New Learner');
      assert.equal(await ungraded.locator('.gs-forecast').innerText(), '—');
      assert.equal(await ungraded.locator('.gs-final').innerText(), '—');
      assert.equal(state.requests.filter((r) => r.pathname === '/api/ai/rescore').length, 0);
      await page.screenshot({ path: path.resolve(frontend, '../.tmp/grade-summary-light.png'), fullPage: true });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.waitForFunction(() => document.querySelector('.sidebar').getBoundingClientRect().right <= 1);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
      await page.screenshot({ path: path.resolve(frontend, '../.tmp/grade-summary-mobile.png'), fullPage: true });
      await page.setViewportSize({ width: 1500, height: 1000 });
      await page.getByRole('button', { name: 'Update predictions', exact: true }).click();
      await page.getByRole('button', { name: 'Update predictions', exact: true }).waitFor();
      const rescore = state.requests.find((r) => r.pathname === '/api/ai/rescore');
      assert.deepEqual(JSON.parse(rescore.body), { section_id: 10, subject: 'Mathematics', limit: 2000 });
      await page.getByLabel('Find a learner').fill('S001');
      assert.equal(await page.locator('.gs-table tbody tr').count(), 1);
      assert.match(await page.locator('.gs-student').innerText(), /Alexandra Gabrielle/);
      await page.getByLabel('Find a learner').fill('New Learner');
      assert.equal(await page.locator('.gs-table tbody tr').count(), 1);
      await page.getByLabel('Find a learner').fill('');
      state.summaryDelay = 300;
      await page.getByLabel('Section', { exact: true }).selectOption('11');
      await page.getByText('No learners enrolled', { exact: true }).waitFor();
      assert.equal(await page.getByLabel('Subject', { exact: true }).inputValue(), '200');
      assert.equal(await page.locator('.gs-table tbody tr').count(), 0);
      assert.deepEqual(state.errors, []);
      passed.push('Compact summary keeps grades, ranges and source visible; forecast details expand by keyboard and click; ID search, explicit predictions and class filters work.');
      await context.close();
    }
    {
      const { context, page, state } = await fixture(browser, origin);
      await page.goto(`${origin}/class-record?section_id=10&subject_id=101`);
      await page.locator('.cr-risk-name.risk-text-critical').waitFor();
      assert.equal(await page.getByLabel('Subject', { exact: true }).inputValue(), '101');
      assert.equal(await page.getByLabel('Term', { exact: true }).inputValue(), '2');
      assert.equal(await page.locator('.risk-legend').count(), 1);
      assert.deepEqual(await page.locator('.risk-legend .badge').allTextContents(), ['Critical', 'At risk', 'Watch', 'On track']);
      assert.equal(await page.locator('.cr-table tbody .badge-compact').count(), 4);
      assert.equal(await page.locator('.cr-risk-label').count(), 0);
      const newLearnerCell = page.locator('.cr-table tbody .cr-name').filter({ hasText: 'New Learner' });
      assert.equal(await newLearnerCell.locator('.badge-compact').count(), 0);
      assert.equal(await newLearnerCell.evaluate((el) => {
        const copy = el.cloneNode(true); copy.querySelectorAll('.sr-only, .visually-hidden').forEach((node) => node.remove());
        return /No standing yet|Risk not loaded/.test(copy.textContent);
      }), false);
      for (const theme of ['light', 'dark']) {
        await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), theme);
        for (const level of ['critical', 'high', 'moderate']) {
          const cell = page.locator(`.cr-name:has(.risk-text-${level})`);
          assert.equal(await cell.locator('.cr-risk-name').evaluate((el) => getComputedStyle(el).color),
            await cell.locator('.badge').evaluate((el) => getComputedStyle(el).color));
          assert.equal(await cell.locator('.badge-compact').innerText(), '');
          assert.ok(await cell.locator('.badge-compact').getAttribute('aria-label'));
          assert.ok(await cell.locator('.badge-compact').getAttribute('title'));
        }
      }
      await page.screenshot({ path: path.resolve(frontend, '../.tmp/class-record-risk-dark.png'), fullPage: true });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.waitForFunction(() => document.querySelector('.sidebar').getBoundingClientRect().right <= 1);
      const nameLayout = await page.locator('.cr-name:has(.risk-text-critical) .cr-name-line').evaluate((el) => {
        const number = el.querySelector('.cr-no').getBoundingClientRect(), name = el.querySelector('.cr-risk-name').getBoundingClientRect();
        return { numberRight: number.right, numberTop: number.top, nameLeft: name.left, nameTop: name.top,
          nameHeight: name.height, lineHeight: parseFloat(getComputedStyle(el.querySelector('.cr-risk-name')).lineHeight) };
      });
      assert.ok(nameLayout.nameLeft >= nameLayout.numberRight, 'wrapped name stays to the right of the row number');
      assert.ok(Math.abs(nameLayout.nameTop - nameLayout.numberTop) < 4, `row number aligns with the first name line (${JSON.stringify(nameLayout)})`);
      assert.ok(nameLayout.nameHeight > nameLayout.lineHeight * 1.5, 'long learner name wraps on mobile');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
      await page.screenshot({ path: path.resolve(frontend, '../.tmp/class-record-risk-mobile.png'), fullPage: true });
      await page.setViewportSize({ width: 1500, height: 1000 });
      await page.locator('.cr-table tbody .cr-input').first().fill('9');
      await page.getByRole('button', { name: 'Save now', exact: true }).click();
      await page.locator('.cr-risk-name.risk-text-critical').waitFor({ state: 'detached' });
      assert.equal(await page.locator('.cr-risk-name.risk-text-low').count(), 2);
      await page.locator('.cr-table tbody .cr-input').first().fill('8');
      await page.getByRole('button', { name: 'View grade summary' }).click();
      await page.waitForURL('**/grade-summary?section_id=10&subject_id=101');
      await page.locator('.gs-table tbody tr').last().waitFor();
      assert.equal(state.saved, true);
      const writes = state.requests.filter((r) => r.pathname === '/api/paces/bulk');
      assert.equal(writes.length, 2);
      assert.equal(JSON.parse(writes[1].body).entries[0].ww_scores[0], 8);
      assert.equal(JSON.parse(writes[1].body).entries[0].subject, 'English');
      assert.equal(await page.getByLabel('Subject', { exact: true }).inputValue(), '101');
      assert.deepEqual(state.errors, []);
      passed.push('Class Record uses one legend and compact accessible risk indicators, matches badge colors in both themes, wraps long names, and saves before opening the summary.');
      await context.close();
    }
    {
      const { context, page, state } = await fixture(browser, origin);
      state.failSummary = true;
      await page.goto(`${origin}/grade-summary`);
      await page.getByText('Synthetic summary unavailable', { exact: true }).waitFor();
      assert.equal(await page.locator('.gs-table tbody tr').count(), 0);
      state.failSummary = false;
      await page.getByRole('button', { name: 'Refresh', exact: true }).click();
      await page.locator('.gs-table tbody tr').last().waitFor();
      assert.deepEqual(state.errors, []);
      passed.push('Summary read failures clear displayed data and recover through Refresh.');
      await context.close();
    }
    process.stdout.write(JSON.stringify({ passed }, null, 2) + '\n');
  } finally { await browser.close(); await new Promise((resolve) => server.close(resolve)); }
}
run().catch((error) => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; server.close(); });
