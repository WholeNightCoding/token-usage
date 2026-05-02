# Screenshots

The READMEs reference one full-page screenshot here:

- **`01-dashboard.png`** — full dashboard at 1440 × 3200, captured headless via `chrome-headless-shell`. Shows everything in one shot: header → KPIs → Daily trend → by-model → Top 10 projects → Realtime → Patterns panel (toolbar + 8 cards) → Detail table.

To regenerate:

```bash
# 1. Make sure the dashboard is running
python3 ~/.claude/skills/token-usage/dashboard/server.py --no-open --port 8787

# 2. Find Chrome (or use ~/Library/Caches/ms-playwright/.../chrome-headless-shell)
CHROME=$(find ~/Library/Caches/ms-playwright -name 'chrome-headless-shell' -perm +111 2>/dev/null | head -1)
[ -z "$CHROME" ] && CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# 3. Capture
TMPDIR=$(mktemp -d)
"$CHROME" --headless --disable-gpu --hide-scrollbars \
  --user-data-dir="$TMPDIR" \
  --window-size=1440,3200 \
  --virtual-time-budget=8000 \
  --screenshot=01-dashboard.png \
  http://127.0.0.1:8787/
```
