/* Run: node audit/assignment-ownership-browser.cjs
   Uses synthetic responses and a local bundle; never writes to a live API. */
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const esbuild = require('../frontend/node_modules/esbuild');
const { chromium } = require('./runtime/node_modules/playwright');

const frontend = path.resolve(__dirname, '../frontend');
const bundle = esbuild.buildSync({
  stdin: { contents: `
    import React from 'react';
    import { createRoot } from 'react-dom/client';
    import { MemoryRouter } from 'react-router-dom';
    import { ThemeProvider } from './src/context/ThemeContext';
    import { AuthProvider } from './src/context/AuthContext';
    import { ToastProvider } from './src/context/ToastContext';
    import ClassManagementPage from './src/pages/app/ClassManagementPage';
    createRoot(document.getElementById('root')).render(
      <React.StrictMode><ThemeProvider><AuthProvider><ToastProvider>
        <MemoryRouter><ClassManagementPage /></MemoryRouter>
      </ToastProvider></AuthProvider></ThemeProvider></React.StrictMode>
    );
  `, resolveDir: frontend, loader: 'jsx' },
  bundle: true, write: false, platform: 'browser', format: 'iife', jsx: 'automatic',
  define: { 'import.meta.env': '{}', 'process.env.NODE_ENV': '"development"' },
});
const css = ['tokens.css', 'base.css', 'layout.css', 'pages.css']
  .map(name => fs.readFileSync(path.join(frontend, 'src/styles', name))).join('\n');
const server = http.createServer((request, response) => {
  if (request.url === '/bundle.js') { response.setHeader('Content-Type', 'text/javascript'); response.end(bundle.outputFiles[0].text); }
  else if (request.url === '/styles.css') { response.setHeader('Content-Type', 'text/css'); response.end(css); }
  else { response.setHeader('Content-Type', 'text/html'); response.end('<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/styles.css"></head><body><div id="root"></div><script src="/bundle.js"></script></body></html>'); }
});
const teacher = (id, first_name, extra = {}) => ({ id, first_name, last_name: 'Teacher', role: 'teacher', is_active: true, is_approved: true, account_status: 'approved', ...extra });
const teachers = [teacher('alice', 'Alice'), teacher('bob', 'Bob'), teacher('cara', 'Cara'),
  teacher('pending', 'Pending', { is_approved: false, account_status: 'pending' }),
  teacher('inactive', 'Inactive', { is_active: false, account_status: 'deactivated' })];
const sections = [
  { section_id: 10, grade_level_id: 1, name: 'Faith', grade_level_name: 'Grade 7' },
  { section_id: 11, grade_level_id: 1, name: 'Hope', grade_level_name: 'Grade 7' },
  { section_id: 12, grade_level_id: 2, name: 'Grace', grade_level_name: 'Grade 8' },
];
const subjects = [
  { subject_id: 20, grade_level_id: 1, subject_name: 'English', is_active: true },
  { subject_id: 21, grade_level_id: 1, subject_name: 'Mathematics', is_active: true },
  { subject_id: 22, grade_level_id: 1, subject_name: 'Science', is_active: true },
  { subject_id: 23, grade_level_id: 1, subject_name: 'Retired subject', is_active: false },
  { subject_id: 30, grade_level_id: 2, subject_name: 'Grade 8 English', is_active: true },
];
let identity = 2;
let assignments = [
  { assignment_id: 1, teacher_id: 'alice', teacher_name: 'Alice Teacher', section_id: 10, section_name: 'Faith', subject_id: 20, subject_name: 'English', is_adviser: true },
  { assignment_id: 2, teacher_id: 'cara', teacher_name: 'Cara Teacher', section_id: 11, section_name: 'Hope', subject_id: null, subject_name: null, is_adviser: true },
];
const posts = [];
let raceOnScience = false;
function makeAssignment(body, assignedTeacher = body.teacher_id) {
  return { ...body, assignment_id: ++identity, teacher_id: assignedTeacher,
    teacher_name: `${teachers.find(row => row.id === assignedTeacher).first_name} Teacher`,
    section_name: sections.find(row => row.section_id === body.section_id).name,
    subject_name: subjects.find(row => row.subject_id === body.subject_id)?.subject_name || null };
}

async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const user = { id: 'principal', role: 'admin', school_id: 1, first_name: 'Test', last_name: 'Principal' };
    await context.addInitScript(user => {
      localStorage.setItem('trace.access_token', 'synthetic-only');
      localStorage.setItem('trace.user', JSON.stringify(user));
    }, user);
    await context.route('**/api/**', async route => {
      const request = route.request();
      const pathname = new URL(request.url()).pathname;
      const reply = (body, status = 200) => route.fulfill({ status, json: body, headers: { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': 'GET,POST,OPTIONS' } });
      if (request.method() === 'OPTIONS') return reply({}, 204);
      if (request.method() === 'POST') {
        assert.equal(pathname, '/api/teacher-assignments');
        const body = request.postDataJSON();
        posts.push(body);
        if (raceOnScience && body.subject_id === 22) {
          raceOnScience = false;
          assignments.push(makeAssignment(body, 'alice'));
          return reply({ detail: 'Science is already assigned to Alice Teacher. Remove the existing assignment before assigning another teacher.' }, 409);
        }
        assert(!assignments.some(row => row.section_id === body.section_id && (row.subject_id === null || body.subject_id === null || row.subject_id === body.subject_id)), 'UI must not post an occupied class');
        const saved = makeAssignment(body);
        assignments.push(saved);
        return reply(saved, 201);
      }
      if (pathname === '/api/auth/me') return reply(user);
      if (pathname === '/api/schools/me') return reply({ school_id: 1, name: 'Synthetic school', tracks_units: false });
      if (pathname === '/api/school-years') return reply([{ school_year_id: 1, year: '2026-2027', is_current: true }]);
      if (pathname === '/api/grade-levels') return reply([{ grade_level_id: 1, name: 'Grade 7' }, { grade_level_id: 2, name: 'Grade 8' }]);
      if (pathname === '/api/sections') return reply(sections);
      if (pathname === '/api/subjects') return reply(subjects);
      if (pathname === '/api/teacher-assignments') return reply(assignments);
      if (pathname === '/api/staff') return reply(teachers);
      throw new Error(`Unexpected API ${request.method()} ${pathname}`);
    });
    const page = await context.newPage();
    page.setDefaultTimeout(6000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${origin}/class-management?tab=assignments`);
    await page.getByRole('button', { name: 'Add assignment', exact: true }).click();
    let dialog = page.getByRole('dialog');
    await dialog.locator('#a_teacher option[value="bob"]').waitFor({ state: 'attached' });
    assert.equal(await dialog.locator('#a_teacher option[value="pending"], #a_teacher option[value="inactive"]').count(), 0, 'only approved active teachers are selectable');
    await dialog.locator('#a_teacher').selectOption('bob');
    assert.equal(await dialog.locator('select[id^="a_subject_"]').isDisabled(), true, 'subject requires section selection');
    const section = dialog.locator('select[id^="a_section_"]');
    assert.equal(await section.locator('option[value="11"]').isDisabled(), true, 'wildcard owner reserves the entire section');
    assert.match(await section.locator('option[value="11"]').innerText(), /Cara Teacher/);
    await section.selectOption('10');
    let subject = dialog.locator('select[id^="a_subject_"]').first();
    assert.equal(await subject.locator('option[value="20"]').isDisabled(), true);
    assert.match(await subject.locator('option[value="20"]').innerText(), /Alice Teacher/);
    assert.equal(await subject.locator('option[value="*"]').isDisabled(), true, 'wildcard cannot overlap a specific class');
    assert.equal(await subject.locator('option[value="23"], option[value="30"]').count(), 0, 'subject choices respect active status and section grade');
    await subject.selectOption('21');
    await dialog.getByRole('button', { name: 'Add another subject', exact: true }).click();
    const sectionsInForm = dialog.locator('select[id^="a_section_"]');
    await sectionsInForm.nth(1).selectOption('10');
    let secondSubject = dialog.locator('select[id^="a_subject_"]').nth(1);
    assert.equal(await secondSubject.locator('option[value="21"]').isDisabled(), true, 'draft rows cannot duplicate each other');
    await secondSubject.selectOption('22');
    await subject.evaluate(element => { element.value = '22'; element.dispatchEvent(new Event('change', { bubbles: true })); });
    await dialog.getByRole('alert').filter({ hasText: 'overlaps another assignment' }).first().waitFor();
    assert.equal(await dialog.getByRole('button', { name: 'Create 2 assignments', exact: true }).isDisabled(), true, 'submit guard catches programmatic invalid selections');
    assert.equal(posts.length, 0);
    await subject.selectOption('21');
    await page.setViewportSize({ width: 320, height: 812 });
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => document.documentElement.setAttribute('data-theme', theme), theme);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `assignment modal fits 320px ${theme}`);
    }
    raceOnScience = true;
    await dialog.getByRole('button', { name: 'Create 2 assignments', exact: true }).click();
    await dialog.getByRole('alert').filter({ hasText: 'already assigned to Alice Teacher' }).waitFor();
    assert.equal(await dialog.locator('select[id^="a_section_"]').count(), 1, 'partial save retains only unsaved rows');
    assert.equal(await dialog.getByRole('button', { name: 'Create', exact: true }).isDisabled(), true, 'fresh conflict disables retrying an occupied class');
    assert.deepEqual(posts.map(post => post.subject_id), [21, 22], 'successful first row is not submitted twice');
    await dialog.locator('select[id^="a_section_"]').selectOption('12');
    await dialog.locator('select[id^="a_subject_"]').selectOption('*');
    await page.getByRole('button', { name: 'Dismiss', exact: true }).click();
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();
    await dialog.waitFor({ state: 'detached' });
    assert.equal(posts.at(-1).subject_id, null, 'explicit All subjects serializes to backend wildcard');
    assert.equal(posts.at(-1).section_id, 12);
    assert.equal(posts.at(-1).teacher_id, 'bob');
    assert.deepEqual(errors, []);
    console.log('PASS: occupied subject/section and wildcard choices show owners, approved teachers only, grade filtering, draft overlap guard, 409 refresh/partial-save recovery, explicit wildcard payload, and 320px dark/light.');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
