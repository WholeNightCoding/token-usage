---
name: token-usage
description: Use when the user asks how many Claude Code tokens were consumed — today, yesterday, this week, this month, on a specific date, in a rolling window (last Nh/Nd), or across a custom date/time range — or wants a per-model breakdown with cache-read vs. cache-create split. Also applies when `~/.claude/stats-cache.json` is stale (it only updates through the previous day) or when the user wants a billing-equivalent estimate from local transcripts. Also triggers for visual/browser-based token usage monitoring — "dashboard", "监控板", "面板", "show me in a browser", live rate, per-project breakdown — via the local web UI at `dashboard/server.py`.
---

# Token Usage Reporter

## Overview

Aggregates Claude Code token `usage` records from local JSONL transcripts under `~/.claude/projects/**/*.jsonl` for any time range. More accurate than `~/.claude/stats-cache.json`, which lags by one day.

**Core principle:** parse raw transcripts → filter by `timestamp` → dedupe by `message.id` → sum per model.

## When to Use

Trigger symptoms:
- "How many tokens did I use today / yesterday / this week / last 6 hours?"
- "Token usage between 2026-04-01 and 2026-04-15"
- "Breakdown by model" or "how much was cache vs. new input?"
- Questions about cost / billing-equivalent tokens for a period
- `stats-cache.json` doesn't include today's data
- User wants a **visual dashboard / monitoring page** (e.g. "token 监控板", "打开 token 面板", "in a browser", per-project top-N, live rate) → use the dashboard, not a one-shot CLI table

**Do NOT use for:** message/session counts (those are in `stats-cache.json` — read it directly).

## Quick Reference

Script: `~/.claude/skills/token-usage/scripts/count_tokens.py` (invoke with `python3`).

| Range flag | Scope |
|---|---|
| `--today` (default) | Today, local tz |
| `--yesterday` | Yesterday, local tz |
| `--this-week` | Mon–Sun of current week |
| `--this-month` | Current calendar month |
| `--date YYYY-MM-DD` | One specific day |
| `--last 7d` / `12h` / `30m` | Rolling window ending now |
| `--from X --to Y` | Explicit range, `YYYY-MM-DD` or `YYYY-MM-DD HH:MM[:SS]` |

Modifiers: `--by-day` (per-day table for multi-day ranges), `--json` (machine-readable), `--projects-dir PATH` (override transcript root).

## Dashboard (visual, browser-based)

Use when the user wants ongoing monitoring, live rate, per-project breakdown, or just a more navigable view than a CLI table.

```bash
python3 ~/.claude/skills/token-usage/dashboard/server.py
# default: 127.0.0.1:8787, auto-opens browser
# flags: --port N, --host ADDR (keep 127.0.0.1 unless asked), --no-open
```

Shows: total + billing-equiv + USD estimate (pay-as-you-go) + last-1h rate cards, stacked daily-trend line, per-model doughnut, top-10 projects bar, realtime 1h line (auto 10s refresh), and a model×project detail table. Range selector: Today / Yesterday / This week / This month / 7d / 30d.

Server characteristics:
- Reads the same `~/.claude/projects/**/*.jsonl` the CLI uses; numbers match to the token
- Caches full corpus in memory; range switches don't rescan disk (`~45ms` warm, `~1.5s` cold on first hit)
- Invalidates cache on file mtime/size change — new activity is reflected on next refresh
- Binds `127.0.0.1` only; no auth, no logging to disk

**Do NOT launch the dashboard just to answer a one-shot CLI-style question.** Use `count_tokens.py` instead for "how many tokens yesterday" — it's faster and fits better in chat.

## Canonical Example

```bash
python3 ~/.claude/skills/token-usage/scripts/count_tokens.py --from 2026-04-01 --to 2026-04-21 --by-day
```

Output columns: `MODEL | MSGS | INPUT | OUTPUT | CACHE_READ | CACHE_CREATE | TOTAL`, plus grand total and a **billing-equivalent input tokens** estimate using weights `input=1×, cache_read=0.1×, cache_create_5m=1.25×, cache_create_1h=2×, output=5×`. CACHE_CREATE in the table is the 5m+1h sum; the billing-equiv splits them out via the per-record `cache_creation.ephemeral_1h_input_tokens` field.

## Implementation Notes

- Scans every `.jsonl` under the projects dir, including nested `subagents/` transcripts.
- Timestamps in transcripts are UTC ISO-8601; local-tz flags are converted to UTC for filtering.
- Dedupes on `message.id` or `uuid` by **field-wise max** across copies (same id can appear in parent + subagent files; streaming snapshots carry partial output_tokens that the final record supersedes).
- `<synthetic>` model rows come from cache-only compaction events; safe to ignore — they always sum to zero.
- 1h ephemeral cache writes are priced at 2× input (vs 1.25× for 5m); the billing-equiv reads `cache_creation.ephemeral_1h_input_tokens` separately.

## Common Mistakes

- **Reporting `cache_read` as new-input cost.** It's 0.1× in billing; cite billing-equiv when cost is the question.
- **Using `stats-cache.json` for "today".** It only updates through the previous day.
- **Pre-converting dates to UTC.** The script handles local-tz; pass the user's date as-is.
- **Filtering by file mtime.** Transcripts append throughout a session; always filter by the JSONL `timestamp` field.

## Reporting Results

Lead with grand total + short-form; then the per-model table; then billing-equiv if cost was the real question. Match the user's language for prose; keep the numeric table as-is.
