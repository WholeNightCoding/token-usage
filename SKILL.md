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

Server: same data source as CLI (numbers match), in-memory cached + mtime-invalidated, binds `127.0.0.1` only, no auth.

**Do NOT launch the dashboard just to answer a one-shot CLI-style question.** Use `count_tokens.py` instead for "how many tokens yesterday" — it's faster and fits better in chat.

## Canonical Example

```bash
python3 ~/.claude/skills/token-usage/scripts/count_tokens.py --from 2026-04-01 --to 2026-04-21 --by-day
```

Output columns: `MODEL | MSGS | INPUT | OUTPUT | CACHE_READ | CACHE_CREATE | TOTAL`, plus grand total and a **billing-equivalent input tokens** estimate using weights `input=1×, cache_read=0.1×, cache_create_5m=1.25×, cache_create_1h=2×, output=5×`. CACHE_CREATE in the table is the 5m+1h sum; the billing-equiv splits them out via the per-record `cache_creation.ephemeral_1h_input_tokens` field.

## Reading the output

- **Scope**: covers Claude Code only. Anthropic API direct calls, Claude.ai web/desktop usage are NOT in these transcripts — totals will be lower than your actual Anthropic billing.
- **Sub-agent dedup is automatic**: same `message.id` appearing in parent + sub-agent transcripts is merged via field-wise max. The numbers you see are post-dedup; no double-counting.
- `<synthetic>` model rows are cache-only compaction events; always sum to zero, safe to ignore.
- `cache_create_5m` is priced at 1.25× input, `cache_create_1h` at 2×; billing-equiv splits them via `cache_creation.ephemeral_1h_input_tokens` per record.

(Internals — dedup-by-max, UTC handling, subagent scanning — see CLAUDE.md.)

## Common Mistakes

- **Reporting `cache_read` as new-input cost.** It's 0.1× in billing; cite billing-equiv when cost is the question.
- **Using `stats-cache.json` for "today".** It only updates through the previous day.
- **Pre-converting dates to UTC.** The script handles local-tz; pass the user's date as-is.
- **Filtering by file mtime.** Transcripts append throughout a session; always filter by the JSONL `timestamp` field.

