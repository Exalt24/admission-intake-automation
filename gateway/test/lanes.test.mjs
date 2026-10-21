// Unit tests for the gateway Worker: no network, no runtime. A fake KV and a fake fetch stand in for Cloudflare and the Logic App.
// The last group reads the real workflow.json so the Worker and the Logic App cannot drift apart on what an identifier is.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import worker, { classify, ADMISSION_FIELDS, CLINICAL_IDENTIFIERS, CONTACT_OVERLAP } from '../worker.js';

const here = path.dirname(fileURLToPath(import.meta.url));

function fakeKV() {
  const store = new Map();
  return {
    store,
    async put(k, v) { store.set(k, v); },
    async get(k) { return store.get(k) ?? null; },
    async delete(k) { store.delete(k); },
    async list({ prefix }) { return { keys: [...store.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })) }; },
  };
}

const baseEnv = () => ({ CHAIN_KEY: 'chain', INTAKE_KEY: 'intake', ADMISSION_URL: 'http://logic.app/trigger', RECORDS: fakeKV() });
const post = (p, body, headers = {}, raw) => new Request(`https://gw.test${p}`, { method: 'POST', headers: { 'content-type': 'application/json', ...headers }, body: raw ?? JSON.stringify(body) });
const admissionBody = { admissionId: 'ADM-1', facilityId: 'FAC-07', residentRef: 'R-1', admitDate: '2026-10-05', careLevel: 'memory-care' };

test('a contact lead is stored in the contact lane', async () => {
  const env = baseEnv();
  const res = await worker.fetch(post('/ingest', { name: 'Ada', email: 'ada@example.com', message: 'hi', phone: '555-0100' }, { 'x-chain-key': 'chain' }), env);
  assert.equal(res.status, 201);
  assert.equal(env.RECORDS.store.size, 1);
});

test('the contact lane refuses an admission and stores nothing', async () => {
  const env = baseEnv();
  const res = await worker.fetch(post('/ingest', admissionBody, { 'x-chain-key': 'chain' }), env);
  assert.equal(res.status, 422);
  assert.equal((await res.json()).error, 'admission_data_on_contact_lane');
  assert.equal(env.RECORDS.store.size, 0);
});

test('the contact lane refuses each clinical identifier on its own, whatever else the body holds', async () => {
  for (const field of CLINICAL_IDENTIFIERS) {
    const env = baseEnv();
    const res = await worker.fetch(post('/ingest', { name: 'Ada', email: 'a@b.c', [field]: 'x' }, { 'x-chain-key': 'chain' }), env);
    assert.equal(res.status, 422, field);
    assert.equal(env.RECORDS.store.size, 0, field);
  }
});

test('an empty clinical field does not misroute an ordinary lead', () => {
  assert.equal(classify({ name: 'Ada', ssn: '', dob: null }).lane, 'contact');
});

test('the contact lane still needs its key, and a bad body is a 400', async () => {
  const env = baseEnv();
  assert.equal((await worker.fetch(post('/ingest', { name: 'a' }, { 'x-chain-key': 'nope' }), env)).status, 401);
  assert.equal((await worker.fetch(post('/ingest', null, { 'x-chain-key': 'chain' }, 'not json'), env)).status, 400);
  assert.equal((await worker.fetch(post('/ingest', [1, 2], { 'x-chain-key': 'chain' }), env)).status, 400);
});

test('the admission lane refuses an unknown caller at the edge and never reaches the Logic App', async () => {
  let calls = 0;
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => { calls += 1; return new Response('{}'); };
  try {
    const env = baseEnv();
    assert.equal((await worker.fetch(post('/admission', admissionBody, { 'x-intake-key': 'wrong' }), env)).status, 401);
    assert.equal((await worker.fetch(post('/admission', admissionBody), env)).status, 401);
    assert.equal(calls, 0);
  } finally { globalThis.fetch = realFetch; }
});

test('the admission lane forwards the body and returns the Logic App answer unchanged', async () => {
  const realFetch = globalThis.fetch;
  let seen;
  globalThis.fetch = async (url, init) => { seen = { url, init }; return new Response(JSON.stringify({ status: 'accepted', trackingId: 't' }), { status: 202, headers: { 'content-type': 'application/json' } }); };
  try {
    const res = await worker.fetch(post('/admission', admissionBody, { 'x-intake-key': 'intake' }), baseEnv());
    assert.equal(res.status, 202);
    assert.deepEqual(await res.json(), { status: 'accepted', trackingId: 't' });
    assert.equal(seen.url, 'http://logic.app/trigger');
    assert.equal(seen.init.headers['x-intake-key'], 'intake');
    assert.deepEqual(JSON.parse(seen.init.body), admissionBody);
  } finally { globalThis.fetch = realFetch; }
});

test('every status the Logic App can answer with passes through, and an unreachable Logic App is a 502', async () => {
  const realFetch = globalThis.fetch;
  try {
    for (const status of [400, 401, 422, 502]) {
      globalThis.fetch = async () => new Response(JSON.stringify({ n: status }), { status });
      assert.equal((await worker.fetch(post('/admission', admissionBody, { 'x-intake-key': 'intake' }), baseEnv())).status, status);
    }
    globalThis.fetch = async () => { throw new TypeError('network down'); };
    const res = await worker.fetch(post('/admission', admissionBody, { 'x-intake-key': 'intake' }), baseEnv());
    assert.equal(res.status, 502);
    assert.equal((await res.json()).status, 'unreachable');
  } finally { globalThis.fetch = realFetch; }
});

test('the admission lane says so when it is not configured', async () => {
  const env = baseEnv();
  delete env.ADMISSION_URL;
  assert.equal((await worker.fetch(post('/admission', admissionBody, { 'x-intake-key': 'intake' }), env)).status, 503);
});

test('records can be read back and cleared', async () => {
  const env = baseEnv();
  await worker.fetch(post('/ingest', { name: 'Ada' }, { 'x-chain-key': 'chain' }), env);
  const read = await worker.fetch(new Request('https://gw.test/records', { headers: { 'x-chain-key': 'chain' } }), env);
  assert.equal((await read.json()).count, 1);
  const del = await worker.fetch(new Request('https://gw.test/records', { method: 'DELETE', headers: { 'x-chain-key': 'chain' } }), env);
  assert.equal((await del.json()).cleared, 1);
});

// The two lanes share one definition of an identifier. These read the workflow the Logic Apps runtime really runs.
const workflow = JSON.parse(readFileSync(path.join(here, '..', '..', 'logicapp', 'admission-intake', 'workflow.json'), 'utf8'));
const logicAppRefuses = (() => {
  const text = JSON.stringify(workflow.definition.actions.Check_caller.actions.Check_for_direct_identifiers.expression);
  return [...text.matchAll(/triggerBody\(\)\?\[\\?'([A-Za-z]+)\\?'\]/g)].map((m) => m[1]);
})();
const logicAppRequires = (() => {
  const text = JSON.stringify(workflow.definition.actions.Check_caller.actions.Check_for_direct_identifiers.else.actions.Validate_payload.expression);
  return [...text.matchAll(/triggerBody\(\)\?\[\\?'([A-Za-z]+)\\?'\]/g)].map((m) => m[1]);
})();

test('the Logic App refuses exactly the identifiers the Worker classifies (a new one must be classified here)', () => {
  assert.ok(logicAppRefuses.length >= 8, `read ${logicAppRefuses.length} fields from workflow.json`);
  assert.deepEqual([...logicAppRefuses].sort(), [...CLINICAL_IDENTIFIERS, ...CONTACT_OVERLAP].sort());
});

test('the Logic App requires exactly the fields the Worker treats as an admission', () => {
  assert.deepEqual([...logicAppRequires].sort(), [...ADMISSION_FIELDS].sort());
});
