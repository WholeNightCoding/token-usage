# Screenshots

The READMEs reference 3 PNGs in this directory:

| File | What it captures | How |
|---|---|---|
| `01-dashboard.png` | Dashboard top: KPIs, Daily trend, by-model, Top-10 projects, Realtime | full-page, clipped to the top of `#patterns-panel` |
| `02-patterns.png` | Patterns panel (toolbar + 8 cards) | element-bound (`#patterns-panel.screenshot()`) |
| `03-ai-interpret.png` | AI 解读 inline result | clicks the button, waits for the LLM, screenshots `#interpret-section` |

All three are captured by `capture.ts` in this directory. Element-bound shots (02-03) use puppeteer-core's `ElementHandle.screenshot()` so they crop exactly to the DOM rect — no manual coordinates needed.

## Regenerating

```bash
# 1. Start the dashboard
python3 ~/.claude/skills/token-usage/dashboard/server.py --no-open --port 8787

# 2. One-time deps install (uses Bun, ~10s; chromium is the one
#    already cached at ~/Library/Caches/ms-playwright/.../chrome-headless-shell)
mkdir -p /tmp/shot && cd /tmp/shot
bun init -y
bun add puppeteer-core

# 3. Run the capture script (~90s — 80s of which is the LLM call for shot 03)
bun run ~/.claude/skills/token-usage/docs/screenshots/capture.ts
```

Override the chromium path or URL with env vars:

```bash
CHROME=/path/to/chrome URL=http://127.0.0.1:9000/ bun run capture.ts
```

## Why not the gstack `browse` skill

`browse` has a `screenshot --selector` flag which is the obvious tool for this job. But its daemon (a bun + playwright service) was hanging on every command in our environment, so we bypassed it and drove `chrome-headless-shell` directly via `puppeteer-core`. This script is ~80 lines, has no daemon, runs to completion in one shot, and uses the chromium binary that gstack/playwright already downloaded — no extra disk cost.
