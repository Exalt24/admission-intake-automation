// The "system of record" at the end of the chain: a Zap POSTs a lead here, it is stored in KV, and GET /records reads them back so a test can
// prove the record really arrived. Both routes need the shared key in x-chain-key (a Worker secret, never in the repo).
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.headers.get('x-chain-key') !== env.CHAIN_KEY) {
      return json({ error: 'unauthorized' }, 401);
    }
    if (request.method === 'POST' && url.pathname === '/ingest') {
      let body;
      try { body = await request.json(); } catch { return json({ error: 'bad_json' }, 400); }
      if (!body || typeof body !== 'object') return json({ error: 'bad_json' }, 400);
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

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'content-type': 'application/json' } });
}
