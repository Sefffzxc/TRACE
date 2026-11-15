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
  { section_id: 13, grade_level_id: 2, name: 'Peace', grade_level_name: 'Grade 8' },
];
const subjects = [
  { subject_id: 20, grade_level_id: 1, subject_name: 'English', is_active: true },
  { subject_id: 21, grade_level_id: 1, subject_name: 'Mathematics', is_active: true },
  { subject_id: 22, grade_level_id: 1, subject_name: 'Science', is_active: true },
  { subject_id: 23, grade_level_id: 1, subject_name: 'Retired subject', is_active: false },
  { subject_id: 30, grade_level_id: 2, subject_name: 'Grade 8 English', is_active: true },
];
let identity = 4;
let assignments = [
  { assignment_id: 1, teacher_id: 'alice', teacher_name: 'Alice Teacher', section_id: 10, section_name: 'Faith', subject_id: 20, subject_name: 'English', is_adviser: true },
  { assignment_id: 2, teacher_id: 'cara', teacher_name: 'Cara Teacher', section_id: 11, section_name: 'Hope', subject_id: null, subject_name: null, is_adviser: true },
  { assignment_id: 3, teacher_id: 'cara', teacher_name: 'Cara Teacher', section_id: 12, section_name: 'Grace', subject_id: null, subject_name: null, is_adviser: false },
  { assignment_id: 4, teacher_id: 'alice', teacher_name: 'Alice Teacher', section_id: 10, section_name: 'Faith', subject_id: null, subject_name: null, is_adviser: true },
];
let advisers = [
  { adviser_id: 1, section_id: 10, section_name: 'Faith', grade_level_name: 'Grade 7', teacher_id: 'alice', teacher_name: 'Alice Teacher' },
  { adviser_id: 2, section_id: 11, section_name: 'Hope', grade_level_name: 'Grade 7', teacher_id: 'cara', teacher_name: 'Cara Teacher' },
];
const posts = [];
const adviserWrites = [];
const reviews = [];
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
      if (request.method() === 'PUT') {
        assert.match(pathname, /^\/api\/sections\/\d+\/adviser$/);
        const sectionId = Number(pathname.split('/')[3]);
        const { teacher_id } = request.postDataJSON();
        adviserWrites.push({ section_id: sectionId, teacher_id });
        const row = { adviser_id: sectionId, section_id: sectionId, teacher_id,
          section_name: sections.find(item => item.section_id === sectionId).name,
          teacher_name: `${teachers.find(item => item.id === teacher_id).first_name} Teacher` };
        advisers = advisers.filter(item => item.section_id !== sectionId).concat(row);
        return reply(row);
      }
      if (request.method() === 'DELETE') {
        assert.match(pathname, /^\/api\/sections\/\d+\/adviser$/);
        advisers = advisers.filter(item => item.section_id !== Number(pathname.split('/')[3]));
        return route.fulfill({ status: 204 });
      }
      if (request.method() === 'POST' && pathname.endsWith('/review')) {
        const id = Number(pathname.split('/')[3]);
        const previous = assignments.find(item => item.assignment_id === id);
        const body = request.postDataJSON();
        reviews.push({ id, ...body });
        assignments = assignments.filter(item => item.assignment_id !== id);
        for (const subjectId of body.subject_ids) {
          if (!assignments.some(item => item.teacher_id === previous.teacher_id && item.section_id === previous.section_id && item.subject_id === subjectId)) {
            assignments.push(makeAssignment({ teacher_id: previous.teacher_id, section_id: previous.section_id, subject_id: subjectId }));
          }
        }
        return reply({ reviewed: true });
      }
      if (request.method() === 'POST') {
        assert.equal(pathname, '/api/teacher-assignments');
        const body = request.postDataJSON();
        posts.push(body);
        if (raceOnScience && body.subject_id === 22) {
          raceOnScience = false;
          assignments.push(makeAssignment(body, 'alice'));
          return reply({ detail: 'Science is already assigned to Alice Teacher. Remove the existing assignment before assigning another teacher.' }, 409);
        }
        assert.notEqual(body.subject_id, null, 'new teaching assignments require a concrete subject');
        assert.equal('is_adviser' in body, false, 'teaching form never grants advisory access');
        assert(!assignments.some(row => row.section_id === body.section_id && !(row.subject_id === null && row.is_adviser) && (row.subject_id === null || row.subject_id === body.subject_id)), 'UI must not post an occupied teaching class');
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
      if (pathname === '/api/section-advisers') return reply(advisers);
      if (pathname === '/api/staff') return reply(teachers);
      throw new Error(`Unexpected API ${request.method()} ${pathname}`);
    });
    const page = await context.newPage();
    page.setDefaultTimeout(6000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${origin}/class-management?tab=assignments`);
    await page.getByRole('button', { name: 'Add subject teacher', exact: true }).click();
    let dialog = page.getByRole('dialog');
    await dialog.locator('#a_teacher option[value="bob"]').waitFor({ state: 'attached' });
    assert.equal(await dialog.locator('#a_teacher option[value="pending"], #a_teacher option[value="inactive"]').count(), 0, 'only approved active teachers are selectable');
    await dialog.locator('#a_teacher').selectOption('bob');
    assert.equal(await dialog.locator('select[id^="a_subject_"]').isDisabled(), true, 'subject requires section selection');
    const section = dialog.locator('select[id^="a_section_"]');
    assert.equal(await section.locator('option[value="11"]').isDisabled(), false, 'legacy adviser access does not reserve teaching subjects');
    assert.equal(await section.locator('option[value="12"]').isDisabled(), true, 'legacy true-all-subject teaching still reserves the section until explicit review');
    assert.match(await section.locator('option[value="12"]').innerText(), /Cara Teacher/);
    await section.selectOption('11');
    assert.equal(await dialog.locator('select[id^="a_subject_"] option[value="20"]').isDisabled(), false);
    await section.selectOption('10');
    let subject = dialog.locator('select[id^="a_subject_"]').first();
    assert.equal(await subject.locator('option[value="20"]').isDisabled(), true);
    assert.match(await subject.locator('option[value="20"]').innerText(), /Alice Teacher/);
    assert.equal(await subject.locator('option[value="*"]').count(), 0, 'new teaching assignments do not offer All subjects');
    assert.equal(await dialog.locator('input[type="checkbox"]').count(), 0, 'teaching form does not offer adviser checkbox');
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
    await dialog.locator('select[id^="a_section_"]').selectOption('13');
    await dialog.locator('select[id^="a_subject_"]').selectOption('30');
    await page.getByRole('button', { name: 'Dismiss', exact: true }).click();
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();
    await dialog.waitFor({ state: 'detached' });
    assert.equal(posts.at(-1).subject_id, 30);
    assert.equal(posts.at(-1).section_id, 13);
    assert.equal(posts.at(-1).teacher_id, 'bob');
    const teachingSnapshot = JSON.stringify(assignments);
    await page.getByRole('button', { name: /Section advisers/ }).click();
    await page.getByRole('row').filter({ hasText: 'Faith' }).getByTitle('Edit', { exact: true }).click();
    dialog = page.getByRole('dialog');
    assert.equal(await dialog.locator('#adviser_section').isDisabled(), true, 'editing adviser cannot silently move sections');
    assert.equal(await dialog.locator('select[id^="a_subject_"]').count(), 0, 'adviser form has no teaching subject choice');
    assert.equal(await dialog.locator('#adviser_teacher option[value="pending"], #adviser_teacher option[value="inactive"]').count(), 0);
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => document.documentElement.setAttribute('data-theme', theme), theme);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `adviser modal fits 320px ${theme}`);
    }
    await dialog.locator('#adviser_teacher').selectOption('bob');
    await dialog.getByRole('button', { name: 'Save changes', exact: true }).click();
    await dialog.waitFor({ state: 'detached' });
    assert.deepEqual(adviserWrites.at(-1), { section_id: 10, teacher_id: 'bob' });
    assert.equal(JSON.stringify(assignments), teachingSnapshot, 'replacing adviser preserves all teaching assignments');
    await page.getByRole('row').filter({ hasText: 'Faith' }).getByTitle('Remove', { exact: true }).click();
    dialog = page.getByRole('dialog');
    await dialog.getByText('This removes advisory viewing access.', { exact: false }).waitFor();
    await dialog.getByRole('button', { name: 'Remove', exact: true }).click();
    await dialog.waitFor({ state: 'detached' });
    assert.equal(JSON.stringify(assignments), teachingSnapshot, 'removing adviser preserves all teaching assignments');
    await page.getByRole('button', { name: 'Add section adviser', exact: true }).click();
    dialog = page.getByRole('dialog');
    await dialog.locator('#adviser_section').selectOption('13');
    await dialog.locator('#adviser_teacher').selectOption('cara');
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();
    await dialog.waitFor({ state: 'detached' });
    assert.deepEqual(adviserWrites.at(-1), { section_id: 13, teacher_id: 'cara' });
    assert.equal(JSON.stringify(assignments), teachingSnapshot, 'adding adviser to an occupied class adds no teaching rights');
    await page.getByRole('button', { name: /Subject teachers/ }).click();
    await page.getByRole('row').filter({ hasText: 'Hope' }).getByRole('button', { name: 'Review', exact: true }).click();
    dialog = page.getByRole('dialog');
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => document.documentElement.setAttribute('data-theme', theme), theme);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `legacy review modal fits 320px ${theme}`);
    }
    assert.equal(await dialog.getByRole('button', { name: 'Confirm teaching subjects', exact: true }).isDisabled(), true, 'empty review requires explicit confirmation');
    await dialog.getByLabel('I confirm this older assignment was for advisory access only.', { exact: false }).check();
    await dialog.getByRole('button', { name: 'Confirm teaching subjects', exact: true }).click();
    await dialog.waitFor({ state: 'detached' });
    assert.deepEqual(reviews.at(-1), { id: 2, subject_ids: [] });
    assert(advisers.some(item => item.section_id === 11 && item.teacher_id === 'cara'), 'reviewing adviser-only row preserves independent adviser access');
    await page.getByRole('row').filter({ hasText: 'Grace' }).getByRole('button', { name: 'Review', exact: true }).click();
    dialog = page.getByRole('dialog');
    await dialog.getByLabel('Grade 8 English', { exact: false }).check();
    await dialog.getByLabel('I confirm these 1 teaching subject', { exact: false }).check();
    await dialog.getByRole('button', { name: 'Confirm teaching subjects', exact: true }).click();
    await dialog.waitFor({ state: 'detached' });
    assert.deepEqual(reviews.at(-1), { id: 3, subject_ids: [30] });
    await page.getByRole('row').filter({ hasText: 'Faith' }).filter({ hasText: 'All subjects' }).getByRole('button', { name: 'Review', exact: true }).click();
    dialog = page.getByRole('dialog');
    assert.equal(await dialog.getByText('Advisory viewing access to all subjects will remain.', { exact: false }).count(), 0, 'stale legacy adviser flags do not claim advisory access after separate adviser removal');
    assert.equal(await dialog.getByLabel('Mathematics', { exact: false }).isDisabled(), true, 'review cannot claim another teacher’s subject');
    const english = dialog.getByLabel('English', { exact: false });
    assert.equal(await english.isDisabled(), false, 'review can retain teacher’s existing subject without duplicating it');
    await english.check();
    await dialog.getByLabel('I confirm these 1 teaching subject', { exact: false }).check();
    await dialog.getByRole('button', { name: 'Confirm teaching subjects', exact: true }).click();
    await dialog.waitFor({ state: 'detached' });
    assert.deepEqual(reviews.at(-1), { id: 4, subject_ids: [20] });
    assert.equal(assignments.filter(item => item.section_id === 10 && item.subject_id === 20).length, 1);
    assert.deepEqual(errors, []);
    console.log('PASS: independent adviser add/change/remove, adviser does not reserve subjects, specific teaching ownership, approved teachers only, grade filtering, draft overlap, partial/race recovery, legacy adviser-only and concrete-subject review with explicit confirmation, and 320px dark/light.');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
