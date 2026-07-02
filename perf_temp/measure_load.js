// Baseline load-time harness. Serves the REAL web/index.html via uvicorn, intercepts
// the CDN URLs and fulfills them from perf_temp/mirror/ (exact same bytes), so CPU
// costs (Babel transpile, React mount, JSONL parse, chart render) are measured in a
// real headless Chrome. Network download time is ~0 here (localhost) and is modeled
// separately from known byte sizes. Run: node perf_temp/measure_load.js <baseURL>
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

const BASE = process.argv[2] || 'http://127.0.0.1:8150';
const MIRROR = path.join(__dirname, 'mirror');
const MAP = [
  ['react-dom.production.min.js', 'react-dom.production.min.js', 'application/javascript'],
  ['react.production.min.js', 'react.production.min.js', 'application/javascript'],
  ['react-dom.development.js', 'react-dom.development.js', 'application/javascript'],
  ['react.development.js', 'react.development.js', 'application/javascript'],
  ['babel.min.js', 'babel.min.js', 'application/javascript'],
  ['echarts', 'echarts.min.js', 'application/javascript'],
];

function localFor(url) {
  for (const [needle, file, ct] of MAP) if (url.includes(needle)) return [path.join(MIRROR, file), ct];
  return null;
}

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  await page.setCacheEnabled(false); // cold load

  const reqLog = [];
  await page.setRequestInterception(true);
  page.on('request', (req) => {
    const lf = localFor(req.url());
    if (lf && fs.existsSync(lf[0])) {
      const body = fs.readFileSync(lf[0]);
      reqLog.push({ url: req.url(), bytes: body.length, src: 'mirror' });
      req.respond({ status: 200, headers: { 'access-control-allow-origin': '*', 'cache-control': 'no-store' }, contentType: lf[1], body });
    } else if (req.url().includes('fonts.googleapis') || req.url().includes('fonts.gstatic')) {
      req.abort(); // fonts non-essential for this measurement; avoid external dependency
    } else {
      req.continue();
    }
  });
  page.on('response', async (res) => {
    const u = res.url();
    if (u.startsWith(BASE)) {
      try { const buf = await res.buffer(); reqLog.push({ url: u.replace(BASE,''), bytes: buf.length, src: 'local' }); } catch {}
    }
  });

  const t0 = Date.now();
  await page.goto(BASE + '/', { waitUntil: 'load', timeout: 60000 });

  // Wait until the library data has loaded (pill flips to "live data") or timeout.
  let libMs = null;
  try {
    await page.waitForFunction(() => {
      const el = document.querySelector('.pill-live');
      return el && /live data/i.test(el.textContent);
    }, { timeout: 30000 });
    libMs = Date.now() - t0;
  } catch { libMs = null; }

  // Paint + navigation timing from the browser.
  const timing = await page.evaluate(() => {
    const paints = performance.getEntriesByType('paint');
    const nav = performance.getEntriesByType('navigation')[0] || {};
    const fcp = paints.find(p => p.name === 'first-contentful-paint');
    const fp = paints.find(p => p.name === 'first-paint');
    const res = performance.getEntriesByType('resource').map(r => ({
      name: r.name.split('/').pop().slice(0,40), dur: Math.round(r.duration),
      start: Math.round(r.startTime),
    }));
    return {
      firstPaint: fp ? Math.round(fp.startTime) : null,
      firstContentfulPaint: fcp ? Math.round(fcp.startTime) : null,
      domContentLoaded: Math.round(nav.domContentLoadedEventEnd || 0),
      loadEvent: Math.round(nav.loadEventEnd || 0),
      resources: res.sort((a,b)=>b.dur-a.dur).slice(0,8),
    };
  });

  // Click-load: open a chart tab (Timeline) and time until an ECharts <canvas> renders.
  async function clickNav(label) {
    return page.evaluate((lbl) => {
      const b = [...document.querySelectorAll('.sidenav-item')].find(x => x.textContent.trim().toLowerCase().includes(lbl));
      if (b) b.click();
      return !!b;
    }, label.toLowerCase());
  }
  let chartMs = null;
  const c0 = Date.now();
  if (await clickNav('timeline')) {
    try {
      await page.waitForFunction(() => {
        const v = [...document.querySelectorAll('canvas')].some(c => c.offsetParent !== null && c.width > 0);
        return v;
      }, { timeout: 15000 });
      chartMs = Date.now() - c0;
    } catch { chartMs = null; }
  }

  // Page-nav: switch between two already-loaded non-chart views; time the DOM toggle.
  const navTimes = [];
  for (const lbl of ['genres', 'albums', 'overview']) {
    const n0 = Date.now();
    await clickNav(lbl);
    await new Promise(r => setTimeout(r, 30));
    navTimes.push({ view: lbl, ms: Date.now() - n0 });
  }

  const critical = reqLog.filter(r => r.src === 'mirror').reduce((s, r) => s + r.bytes, 0);
  const out = {
    base: BASE,
    paint: timing,
    libraryDataLoadedMs: libMs,
    chartTabRenderMs: chartMs,
    pageNavMs: navTimes,
    criticalCdnBytes: critical,
    requests: reqLog.map(r => ({ ...r, kb: +(r.bytes/1024).toFixed(1) })),
  };
  console.log(JSON.stringify(out, null, 2));
  await browser.close();
})().catch(e => { console.error('HARNESS ERROR:', e.message); process.exit(1); });
