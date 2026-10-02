// Offline tests: no network, browser extensions, credentials or production writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script); // Parse the complete browser script, including startup.
const elements = new Map();
const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, {innerHTML:'', textContent:'', style:{}, querySelectorAll:()=>[]});
    return elements.get(id);
  },
  querySelectorAll: () => [],
};
const context = vm.createContext({URL, document, console});
vm.runInContext(script.split('(async () => {')[0], context);
const run = expression => vm.runInContext(expression, context);
let count = 0;
function test(name, fn) { fn(); count++; console.log('ok:', name); }

const at = '2026-09-05T00:00:00Z';
const now = "Date.parse('2026-09-05T00:01:00Z')";
const status = () => ({schema_version:1,room:'lobby',last_attempt_at:at,
  collection_started_at:at,collection_completed_at:at,last_attempt_outcome:'succeeded',
  last_successful_collection_at:at,max_age_seconds:7200});
const snapshot = () => ({room:'lobby',fetch_status:'ok',generated_at:at,
  collection_completed_at:at,messages:[{seq:1,ts:at,from:'a',text:'b'}]});
const state = (s, d, clock = now) => {
  context.inputStatus = s; context.inputSnapshot = d;
  return run(`evidenceState(inputStatus, inputSnapshot, ${clock})[0]`);
};

test('Successful complete collection is FRESH', () => {
  assert.equal(state(status(), snapshot()), 'FRESH');
});
test('Failed collection after success leaves historical rows visibly FAILED', () => {
  const failed = {...status(),last_attempt_outcome:'failed',last_attempt_at:'2026-09-05T00:01:00Z'};
  assert.equal(state(failed, snapshot()), 'FAILED');
  assert.match(run(`freshness(inputSnapshot, inputSnapshot.messages, ${now}, inputStatus)`),
    /FAILED:.*historical evidence, not current status/);
  run('STATUS = inputStatus; SNAPSHOT = inputSnapshot; ALL = normalizeRows(inputSnapshot); render(); updateHealth()');
  assert.match(elements.get('list').innerHTML, /class="text">b</);
  assert.match(elements.get('health').textContent, /FAILED:.*historical evidence/);
});
test('Missed collection ages success into STALE', () => {
  assert.equal(state(status(), snapshot(), "Date.parse('2026-09-05T03:00:00Z')"), 'STALE');
});
test('Old message time stays separate from a fresh fetch', () => {
  const old = {...snapshot(),messages:[{seq:1,ts:'2026-09-03T00:00:00Z',from:'a',text:'b'}]};
  context.inputStatus = status(); context.inputSnapshot = old;
  assert.equal(state(status(), old), 'FRESH');
  assert.match(run(`freshness(inputSnapshot, inputSnapshot.messages, ${now}, inputStatus)`),
    /Retained messages are older than the collection freshness window/);
});
test('First-ever failure and missing status cannot be FRESH', () => {
  assert.equal(state({...status(),last_attempt_outcome:'failed',last_successful_collection_at:null}, null), 'FAILED');
  assert.equal(state(null, snapshot()), 'UNKNOWN');
  assert.equal(state({schema_version:1}, snapshot()), 'UNKNOWN');
});
test('Malformed or incomplete evidence fails closed', () => {
  assert.equal(state(status(), {...snapshot(),fetch_status:'partial'}), 'FAILED');
  assert.equal(state(status(), {...snapshot(),collection_completed_at:'2026-09-04T00:00:00Z'}), 'FAILED');
  assert.equal(state({...status(),last_attempt_at:null}, snapshot()), 'UNKNOWN');
  assert.equal(state(status(), null), 'FAILED');
});
test('Recovery after failure becomes FRESH only with matching evidence', () => {
  const failed = {...status(),last_attempt_outcome:'failed'};
  assert.equal(state(failed, snapshot()), 'FAILED');
  assert.equal(state(status(), snapshot()), 'FRESH');
});
test('Running process does not prove fresh evidence', () => {
  assert.equal(state({...status(),last_attempt_outcome:'running'}, snapshot()), 'UNKNOWN');
  assert.equal(state({...status(),last_attempt_outcome:'running'}, snapshot(),
    "Date.parse('2026-09-05T03:00:00Z')"), 'STALE');
});
test('Both proof markers are unverified heuristics', () => {
  for (const text of ['technocore-proof-v1', 'technocore-contribution-proof-v1']) {
    context.input = {text};
    assert.equal(run('classify(input).kind'), 'proof');
  }
  assert.match(html, /no cryptographic verification of message signatures/);
});
test('Malformed records rejected and valid records sorted', () => {
  assert.throws(() => run('normalizeRows({messages:[null]})'));
  assert.throws(() => run('normalizeRows({})'));
  context.input = {messages:[{seq:2,ts:'',from:'a',text:'b'},{seq:1,ts:'',from:'c',text:'d'}]};
  assert.equal(run('normalizeRows(input)[0].seq'), 1);
});
test('All sender strings count as senders, never verified DIDs', () => {
  assert.equal(run("stats([{from:'alice',ts:'',c:{urls:[],kind:'other'}}])[1][0]"), 'distinct senders');
  assert.equal(run('filter'), 'all');
});
test('Injected markup escaped; known platforms get no trust exemption', () => {
  context.input = {messages:[{seq:1,ts:'2026-09-05T00:00:00Z',
    from:'<img src=x onerror=alert(1)>',
    text:'technocore-proof-v1 <script>alert(1)</script> https://github.com/example'}]};
  run('ALL = normalizeRows(input); render()');
  const rendered = elements.get('list').innerHTML;
  assert.ok(!rendered.includes('<script>'));
  assert.ok(!rendered.includes('<img'));
  assert.ok(!rendered.includes('<a '));
  assert.match(rendered, /unverified · github.com/);
  assert.match(rendered, /proof text · not crypto-verified/);
  assert.match(rendered, /sender unverified/);
  assert.ok(!html.includes('KNOWN_GOOD'));
});
test('Invalid and timezone-less timestamps are unknown', () => {
  assert.equal(run("timestamp('not a date')"), null);
  assert.equal(run("timestamp('2026-09-05T00:00:00')"), null);
});
test('Scope and official alternatives are explicit', () => {
  assert.match(html, /partial snapshot, not Technocore's full retained ring/);
  assert.match(html, /https:\/\/technocore\.chat\/humans/);
  assert.match(html, /\/r\/.+\/export/);
  assert.match(html, /collection_scope === 'bounded-retained-sample'/);
});
(async () => {
  context.inputStatus = status();
  context.inputSnapshot = snapshot();
  run('STATUS = inputStatus; SNAPSHOT = inputSnapshot; ALL = normalizeRows(inputSnapshot)');
  context.fetch = async () => ({ok:false});
  await run('refreshStatus()');
  assert.equal(run('STATUS'), null);
  assert.match(elements.get('health').textContent, /^UNKNOWN:/);
  context.fetch = async () => ({ok:true,json:async () => ({...status(),last_attempt_outcome:'failed'})});
  await run('refreshStatus()');
  assert.match(elements.get('health').textContent, /^FAILED:/);
  count++;
  console.log('ok: Open viewer refreshes attempt status and fails closed on status errors');
  console.log(`${count} viewer test groups passed.`);
})().catch(error => { console.error(error); process.exitCode = 1; });
