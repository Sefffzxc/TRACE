import fs from 'node:fs/promises';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

// Audit only: evaluates unchanged source helpers with fake storage/network.
// It makes no real API calls and writes no application data.
const root = new URL('../', import.meta.url);
const read = (p) => fs.readFile(new URL(p, root), 'utf8');
const source = await read('frontend/src/api/client.js');
const cases = [];
async function client(fetchImpl) {
  const values = new Map([
    ['trace.access_token', 'expired-access'],
    ['trace.refresh_token', 'valid-refresh'],
  ]);
  const events = [];
  const calls = [];
  const context = vm.createContext({
    localStorage: { getItem: k => values.get(k) || null, setItem: (k,v) => values.set(k,v), removeItem: k => values.delete(k) },
    window: { dispatchEvent: e => events.push(e.type) },
    CustomEvent: class { constructor(type) { this.type = type; } },
    URL, FormData, setTimeout,
    fetch: async (url, options) => { calls.push({url, options}); return fetchImpl(url, options); },
  });
  vm.runInContext(source
    .replace('import.meta.env.VITE_API_URL', "'http://audit.invalid'")
    .replace(/export /g, '') + '\nglobalThis.subject = { api, tokens };', context);
  return { ...context.subject, calls, events };
}
const json = (status, payload) => ({ status, ok: status >= 200 && status < 300,
  headers: { get: () => 'application/json' }, json: async () => payload });
{
  const subject = await client(url => url.endsWith('/refresh')
    ? json(200, {access_token:'fresh'}) : json(401, {detail:'Token expired'}));
  await assert.rejects(subject.api.get('/api/auth/me'), /Token expired/);
  assert.equal(subject.calls.length, 1);
  cases.push({name:'Expired auth/me does not try valid refresh token', confirmed:true, requests:subject.calls.map(c=>c.url)});
}
{
  const subject = await client(() => json(403, {detail:'Account deactivated'}));
  await assert.rejects(subject.api.get('/api/students'), /Account deactivated/);
  assert.equal(subject.events.length, 0);
  assert.equal(subject.tokens.access, 'expired-access');
  cases.push({name:'403 does not expire local session or clear tokens', confirmed:true});
}
{
  const subject = await client(() => new Promise(() => {}));
  const outcome = await Promise.race([
    subject.api.get('/api/students').then(() => 'resolved', () => 'rejected'),
    new Promise(resolve => setTimeout(() => resolve('pending'), 50)),
  ]);
  assert.equal(outcome, 'pending');
  assert.equal(subject.calls[0].options.signal, undefined);
  cases.push({name:'Request supplies no AbortSignal and remains pending when network hangs', confirmed:true});
}
{
  const theme = await read('frontend/src/context/ThemeContext.jsx');
  const body = theme.slice(theme.indexOf('function initialTheme()'), theme.indexOf('export function ThemeProvider'));
  const context = vm.createContext({localStorage:{getItem(){throw new Error('SecurityError: storage blocked');}}});
  vm.runInContext("const STORAGE_KEY = 'trace.theme';\n"+body+'\nglobalThis.initialTheme = initialTheme;', context);
  assert.throws(() => context.initialTheme(), /SecurityError/);
  cases.push({name:'Theme initial render throws when browser storage is blocked', confirmed:true});
}
{
  const grading = await import(new URL('frontend/src/lib/grading.js', root));
  const result = grading.computeRow(
    {ww_scores:[10,null],pt_scores:[10],exam_scores:[10,10,10]},
    {template:'core',ww_highest:[10,10],pt_highest:[10],exam_highest:[10,10,10],exam_subweights:[30,30,40]});
  assert.equal(result.complete, true);
  cases.push({name:'Missing written-work score still marks record complete', confirmed:true, result});
}
{
  const require = createRequire(new URL('frontend/package.json', root));
  const React = require('react');
  const {ReferenceLine} = require('recharts');
  const {renderByOrder} = require('recharts/lib/util/ReactUtils');
  function PassingLine(props) { return React.createElement(ReferenceLine, props); }
  let rendered = 0;
  const result = renderByOrder([React.createElement(PassingLine, {y:75})], {ReferenceLine:{handler(){rendered++;}}});
  assert.equal(rendered,0);
  assert.equal(result.length,0);
  cases.push({name:'Installed Recharts discards PassingLine wrapper', confirmed:true});
}
{
  const roster = await read('frontend/src/pages/app/RosterPage.jsx');
  const start = roster.indexOf('const rows = useMemo(') + 'const rows = useMemo('.length;
  const end = roster.indexOf('}, [enrollments, paces, drafts, subject, search, defaultExpected]);', start) + 1;
  const saveStart = roster.indexOf('const save = async () =>');
  const saveEnd = roster.indexOf('\n  if (loading)', saveStart);
  const posted = [];
  const context = vm.createContext({
    enrollments:[{enrollment_id:1,student_id:1,student_name:'Alice'},{enrollment_id:2,student_id:2,student_name:'Bob'}],
    paces:[], drafts:{1:{current:'3'},2:{current:'4'}}, search:'', defaultExpected:6,
    subject:{subject_name:'English',total_paces:12}, unitLabel:'PACE',
    setSaving(){}, setDrafts(next){context.drafts=next;}, loadRoster(){},
    toast:{success(){},warning(){},info(){},error(e){throw new Error(e);}},
    paceApi:{async bulkSave(entries){posted.push(entries);return {saved:entries.length};}},
  });
  vm.runInContext('globalThis.getRows = '+roster.slice(start,end)+';\n'+roster.slice(saveStart,saveEnd)+'\nglobalThis.save = save;',context);
  assert.equal(context.getRows()[0].current,'3');
  context.subject={subject_name:'Science',total_paces:12};
  assert.equal(context.getRows()[0].current,'3');
  context.search='Alice';
  context.rows=context.getRows();
  await context.save();
  assert.equal(posted[0][0].subject,'Science');
  assert.equal(posted[0].length,1);
  assert.equal(Object.keys(context.drafts).length,0);
  cases.push({name:'Roster subject switch moves draft to Science; filtered save clears Bob without posting him',confirmed:true,posted:posted[0]});
}
{
  const source = await read('frontend/src/pages/app/ClassRecordPage.jsx');
  const grading = await import(new URL('frontend/src/lib/grading.js', root));
  const start = source.indexOf('const rows = useMemo(') + 'const rows = useMemo('.length;
  const end = source.indexOf('}, [enrollments, paces, drafts, subject, search, setup, term]);',start)+1;
  const saveStart = source.indexOf('const save = async () =>');
  const saveEnd = source.indexOf('\n  /** Switching record type',saveStart);
  const posted=[];
  const context=vm.createContext({
    enrollments:[{enrollment_id:1,student_id:1,student_name:'Alice'},{enrollment_id:2,student_id:2,student_name:'Bob'}],
    paces:[],drafts:{1:{ww_scores:['3']},2:{ww_scores:['4']}},search:'Alice',term:1,sectionId:'1',subjectId:'1',
    subject:{subject_name:'English'},setup:{template:'core',ww_highest:[10],pt_highest:[10],exam_highest:[10,10,10],exam_subweights:[30,30,40]},
    toScore:raw=>raw===''||raw==null?null:Number(raw),asText:v=>v==null?'':String(v),computeRow:grading.computeRow,
    setSaving(){},setDrafts(next){context.drafts=next;},loadRoster(){},
    toast:{success(){},error(e){throw new Error(e);},info(){}},
    paceApi:{async bulkSave(entries){posted.push(entries);return {saved:entries.length};}},
  });
  vm.runInContext('globalThis.getRows = '+source.slice(start,end)+';\n'+source.slice(saveStart,saveEnd)+'\nglobalThis.save = save;',context);
  context.rows=context.getRows();
  await context.save();
  assert.equal(posted[0].length,1);
  assert.equal(Object.keys(context.drafts).length,0);
  cases.push({name:'Class-record filtered save clears hidden Bob without posting his scores',confirmed:true,posted:posted[0]});
}
console.log(JSON.stringify({date:'2026-10-20',cases}, null, 2));
