/* Run: node audit/captcha-loader-browser.cjs
   Uses existing esbuild and audit Playwright, synthetic keys, and a mocked
   provider script matching Cloudflare's async ready() error 3857. No database,
   form submissions, or external provider requests are made. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const esbuild = require('../frontend/node_modules/esbuild');
const { chromium } = require('./runtime/node_modules/playwright');

const frontend = path.resolve(__dirname, '../frontend');
const built = esbuild.buildSync({
  stdin: { contents: `
    import React, { useState } from 'react';
    import { createRoot } from 'react-dom/client';
    import CaptchaField from './src/components/common/CaptchaField';
    import { ThemeProvider, useTheme } from './src/context/ThemeContext';
    function Demo() {
      const [token, setToken] = useState('');
      const [text, setText] = useState('');
      const [resetKey, setReset] = useState(0);
      const [show, setShow] = useState(true);
      const {toggle} = useTheme();
      return <main style={{width:'calc(100% - 40px)',maxWidth:480,margin:'20px auto'}}>
        <input aria-label="Email" value={text} onChange={e => setText(e.target.value)} />
        <button onClick={() => setReset(k => k+1)}>Reset</button>
        <button onClick={toggle}>Theme</button>
        <button onClick={() => setShow(v => !v)}>Toggle</button>
        {show && <CaptchaField siteKey="synthetic-public-key" action="register" token={token} onTokenChange={value => setToken(value)} resetKey={resetKey} />}
        <output id="token">{token || 'empty'}</output>
      </main>;
    }
    createRoot(document.getElementById('root')).render(<React.StrictMode><ThemeProvider><Demo /></ThemeProvider></React.StrictMode>);
  `, resolveDir: frontend, loader: 'jsx' },
  bundle: true, write: false, platform: 'browser', format: 'iife', jsx: 'automatic',
  define: { 'import.meta.env': '{}', 'process.env.NODE_ENV': '"development"' },
});
const css = ['tokens.css', 'base.css', 'auth.css'].map(name => fs.readFileSync(path.join(frontend, 'src/styles', name))).join('\n');
const server = http.createServer((req, res) => {
  if (req.url === '/bundle.js') { res.setHeader('Content-Type', 'text/javascript'); res.end(built.outputFiles[0].text); }
  else if (req.url === '/styles.css') { res.setHeader('Content-Type', 'text/css'); res.end(css); }
  else { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/styles.css"></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>'); }
});
const stub = `
window.widgets = []; window.removedWidgets = [];
window.readyCalls = 0;
const loadedAsync = document.currentScript.async || document.currentScript.defer;
window.turnstile = {
  ready(callback) {
    window.readyCalls += 1;
    if (loadedAsync) throw new Error('[Cloudflare Turnstile] Remove async/defer from the Turnstile api.js script tag before using turnstile.ready(). (3857)');
    callback();
  },
  render(host, options) {
    const id = String(window.widgets.length + 1);
    const button = document.createElement('button');
    button.textContent = 'Verify me';
    button.style.cssText = options.size === 'compact' ? 'width:150px;height:140px' : 'width:100%;min-width:300px;height:65px';
    button.addEventListener('click', () => options.callback('synthetic-token-'+id));
    host.appendChild(button);
    window.widgets.push({ id, options, host });
    return id;
  },
  remove(id) { window.removedWidgets.push(id); window.widgets.find(w => w.id === id)?.host.replaceChildren(); }
};`;

async function run() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 375, height: 812 } });
    page.setDefaultTimeout(5000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('https://challenges.cloudflare.com/turnstile/v0/api.js?*', route => route.fulfill({contentType:'text/javascript',body:stub}));
    await page.goto(origin);
    await page.getByRole('button', { name: 'Verify me', exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.widgets.length), 1, 'StrictMode mounts only one live widget');
    assert.equal(await page.evaluate(() => window.readyCalls), 0, 'async scripts must never call turnstile.ready');
    assert.equal(await page.locator('#trace-turnstile-script').count(), 1);
    await page.getByRole('button', { name: 'Verify me', exact: true }).click();
    assert.equal(await page.locator('#token').innerText(), 'synthetic-token-1');
    await page.getByLabel('Email').fill('teacher@example.com');
    assert.equal(await page.evaluate(() => window.widgets.length), 1, 'inline callbacks and edits preserve challenge');
    assert.equal(await page.locator('#token').innerText(), 'synthetic-token-1');
    await page.evaluate(() => window.widgets.at(-1).options['expired-callback']());
    await page.getByRole('alert').filter({hasText:'Verification expired.'}).waitFor();
    assert.equal(await page.locator('#token').innerText(), 'empty');
    assert.equal(await page.locator('#captcha').getAttribute('aria-invalid'), 'true');
    await page.locator('#captcha').focus();
    assert.equal(await page.locator('#captcha').evaluate(e => e === document.activeElement), true);
    await page.getByRole('button', {name:'Retry verification'}).click();
    await page.waitForFunction(() => window.widgets.length === 2);
    await page.evaluate(() => window.widgets[0].options.callback('stale-token'));
    assert.equal(await page.locator('#token').innerText(), 'empty', 'removed callback ignored');
    await page.getByRole('button', {name:'Verify me',exact:true}).click();
    await page.getByRole('button', {name:'Reset',exact:true}).click();
    await page.waitForFunction(() => window.widgets.length === 3);
    await page.waitForFunction(() => document.getElementById('token').textContent === 'empty');
    assert.equal(await page.locator('#token').innerText(), 'empty');
    const beforeTheme = await page.evaluate(() => window.widgets.at(-1).options.theme);
    await page.getByRole('button', {name:'Theme',exact:true}).click();
    await page.waitForFunction(() => window.widgets.length === 4);
    assert.notEqual(await page.evaluate(() => window.widgets.at(-1).options.theme), beforeTheme);
    await page.evaluate(() => window.widgets.at(-1).options['error-callback']('network-error'));
    await page.getByRole('alert').filter({hasText:'Security verification failed.'}).waitFor();
    assert.equal(await page.locator('#token').innerText(), 'empty');
    await page.getByRole('button', {name:'Retry verification'}).click();
    await page.waitForFunction(() => window.widgets.length === 5);
    await page.evaluate(() => window.widgets.at(-1).options['timeout-callback']());
    await page.getByRole('alert').filter({hasText:'Verification timed out.'}).waitFor();
    await page.getByRole('button', {name:'Toggle',exact:true}).click();
    assert.equal(await page.locator('#captcha').count(), 0);
    assert.equal(await page.evaluate(() => window.removedWidgets.length), 5, 'all live widgets cleaned up');
    await page.getByRole('button', {name:'Toggle',exact:true}).click();
    await page.getByRole('button', {name:'Verify me',exact:true}).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, '375px layout has no horizontal overflow');
    const beforeNarrow = await page.evaluate(() => window.widgets.length);
    await page.setViewportSize({ width: 320, height: 812 });
    await page.waitForFunction(count => window.widgets.length === count + 1, beforeNarrow);
    assert.equal(await page.evaluate(() => window.widgets.at(-1).options.size), 'compact');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, '320px resize uses compact without horizontal overflow');
    await page.setViewportSize({ width: 375, height: 812 });
    await page.waitForFunction(count => window.widgets.length === count + 2, beforeNarrow);
    assert.equal(await page.evaluate(() => window.widgets.at(-1).options.size), 'flexible');
    assert.deepEqual(errors, []);
    console.log('PASS: StrictMode, singleton loader, edit stability, token expiration, stale callbacks, focus, retry, reset, theme, challenge error/timeout, cleanup, mobile375.');
    await page.close();

    const failedPage = await browser.newPage({ viewport: { width: 320, height: 812 } });
    let scriptCalls = 0;
    await failedPage.route('https://challenges.cloudflare.com/turnstile/v0/api.js?*', route => ++scriptCalls === 1 ? route.abort('failed') : route.fulfill({contentType:'text/javascript',body:stub}));
    await failedPage.goto(origin);
    await failedPage.getByRole('alert').filter({hasText:'Security verification could not load.'}).waitFor();
    await failedPage.getByRole('button', {name:'Retry verification'}).click();
    await failedPage.getByRole('button', {name:'Verify me',exact:true}).waitFor();
    assert.equal(scriptCalls, 2);
    assert.equal(await failedPage.evaluate(() => window.readyCalls), 0, 'retry never calls ready either');
    assert.equal(await failedPage.locator('#trace-turnstile-script').count(), 1);
    assert.equal(await failedPage.evaluate(() => window.widgets.at(-1).options.size), 'compact', 'initial320 load selects compact');
    assert.equal(await failedPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'initial320 page does not overflow');
    await failedPage.getByRole('button', {name:'Verify me',exact:true}).click();
    assert.equal(await failedPage.locator('#token').innerText(), 'synthetic-token-1');
    console.log('PASS: initial script failure invalidates token and manual Retry reloads exactly once.');
    await failedPage.close();
  } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => server.close());
