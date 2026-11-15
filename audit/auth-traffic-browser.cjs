/* Run: node audit/auth-traffic-browser.cjs
   Uses existing frontend esbuild and audit/runtime Playwright. All API calls
   use synthetic responses; no live account, CAPTCHA, or email is involved. */
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const esbuild = require('../frontend/node_modules/esbuild');
const { chromium } = require('./runtime/node_modules/playwright');

const frontend = path.resolve(__dirname, '../frontend');
const built = esbuild.buildSync({
  stdin: { contents: `
    import React from 'react';
    import { createRoot } from 'react-dom/client';
    import { MemoryRouter, Routes, Route } from 'react-router-dom';
    import { ThemeProvider } from './src/context/ThemeContext';
    import { AuthProvider } from './src/context/AuthContext';
    import { ToastProvider } from './src/context/ToastContext';
    import ApprovalsPage from './src/pages/app/ApprovalsPage';
    import LoginPage from './src/pages/auth/LoginPage';
    import RegisterPage from './src/pages/auth/RegisterPage';
    import { api } from './src/api/client';
    window.testApi = api;
    createRoot(document.getElementById('root')).render(
      <React.StrictMode><ThemeProvider><AuthProvider><ToastProvider>
        <MemoryRouter initialEntries={[location.pathname]}><Routes>
          <Route path="/approvals" element={<ApprovalsPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
        </Routes></MemoryRouter>
      </ToastProvider></AuthProvider></ThemeProvider></React.StrictMode>
    );
  `, resolveDir: frontend, loader: 'jsx' },
  bundle: true, write: false, platform: 'browser', format: 'iife', jsx: 'automatic',
  define: { 'import.meta.env': '{}', 'process.env.NODE_ENV': '"development"' },
});
const css = ['tokens.css', 'base.css', 'layout.css', 'pages.css', 'auth.css']
  .map(name => fs.readFileSync(path.join(frontend, 'src/styles', name))).join('\n');
const server = http.createServer((req, res) => {
  if (req.url === '/bundle.js') { res.setHeader('Content-Type', 'text/javascript'); res.end(built.outputFiles[0].text); }
  else if (req.url === '/styles.css') { res.setHeader('Content-Type', 'text/css'); res.end(css); }
  else { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/styles.css"></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>'); }
});
const teacher = id => ({ id: String(id), first_name: 'Teacher', last_name: String(id),
  role: 'teacher', email: `teacher${id}@example.test`, contact_number: '09170000000', created_at: '2026-11-15T01:00:00Z' });
const json = (route, body, status = 200, headers = {}) => route.fulfill({ status, json: body, headers: { 'access-control-allow-origin': '*', ...headers } });

async function run() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.setDefaultTimeout(5000);
    await page.clock.install();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let rows = [teacher(1)];
    let pendingFails = false;
    let auditFails = true;
    let pendingCalls = 0;
    let auditCalls = 0;
    let releaseAudit;
    let holdAudit = false;
    let loginPosts = 0;
    let registrationPosts = 0;
    await page.route('**/api/**', async route => {
      const pathname = new URL(route.request().url()).pathname;
      if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers: { 'access-control-allow-origin': '*', 'access-control-allow-methods': 'GET,POST,PATCH,OPTIONS', 'access-control-allow-headers': '*' } });
      if (pathname === '/api/staff/pending') {
        pendingCalls++;
        return json(route, pendingFails ? { detail: 'Queue temporarily unavailable' } : rows, pendingFails ? 503 : 200);
      }
      if (pathname === '/api/staff/audit-logs') {
        auditCalls++;
        if (holdAudit) await new Promise(resolve => { releaseAudit = resolve; });
        return json(route, auditFails ? { detail: 'Audit temporarily unavailable' } : [], auditFails ? 503 : 200);
      }
      if (pathname === '/api/auth/captcha-config') return json(route, { enabled: false });
      if (pathname === '/api/schools/directory') return json(route, [{ school_id: 1, name: 'Synthetic School', city: 'Test city' }]);
      if (pathname === '/api/auth/login') {
        loginPosts++;
        if (loginPosts === 3) return json(route, { detail: 'Account temporarily locked. Try again in 1 minute(s).' }, 401, { 'retry-after': '2', 'access-control-expose-headers': 'Retry-After' });
        return json(route, { detail: loginPosts === 1 ? 'Too many sign-ins' : 'Invalid email or password.' }, loginPosts === 1 ? 429 : 401, loginPosts === 1 ? { 'retry-after': '2', 'access-control-expose-headers': 'Retry-After' } : {});
      }
      if (pathname === '/api/auth/register') {
        registrationPosts++;
        return registrationPosts === 1
          ? json(route, { detail: 'Too many registrations' }, 429, { 'retry-after': '2', 'access-control-expose-headers': 'Retry-After' })
          : json(route, { message: 'Saved', is_approved: false }, 201);
      }
      if (pathname === '/api/date-cooldown') return json(route, { detail: 'Wait' }, 429, { 'retry-after': new Date(await page.evaluate(() => Date.now()) + 3000).toUTCString(), 'access-control-expose-headers': 'Retry-After' });
      throw new Error(`Unexpected mocked API call: ${pathname}`);
    });
    await page.goto(`${origin}/approvals`);
    await page.getByText('teacher1@example.test', { exact: true }).waitFor();
    await page.getByRole('alert').filter({ hasText: 'Audit trail: Audit temporarily unavailable' }).waitFor();
    assert.equal(await page.getByText('Nothing waiting for review', { exact: true }).count(), 0, 'failed audit must not hide the successful queue');
    assert.equal(pendingCalls, 1, 'StrictMode shares pending request');
    assert.equal(auditCalls, 1, 'StrictMode shares audit request');

    pendingFails = true;
    await page.getByRole('button', { name: 'Refresh requests', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'Queue temporarily unavailable' }).waitFor();
    assert.equal(await page.getByText('teacher1@example.test', { exact: true }).count(), 1, 'temporary refresh failures retain existing requests');
    pendingFails = false;
    auditFails = false;
    holdAudit = true;
    rows = [teacher(1), teacher(2)];
    await page.getByRole('button', { name: 'Refresh requests', exact: true }).click();
    await page.getByText('teacher2@example.test', { exact: true }).waitFor();
    assert.equal(await page.getByRole('alert').filter({ hasText: 'Queue temporarily unavailable' }).count(), 0, 'successful queue refresh clears its own error before audit finishes');
    await page.waitForFunction(() => !document.querySelector('.card-header button').disabled);
    while (!releaseAudit) await new Promise(resolve => setTimeout(resolve, 5));
    releaseAudit();
    holdAudit = false;
    await page.waitForFunction(() => !document.querySelector('[role="alert"]'));
    rows = [teacher(1), teacher(2), teacher(3)];
    const auditBeforePoll = auditCalls;
    await page.clock.fastForward(30000);
    await page.getByText('teacher3@example.test', { exact: true }).waitFor();
    assert.equal(auditCalls, auditBeforePoll, 'background refresh only fetches pending requests');
    rows.push(teacher(4));
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.getByText('teacher4@example.test', { exact: true }).waitFor();
    await page.setViewportSize({ width: 320, height: 812 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'approval refresh fits a 320px screen');
    const dateRetry = await page.evaluate(async () => {
      try { await window.testApi.get('/api/date-cooldown', undefined, { auth: false }); }
      catch (error) { return error.retryAfter; }
    });
    assert(dateRetry > 0 && dateRetry <= 3, 'HTTP-date Retry-After is exposed as seconds');

    await page.goto(`${origin}/login`);
    await page.getByLabel('School email').fill('teacher@example.test');
    await page.getByLabel('Password', { exact: true }).fill('Example123!');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.getByRole('status').filter({ hasText: 'Try again in 2 second(s).' }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Sign in', exact: true }).isDisabled(), true);
    await page.evaluate(() => document.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
    assert.equal(loginPosts, 1, 'cooldown blocks repeat submissions');
    await page.clock.fastForward(2100);
    await page.waitForFunction(() => !document.querySelector('button[type="submit"]').disabled);
    assert.equal(loginPosts, 1, 'cooldown expiry does not automatically retry');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'Invalid email or password.' }).waitFor();
    assert.equal(loginPosts, 2);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'Your account is temporarily locked.' }).waitFor();
    await page.getByRole('status').filter({ hasText: 'Try again in 2 second(s).' }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Sign in', exact: true }).isDisabled(), true, '401 account lockout also uses its exact Retry-After');
    await page.clock.fastForward(2100);
    await page.waitForFunction(() => !document.querySelector('button[type="submit"]').disabled);
    assert.equal(loginPosts, 3, 'account lockout expiry does not auto-retry');

    await page.goto(`${origin}/register`);
    await page.getByLabel('Your school', { exact: false }).selectOption('1');
    await page.getByLabel('First name', { exact: false }).fill('Example');
    await page.getByLabel('Last name', { exact: false }).fill('Teacher');
    await page.locator('#reg_email').fill('newteacher@example.test');
    await page.getByLabel('Contact number', { exact: false }).fill('09170000000');
    await page.locator('#reg_password').fill('Example123!');
    await page.getByLabel('Confirm password', { exact: false }).fill('Example123!');
    await page.getByRole('button', { name: 'Submit request', exact: true }).click();
    await page.getByRole('status').filter({ hasText: 'Try again in 2 second(s).' }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Submit request', exact: true }).isDisabled(), true);
    await page.clock.fastForward(2100);
    await page.waitForFunction(() => !document.querySelector('button[type="submit"]').disabled);
    assert.equal(registrationPosts, 1, 'registration does not auto-retry after waiting');
    await page.getByRole('button', { name: 'Submit request', exact: true }).click();
    await page.getByRole('heading', { name: 'Registration received', exact: true }).waitFor();
    assert.equal(registrationPosts, 2);
    assert.deepEqual(errors, []);
    console.log('PASS: successful pending queue survives failed/stalled audit, manual refresh clears errors, 30s/focus refresh, numeric/date Retry-After, login/registration cooldown and manual recovery without automatic retry.');
    await page.close();
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
run().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
