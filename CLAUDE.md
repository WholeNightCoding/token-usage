# CLAUDE.md

Project-specific guidance for [Claude Code](https://claude.com/claude-code) when working on this repository.

This file is auto-loaded by Claude Code into its context whenever you open this repo. It tells Claude Code what this project is, what conventions to follow, and how to navigate the codebase.

---

## What is this project

`token-usage` is a Claude Code skill that does local-first analytics on your own Claude Code transcripts (`~/.claude/projects/**/*.jsonl`). Three interfaces:

- **CLI** — `scripts/count_tokens.py`, `scripts/analyze.py`
- **Browser dashboard** — `dashboard/server.py` + static frontend
- **Standalone reports** — Markdown / HTML / JSON exports from `analyze.py`

Plus an **AI 解读** button in the dashboard that shells out to the local `claude` CLI for narrative interpretation of the patterns.

The skill itself is auto-invoked by Claude Code when the user asks "how many tokens did I use today" / "打开 token 监控板" / similar.

---

## Hard rules

These are non-negotiable. Don't suggest changes that violate them without explicit user approval:

1. **Pure stdlib only.** No `numpy`, `pandas`, `scipy`, `requests`, `flask`, `fastapi`, `pyyaml`, etc. on the Python side. The dashboard frontend uses Chart.js loaded from a CDN — that's the only external runtime dependency in the whole project.
2. **Zero install steps.** A user who clones the repo into `~/.claude/skills/token-usage/` should be able to run any script with `python3 scripts/foo.py` immediately. No `pip install -r requirements.txt`. No `npm install`.
3. **Don't touch `~/.claude/projects/**/*.jsonl`.** Read-only, ever. Those are the user's transcripts.
4. **Chinese UI strings stay Chinese.** Don't "translate" 用法画像 / 突发型 / 晚高峰 etc. to English. The user-facing language is Chinese; code/comments are English.
5. **No telemetry, no analytics, no outbound calls.** Only outbound call permitted: the local `claude` CLI subprocess from the AI 解读 button (server.py `h_interpret`).

---

## Codebase map (for orientation)

```
SKILL.md                    Skill manifest. Claude Code reads this to discover the skill.
README.md                   English landing page (GitHub default).
README.zh-CN.md             Chinese landing page.

scripts/
  token_stats.py            CORE — JSONL parsing, dedup-by-message-id (field-wise max),
                            time-window filtering, bucket aggregation, billing-equiv
                            (5m/1h ephemeral cache split). Imported by everything else.
  count_tokens.py           CLI: per-model token totals for any time range.
  analyze.py                CLI: full Patterns analysis + report export (md/html/json).

analysis/                    PURE-PYTHON analytics (no deps; each module is self-contained).
  features.py               Descriptive stats / Goh-Barabási burstiness / Gini /
                            ACF (multi-lag) / run-length / Shannon entropy.
  seasonal.py               Hour-of-day, day-of-week aggregations.
  changepoint.py            Binary segmentation + BIC penalty.
  markov.py                 2-state and 3-state Markov chains + stationary distribution
                            + dwell times + stickiness.
  report.py                 Renderers: terminal / Markdown / HTML / JSON.

dashboard/
  server.py                 Stdlib http.server with JSON API (/api/summary,
                            /api/realtime, /api/patterns, /api/interpret, etc.).
  static/
    index.html              Single-page UI. Patterns panel toolbar + 8 cards + inline
                            AI 解读 section.
    app.js                  Vanilla JS. Chart.js for the realtime/cp/dashboard charts;
                            div-based bar charts for ACF / hour-of-day / day-of-week.
    styles.css              Theme tokens (Apple / Material / Linear / Terminal).

menubar/
  app.py                    macOS menubar (rumps). Optional. Has its own .venv (gitignored).

docs/
  screenshots/              README screenshots.
```

**The single source of truth for token math is `scripts/token_stats.py`.** Don't reinvent dedup or billing-equiv logic anywhere else — import from there.

---

## How the analysis modules talk to each other

```
                     transcripts (JSONL)
                            │
                            ▼
            scripts/token_stats.py
            ─ scan_records() → list[Record]
            ─ aggregate_by_bucket(records, bucket_sec)
            ─ aggregate_by_day(records)
                            │
                            ▼
              ┌─── densify (in caller) ───┐
              │                            │
              ▼                            ▼
      list[int] (values)         list[(date_str, int)] (daily totals)
              │                            │
   ┌──────────┼──────────┐                 │
   ▼          ▼          ▼                 ▼
features   markov     seasonal       changepoint
.py        .py        .py            .py
   │          │          │                 │
   └──────────┴──────────┴─────────────────┘
                       │
                       ▼
                report.py (assemble + render)
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
     terminal       Markdown        HTML
                                       │
                                  +tooltips
```

Important: each `analysis/*.py` module takes pre-bucketed primitives (lists/tuples), NOT `Record` dataclass instances. This keeps each module testable in isolation and dependency-free.

---

## Conventions

### Python style
- Python 3.11+ syntax allowed (`list[int]`, `int | None`, etc.)
- 4-space indent, no tabs
- Type hints on public functions
- Module docstrings at top, not for every function
- Comments explain *why*, not *what*. Default to no comments.
- Functions < 50 lines preferred; the analysis modules respect this.

### JS style (dashboard frontend)
- Vanilla JS, ES2022+
- No build step — files are served as-is by `dashboard/server.py`
- `'use strict'` at top of `app.js`
- Prefix internal helpers with no underscore (no `_foo` convention here)
- Avoid jQuery-style chaining; one statement per line
- DOM access via the `$` helper (which is just `document.querySelector`)

### CSS
- CSS variables for theming (see `:root` and `[data-theme="..."]` blocks)
- Class names: kebab-case
- Section dividers as block-comment banners

### Commits
- Conventional commits in **English**: `feat: ...`, `fix: ...`, `chore: ...`, `docs: ...`, `refactor: ...`
- PR/commit body can be Chinese or English (user preference)
- Always include `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` when the work was done with Claude Code

### Branching / shipping
- Main branch: `main`
- Non-trivial changes: feature branch → PR → user reviews → merge → cleanup. Don't push directly to `main` for features.
- The user's global `~/.claude/CLAUDE.md` defines a strict GATE-based workflow (worktree → dry-run → GATE 1 ask user → merge after GATE 2 → restart → GATE 3 → cleanup after GATE 4). Follow it for code changes. Doc-only changes can be lighter-touch but still go through PR for visibility.

---

## Common dev tasks

### Run the dashboard locally
```bash
python3 dashboard/server.py --no-open --port 8787
# then open http://127.0.0.1:8787/
```

### Test a CLI quickly
```bash
python3 scripts/count_tokens.py --today
python3 scripts/analyze.py --days 7 --html /tmp/r.html
```

### Adding a new statistical method to the Patterns panel
1. Implement in `analysis/<your_module>.py` as a pure function taking pre-densified primitives
2. Wire it into `dashboard/server.py:_compute_patterns()` to add a new top-level key in the returned dict
3. Render in `dashboard/static/app.js:renderPatterns()` — add a new card in `index.html`'s `.patterns-grid`
4. Add a `?` tooltip explaining the math (HTML-inline, see existing examples)
5. Update `analysis/report.py` so `analyze.py --html` exports it too
6. Update both READMEs

### Adding a new dashboard chart
- Use Chart.js (already loaded) for canvas-based charts
- Use div-based `.bar-row` markup for horizontal bar charts (cheaper, no canvas)
- For theming, read CSS vars via `getComputedStyle()`; don't hardcode colors

### Editing the AI 解读 prompt
The prompt template lives in `dashboard/server.py:h_interpret()`. Keep it concise (Claude doesn't need verbose framing). Default model is `claude-sonnet-4-6`; override with `TOKEN_USAGE_LLM_MODEL` env var.

---

## Things to be careful about

- **Bucket size & seasonal.** When user picks bucket > 1 hour, hour-of-day/dow get computed on a separate ≤1h-bucket time series in `_compute_patterns`. Don't collapse this back to using the user's bucket — you'd see only 4 nonzero hours for a 6h bucket.
- **JSON serialization.** `changepoint.detect()` can return `float('inf')` for `pct_change`. The server runs `_json_safe(o)` to convert these to the string `"inf"` before sending. Don't bypass this.
- **Cache-write pricing split.** Anthropic charges 1.25× input rate for 5-min ephemeral cache writes, 2.0× for 1-hour writes. `token_stats.py` reads `usage.cache_creation.ephemeral_1h_input_tokens` separately. Don't simplify back to a single multiplier.
- **Dedup-by-max.** Same `message.id` can appear in multiple JSONL files (parent session + subagent). Keep the field-wise-max merge in `scan_records()`; the streaming snapshot may have `output_tokens=1` and the final has the real count.
- **`<synthetic>` model.** Compaction events show up with `model="<synthetic>"` and zero usage everywhere. Safe to ignore but don't drop without verifying.
- **Don't break `count_tokens.py`'s output format.** Other people pipe it through `--json` for downstream tools.

---

## Battle scars (things we already learned)

- `gh CLI` org slug for `WholeNightCoding` is **PascalCase** (`WholeNightCoding`), not `whole-night-coding`. The latter 404s.
- The `claude` CLI returns markdown that may be wrapped in ` ```markdown ` fences depending on prompt. Strip before rendering, or instruct it not to wrap (see existing prompt).
- `subprocess.run([claude_bin, "--print", prompt])` typically takes 10-90 seconds for the AI 解读 endpoint. Set timeout to 120s, not less.
- `http.server.ThreadingHTTPServer` is single-process but multi-thread; the LLM call blocks one thread for ~minute. Don't worry about it for personal use.

---

## When the user opens this repo and asks for changes

- Default to running `python3 dashboard/server.py --no-open` first to confirm baseline works before changing anything dashboard-related.
- For analysis changes, write a small test invocation in `python3 -c "..."` to verify before committing.
- Patterns/analytics PRs: regenerate at least one HTML report to eyeball the visual.
- Reach for the existing `tooltip` system before inventing a new explainer pattern.
