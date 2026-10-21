// Records the README hero clip: the real Zap history screenshot (docs/zap-history.png), then the real output of scripts/e2e_check.py replayed line by line
// (captured to demo/e2e_output.txt). Nothing on screen is invented. Usage: node demo/record.cjs <out_dir>
const fs = require('fs');
const path = require('path');
const { chromium } = require('C:/Users/' + process.env.USERNAME + '/AppData/Roaming/npm/node_modules/playwright');
const out = process.argv[2];
const png = fs.readFileSync(path.join(__dirname, '..', 'docs', 'zap-history.png')).toString('base64');
const read = (f) => fs.readFileSync(path.join(__dirname, f), 'utf8').split(/\r?\n/).filter((l) => l.trim());
const lines = [...read('e2e_output.txt'), '--- the chain: Jotform form, Zapier, Airtable ---', ...read('e2e_chain_output.txt')];

const historyHtml = `<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:#fff}img{width:100%;display:block}
#cap{position:fixed;left:0;right:0;bottom:0;background:#161b22;color:#e6edf3;font:15px Consolas,monospace;padding:12px 20px}</style>
<img src="data:image/png;base64,${png}"><div id="cap">Zapier, Zap history: the live Zap's run shows Successful</div>`;
const termHtml = `<!doctype html><meta charset="utf-8"><style>
html,body{margin:0;background:#0d1117;color:#c9d1d9;font:16px/1.6 Consolas,'Cascadia Mono',monospace}
header{padding:14px 22px;background:#161b22;border-bottom:1px solid #30363d;color:#e6edf3}
#t{padding:16px 22px;white-space:pre-wrap}.ok{color:#3fb950}.bad{color:#f85149}.dim{color:#8b949e}</style>
<header>e2e_check.py (hook to Worker), then e2e_chain.py (Jotform form to Airtable)</header><div id="t"></div>`;

(async () => {
  const b = await chromium.launch({ headless: true });
  const ctx = await b.newContext({ viewport: { width: 1100, height: 640 }, recordVideo: { dir: out, size: { width: 1100, height: 640 } } });
  const p = await ctx.newPage();
  await p.setContent(historyHtml);
  await p.waitForTimeout(3500);
  await p.setContent(termHtml);
  await p.waitForTimeout(800);
  for (const l of lines) {
    await p.evaluate((l) => {
      const d = document.createElement('div');
      const m = l.match(/^(PASS|FAIL)\s+(.*)$/);
      const esc = (s) => s.replace(/</g, '&lt;');
      d.innerHTML = m ? `<span class="${m[1] === 'PASS' ? 'ok' : 'bad'}">${m[1]}</span>  ${esc(m[2])}` : `<span class="dim">${esc(l)}</span>`;
      document.getElementById('t').appendChild(d);
    }, l);
    await p.waitForTimeout(650);
  }
  await p.waitForTimeout(2500);
  await ctx.close();
  await b.close();
})();
