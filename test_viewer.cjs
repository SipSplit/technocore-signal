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

test('Legacy creation timestamp never proves a successful fetch', () => {
  const result = run("freshness({generated_at:'2026-09-05T00:00:00Z'}, [], Date.parse('2026-09-05T00:01:00Z'))");
  assert.match(result, /Last successful fetch unknown/);
});
test('Stale, partial and future fetches are explicit', () => {
  for (const [stamp, status, expected] of [
    ['2026-09-04T00:00:00Z','ok',/Stale fetch/],
    ['2026-09-05T00:00:00Z','partial',/Partial fetch/],
    ['2026-09-06T00:00:00Z','ok',/future/],
  ]) {
    context.input = {last_successful_fetch_at:stamp, fetch_status:status};
    assert.match(run("freshness(input, [], Date.parse('2026-09-05T00:01:00Z'))"), expected);
  }
});
test('Message time stays separate from successful fetch time', () => {
  assert.match(run("freshness({fetch_status:'ok',last_successful_fetch_at:'2026-09-05T00:00:00Z'}, [{ts:'2026-09-03T00:00:00Z'}],Date.parse('2026-09-05T00:01:00Z'))"), /does not prove an outage/);
});
test('Both proof markers are unverified heuristics', () => {
  for (const text of ['technocore-proof-v1', 'technocore-contribution-proof-v1']) {
    context.input = {text};
    assert.equal(run('classify(input).kind'), 'proof');
  }
  assert.match(html, /does not verify message signatures/);
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
  assert.match(rendered, /proof text · unverified/);
  assert.ok(!html.includes('KNOWN_GOOD'));
});
test('Invalid and timezone-less timestamps are unknown', () => {
  assert.equal(run("timestamp('not a date')"), null);
  assert.equal(run("timestamp('2026-09-05T00:00:00')"), null);
});
console.log(`${count} viewer test groups passed.`);
