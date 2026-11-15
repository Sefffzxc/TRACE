/* Run: node audit/resubmission-browser.cjs
   Browser regression for restricted rejected-application corrections. Uses the
   existing frontend esbuild and audit/runtime Playwright with synthetic API
   responses and CAPTCHA only; no external accounts, emails or database writes. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const esbuild = require('../frontend/node_modules/esbuild');
const { chromium } = require('./runtime/node_modules/playwright');

const frontend = path.resolve(__dirname, '../frontend');
const built = esbuild.buildSync({
  stdin: { contents: `
    import React from 'react';
    import { createRoot } from 'react-dom/client';
    import { BrowserRouter, Routes, Route } from 'react-router-dom';
    import { ThemeProvider } from './src/context/ThemeContext';
    import { AuthProvider } from './src/context/AuthContext';
    import ResubmitPage from './src/pages/auth/ResubmitPage';
    import LoginPage from './src/pages/auth/LoginPage';
    import RegisterPage from './src/pages/auth/RegisterPage';
    createRoot(document.getElementById('root')).render(<React.StrictMode><ThemeProvider><AuthProvider><BrowserRouter><Routes>
      <Route path="/resubmit" element={<ResubmitPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
    </Routes></BrowserRouter></AuthProvider></ThemeProvider></React.StrictMode>);
  `, resolveDir: frontend, loader: 'jsx' },
  bundle: true, write: false, platform: 'browser', format: 'iife', jsx: 'automatic',
  define: { 'import.meta.env': '{"VITE_API_URL":"http://api.invalid"}', 'process.env.NODE_ENV': '"development"' },
});
const css = ['tokens.css', 'base.css', 'auth.css', 'pages.css'].map((name) => fs.readFileSync(path.join(frontend, 'src/styles', name))).join('\n');
const server = http.createServer((req, res) => {
  if (req.url === '/bundle.js') { res.setHeader('Content-Type', 'text/javascript'); res.end(built.outputFiles[0].text); }
  else if (req.url === '/styles.css') { res.setHeader('Content-Type', 'text/css'); res.end(css); }
  else if (req.url.startsWith('/places/')) { res.setHeader('Content-Type', 'application/json'); res.end('[]'); }
  else { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/styles.css"></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>'); }
});
const stub = `window.widgets = []; window.turnstile = {
  render(host, options) { const id = String(window.widgets.length+1); const button = document.createElement('button');
    button.type='button'; button.textContent='Solve synthetic verification'; host.appendChild(button);
    button.onclick=()=>options.callback('synthetic:'+options.action+':'+id);
    window.widgets.push({id,options,host}); return id; },
  remove(id) { window.widgets.find(w=>w.id===id)?.host.replaceChildren(); }
};`;
const schoolProfile = { name: 'Rejected School', short_name: 'RS', school_type: 'private', address: 'Purok 1', city: 'City', province: 'Province', region: 'Region', contact_number: null, contact_email: null, tracks_units: false, unit_label_singular: 'Module', unit_label_plural: 'Modules', default_units_per_subject: 12, passing_mark: 75, grading_periods: 4, grading_period_label: 'Quarter' };
const teacher = { id: 'teacher-id', email: 'teacher@example.com', role: 'teacher', first_name: 'Test', middle_name: null, last_name: 'Teacher', contact_number: '09170000000', employee_no: 'E1', department: 'Math', rejection_reason: 'Please correct your contact number.', school_id: 1, school_name: 'Original School', school: null, can_change_school: true, history: [{ id: 'history-id', action: 'reject', from_status: 'pending', to_status: 'rejected', reason: 'Please correct your contact number.', detail: '<script>not executable</script>', created_at: '2026-11-15T01:00:00Z' }] };

async function run() {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const checks = [];
  try {
    for (const width of [375, 1440]) {
      const context = await browser.newContext({ viewport: { width, height: 900 } });
      const requests = [];
      const errors = [];
      let app = { ...teacher }, configFailed = false, rejectToken = false, throttle = false;
      let expiresIn = 900;
      let loginReply = { status: 401, detail: 'Invalid email or password.' };
      let loginGate = null;
      await context.route('https://images.unsplash.com/**', (route) => route.abort());
      await context.route('https://challenges.cloudflare.com/**', (route) => route.fulfill({ contentType: 'text/javascript', body: stub }));
      await context.route('**/api/**', async (route) => {
        const request = route.request();
        const pathname = new URL(request.url()).pathname;
        let status = 200, body = {};
        const headers = { 'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Headers': 'Content-Type,Authorization', 'Access-Control-Allow-Methods': 'GET,POST,OPTIONS', 'Access-Control-Expose-Headers': 'Retry-After' };
        if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
        if (pathname === '/api/auth/captcha-config') { status = configFailed ? 503 : 200; body = configFailed ? { detail: 'Verification unavailable.' } : { enabled: true, site_key: 'synthetic-key' }; }
        else if (pathname === '/api/schools/directory') body = [{ school_id: 1, name: 'Original School', city: 'City A' }, { school_id: 2, name: 'Correct School', city: 'City B' }];
        if (request.method() === 'POST') {
          const payload = request.postDataJSON();
          requests.push({ pathname, payload, authorization: request.headers().authorization });
          if (pathname === '/api/auth/login') {
            // Capture the response before waiting so late arrivals still
            // represent the credentials actually submitted by this request.
            const reply = loginReply;
            const gate = loginGate;
            if (gate) await gate;
            status = reply.status;
            body = { detail: reply.detail };
          } else if (pathname === '/api/auth/resubmission/request') {
            assert(payload.captcha_token?.startsWith('synthetic:resubmit:'));
            if (throttle) { throttle = false; status = 429; headers['Retry-After'] = '2'; body = { detail: 'Too many requests.' }; }
            else { status = 202; body = { message: 'If eligible, a code is on its way.', challenge_id: 'synthetic-challenge' }; }
          } else if (pathname === '/api/auth/resubmission/verify') {
            assert.equal(payload.challenge_id, 'synthetic-challenge');
            if (payload.code !== '123456') { status = 400; body = { detail: 'Invalid or expired code.' }; }
            else body = { resubmission_token: 'synthetic-restricted-token-not-for-login', expires_in: expiresIn, application: app };
          } else if (pathname === '/api/auth/resubmission/submit') {
            assert.equal(payload.token, 'synthetic-restricted-token-not-for-login');
            assert.equal(payload.email, undefined); assert.equal(payload.role, undefined); assert.equal(payload.password, undefined);
            if (rejectToken) { rejectToken = false; status = 401; body = { detail: 'Verification expired. Verify email again.' }; }
            else body = { message: 'Corrected application pending review.', account_status: 'pending' };
          } else if (pathname === '/api/auth/register') { status = 409; body = { detail: 'An account with that email address already exists.' }; }
          else throw new Error('Unexpected API mutation: ' + pathname);
        }
        await route.fulfill({ status, headers, contentType: 'application/json', body: JSON.stringify(body) });
      });
      const page = await context.newPage();
      page.setDefaultTimeout(8000);
      page.on('pageerror', (error) => errors.push(error.message));
      const submit = () => page.locator('button[type="submit"]').click();
      const solve = () => page.getByRole('button', { name: 'Solve synthetic verification', exact: true }).click();
      const recovery = () => page.getByRole('button', { name: 'Correct and resubmit', exact: true });
      const rejectedReply = { status: 403, detail: { code: 'application_rejected', message: 'Your application was rejected. Reason: Please correct your contact number.', can_resubmit: true } };
      const openLogin = async () => {
        await page.goto(origin + '/login');
        await page.locator('button[type="submit"]:enabled').waitFor();
        assert.equal(await recovery().count(), 0, 'Recovery is hidden before an eligible rejected login');
        await page.locator('#email').fill(teacher.email);
        await page.locator('#password').fill('SyntheticPass1!');
      };
      const finishLogin = async () => {
        await page.locator('button[type="submit"]:enabled').waitFor();
        await page.getByRole('alert').waitFor();
      };
      const login = async (reply) => {
        loginReply = reply;
        await submit();
        await finishLogin();
      };
      const setCode = (value) => page.getByLabel('Digit 1', { exact: true }).fill(value[0]).then(async () => { for (let i = 1; i < value.length; i++) await page.getByLabel('Digit ' + (i + 1), { exact: true }).fill(value[i]); });
      const requestAndVerify = async () => {
        await page.goto(origin + '/resubmit');
        await page.locator('#email').fill(app.email);
        await solve(); await submit();
        await page.getByRole('heading', { name: 'Verify your email', exact: true }).waitFor();
        await setCode('123456');
        const before = requests.filter((r) => r.pathname.endsWith('/verify')).length;
        assert.equal(requests.filter((r) => r.pathname.endsWith('/verify')).length, before, 'Completing the code never automatically submits');
        await submit();
        await page.getByRole('heading', { name: 'Correct your application', exact: true }).waitFor();
      };
      for (const [label, reply] of [
        ['pending account', { status: 403, detail: 'Your account is awaiting approval.' }],
        ['blocked account', { status: 403, detail: 'Your account is blocked.' }],
        ['deactivated account', { status: 403, detail: 'Your account has been deactivated.' }],
        ['wrong password', { status: 401, detail: 'Invalid email or password.' }],
        ['unknown email', { status: 401, detail: 'Invalid email or password.' }],
        ['generic error mentioning rejection', { status: 403, detail: 'Your application was rejected. Please correct your details.' }],
        ['unrelated structured error', { status: 403, detail: { code: 'account_pending', message: 'Your application was rejected.', can_resubmit: true } }],
        ['ineligible rejected account', { status: 403, detail: { code: 'application_rejected', message: 'Application rejected.', can_resubmit: false } }],
        ['malformed eligibility flag', { status: 403, detail: { code: 'application_rejected', message: 'Application rejected.', can_resubmit: 'true' } }],
        ['wrong HTTP status', { status: 401, detail: { code: 'application_rejected', message: 'Application rejected.', can_resubmit: true } }],
      ]) {
        await openLogin(); await login(reply);
        assert.equal(await recovery().count(), 0, label + ' must never expose correction');
      }
      await openLogin(); await login(rejectedReply);
      await recovery().waitFor();
      const formBox = await page.locator('form').boundingBox();
      const recoveryBox = await recovery().locator('..').boundingBox();
      assert(recoveryBox.y - (formBox.y + formBox.height) >= 16, 'Recovery has visible separation from sign-in form');
      await page.locator('#email').fill('other@example.com');
      assert.equal(await recovery().count(), 0, 'Changing email clears recovery');
      await page.locator('#email').fill(teacher.email);
      assert.equal(await recovery().count(), 0, 'Restoring email does not revive previous eligibility');
      await login(rejectedReply); await recovery().waitFor();
      await page.locator('#password').fill('DifferentPass1!');
      assert.equal(await recovery().count(), 0, 'Changing password clears recovery');
      await page.locator('#password').fill('SyntheticPass1!');
      assert.equal(await recovery().count(), 0, 'Restoring password does not revive previous eligibility');
      await login(rejectedReply); await recovery().waitFor();
      let releaseLogin;
      loginGate = new Promise((resolve) => { releaseLogin = resolve; });
      loginReply = { status: 401, detail: 'Invalid email or password.' };
      await submit();
      assert.equal(await recovery().count(), 0, 'Starting another login immediately hides recovery');
      releaseLogin(); loginGate = null; await finishLogin();
      assert.equal(await recovery().count(), 0, 'Failed next attempt does not retain recovery');

      loginGate = new Promise((resolve) => { releaseLogin = resolve; });
      loginReply = rejectedReply;
      const delayedRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/auth/login' && request.method() === 'POST');
      await submit(); await delayedRequest;
      await page.locator('#email').fill('changed@example.com');
      await page.locator('#password').fill('ChangedPass1!');
      releaseLogin(); loginGate = null; await page.locator('button[type="submit"]:enabled').waitFor();
      assert.equal(await recovery().count(), 0, 'Delayed rejection for previous credentials never reveals recovery');
      assert.equal(await page.getByRole('alert').count(), 0, 'Stale responses never display rejection details for changed credentials');
      await page.locator('#email').fill(teacher.email); await page.locator('#password').fill('SyntheticPass1!');
      assert.equal(await recovery().count(), 0, 'Old response cannot reappear after credentials are restored');
      await login(rejectedReply); await recovery().waitFor();
      checks.push('eligible rejected-login recovery gate, all other states hidden, spacing and stale response protection @' + width);
      await recovery().click();
      assert.equal(await page.locator('#email').inputValue(), teacher.email, 'Login carries email in Router state');
      await page.locator('#email').fill(''); await submit();
      await page.waitForFunction(() => document.activeElement.id === 'email');
      assert.equal(requests.filter((request) => request.pathname.startsWith('/api/auth/resubmission/')).length, 0);
      await page.locator('#email').fill(teacher.email); await submit();
      await page.waitForFunction(() => document.activeElement.id === 'captcha');
      assert.equal(requests.filter((request) => request.pathname.startsWith('/api/auth/resubmission/')).length, 0, 'Unsolved CAPTCHA never sends OTP');
      await solve(); throttle = true; await submit();
      await page.getByRole('status').filter({ hasText: 'Try again in' }).waitFor();
      assert.equal(await page.locator('button[type="submit"]').isDisabled(), true);
      await page.waitForFunction(() => !document.querySelector('button[type="submit"]').disabled);
      await solve(); await submit();
      await page.getByRole('heading', { name: 'Verify your email', exact: true }).waitFor();
      await setCode('000000'); await submit();
      await page.getByRole('alert').filter({ hasText: 'Invalid or expired code.' }).waitFor();
      await setCode('123456');
      const verifyBefore = requests.filter((r) => r.pathname.endsWith('/verify')).length;
      await page.waitForTimeout(100);
      assert.equal(requests.filter((r) => r.pathname.endsWith('/verify')).length, verifyBefore, 'No automatic duplicate OTP request');
      await submit();
      await page.getByRole('heading', { name: 'Correct your application', exact: true }).waitFor();
      assert((await page.locator('.alert-info').innerText()).includes('Please correct your contact number.'));
      await page.getByText('Previous decisions (1)', { exact: true }).click();
      assert.equal(await page.getByText('<script>not executable</script>', { exact: true }).count(), 1);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'Correction form stays within viewport');
      assert.equal(await page.locator('input[type="password"],input[type="email"]').count(), 0, 'Primary email and password are not editable');
      assert.equal(await page.evaluate(() => Object.values(localStorage).some((value) => value.includes('synthetic-restricted-token'))), false, 'Restricted token never stored');
      assert(!page.url().includes('synthetic-restricted-token'));
      await page.locator('#first_name').fill(''); await submit();
      await page.waitForFunction(() => document.activeElement.id === 'first_name');
      await page.locator('#first_name').fill('Corrected Teacher');
      await page.getByRole('checkbox', { name: 'I selected the wrong school', exact: true }).check();
      await page.locator('#school_id option[value="2"]').waitFor({ state: 'attached' });
      await page.locator('#school_id').selectOption('2'); await submit();
      await page.waitForFunction(() => document.activeElement.id === 'confirm_school_change');
      assert.equal(requests.filter((r) => r.pathname.endsWith('/submit')).length, 0);
      await page.locator('#confirm_school_change').check(); await submit();
      await page.getByRole('heading', { name: 'Application resubmitted', exact: true }).waitFor();
      const saved = requests.find((r) => r.pathname.endsWith('/submit')).payload;
      assert.equal(saved.first_name, 'Corrected Teacher'); assert.equal(saved.school_id, 2); assert.equal(saved.confirm_school_change, true);
      assert.equal(saved.school, undefined);
      checks.push('teacher identity proof, CAPTCHA, cooldown, explicit OTP, safe history, validation, school-change consent @' + width);

      app = { ...teacher, can_change_school: false };
      await requestAndVerify();
      assert.equal(await page.getByRole('checkbox', { name: 'I selected the wrong school', exact: true }).count(), 0);
      rejectToken = true; await submit();
      await page.getByRole('alert').filter({ hasText: 'Verification expired.' }).waitFor();
      assert.equal(await page.locator('button[type="submit"]').isDisabled(), true);
      await page.getByRole('button', { name: 'Verify email again', exact: true }).click();
      await page.getByRole('heading', { name: 'Correct and resubmit', exact: true }).waitFor();
      checks.push('school-change boundary and expired server token restart @' + width);

      app = { ...teacher, role: 'admin', email: 'principal@example.com', school: { ...schoolProfile }, school_name: schoolProfile.name, can_change_school: false };
      await requestAndVerify();
      await page.locator('#school_name').fill('Corrected School');
      await page.locator('#school_city').fill('Corrected City');
      await page.locator('#school_contact_email').fill('office@example.com');
      await page.getByRole('checkbox', { name: 'Teachers track numbered booklets or modules', exact: true }).check();
      await page.locator('#school_unit_label_singular').fill('Booklet');
      await page.locator('#school_unit_label_plural').fill('Booklets');
      await submit();
      await page.getByRole('heading', { name: 'Application resubmitted', exact: true }).waitFor();
      const principal = requests.filter((r) => r.pathname.endsWith('/submit')).at(-1).payload;
      assert.equal(principal.school.name, 'Corrected School'); assert.equal(principal.school.city, 'Corrected City');
      assert.equal(principal.school.contact_email, 'office@example.com'); assert.equal(principal.school.tracks_units, true);
      assert.equal(principal.school.unit_label_plural, 'Booklets'); assert.equal(principal.school.passing_mark, 75);
      assert.equal(principal.school_id, undefined);
      checks.push('principal full school profile correction and pending confirmation @' + width);

      expiresIn = 0;
      await requestAndVerify(); await submit();
      await page.getByRole('alert').filter({ hasText: 'Your verification has expired.' }).waitFor();
      assert.equal(await page.locator('button[type="submit"]').isDisabled(), true, 'Client expiry blocks another submission');
      expiresIn = 900;

      await requestAndVerify();
      await page.reload();
      await page.getByRole('heading', { name: 'Correct and resubmit', exact: true }).waitFor();
      assert.equal(await page.getByRole('heading', { name: 'Correct your application', exact: true }).count(), 0, 'Reload discards restricted token');
      configFailed = true; await page.reload();
      await page.getByRole('button', { name: 'Retry security settings', exact: true }).waitFor();
      assert.equal(await page.locator('button[type="submit"]').isDisabled(), true, 'Configuration failure stays closed');
      configFailed = false; await page.getByRole('button', { name: 'Retry security settings', exact: true }).click();
      await solve();
      assert.equal(await page.locator('button[type="submit"]').isDisabled(), false);
      await page.goto(origin + '/register');
      await page.locator('#school_id option[value="1"]').waitFor({ state: 'attached' });
      await page.locator('#school_id').selectOption('1');
      await page.locator('#first_name').fill('Test'); await page.locator('#last_name').fill('Teacher');
      await page.locator('#reg_email').fill(teacher.email); await page.locator('#contact').fill('09170000000');
      await page.locator('#reg_password').fill('SyntheticPass1!'); await page.locator('#confirm').fill('SyntheticPass1!');
      await solve(); await submit();
      assert.equal(await page.getByRole('button', { name: /correct and resubmit/i }).count(), 0, 'Registration never exposes correction directly');
      await page.locator('#reg_email').locator('..').getByRole('button').click();
      await page.getByRole('heading', { name: 'Welcome back', exact: true }).waitFor();
      assert.equal(await page.locator('#email').inputValue(), teacher.email, 'Duplicate registration returns to sign-in with the existing email');
      assert.equal(await recovery().count(), 0, 'Returning from duplicate registration still requires a rejected login');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'No horizontal overflow');
      assert(requests.every((r) => r.authorization === undefined), 'Restricted flow never sends normal authorization');
      assert.deepEqual(errors, []);
      checks.push('local expiry, token discarded on reload, fail-closed config retry, registration sign-in recovery, responsive layout @' + width);
      await context.close();
    }
    checks.forEach((check) => console.log('PASS: ' + check));
  } finally { await browser.close(); server.close(); }
}
run().catch((error) => { console.error(error); server.close(); process.exitCode = 1; });
