// The one front door of the intake system. Two lanes sit behind it and the Worker decides which one a body belongs in:
//   POST /ingest      contact lane: a lead from a Zap (name, email, message) is stored in KV. x-chain-key.
//   POST /admission   admission lane: forwarded to the admission-intake Logic App, which writes both systems. x-intake-key.
//   GET|DELETE /records   read back or clear the contact lane (tests). x-chain-key.
// The two lanes must never disagree about what a clinical identifier is, so the contact lane refuses any body that looks like an admission
// or carries one (422), and test/lanes.test.mjs fails if this list drifts from the list the Logic App refuses.

// Fields only an admission carries: a body with any of them belongs on /admission.
export const ADMISSION_FIELDS = ['admissionId', 'facilityId', 'residentRef', 'admitDate', 'careLevel'];
// Identifiers that must never land in the contact lane's store.
export const CLINICAL_IDENTIFIERS = ['residentName', 'patientName', 'ssn', 'dob', 'dateOfBirth'];
// Identifiers the Logic App also refuses that are ordinary fields on a contact lead: allowed here, refused there.
export const CONTACT_OVERLAP = ['name', 'phone', 'address'];

const present = (body, field) => body[field] !== undefined && body[field] !== null && body[field] !== '';

export function classify(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return { lane: null, reason: 'bad_json' };
  const clinical = [...ADMISSION_FIELDS, ...CLINICAL_IDENTIFIERS].filter((f) => present(body, f));
  return clinical.length ? { lane: 'admission', fields: clinical } : { lane: 'contact', fields: [] };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === 'POST' && url.pathname === '/admission') return admission(request, env);

    if (request.headers.get('x-chain-key') !== env.CHAIN_KEY) {
      return json({ error: 'unauthorized' }, 401);
    }
    if (request.method === 'POST' && url.pathname === '/ingest') {
      let body;
      try { body = await request.json(); } catch { return json({ error: 'bad_json' }, 400); }
      const verdict = classify(body);
      if (!verdict.lane) return json({ error: 'bad_json' }, 400);
      if (verdict.lane === 'admission') {
        return json({ error: 'admission_data_on_contact_lane', message: 'Send admissions to /admission. The contact lane stores nothing that looks clinical.', fields: verdict.fields }, 422);
      }
      const id = crypto.randomUUID();
      const record = { id, receivedAt: new Date().toISOString(), source: request.headers.get('user-agent') || '', data: body };
      await env.RECORDS.put(`rec:${record.receivedAt}:${id}`, JSON.stringify(record), { expirationTtl: 60 * 60 * 24 * 7 });
      return json({ stored: true, id }, 201);
    }
    if (request.method === 'GET' && url.pathname === '/records') {
      const list = await env.RECORDS.list({ prefix: 'rec:' });
      const records = [];
      for (const k of list.keys) {
        const raw = await env.RECORDS.get(k.name);   // KV listings lag behind deletes, so a listed key can already be gone
        if (raw) records.push(JSON.parse(raw));
      }
      return json({ count: records.length, records });
    }
    if (request.method === 'DELETE' && url.pathname === '/records') {
      const list = await env.RECORDS.list({ prefix: 'rec:' });
      for (const k of list.keys) await env.RECORDS.delete(k.name);
      return json({ cleared: list.keys.length });
    }
    return json({ error: 'not_found' }, 404);
  },
};

// The Logic App still checks the key and every identifier rule itself; the Worker only refuses early what it can see at the edge and passes
// the Logic App's own answer (202, 400, 401, 422, 502) back unchanged, so a caller sees one behaviour whichever way it came in.
async function admission(request, env) {
  if (!env.ADMISSION_URL || !env.INTAKE_KEY) return json({ error: 'lane_not_configured' }, 503);
  if (request.headers.get('x-intake-key') !== env.INTAKE_KEY) return json({ error: 'unauthorized', message: 'Missing or wrong x-intake-key.' }, 401);
  const raw = await request.text();
  let parsed;
  try { parsed = JSON.parse(raw); } catch { return json({ error: 'bad_json' }, 400); }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return json({ error: 'bad_json' }, 400);
  let upstream;
  try {
    upstream = await fetch(env.ADMISSION_URL, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-intake-key': env.INTAKE_KEY },
      body: raw,
      signal: AbortSignal.timeout(Number(env.ADMISSION_TIMEOUT_MS) || 60000),   // the Logic App retries a downstream three times, five seconds apart
    });
  } catch {
    return json({ status: 'unreachable', message: 'The admission workflow did not answer.' }, 502);
  }
  return new Response(await upstream.text(), { status: upstream.status, headers: { 'content-type': upstream.headers.get('content-type') || 'application/json' } });
}

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'content-type': 'application/json' } });
}
