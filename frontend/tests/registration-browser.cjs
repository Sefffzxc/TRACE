/* Actual registration controls and grade modal with synthetic API responses.
   Run: node frontend/tests/registration-browser.cjs */
const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { chromium } = require('../../audit/runtime/node_modules/playwright');
const frontend = path.resolve(__dirname, '..');
const entry = `
import React from 'react'; import {createRoot} from 'react-dom/client';
import {BrowserRouter} from 'react-router-dom';
import {ThemeProvider} from './src/context/ThemeContext';
import RegisterPage from './src/pages/auth/RegisterPage';
import {GradeSectionsModal} from './src/pages/app/GradeSections';
import {registrationApiErrors} from './src/pages/auth/registrationValidation';
import {sectionLabel} from './src/lib/classAccess';
window.registrationApiErrors=registrationApiErrors;
window.sectionLabel=sectionLabel;
const record=new URLSearchParams(location.search).has('edit')?{grade_level_id:1,level:'2',name:'Grade 2'}:null;
const modal=<GradeSectionsModal record={record} data={{sections:[],assignments:[]}} busy={false}
  onSave={value=>{window.savedGrade=value}} onClose={()=>{}}/>;
createRoot(document.getElementById('root')).render(<BrowserRouter><ThemeProvider>
  {location.pathname==='/grade'?modal:<RegisterPage/>}
</ThemeProvider></BrowserRouter>);
`;
let bundle = '';
const server = http.createServer((req, res) => {
  if (req.url === '/bundle.js') { res.setHeader('Content-Type', 'text/javascript'); res.end(bundle); }
  else { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><div id="root"></div><script type="module" src="/bundle.js"></script>'); }
});

async function run() {
  const { build } = await import(pathToFileURL(path.join(frontend, 'node_modules/vite/dist/node/index.js')).href);
  const virtualEntry = path.join(frontend, 'registration-browser-harness.jsx').replace(/\\/g, '/');
  const result = await build({ configFile: false, root: frontend, logLevel: 'error', esbuild: { jsx: 'automatic' },
    define: { 'import.meta.env.VITE_API_URL': JSON.stringify('http://api.invalid') },
    plugins: [{ name: 'registration-harness', resolveId(id) { if (id === virtualEntry) return id; }, load(id) { if (id === virtualEntry) return entry; } }],
    build: { write: false, minify: false, rollupOptions: { input: virtualEntry, output: { inlineDynamicImports: true } } } });
  bundle = result.output.find((item) => item.type === 'chunk').code;
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('http://api.invalid/**', (route) => {
      const url = new URL(route.request().url());
      const payload = url.pathname === '/api/auth/captcha-config' ? { enabled: false } :
        [{ school_id: 1, name: 'Camp Evangelista Es', city: 'Cagayan de Oro' }];
      return route.fulfill({ json: payload, headers: { 'Access-Control-Allow-Origin': origin } });
    });
    await page.goto(`${origin}/register`);
    await page.locator('#school_id').selectOption('1');
    for (const number of ['abcdefg1234', '0912345678', '08123456789']) {
      await page.locator('#contact').fill(number);
      await page.getByRole('button', { name: 'Submit request', exact: true }).click();
      assert.match(await page.locator('#contact-error').innerText(), /11-digit Philippine mobile number starting with 09/);
    }
    await page.locator('#contact').fill('09123456789');
    await page.getByRole('button', { name: 'Submit request', exact: true }).click();
    assert.equal(await page.locator('#contact-error').count(), 0);
    await page.getByLabel('A school administrator', { exact: true }).check();
    await page.locator('#s_name').fill('123456');
    await page.locator('#a_contact').fill('abcdefghijk');
    await page.getByRole('button', { name: 'Register school', exact: true }).click();
    assert.match(await page.locator('#s_name-error').innerText(), /must contain letters/);
    assert.match(await page.locator('#a_contact-error').innerText(), /11-digit/);
    await page.locator('#s_name').fill("  LORD'S   BLESSING CHRISTIAN ACADEMY  ");
    await page.locator('#a_contact').focus();
    assert.equal(await page.locator('#s_name').inputValue(), "Lord's Blessing Christian Academy");
    const mapped = await page.evaluate(() => ['teacher', 'school'].map((mode) => window.registrationApiErrors({
      status: 409, message: 'An account with that contact number already exists.',
    }, mode).fields));
    assert.deepEqual(mapped.map(Object.keys), [['contact'], ['a_contact']]);
    for (const edit of [false, true]) {
      await page.goto(`${origin}/grade${edit ? '?edit' : ''}`);
      await page.getByLabel('Grade level', { exact: false }).fill('2');
      assert.equal(await page.getByLabel('Display name', { exact: false }).count(), 0);
      if (!edit) await page.getByRole('textbox', { name: 'Section 1 *', exact: true }).fill('Charity');
      await page.getByRole('button', { name: edit ? 'Save changes' : 'Create grade & sections', exact: true }).click();
      const saved = await page.evaluate(() => window.savedGrade);
      assert.equal(saved.level, '2');
      assert.equal(Object.hasOwn(saved, 'name'), false);
    }
    const label = await page.evaluate(() => {
      const section = { section_id: 1, name: 'Charity', grade_level_name: 'Grade 2' };
      return window.sectionLabel(section, [section], true);
    });
    assert.equal(label, 'Grade 2 - Charity');
    assert.deepEqual(errors, []);
    process.stdout.write('Passed: teacher and principal phone validation; school name validation/title case; duplicate error fields; grade create/edit inputs; class record label.\n');
  } finally { await browser.close(); await new Promise((resolve) => server.close(resolve)); }
}
run().catch((error) => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; server.close(); });
