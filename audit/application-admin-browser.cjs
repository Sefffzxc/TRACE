/* Run: node audit/application-admin-browser.cjs
   All requests use invented fixtures. No account, email or live API is used. */
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
    import { ToastProvider } from './src/context/ToastContext';
    import ApprovalsPage from './src/pages/app/ApprovalsPage';
    import SchoolsAdminPage from './src/pages/app/SchoolsAdminPage';
    createRoot(document.getElementById('root')).render(
      <React.StrictMode><ThemeProvider><ToastProvider>
        <MemoryRouter initialEntries={[location.pathname]}><Routes>
          <Route path="/approvals" element={<ApprovalsPage />} />
          <Route path="/schools" element={<SchoolsAdminPage />} />
        </Routes></MemoryRouter>
      </ToastProvider></ThemeProvider></React.StrictMode>
    );
  `, resolveDir: frontend, loader: 'jsx' },
  bundle: true, write: false, platform: 'browser', format: 'iife', jsx: 'automatic',
  define: { 'import.meta.env': '{}', 'process.env.NODE_ENV': '"development"' },
});
const css = ['tokens.css', 'base.css', 'layout.css', 'pages.css']
  .map(name => fs.readFileSync(path.join(frontend, 'src/styles', name))).join('\n');
const server = http.createServer((req, res) => {
  if (req.url === '/bundle.js') { res.setHeader('Content-Type', 'text/javascript'); res.end(built.outputFiles[0].text); }
  else if (req.url === '/styles.css') { res.setHeader('Content-Type', 'text/css'); res.end(css); }
  else { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/styles.css"></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>'); }
});
const teacher = (id, status) => ({ id, status, first_name: 'Applicant', last_name: String(id), role: 'teacher',
  email: `applicant${id}@example.test`, contact_number: '09170000000', created_at: '2026-11-15T01:00:00Z',
  rejection_reason: ['rejected', 'blocked'].includes(status) ? 'Please correct your contact details.' : null });
const school = (id, status) => ({ school_id: id, name: `School ${id}`, status, principal_name: 'School Head',
  principal_email: `principal${id}@example.test`, city: 'Test city', school_type: 'public', staff_count: 1,
  created_at: '2026-11-15T01:00:00Z', rejection_reason: ['rejected', 'blocked'].includes(status) ? 'Please correct the school details.' : null });
const decisionHistory = id => [
  { id: `${id}-2`, action: 'resubmitted', from_status: 'rejected', to_status: 'pending', reason: null,
    detail: 'Corrected contact number.', actor_name: 'Applicant', created_at: '2026-11-16T01:00:00Z' },
  { id: `${id}-1`, action: 'rejected', from_status: 'pending', to_status: 'rejected', reason: 'Earlier decision is retained.',
    actor_name: 'School Administrator', created_at: '2026-11-15T01:00:00Z' },
];
const json = (route, body, status = 200) => route.fulfill({ status, json: body, headers: { 'access-control-allow-origin': '*' } });

async function run() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.setDefaultTimeout(7000);
    await page.clock.install();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let staffRows = [teacher('staff-pending', 'pending'), teacher('staff-rejected', 'rejected'), teacher('staff-approve', 'rejected')];
    let schoolRows = [school('school-pending', 'pending'), school('school-rejected', 'rejected'), school('school-approve', 'rejected'), school('school-suspended', 'deactivated')];
    let auditFails = true;
    let historyFails = false;
    let queueFails = false;
    let holdRejected;
    let holdRejectedRequest = false;
    let queueCalls = 0;
    const mutations = [];
    await page.route('**/api/**', async route => {
      const req = route.request();
      const url = new URL(req.url());
      const pathname = url.pathname;
      if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: { 'access-control-allow-origin': '*', 'access-control-allow-methods': 'GET,PATCH,OPTIONS', 'access-control-allow-headers': '*' } });
      if (pathname === '/api/staff/audit-logs') return json(route, auditFails ? { detail: 'Audit unavailable' } : [], auditFails ? 503 : 200);
      if (/\/application-history$/.test(pathname)) return json(route, historyFails ? { detail: 'History unavailable' } : decisionHistory(pathname), historyFails ? 503 : 200);
      if (req.method() === 'PATCH') {
        const payload = req.postDataJSON();
        mutations.push({ pathname, ...payload });
        const rows = pathname.startsWith('/api/staff/') ? staffRows : schoolRows;
        const row = rows.find(row => (row.id || row.school_id) === pathname.split('/').pop());
        assert(row, 'decision keeps the existing account/school identity');
        if (['reject', 'block'].includes(payload.action)) assert(payload.reason?.trim(), 'reason required');
        row.status = ({ reject: 'rejected', approve: 'approved', reopen: 'pending', block: 'blocked', reactivate: 'approved', deactivate: 'deactivated' })[payload.action];
        if (payload.reason) row.rejection_reason = payload.reason;
        return json(route, { message: 'Application updated.' });
      }
      if (pathname === '/api/staff/pending' || pathname === '/api/staff/applications') {
        queueCalls++;
        const status = pathname.endsWith('/pending') ? 'pending' : url.searchParams.get('status');
        const snapshot = structuredClone(staffRows.filter(row => row.status === status));
        if (status === 'rejected' && holdRejectedRequest) await new Promise(resolve => { holdRejected = resolve; });
        return json(route, queueFails ? { detail: 'Queue unavailable' } : snapshot, queueFails ? 503 : 200);
      }
      if (pathname === '/api/schools') {
        const status = url.searchParams.get('status');
        return json(route, schoolRows.filter(row => !status || row.status === status));
      }
      throw new Error(`Unexpected mocked API call: ${req.method()} ${pathname}`);
    });

    await page.goto(`${origin}/approvals`);
    await page.getByText('applicantstaff-pending@example.test', { exact: true }).waitFor();
    await page.getByRole('alert').filter({ hasText: 'Audit unavailable' }).waitFor();
    assert.equal(queueCalls, 1, 'StrictMode shares queue request');
    await page.getByRole('button', { name: 'Reject', exact: true }).click();
    assert.equal(await page.getByRole('button', { name: 'Reject request', exact: true }).isDisabled(), true);
    await page.getByLabel('Reason (required)').fill('   ');
    assert.equal(await page.getByRole('button', { name: 'Reject request', exact: true }).isDisabled(), true, 'whitespace is not a reason');
    await page.getByLabel('Reason (required)').fill('  Correct your contact number.  ');
    await page.getByRole('button', { name: 'Reject request', exact: true }).click();
    await page.getByText('Nothing waiting for review', { exact: true }).waitFor();
    assert.equal(mutations.at(-1).reason, 'Correct your contact number.');
    await page.getByRole('button', { name: 'Rejected', exact: true }).click();
    const rejected = page.locator('.approval').filter({ hasText: 'applicantstaff-pending@example.test' });
    await rejected.getByRole('button', { name: 'History', exact: true }).click();
    await page.getByText('Earlier decision is retained.', { exact: true }).waitFor();
    await page.getByText('Corrected contact number.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    historyFails = true;
    await rejected.getByRole('button', { name: 'History', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'History unavailable' }).waitFor();
    historyFails = false;
    await page.getByRole('button', { name: 'Retry history', exact: true }).click();
    await page.getByText('Earlier decision is retained.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    await rejected.getByRole('button', { name: 'Reopen', exact: true }).click();
    await rejected.waitFor({ state: 'detached' });
    await page.getByRole('button', { name: 'Pending', exact: true }).click();
    await page.getByText('applicantstaff-pending@example.test', { exact: true }).waitFor();
    queueFails = true;
    await page.getByRole('button', { name: 'Refresh requests', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'Queue unavailable' }).waitFor();
    assert.equal(await page.getByText('applicantstaff-pending@example.test', { exact: true }).count(), 1, 'refresh failure retains applications');
    queueFails = false;
    staffRows.push(teacher('staff-polled', 'pending'));
    await page.clock.fastForward(30000);
    await page.getByText('applicantstaff-polled@example.test', { exact: true }).waitFor();
    staffRows.push(teacher('staff-focused', 'pending'));
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await page.getByText('applicantstaff-focused@example.test', { exact: true }).waitFor();
    holdRejectedRequest = true;
    await page.getByRole('button', { name: 'Rejected', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.skeleton'));
    await page.getByRole('button', { name: 'Pending', exact: true }).click();
    await page.getByText('applicantstaff-pending@example.test', { exact: true }).waitFor();
    while (!holdRejected) await new Promise(resolve => setTimeout(resolve, 5));
    holdRejected();
    holdRejectedRequest = false;
    await page.waitForTimeout(30);
    assert.equal(await page.getByText('applicantstaff-rejected@example.test', { exact: true }).count(), 0, 'late rejected response cannot overwrite pending tab');
    await page.getByRole('button', { name: 'Rejected', exact: true }).click();
    const approving = page.locator('.approval').filter({ hasText: 'applicantstaff-approve@example.test' });
    await approving.getByRole('button', { name: 'Approve', exact: true }).click();
    await approving.waitFor({ state: 'detached' });
    const abusive = page.locator('.approval').filter({ hasText: 'applicantstaff-rejected@example.test' });
    await abusive.getByRole('button', { name: 'Block', exact: true }).click();
    assert.equal(await page.getByRole('button', { name: 'Block application', exact: true }).isDisabled(), true);
    await page.getByLabel('Reason (required)').fill('Repeated abusive submissions.');
    await page.getByRole('button', { name: 'Block application', exact: true }).click();
    await abusive.waitFor({ state: 'detached' });
    await page.getByRole('button', { name: 'Blocked', exact: true }).click();
    await page.getByText('applicantstaff-rejected@example.test', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Approve', exact: true }).count(), 0, 'blocked requires reopening before ordinary approval');
    assert.equal(await page.getByRole('button', { name: /Delete|Remove/i }).count(), 0, 'rejection is not deletion');
    await page.setViewportSize({ width: 320, height: 812 });
    for (const theme of ['dark', 'light']) {
      await page.evaluate(theme => document.documentElement.setAttribute('data-theme', theme), theme);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `staff queue fits 320px ${theme}`);
      await page.getByRole('button', { name: 'History', exact: true }).click();
      await page.getByText('Earlier decision is retained.', { exact: true }).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'history modal fits mobile');
      await page.getByRole('button', { name: 'Close', exact: true }).click();
    }
    await page.getByRole('button', { name: 'Reopen', exact: true }).click();
    await page.getByText('No blocked requests', { exact: true }).waitFor();

    await page.goto(`${origin}/schools`);
    await page.getByText('School school-pending', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Reject', exact: true }).click();
    assert.equal(await page.getByRole('button', { name: 'Reject registration', exact: true }).isDisabled(), true);
    await page.getByLabel('Reason (required)').fill('School permit needs correction.');
    await page.getByRole('button', { name: 'Reject registration', exact: true }).click();
    await page.getByText('Nothing waiting for review', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Rejected', exact: true }).click();
    const rejectedSchool = page.locator('.approval').filter({ hasText: 'School school-pending' });
    await rejectedSchool.getByRole('button', { name: 'History', exact: true }).click();
    await page.getByText('Earlier decision is retained.', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'school history fits mobile');
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    await rejectedSchool.getByRole('button', { name: 'Reopen', exact: true }).click();
    await rejectedSchool.waitFor({ state: 'detached' });
    const directSchool = page.locator('.approval').filter({ hasText: 'School school-approve' });
    await directSchool.getByRole('button', { name: 'Approve', exact: true }).click();
    await directSchool.waitFor({ state: 'detached' });
    const blockedSchool = page.locator('.approval').filter({ hasText: 'School school-rejected' });
    await blockedSchool.getByRole('button', { name: 'Block', exact: true }).click();
    assert.equal(await page.getByRole('button', { name: 'Block application', exact: true }).isDisabled(), true);
    await page.getByLabel('Reason (required)').fill('Repeated invalid applications.');
    await page.getByRole('button', { name: 'Block application', exact: true }).click();
    await blockedSchool.waitFor({ state: 'detached' });
    await page.getByRole('button', { name: 'Blocked', exact: true }).click();
    await page.getByText('School school-rejected', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Approve', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: 'Restore', exact: true }).count(), 0, 'blocked school cannot bypass review through restore');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'school queue fits 320px');
    await page.getByRole('button', { name: 'Reopen', exact: true }).click();
    await page.getByText('No schools to show', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Pending', exact: true }).click();
    await page.getByText('School school-rejected', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Suspended', exact: true }).click();
    await page.getByRole('button', { name: 'Restore', exact: true }).click();
    await page.getByText('No schools to show', { exact: true }).waitFor();
    assert.equal(mutations.at(-1).action, 'reactivate', 'existing suspension recovery remains separate');
    auditFails = false;
    assert.deepEqual(errors, []);
    console.log('PASS: staff and school rejection reasons, retained history/retry, rejected direct approval/reopen/block, blocked reopening only, stale-tab response protection, audit independence, refresh/poll/focus, 320px dark/light, and suspension recovery.');
    await page.close();
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
run().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
