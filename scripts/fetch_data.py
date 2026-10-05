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


PRE, POST = 90, 60  # trading days shown before / after election day


def _d(x) -> dt.date:
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x))


def cycle_path(close: pd.Series, anchor: dt.date) -> dict | None:
    """Price path around `anchor`, rebased to 100 at the start of the window.

    Offsets are trading days relative to election day (day 0 = first session on/after it).
    For an upcoming election the path stops today, at a negative offset.
    """
    close = close.dropna()
    if close.empty:
        return None
    last = close.index[-1].date()
    if anchor <= last:  # past (or just happened)
        pos = int(close.index.searchsorted(pd.Timestamp(anchor)))
        if pos - PRE < 0:
            return None
        win = close.iloc[pos - PRE: pos + POST + 1]
        offsets = list(range(-PRE, -PRE + len(win)))
    else:  # upcoming: today is `n` sessions before day 0
        day0 = pd.Timestamp(anchor) + pd.offsets.BDay(0)  # roll weekend election to Monday
        n = int(pd.bdate_range(last, day0).size) - 1
        if n >= PRE:
            return {"points": [], "days_to_go": n}
        win = close.iloc[-(PRE - n + 1):]
        offsets = list(range(-n - len(win) + 1, -n + 1))
    base = float(win.iloc[0])
    pts = [[o, round(float(v) / base * 100, 2)] for o, v in zip(offsets, win)]
    lookup = dict(pts)

    def ret(a, b):
        return round((lookup[b] / lookup[a] - 1) * 100, 2) if a in lookup and b in lookup else None

    end = max(lookup)
    return {
        "points": pts,
        "run_up": ret(-PRE, 0 if 0 in lookup else end),   # into the vote
        "reaction": ret(-1, 0),                            # first session after the vote
        "week_after": ret(-1, 5),
        "after": ret(0, POST) if POST in lookup else None,  # 3 months after
    }


def build_politics(cfg: dict, today: dt.date) -> tuple[list, list]:
    """Event-study data for politics.yml. Returns (events, calendar items)."""
    events = cfg.get("events", [])
    tickers = sorted({t for e in events for t in e.get("etfs", [])})
    hist = yf.download(tickers, start="2009-06-01", interval="1d", auto_adjust=True,
                       progress=False, group_by="ticker", threads=True) if tickers else None

    def closes(t):
        try:
            return hist[t]["Close"] if isinstance(hist.columns, pd.MultiIndex) else hist["Close"]
        except Exception:
            return pd.Series(dtype=float)

    out, cal = [], []
    for e in events:
        date = _d(e["date"])
        ev = {k: e.get(k) for k in ("id", "title", "country", "status", "etfs", "drivers")}
        ev["date"] = date.isoformat()
        ev["milestones"] = [{"date": _d(m["date"]).isoformat(), "label": m["label"]} for m in e.get("milestones", [])]
        ev["cycles"] = {}
        for t in e.get("etfs", []):
            c = closes(t)
            ev["cycles"][t] = {
                "current": cycle_path(c, date),
                "past": [dict(label=p["label"], date=_d(p["date"]).isoformat(), **(cycle_path(c, _d(p["date"])) or {}))
                         for p in e.get("past", [])],
            }
        if e.get("polls"):
            p = e["polls"]
            ev["polls"] = {"labels": p["labels"], "note": p.get("note", ""),
                           "rows": sorted([{**r, "date": _d(r["date"]).isoformat()} for r in p["rows"]],
                                          key=lambda r: r["date"])}
        ev["news"] = google_news(e.get("news_query", e["title"]), 10) if e.get("news_query") else []
        out.append(ev)

        for when, label in [(date, e["title"])] + [(_d(m["date"]), f'{e["country"]}: {m["label"]}') for m in e.get("milestones", [])]:
            if when >= today:
                cal.append({"date": when.isoformat(), "title": label, "tags": [e["country"], "Politics"],
                            "etfs": e.get("etfs", []), "event_id": e["id"]})
    for c in cfg.get("calendar", []):
        if _d(c["date"]) >= today:
            cal.append({"date": _d(c["date"]).isoformat(), "title": c["title"],
                        "tags": c.get("tags", []), "etfs": c.get("etfs", [])})
    out.sort(key=lambda x: x["date"])
    return out, cal


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
    political, pol_cal = [], []
    pol_file = ROOT / "politics.yml"
    if pol_file.exists():
        try:
            political, pol_cal = build_politics(yaml.safe_load(pol_file.read_text()) or {}, today)
        except Exception as exc:
            print(f"politics section failed: {exc}")
    events.extend(pol_cal)
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
        "political": political,
        "cycle_window": [-PRE, POST],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1))
    print(f"Wrote {OUT} — {len(out_etfs)} ETFs, {len(feed)} headlines, {len(events)} events")


if __name__ == "__main__":
    main()
