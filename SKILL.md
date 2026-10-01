---
name: token-usage
description: Report local Claude Code and Codex token usage for today, a date, a rolling window, or a custom range, with model, project, day, and source breakdowns. Use the local dashboard for visual monitoring and work-efficiency analysis; use the CLI for one-shot questions.
---

# Token Usage Reporter

Read local transcripts without modifying them. Default scope is both Claude Code and Codex; use `--source claude` or `--source codex` when the user names one. These are local recorded tokens, not account quota percentages or subscription charges.

## One-shot reports

Run from this skill's actual directory; do not assume it lives in `~/.claude/skills` or `~/.codex/skills`.

```bash
python3 scripts/count_tokens.py --today
python3 scripts/count_tokens.py --source codex --yesterday
python3 scripts/count_tokens.py --source claude --this-week
python3 scripts/count_tokens.py --from 2026-10-01 --to 2026-10-07 --by-day
python3 scripts/count_tokens.py --source codex --last 12h --json
```

Range flags: `--today` (default), `--yesterday`, `--this-week`, `--this-month`, `--all`, `--date YYYY-MM-DD`, `--last 7d|12h|30m`, or `--from X --to Y`. Dates/times use local timezone. JSON retains the existing `by_model` and `total` fields, and adds `by_source`, reasoning-output and pricing-scope metadata.

Sources:

- Claude Code: `~/.claude/projects/**/*.jsonl`; override with `--projects-dir`.
- Codex CLI/Desktop: `${CODEX_HOME:-~/.codex}/sessions/**/*.jsonl` and `archived_sessions/**/*.jsonl`; override with `--codex-home` (the home containing both folders).

## Dashboard

Use when the user asks for a monitoring page, charts, live rates or navigable analysis. Do not launch it for a one-shot token-count question.

```bash
python3 dashboard/server.py
# 127.0.0.1:8787; --no-open, --port N, --projects-dir PATH, --codex-home PATH
```

The source selector (全部 / Claude Code / Codex) applies to totals, trends, models, projects, realtime, efficiency and Patterns. The dashboard caches the corpus and invalidates it when transcript files change. Keep it bound to loopback unless the user requests another binding.

Other reports use the same scanner:

```bash
python3 scripts/work_efficiency.py 7 --source codex
python3 scripts/analyze.py --days 7 --source codex --html report.html
```

The existing optional AI interpretation button uses the local `claude` CLI, regardless of the selected statistics source; ordinary statistics require only Python's standard library.

## Interpret correctly

- Claude Code: dedupe parent/subagent and streaming snapshots by message ID with field-wise maxima; cache creation retains the 5-minute/1-hour split.
- Codex: prefer per-response `token_usage_record` when present, dedupe response IDs, and suppress mirrored `token_count` events. Older rollouts use cumulative-counter differences, reading preceding counters before applying a time window. Repeated snapshots, counter resets and inherited fork history are handled separately.
- Codex cached input is a subset of input; the INPUT column shows the non-cached remainder. Reasoning output is a subset of OUTPUT. Never add either subset again to total tokens.
- Amounts and billing-equivalent values use the existing Claude pricing configuration only. Codex tokens are marked unpriced. Never apply Claude rates to Codex, or present the API estimate as the user's subscription bill. Mixed-source efficiency disables the billing view and retains raw throughput.
- Missing/deleted/local-unrecorded history cannot be reconstructed. Cloud conversations without local rollout files are outside the report.
- Filter by event timestamp, not file modification date. Do not use `stats-cache.json` to answer today's usage.

For format/dedup details and validation commands, see [docs/codex-usage.md](docs/codex-usage.md).
