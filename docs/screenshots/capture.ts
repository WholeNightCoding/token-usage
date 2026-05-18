// Element-targeted screenshot capture for the README screenshots.
//
// Why a separate script (not part of the skill itself):
//   The skill is pure stdlib. This helper depends on Bun + puppeteer-core,
//   which we install ad-hoc to a temp dir, point at the chromium binary
//   that gstack/playwright already cached on disk, and throw away after.
//
// Usage:
//   1. Start the dashboard:   python3 dashboard/server.py --no-open --port 8787
//   2. Install deps once:     mkdir /tmp/shot && cd /tmp/shot && \
//                             bun init -y && bun add puppeteer-core
//   3. Run:                   bun run docs/screenshots/capture.ts
//
// Output: 4 PNGs in this directory (01-04).

import puppeteer from 'puppeteer-core';

const SHELL =
  process.env.CHROME ||
  `${process.env.HOME}/Library/Caches/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-mac-arm64/chrome-headless-shell`;
const URL = process.env.URL || 'http://127.0.0.1:8787/';
const OUT = process.env.OUT || `${process.env.HOME}/.claude/skills/token-usage/docs/screenshots`;

const browser = await puppeteer.launch({
  executablePath: SHELL,
  headless: 'shell' as any,
  args: ['--no-sandbox', '--disable-gpu', '--hide-scrollbars', '--disable-features=PaintHolding'],
});

try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 2 });

  console.log('navigating…');
  // dashboard auto-refreshes every 10s, so networkidle never settles —
  // domcontentloaded + a fixed wait is what works here
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  await new Promise(r => setTimeout(r, 4000));

  // ------- 01: dashboard top (everything above patterns-panel) -------
  console.log('shot 01 (dashboard top)…');
  await page.evaluate(() => window.scrollTo(0, 0));
  await new Promise(r => setTimeout(r, 400));
  const patternsTop = await page.evaluate(() => {
    const el = document.getElementById('patterns-panel');
    return el ? Math.round(el.getBoundingClientRect().top + window.scrollY) : 1300;
  });
  await page.screenshot({
    path: `${OUT}/01-dashboard.png`,
    clip: { x: 0, y: 0, width: 1440, height: patternsTop },
  });

  // ------- 02: patterns panel only (element-bound) -------
  console.log('shot 02 (patterns panel)…');
  const patterns = await page.$('#patterns-panel');
  if (!patterns) throw new Error('#patterns-panel not found');
  await page.evaluate((el: any) => el.scrollIntoView({ block: 'start' }), patterns);
  await new Promise(r => setTimeout(r, 400));
  await patterns.screenshot({ path: `${OUT}/02-patterns.png` });

  // ------- 04: work-efficiency panel (after switching to 30d) -------
  // 04 before 03 because the AI 解读 call takes 30-90s; doing the cheap shots
  // first means a failure on 03 doesn't cost us 04.
  console.log('shot 04 (work-efficiency, 30d)…');
  // Range nav: switch the whole dashboard to 30d so the panel has a meaningful sample.
  // Native DOM .click() — page.click() doesn't always fire the bound listener.
  await page.evaluate(() => (document.querySelector('[data-range="30d"]') as HTMLElement)?.click());
  await new Promise(r => setTimeout(r, 3500));
  const effPanel = await page.$('#efficiency-panel');
  if (!effPanel) throw new Error('#efficiency-panel not found');
  await page.evaluate((el: any) => el.scrollIntoView({ block: 'start' }), effPanel);
  await new Promise(r => setTimeout(r, 400));
  await effPanel.screenshot({ path: `${OUT}/04-efficiency.png` });

  // ------- 03: AI 解读 — click button, wait for the spinner placeholder to vanish -------
  console.log('shot 03 (AI 解读, may take 30-90s)…');
  await page.click('#patterns-interpret');
  await page.waitForFunction(
    () => {
      const sec = document.getElementById('interpret-section');
      const body = document.getElementById('interpret-body');
      if (!sec || sec.hidden) return false;
      // body still has <p class="muted">⏳ ...</p> while waiting on the LLM
      const loadingP = body?.querySelector('p.muted');
      return loadingP == null;
    },
    { timeout: 130_000, polling: 1000 },
  );
  await new Promise(r => setTimeout(r, 800));
  const interpretSec = await page.$('#interpret-section');
  if (!interpretSec) throw new Error('#interpret-section not found');
  await page.evaluate((el: any) => el.scrollIntoView({ block: 'start' }), interpretSec);
  await new Promise(r => setTimeout(r, 400));
  await interpretSec.screenshot({ path: `${OUT}/03-ai-interpret.png` });

  console.log('done.');
} finally {
  await browser.close();
}
