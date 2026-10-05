"""Fetch ETF prices, trend metrics and news; write docs/data.json for the dashboard.

Runs in GitHub Actions on a schedule (see .github/workflows/update.yml).
Free data only: Yahoo Finance via yfinance, Google News RSS via feedparser.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import time
import urllib.parse
from pathlib import Path

import feedparser
import pandas as pd
import yaml
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "etfs.yml"
OUT = ROOT / "docs" / "data.json"
NEWS_PER_ETF = 8
NEWS_MAX_AGE_DAYS = 10

# Tiny headline lexicon — a rough tone signal, not a trading model.
POS = {"surge", "surges", "rally", "rallies", "gain", "gains", "jump", "jumps", "soar", "soars",
       "record", "beat", "beats", "rise", "rises", "rebound", "boost", "boosts", "upgrade",
       "strong", "growth", "optimism", "cut", "cuts", "easing", "deal", "recovery", "high"}
NEG = {"fall", "falls", "drop", "drops", "plunge", "plunges", "slump", "slumps", "tumble",
       "loss", "losses", "fear", "fears", "risk", "risks", "tariff", "tariffs", "sanction",
       "sanctions", "war", "crisis", "recession", "downgrade", "weak", "slowdown", "selloff",
       "sell-off", "hike", "hikes", "inflation", "default", "probe", "ban", "low", "tension"}
POLITICAL = {"election", "elections", "tariff", "tariffs", "sanction", "sanctions", "war",
             "government", "minister", "president", "parliament", "congress", "policy",
             "trade", "geopolitical", "summit", "vote", "regulation", "ban", "shutdown"}


def headline_tone(title: str) -> int:
    words = set(re.findall(r"[a-z\-]+", title.lower()))
    return len(words & POS) - len(words & NEG)


def is_political(title: str) -> bool:
    return bool(set(re.findall(r"[a-z\-]+", title.lower())) & POLITICAL)


def pct(a: float, b: float) -> float | None:
    if b is None or a is None or b == 0 or math.isnan(a) or math.isnan(b):
        return None
    return round((a / b - 1) * 100, 2)


def rsi(close: pd.Series, n: int = 14) -> float | None:
    if len(close) <= n:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    val = 100 - 100 / (1 + rs.iloc[-1])
    return None if math.isnan(val) else round(float(val), 1)


def metrics(close: pd.Series) -> dict:
    """Returns, moving averages and a simple trend label from a daily close series."""
    close = close.dropna()
    last = float(close.iloc[-1])

    def back(days: int):
        return float(close.iloc[-days - 1]) if len(close) > days else None

    year_start = close[close.index.year == close.index[-1].year]
    sma50 = float(close.tail(50).mean()) if len(close) >= 50 else None
    sma200 = float(close.tail(200).mean()) if len(close) >= 200 else None
    hi52 = float(close.tail(252).max())

    if sma50 and sma200:
        if last > sma50 > sma200:
            trend = "Uptrend"
        elif last < sma50 < sma200:
            trend = "Downtrend"
        else:
            trend = "Mixed"
    else:
        trend = "n/a"

    r1m, r3m = pct(last, back(21)), pct(last, back(63))
    momentum = None if r1m is None or r3m is None else round(0.5 * r1m + 0.5 * r3m, 2)

    return {
        "price": round(last, 2),
        "chg_1d": pct(last, back(1)),
        "chg_1w": pct(last, back(5)),
        "chg_1m": r1m,
        "chg_3m": r3m,
        "chg_ytd": pct(last, float(year_start.iloc[0])) if len(year_start) else None,
        "chg_1y": pct(last, back(252)),
        "sma50": round(sma50, 2) if sma50 else None,
        "sma200": round(sma200, 2) if sma200 else None,
        "rsi14": rsi(close),
        "off_52w_high": pct(last, hi52),
        "trend": trend,
        "momentum": momentum,
        # ~6 months of closes for the sparkline / detail chart
        "history": [[d.strftime("%Y-%m-%d"), round(float(v), 2)] for d, v in close.tail(126).items()],
    }


def google_news(query: str, limit: int) -> list[dict]:
    url = ("https://news.google.com/rss/search?q="
           + urllib.parse.quote(f"{query} when:{NEWS_MAX_AGE_DAYS}d")
           + "&hl=en-US&gl=US&ceid=US:en")
    feed = feedparser.parse(url)
    items = []
    for e in feed.entries[:limit]:
        title = e.get("title", "")
        source = e.get("source", {}).get("title", "")
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        published = None
        if e.get("published_parsed"):
            published = dt.datetime(*e.published_parsed[:6], tzinfo=dt.timezone.utc).isoformat()
        items.append({"title": title, "url": e.get("link"), "source": source,
                      "published": published, "tone": headline_tone(title),
                      "political": is_political(title)})
    return items


def ex_dividend(ticker: yf.Ticker) -> str | None:
    try:
        cal = ticker.calendar or {}
        d = cal.get("Ex-Dividend Date") if isinstance(cal, dict) else None
        return d.isoformat() if d else None
    except Exception:
        return None


def main() -> None:
    cfg = yaml.safe_load(CONFIG.read_text())
    etfs = [dict(e, group=g) for g, items in cfg["groups"].items() for e in items]
    tickers = [e["ticker"] for e in etfs]

    prices = yf.download(tickers, period="14mo", interval="1d", auto_adjust=True,
                         progress=False, group_by="ticker", threads=True)

    out_etfs, all_news = [], []
    for e in etfs:
        t = e["ticker"]
        row = {"ticker": t, "name": e["name"], "group": e["group"]}
        try:
            close = prices[t]["Close"] if isinstance(prices.columns, pd.MultiIndex) else prices["Close"]
            row.update(metrics(close))
        except Exception as exc:  # keep going if one ticker fails
            row["error"] = f"price data unavailable: {exc}"

        news = google_news(f'"{t}" ETF OR {e["query"]}', NEWS_PER_ETF)
        time.sleep(0.5)  # be polite to the RSS endpoint
        row["news"] = news
        row["news_tone"] = round(sum(n["tone"] for n in news) / len(news), 2) if news else 0
        row["ex_dividend"] = ex_dividend(yf.Ticker(t))
        for n in news:
            all_news.append({**n, "ticker": t, "name": e["name"]})
        out_etfs.append(row)

    # Global / political headlines feed
    macro = google_news("global markets geopolitics OR tariffs OR elections OR central bank", 15)

    today = dt.date.today()
    events = []
    for ev in cfg.get("events", []):
        d = ev["date"] if isinstance(ev["date"], dt.date) else dt.date.fromisoformat(str(ev["date"]))
        if d >= today:
            events.append({"date": d.isoformat(), "title": ev["title"], "tags": ev.get("tags", [])})
    for r in out_etfs:
        if r.get("ex_dividend") and r["ex_dividend"] >= today.isoformat():
            events.append({"date": r["ex_dividend"], "title": f'{r["ticker"]} ex-dividend', "tags": ["Dividend"]})
    events.sort(key=lambda x: x["date"])

    # de-duplicate news by title, newest first
    seen, feed = set(), []
    for n in sorted(all_news, key=lambda n: n["published"] or "", reverse=True):
        if n["title"] not in seen:
            seen.add(n["title"])
            feed.append(n)

    ok = sum(1 for r in out_etfs if "price" in r)
    if ok == 0:
        raise SystemExit("No price data fetched — keeping previous data.json")

    data = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "groups": list(cfg["groups"].keys()),
        "etfs": out_etfs,
        "macro_news": macro,
        "news_feed": feed[:60],
        "events": events[:25],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1))
    print(f"Wrote {OUT} — {len(out_etfs)} ETFs, {len(feed)} headlines, {len(events)} events")


if __name__ == "__main__":
    main()
