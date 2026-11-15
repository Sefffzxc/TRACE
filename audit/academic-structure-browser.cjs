/* Run after npm run build: node audit/academic-structure-browser.cjs
   Uses the existing audit Playwright installation, a local static server,
   and synthetic API responses. Never connects to the application database. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('./runtime/node_modules/playwright');

const dist = path.resolve(__dirname, '../frontend/dist');
const mime = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.json': 'application/json' };
const server = http.createServer((request, response) => {
  let filename = path.resolve(dist, '.' + new URL(request.url, 'http://localhost').pathname);
  if (!filename.startsWith(dist + path.sep) && filename !== dist) {
    response.writeHead(403); response.end(); return;
  }
  if (!fs.existsSync(filename) || fs.statSync(filename).isDirectory()) filename = path.join(dist, 'index.html');
  response.writeHead(200, { 'Content-Type': mime[path.extname(filename)] || 'application/octet-stream' });
  fs.createReadStream(filename).pipe(response);
});

const user = { id: '00000000-0000-4000-8000-000000000001', role: 'admin', school_id: 1, first_name: 'Test', last_name: 'Principal', email: 'principal@example.com', is_active: true, is_approved: true, account_status: 'approved' };
const data = {
  levels: [{ grade_level_id: 1, level: '7', name: 'Grade 7', section_count: 1, student_count: 12 }],
  sections: [{ section_id: 10, grade_level_id: 1, name: 'Faith', capacity: 40, enrolled_count: 12, adviser_name: 'Test Teacher', grade_level_name: 'Grade 7' }],
  subjects: [{ subject_id: 20, grade_level_id: 1, subject_name: 'English', grade_level_name: 'Grade 7', total_paces: 0, is_active: true }],
};
const mutations = [], errors = [];
let identity = 100, failNextSave = false;

async function main() {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--no-sandbox', '--disable-gpu-sandbox'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.addInitScript(({ user }) => {
      localStorage.setItem('trace.access_token', 'synthetic-browser-test-token');
      localStorage.setItem('trace.user', JSON.stringify(user));
      localStorage.setItem('trace.theme', 'light');
    }, { user });
    await context.route('**/api/**', async (route) => {
      const request = route.request(), url = new URL(request.url()), method = request.method(), pathname = url.pathname;
      const reply = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', headers: { 'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Headers': 'Authorization,Content-Type', 'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS' }, body: JSON.stringify(body) });
      if (method === 'OPTIONS') return reply({});
      if (method !== 'GET') {
        const body = request.postDataJSON();
        mutations.push({ method, pathname, body });
        if (pathname.includes('grade-levels')) {
          if (failNextSave) { failNextSave = false; return reply({ detail: 'Cannot remove a section with enrollment records.' }, 409); }
          const id = method === 'POST' ? ++identity : Number(pathname.match(/grade-levels\/(\d+)/)[1]);
          const grade = { grade_level_id: id, level: body.level, name: body.name, section_count: body.sections.length, student_count: 0 };
          data.levels = [...data.levels.filter((row) => row.grade_level_id !== id), grade];
          const existing = data.sections;
          data.sections = [...existing.filter((row) => row.grade_level_id !== id), ...body.sections.map((section) => ({
            ...(existing.find((row) => row.section_id === section.section_id) || { enrolled_count: 0, adviser_name: null }),
            ...section, section_id: section.section_id || ++identity, grade_level_id: id, grade_level_name: body.name,
          }))];
          return reply(grade, method === 'POST' ? 201 : 200);
        }
        if (pathname === '/api/subjects') {
          const row = { ...body, subject_id: ++identity, grade_level_name: data.levels.find((grade) => grade.grade_level_id === body.grade_level_id).name };
          data.subjects.push(row);
          return reply(row, 201);
        }
        return reply({});
      }
      if (pathname === '/api/auth/me' || pathname === '/api/staff/me') return reply(user);
      if (pathname === '/api/schools/me') return reply({ school_id: 1, name: 'Synthetic Test School', tracks_units: false, passing_mark: 75 });
      if (pathname === '/api/grade-levels') return reply(data.levels);
      if (pathname === '/api/sections') return reply(data.sections);
      if (pathname === '/api/subjects') return reply(data.subjects);
      if (pathname === '/api/school-years/current') return reply({ year: '2026-2027' });
      if (pathname === '/api/school-years') return reply([{ school_year_id: 1, year: '2026-2027', is_current: true }]);
      return reply([]);
    });
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(origin + '/class-management?tab=sections');
    await page.getByRole('button', { name: 'Add grade & sections', exact: true }).waitFor();
    await page.getByRole('region', { name: 'Grade 7', exact: true }).waitFor();
    assert.equal(await page.locator('.setup-nav button.is-active').innerText(), 'Grades & sections\n1');
    assert.equal(await page.locator('.setup-nav button').filter({ hasText: /^Sections$/ }).count(), 0);

    await page.getByRole('button', { name: 'Add grade & sections', exact: true }).click();
    let dialog = page.getByRole('dialog');
    await dialog.locator('#grade-level').fill('9');
    await dialog.locator('#grade-name').fill('Grade 9');
    await dialog.locator('.grade-section-row input[type="text"], .grade-section-row input:not([type])').first().fill('Faith');
    await dialog.getByRole('button', { name: 'Add section', exact: true }).click();
    await dialog.locator('.grade-section-row').nth(1).locator('input').first().fill('Hope');
    const gradeTop = await dialog.locator('#grade-level').boundingBox();
    const sectionsTop = await dialog.locator('.grade-section-row').first().boundingBox();
    assert(gradeTop.y < sectionsTop.y, 'Grade fields must be above the section fields.');
    assert(!/section code|subject code/i.test(await dialog.innerText()));
    await page.screenshot({ path: path.join(__dirname, 'grades-sections-modal-desktop.png'), fullPage: true });
    await dialog.getByRole('button', { name: 'Create grade & sections', exact: true }).click();
    await dialog.waitFor({ state: 'detached' });
    const created = mutations.find((entry) => entry.pathname === '/api/grade-levels/with-sections');
    assert.deepEqual(created.body, { level: '9', name: 'Grade 9', sections: [{ name: 'Faith', capacity: 40 }, { name: 'Hope', capacity: 40 }] });
    const gradeNine = page.getByRole('region', { name: 'Grade 9', exact: true });
    await gradeNine.getByText('Hope', { exact: true }).waitFor();
    const savedFaith = data.sections.find((row) => row.grade_level_name === 'Grade 9' && row.name === 'Faith').section_id;

    await gradeNine.getByRole('button', { name: 'Edit grade & sections', exact: true }).click();
    dialog = page.getByRole('dialog');
    await dialog.locator('.grade-section-row').first().locator('input').first().fill('Peace');
    await dialog.getByRole('button', { name: 'Remove section 2', exact: true }).click();
    await dialog.getByRole('button', { name: 'Add section', exact: true }).click();
    await dialog.locator('.grade-section-row').nth(1).locator('input').first().fill('Grace');
    await page.setViewportSize({ width: 375, height: 900 });
    await page.screenshot({ path: path.join(__dirname, 'grades-sections-modal-mobile.png'), fullPage: true });
    const layout = await page.evaluate(() => ({
      width: innerWidth, document: document.documentElement.scrollWidth,
      overflow: [...document.querySelectorAll('body *')].map((element) => ({
        tag: element.tagName, class: element.className?.baseVal ?? element.className,
        right: Math.round(element.getBoundingClientRect().right),
      })).filter((element) => element.right > innerWidth),
    }));
    assert(layout.document <= layout.width, `The mobile page must not overflow horizontally: ${JSON.stringify(layout)}`);
    await dialog.getByRole('button', { name: 'Save changes', exact: true }).click();
    await dialog.waitFor({ state: 'detached' });
    const edit = mutations.find((entry) => entry.method === 'PATCH');
    assert.equal(edit.body.sections[0].section_id, savedFaith);
    assert.deepEqual(edit.body.sections.map((section) => section.name), ['Peace', 'Grace']);

    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('.setup-nav').getByRole('button', { name: /Subjects/ }).click();
    await page.getByRole('button', { name: 'Add subject', exact: true }).click();
    dialog = page.getByRole('dialog');
    assert.equal(await dialog.locator('#sub_code').count(), 0);
    await dialog.locator('#sub_grade').selectOption('1');
    await dialog.locator('#sub_name').fill('Mathematics');
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();
    await dialog.waitFor({ state: 'detached' });
    assert(!JSON.stringify(mutations).includes('subject_code'));
    assert(!JSON.stringify(mutations).includes('section_code'));

    await page.locator('.setup-nav').getByRole('button', { name: /Grades & sections/ }).click();
    await page.getByRole('region', { name: 'Grade 7', exact: true }).getByRole('button', { name: 'Edit grade & sections', exact: true }).click();
    dialog = page.getByRole('dialog');
    assert(await dialog.getByRole('button', { name: 'Remove section 1', exact: true }).isDisabled());
    await dialog.locator('#grade-name').fill('Draft grade name');
    failNextSave = true;
    await dialog.getByRole('button', { name: 'Save changes', exact: true }).click();
    await page.getByText('Cannot remove a section with enrollment records.', { exact: true }).waitFor();
    assert(await dialog.isVisible(), 'A failed save must keep the modal open.');
    assert.equal(await dialog.locator('#grade-name').inputValue(), 'Draft grade name');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, checks: ['legacy section link', 'combined navigation', 'grade fields above sections', 'atomic create payload', 'edit preserves IDs', 'add/remove rows', 'mobile layout', 'subject without code', 'protected sections', 'failed save preserves draft'], mutations: mutations.length, browserErrors: errors }));
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; }).finally(() => server.close());
