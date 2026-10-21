// End-to-end check of the gateway against the REAL Logic Apps runtime and the two mock systems (same setup as tests/e2e_workflow.py):
//   python mocks/mock_systems.py ; azurite --silent --location .azurite ; (cd logicapp && func start --port 7071)
//   node tests/e2e_gateway.mjs
// The Worker module runs in Node with a fake KV; everything behind /admission is the real runtime.
import worker from '../gateway/worker.js';

const RUNTIME = 'http://localhost:7071';
const WORKFLOW = 'admission-intake';
const TRIGGER = 'When_an_admission_is_received';
const KEY = 'local-dev-key-change-me';
const A = 'http://127.0.0.1:7801', B = 'http://127.0.0.1:7802', DEAD = 'http://127.0.0.1:7803';
const failures = [];

const j = async (url, init) => { const r = await fetch(url, init); const t = await r.text(); return { status: r.status, body: t ? JSON.parse(t) : null }; };
const state = async (base) => (await j(`${base}/_state`)).body;
const reset = async () => { for (const b of [A, B, DEAD]) await j(`${b}/_reset`, { method: 'POST', body: '{}' }); };
const check = (name, ok, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : `   <- ${detail}`}`); if (!ok) failures.push(name); };

function kv() {
  const m = new Map();
  return { m, put: async (k, v) => m.set(k, v), get: async (k) => m.get(k) ?? null, delete: async (k) => m.delete(k), list: async ({ prefix }) => ({ keys: [...m.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })) }) };
}

async function callbackUrl() {
  const url = `${RUNTIME}/runtime/webhooks/workflow/api/management/workflows/${WORKFLOW}/triggers/${TRIGGER}/listCallbackUrl?api-version=2020-05-01-preview`;
  const deadline = Date.now() + 60000;
  for (;;) {
    try {
      const r = await j(url, { method: 'POST', body: '{}', headers: { 'content-type': 'application/json' } });
      if (r.status === 200) return r.body.value;
    } catch { /* the runtime is still starting */ }
    if (Date.now() > deadline) throw new Error('the Logic Apps runtime did not come up within 60 seconds');
    await new Promise((r) => setTimeout(r, 100));   // FAST-OK: the poll interval of a loop that checks the real condition (the runtime answers), capped at 60 s
  }
}

const ADMISSION = (n) => ({ admissionId: `GW-${n}`, facilityId: 'FAC-07', residentRef: `R-${n}`, admitDate: '2026-10-05', careLevel: 'memory-care' });
const env = (url, store) => ({ CHAIN_KEY: 'chain', INTAKE_KEY: KEY, ADMISSION_URL: url, RECORDS: store });
const req = (path, body, headers = {}) => new Request(`https://gw.test${path}`, { method: 'POST', headers: { 'content-type': 'application/json', ...headers }, body: JSON.stringify(body) });

const url = await callbackUrl();
const store = kv();
const E = env(url, store);

await reset();
let res = await worker.fetch(req('/admission', ADMISSION('1'), { 'x-intake-key': KEY }), E);
let body = await res.json();
check('an admission through the gateway is accepted by the real workflow with 202', res.status === 202 && body.status === 'accepted', `${res.status} ${JSON.stringify(body)}`);
check('both systems received it exactly once', (await state(A)).records.length === 1 && (await state(B)).records.length === 1);
check('the tracking id the caller got is the one stored', (await state(A)).records[0]?.trackingId === body.trackingId);

await reset();
res = await worker.fetch(req('/admission', ADMISSION('2'), { 'x-intake-key': 'wrong' }), E);
check('a wrong key is refused at the edge with 401', res.status === 401, res.status);
check('the refused call reached neither system', (await state(A)).records.length === 0 && (await state(B)).records.length === 0);

await reset();
res = await worker.fetch(req('/admission', { ...ADMISSION('3'), ssn: '123-45-6789' }, { 'x-intake-key': KEY }), E);
body = await res.json();
check('an identifier sent to the admission lane gets the Logic App 422 through the gateway', res.status === 422 && body.error === 'direct_identifiers_not_accepted', `${res.status} ${JSON.stringify(body)}`);
check('the refused identifier is stored nowhere', !JSON.stringify([await state(A), await state(B), await state(DEAD), [...store.m.values()]]).includes('123-45-6789'));

await reset();
res = await worker.fetch(req('/admission', { admissionId: 'GW-4' }, { 'x-intake-key': KEY }), E);
check('a half-filled admission gets the Logic App 400 through the gateway', res.status === 400, res.status);

await reset();
res = await worker.fetch(req('/ingest', { name: 'Ada Lovelace', email: 'ada@example.com', message: 'Tour on Friday?' }, { 'x-chain-key': 'chain' }), E);
check('a contact lead goes to the contact lane and is stored there', res.status === 201 && store.m.size === 1, res.status);
check('a contact lead never reaches the admission systems', (await state(A)).records.length === 0 && (await state(B)).records.length === 0);

const before = store.m.size;
await reset();
res = await worker.fetch(req('/ingest', { ...ADMISSION('5'), name: 'Jane Doe' }, { 'x-chain-key': 'chain' }), E);
check('an admission sent to the contact lane is refused with 422', res.status === 422, res.status);
check('and it is stored in neither the contact lane nor the admission systems', store.m.size === before && (await state(A)).records.length === 0 && (await state(B)).records.length === 0);

await reset();
await j(`${A}/_fail?n=50`, { method: 'POST', body: '{}' });
res = await worker.fetch(req('/admission', ADMISSION('6'), { 'x-intake-key': KEY }), E);
body = await res.json();
check('a downstream that stays down comes back through the gateway as 502 held', res.status === 502 && body.status === 'held', `${res.status} ${JSON.stringify(body)}`);
check('the held admission is in the dead-letter store', JSON.stringify(await state(DEAD)).includes('GW-6'));
await reset();

console.log();
if (failures.length) { console.log(`${failures.length} FAILED: ${failures.join('; ')}`); process.exit(1); }
console.log('all checks passed');
