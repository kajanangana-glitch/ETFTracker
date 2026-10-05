# Global ETF Tracker

A free, self-updating dashboard for country, region and sector ETFs — prices, trend signals,
news headlines (with a political/geopolitical tag) and upcoming macro events.

- **Data**: GitHub Actions runs `scripts/fetch_data.py` every weekday (before the US open and after the close), pulling prices from Yahoo Finance (`yfinance`) and headlines from Google News RSS, and commits `docs/data.json`.
- **Dashboard**: `docs/index.html`, served by GitHub Pages. No API keys, no server.

## Setup (one time)

1. Push these files to the `main` branch.
2. **Settings → Actions → General → Workflow permissions** → choose *Read and write permissions* → Save.
3. **Actions** tab → *Update ETF data* → **Run workflow** (creates the first `docs/data.json`).
4. **Settings → Pages** → Source: *Deploy from a branch* → Branch `main`, folder **`/docs`** → Save.
5. After ~1 minute your dashboard is live at `https://<your-username>.github.io/<repo>/`.

## Customising

Edit **`etfs.yml`**:
- Add/remove ETFs under `groups` (any group name works — it becomes a tab). `query` is the extra Google News search used for that ETF.
- Add upcoming events (elections, central-bank meetings, summits) under `events`. Past dates hide automatically.

Pushing a change to `etfs.yml` triggers a refresh automatically.

## What the columns mean

| Field | Meaning |
|---|---|
| 1D … 1Y | Total-return price change (dividend-adjusted) |
| Trend | **Uptrend** = price > 50-day avg > 200-day avg; **Downtrend** = the reverse; otherwise Mixed |
| RSI | 14-day Relative Strength Index (>70 often called overbought, <30 oversold) |
| vs 52w hi | Distance below the 52-week high |
| News | Dot = average headline tone (simple keyword score); number = headlines in the last 10 days |

## Run locally

```bash
pip install -r requirements.txt
python scripts/fetch_data.py
cd docs && python -m http.server 8000   # open http://localhost:8000
```

*For information only — not investment advice. Yahoo Finance data via yfinance is unofficial and can occasionally fail; the script keeps the previous data if a run gets nothing.*
