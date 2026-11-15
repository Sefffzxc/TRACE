/* Local monitoring regression with synthetic classes, predictions and flags.
   Run: node audit/monitoring-sections-browser.cjs. No live API or AI is used. */
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
    import AiInsightsPage from './src/pages/app/AiInsightsPage';
    import EarlyWarningPage from './src/pages/app/EarlyWarningPage';
    createRoot(document.getElementById('root')).render(
      <React.StrictMode><ThemeProvider><AuthProvider><ToastProvider>
        <MemoryRouter initialEntries={[location.pathname]}><Routes>
          <Route path="/ai-risk" element={<AiInsightsPage />} />
          <Route path="/early-warning" element={<EarlyWarningPage />} />
        </Routes></MemoryRouter>
      </ToastProvider></AuthProvider></ThemeProvider></React.StrictMode>
    );
  `, resolveDir: frontend, loader: 'jsx' },
  bundle: true, write: false, platform: 'browser', format: 'iife', jsx: 'automatic',
  define: { 'import.meta.env': '{}', 'process.env.NODE_ENV': '"development"' },
});
const css = ['tokens.css', 'base.css', 'layout.css', 'pages.css'].map((name) => fs.readFileSync(path.join(frontend, 'src/styles', name))).join('\n');
const server = http.createServer((request, response) => {
  if (request.url === '/bundle.js') { response.setHeader('Content-Type', 'text/javascript'); response.end(bundle.outputFiles[0].text); }
  else if (request.url === '/styles.css') { response.setHeader('Content-Type', 'text/css'); response.end(css); }
  else { response.setHeader('Content-Type', 'text/html'); response.end('<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/styles.css"></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>'); }
});
const sections = [
  { section_id: 10, name: 'Faith', grade_level_name: 'Grade 7' },
  { section_id: 11, name: 'Faith', grade_level_name: 'Grade 8' },
  { section_id: 12, name: 'Hope', grade_level_name: 'Grade 7' },
];
const forecast = (id, sectionId, name) => ({ prediction_id: id, student_id: id, enrollment_id: id,
  student_name: name, section_id: sectionId, section_name: sections.find((row) => row.section_id === sectionId).name,
  subject: 'English', risk_level: 'HIGH', risk_score: 0.75, used_threshold: 0.5,
  source: 'model', model_version: 'synthetic-year-model', predicted_final: 70, range_low: 68, range_high: 73,
  used_baseline_defaults: false, imputed_fields: [], features: { q1_term_grade: 74 } });
const forecasts = [forecast(501, 10, 'Assigned Learner'), forecast(502, 11, 'Advisory Learner')];
const warning = (row) => ({ ...row, warning_id: row.prediction_id, risk_level: 'high', risk_score: 75,
  standing: 70, basis: 'grades', teacher: 'Test Teacher', is_resolved: false, pace_percent: 0, paces_behind: 0 });

async function run() {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    for (const width of [375, 1440]) {
      const context = await browser.newContext({ viewport: { width, height: 1000 } });
      await context.addInitScript(() => localStorage.setItem('trace.user', JSON.stringify({ id: 'synthetic-user', role: 'teacher', first_name: 'Test', last_name: 'Teacher' })));
      let access = { unrestricted: false, grade: { 10: ['English'] }, advisory: [11] };
      let failAccess = false;
      const rescores = [], failures = [];
      await context.route('**/api/**', async (route) => {
        const request = route.request(), url = new URL(request.url());
        const reply = (body, status = 200) => route.fulfill({ status, json: body, headers: { 'access-control-allow-origin': '*' } });
        if (request.method() === 'OPTIONS') return reply({});
        if (url.pathname === '/api/schools/me') return reply({ school_id: 1, tracks_units: false, passing_mark: 75 });
        if (url.pathname === '/api/sections') return reply(sections);
        if (url.pathname === '/api/my-classes') return failAccess ? reply({ detail: 'Assigned classes could not load.' }, 503) : reply(access);
        const rows = access.unrestricted || Object.keys(access.grade).length ? forecasts : [];
        if (url.pathname === '/api/ai/status') return reply({ enabled: true, reachable: false, model_loaded: false });
        if (url.pathname === '/api/ai/predictions') { assert.equal(url.searchParams.get('mine'), 'true'); return reply(rows); }
        if (url.pathname === '/api/ai/summary') return reply({ high: rows.length, total_scored: rows.length, students_high: rows.length, from_model: rows.length });
        if (url.pathname === '/api/ai/rescore') { rescores.push(request.postDataJSON()); return reply({ scored: 1, source: 'model', fallback: 0 }); }
        if (url.pathname === '/api/early-warnings') { assert.equal(url.searchParams.get('mine'), 'true'); return reply(rows.map(warning)); }
        if (url.pathname === '/api/early-warnings/summary') return reply({ by_subject: [] });
        throw new Error('Unexpected mock request: ' + url.pathname);
      });
      const page = await context.newPage();
      page.on('pageerror', (error) => failures.push(error.message));
      for (const route of ['/ai-risk', '/early-warning']) {
        await page.goto(origin + route);
        const picker = page.getByLabel('Filter by section');
        await picker.locator('option[value="11"]').waitFor({ state: 'attached' });
        assert.deepEqual(await picker.locator('option').evaluateAll((options) => options.map((option) => option.value)), ['', '10', '11']);
        assert((await picker.innerText()).includes('My assigned sections'));
        assert(!(await picker.innerText()).includes('Hope'));
        await picker.selectOption('10');
        const table = page.locator('table').first();
        await table.getByRole('button', { name: 'Assigned Learner', exact: true }).waitFor();
        assert.equal(await table.getByRole('button', { name: 'Advisory Learner', exact: true }).count(), 0, 'Same-named sections filter by ID');
        if (route === '/ai-risk') {
          await page.getByRole('button', { name: 'Re-score records', exact: true }).click();
          await page.waitForFunction(() => ![...document.querySelectorAll('button')].some((button) => button.textContent.includes('Re-scoring')));
          assert.equal(rescores.at(-1).section_id, 10, 'Re-score submits numeric section ID');
        }
        await picker.selectOption('11');
        await table.getByRole('button', { name: 'Advisory Learner', exact: true }).waitFor();
        assert.equal(await table.getByRole('button', { name: 'Assigned Learner', exact: true }).count(), 0);
      }
      access = { unrestricted: true, grade: {}, advisory: [] };
      for (const route of ['/ai-risk', '/early-warning']) {
        await page.goto(origin + route);
        await page.getByLabel('Filter by section').locator('option[value="12"]').waitFor({ state: 'attached' });
        assert((await page.getByLabel('Filter by section').innerText()).includes('All sections'));
      }
      access = { unrestricted: false, grade: {}, advisory: [] };
      for (const route of ['/ai-risk', '/early-warning']) {
        await page.goto(origin + route);
        await page.getByText('No assigned classes', { exact: true }).waitFor();
        assert.equal(await page.getByLabel('Filter by section').locator('option').count(), 1);
      }
      failAccess = true;
      for (const route of ['/ai-risk', '/early-warning']) {
        await page.goto(origin + route);
        await page.getByRole('alert').filter({ hasText: 'Assigned classes could not load.' }).waitFor();
        assert.equal(await page.getByLabel('Filter by section').locator('option').count(), 1, 'Access failure never falls back to all sections');
      }
      assert.deepEqual(failures, []);
      await context.close();
      console.log(`PASS: assigned/advisory pickers, principal access, no assignments, fail-closed lookup, same-named section filters and numeric re-score @${width}`);
    }
  } finally { await browser.close(); }
}
run().catch((error) => { console.error(error); process.exitCode = 1; }).finally(() => server.close());
