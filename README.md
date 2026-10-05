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

## Trends view

Open **Trends** in the header (or `…/ETFTracker/#trends`). Every ETF is classified daily from its prices:

- **Leader**: price above its 50- and 200-day averages, up over 3 months, and within 7% of its 52-week high.
- **Turnaround watch**: down over the past year, 15%+ below its high, or below its 200-day average, *and* showing at least 2 of 4 recovery signs: price back above the 50-day average, 50-day average turning up, up over the last month, RSI bounced from oversold.
- **Still falling**: beaten down with fewer than 2 signs.

`ideas.yml` adds the human context: why each market is moving, what could push it higher, the risks, and event-driven themes (oil and Hormuz, global rate hikes, AI chips, emerging-market turnaround, gold and defence). Each theme shows which ETFs have tended to gain or lose in each scenario.

## Political events view

Switch to **Political events** in the header, or open `…/ETFTracker/#politics`. For each election in `politics.yml` you get:

- **This cycle vs past elections**: the ETF's price path around election day, rebased to 100 at 90 trading days before the vote and overlaid on earlier cycles (e.g. EWZ around Brazil's 2010, 2014, 2018 and 2022 elections). "Today" marks where this cycle is.
- **Past cycles in numbers**: run-up into the vote, the day-after reaction, the week after and the 3 months after.
- **Polls vs ETF** (when polls are listed): candidate poll averages plotted against the ETF price, so you can see whether the price followed the polls.
- **What tends to push it higher / lower**: the policy outcomes markets have reacted to.
- **Election news** from Google News.

To add an election, copy one block in `politics.yml` and change the date, ETFs, past election dates and drivers. To update polls, append a row under `polls.rows`.

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
