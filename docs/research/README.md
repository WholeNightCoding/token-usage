# Research playgrounds

Interactive single-file HTML pages for exploring research questions about
Claude Code token-usage analytics. Each file is self-contained — no build
step, no external CDN dependencies — open it directly in a browser.

| File | Question it explores |
|---|---|
| [`release-potential.html`](./release-potential.html) | How much more token output could you realistically produce? Decomposes "potential" into Time-fill × Concurrency-fill, derives a Ceiling from historical P95 (not mean/max), and breaks ceilings down by project familiarity tier (new / mid / mature). Interactive sliders + presets. |

## Opening

```bash
open ~/.claude/skills/token-usage/docs/research/release-potential.html
```

Or, since they're plain HTML, drag the file into any browser tab.

## Status

These are **research artifacts** — exploratory thinking, not shipped
features. Numbers shown are example/baseline values; the methodology is
the contribution. When a hypothesis here graduates to a real metric, it
moves into `scripts/` or `dashboard/` with live data.

Current candidates for graduation:
- **Concurrency analysis** — would need `token_stats.scan_records` to
  preserve session-id (JSONL filename) per record, then a new
  `analysis/concurrency.py` to compute per-minute distinct-session counts
  and P95 of 30-min sustained concurrency.
- **Project-familiarity tiers** — compute per-project score from
  (days_since_first_record × log(total_tokens) × recency_factor), bin
  into quantile tiers, report per-tier ceiling.
