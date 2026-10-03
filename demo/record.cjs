// Replays the REAL output of tests/e2e_workflow.py (captured in logs/e2e_last.txt) in a terminal-style page and records it with Playwright.
// Nothing is invented: every line on screen is a line the test run printed. Usage: node demo/record.cjs <out_dir>
const fs = require('fs');
const path = require('path');
const { chromium } = require('C:/Users/' + process.env.USERNAME + '/AppData/Roaming/npm/node_modules/playwright');

const out = process.argv[2];
const lines = fs.readFileSync(path.join(__dirname, '..', 'logs', 'e2e_last.txt'), 'utf8').split(/\r?\n/).filter((l) => l.trim());
const html = `<!doctype html><meta charset="utf-8"><style>
html,body{margin:0;background:#0d1117;color:#c9d1d9;font:15px/1.5 Consolas,'Cascadia Mono',monospace}
header{padding:14px 22px;background:#161b22;border-bottom:1px solid #30363d;color:#8b949e;font-size:13px}
header b{color:#e6edf3}
#t{padding:16px 22px 80px;white-space:pre-wrap}
.ok{color:#3fb950}.bad{color:#f85149}.dim{color:#8b949e}
#bar{position:fixed;left:0;right:0;bottom:0;padding:12px 22px;background:#161b22;border-top:1px solid #30363d;color:#e6edf3;font-size:14px}
</style>
<header><b>admission-intake</b> &nbsp; real Azure Logic Apps Standard runtime on localhost, two mock systems, one dead-letter store</header>
<div id="t"></div><div id="bar">$ python tests/e2e_workflow.py</div>`;

(async () => {
  const b = await chromium.launch({ headless: true });
  const ctx = await b.newContext({ viewport: { width: 1100, height: 760 }, recordVideo: { dir: out, size: { width: 1100, height: 760 } } });
  const p = await ctx.newPage();
  await p.setContent(html);
  await p.waitForTimeout(1200);
  for (const l of lines) {
    await p.evaluate((l) => {
      const d = document.createElement('div');
      const m = l.match(/^(PASS|FAIL)\s+(.*)$/);
      d.innerHTML = m ? `<span class="${m[1] === 'PASS' ? 'ok' : 'bad'}">${m[1]}</span>  ${m[2].replace(/</g, '&lt;')}` : `<span class="dim">${l.replace(/</g, '&lt;')}</span>`;
      document.getElementById('t').appendChild(d);
      window.scrollTo(0, document.body.scrollHeight);
    }, l);
    await p.waitForTimeout(/transient|stays down|retried|parked/.test(l) ? 650 : 330);
  }
  await p.waitForTimeout(2500);
  await ctx.close();
  await b.close();
})();
