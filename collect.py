#!/usr/bin/env python3
"""Collect free crypto-sentiment inputs and keep data/daily.csv up to date.

Sources (all free):
  - Fear & Greed Index ........ api.alternative.me (no key)
  - BTC price, funding, L/S ... OKX public API (no key, works from US servers)
  - CryptoPanic votes ......... optional, needs free CRYPTOPANIC_TOKEN
                                (forward-only: no history, so it builds up over time)

Usage:
  python collect.py            # update today's row (backfills history on first run)
  python collect.py --backfill # force a history refresh
  python collect.py --mock     # write synthetic data for testing the dashboard

Standard library only.
"""
import argparse
import csv
import json
import math
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA = Path(__file__).parent / "data"
DAILY = DATA / "daily.csv"
CP = DATA / "cryptopanic.csv"
DAILY_COLS = ["date", "price", "fng", "cmc_fng", "funding", "ls_ratio"]
CP_COLS = ["timestamp", "positive", "negative", "posts"]
UA = {"User-Agent": "crypto-sentiment-dashboard/1.0"}
OKX = "https://www.okx.com"
BACKFILL_DAYS = 400
PRICE_BACKFILL_DAYS = 3300   # match Fear & Greed history (2018 onward)
MARKER = DATA / "backfill_v2"  # bump the name to force a fresh backfill
CMC_MARKER = DATA / "backfill_cmc"
CMC = "https://pro-api.coinmarketcap.com"


def get(url, params=None, timeout=20, headers=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def day(ts_ms):
    return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def safe(label, fn, default):
    """Run a fetcher; on any failure log it and carry on with a default."""
    try:
        return fn()
    except Exception as e:  # network, parse, geo-block...
        print(f"[warn] {label} failed: {e}", file=sys.stderr)
        return default


# ---------- fetchers ----------

def fetch_fng():
    """Daily Fear & Greed value 0-100, full history."""
    js = get("https://api.alternative.me/fng/", {"limit": 0, "format": "json"})
    return {
        datetime.fromtimestamp(int(r["timestamp"]), tz=timezone.utc).strftime("%Y-%m-%d"): float(r["value"])
        for r in js["data"]
    }


def fetch_price_history(days=BACKFILL_DAYS):
    """Daily BTC close from OKX history candles (newest first, 100 per page)."""
    out, after = {}, None
    while len(out) < days:
        p = {"instId": "BTC-USDT", "bar": "1Dutc", "limit": 100}
        if after:
            p["after"] = after
        rows = get(f"{OKX}/api/v5/market/history-candles", p).get("data", [])
        if not rows:
            break
        for r in rows:
            out[day(r[0])] = float(r[4])
        after = rows[-1][0]
        time.sleep(0.2)  # stay well inside OKX rate limits
    return out


def fetch_price_now():
    js = get(f"{OKX}/api/v5/market/ticker", {"instId": "BTC-USDT"})
    return float(js["data"][0]["last"])


def fetch_funding_history(days=BACKFILL_DAYS):
    """BTC perp funding (per 8h), averaged per day. OKX may cap history depth."""
    buckets, after = {}, None
    for _ in range(days // 30 + 3):
        p = {"instId": "BTC-USDT-SWAP", "limit": 100}
        if after:
            p["after"] = after
        rows = get(f"{OKX}/api/v5/public/funding-rate-history", p).get("data", [])
        if not rows:
            break
        for r in rows:
            buckets.setdefault(day(r["fundingTime"]), []).append(float(r["fundingRate"]))
        after = rows[-1]["fundingTime"]
    return {d: sum(v) / len(v) for d, v in buckets.items()}


def fetch_funding_now():
    js = get(f"{OKX}/api/v5/public/funding-rate", {"instId": "BTC-USDT-SWAP"})
    return float(js["data"][0]["fundingRate"])


def fetch_ls_history():
    """Daily long/short account ratio (>1 means more accounts long)."""
    js = get(f"{OKX}/api/v5/rubik/stat/contracts/long-short-account-ratio", {"ccy": "BTC", "period": "1D"})
    return {day(r[0]): float(r[1]) for r in js.get("data", [])}


def _cmc_day(ts):
    """CMC timestamps come as unix seconds (string) or ISO text."""
    ts = str(ts)
    if ts.isdigit():
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    return ts[:10]


def _cmc_headers():
    key = os.environ.get("CMC_API_KEY")
    return {"X-CMC_PRO_API_KEY": key, "Accept": "application/json"} if key else None


def fetch_cmc_history():
    """CoinMarketCap Fear & Greed, full history (needs free CMC_API_KEY)."""
    h = _cmc_headers()
    if not h:
        return {}
    out, start = {}, 1
    for _ in range(40):
        rows = get(f"{CMC}/v3/fear-and-greed/historical", {"start": start, "limit": 500}, headers=h).get("data") or []
        if not rows:
            break
        for r in rows:
            out[_cmc_day(r["timestamp"])] = float(r["value"])
        start += len(rows)
        time.sleep(2.5)  # free plan: 30 requests/minute
    return out


def fetch_cmc_now():
    h = _cmc_headers()
    if not h:
        return None
    d = get(f"{CMC}/v3/fear-and-greed/latest", headers=h)["data"]
    return float(d["value"])


def fetch_cryptopanic():
    """Count community bullish/bearish votes on the latest BTC posts.

    Needs a free token from https://cryptopanic.com/developers/api/ in CRYPTOPANIC_TOKEN.
    The API path has changed between plans; override with CRYPTOPANIC_URL if needed.
    """
    token = os.environ.get("CRYPTOPANIC_TOKEN")
    if not token:
        return None
    url = os.environ.get("CRYPTOPANIC_URL", "https://cryptopanic.com/api/developer/v2/posts/")
    js = get(url, {"auth_token": token, "currencies": "BTC", "public": "true"})
    pos = neg = 0
    results = js.get("results", [])
    for post in results:
        v = post.get("votes") or {}
        pos += int(v.get("positive", 0) or 0)
        neg += int(v.get("negative", 0) or 0)
    return pos, neg, len(results)


# ---------- storage ----------

def load_daily():
    if not DAILY.exists():
        return {}
    with open(DAILY, newline="") as f:
        return {r["date"]: r for r in csv.DictReader(f)}


def save_daily(rows):
    DATA.mkdir(exist_ok=True)
    with open(DAILY, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DAILY_COLS)
        w.writeheader()
        for d in sorted(rows):
            w.writerow({c: rows[d].get(c, "") for c in DAILY_COLS})


def merge(rows, col, series):
    for d, v in series.items():
        rows.setdefault(d, {"date": d})[col] = f"{v:.8g}"


def append_cp(pos, neg, posts):
    DATA.mkdir(exist_ok=True)
    new = not CP.exists()
    with open(CP, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(CP_COLS)
        w.writerow([datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), pos, neg, posts])


# ---------- modes ----------

def run_live(force_backfill):
    if (DATA / "MOCK").exists():
        sys.exit("data/ holds synthetic MOCK data. Delete data/MOCK and data/daily.csv first.")
    rows = load_daily()
    if force_backfill or len(rows) < 30 or not MARKER.exists():
        print("Backfilling history...")
        merge(rows, "price", safe("price history", lambda: fetch_price_history(PRICE_BACKFILL_DAYS), {}))
        merge(rows, "fng", safe("fear&greed history", fetch_fng, {}))
        merge(rows, "funding", safe("funding history", fetch_funding_history, {}))
        merge(rows, "ls_ratio", safe("long/short history", fetch_ls_history, {}))
        DATA.mkdir(exist_ok=True)
        MARKER.write_text("history backfilled\n")
    if os.environ.get("CMC_API_KEY") and not CMC_MARKER.exists():
        print("Backfilling CMC Fear & Greed...")
        cmc = safe("cmc history", fetch_cmc_history, {})
        merge(rows, "cmc_fng", cmc)
        if cmc:
            CMC_MARKER.write_text("cmc backfilled\n")
    # today's values, every run
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    r = rows.setdefault(today, {"date": today})
    for col, fn in [("price", fetch_price_now), ("funding", fetch_funding_now)]:
        v = safe(col, fn, None)
        if v is not None:
            r[col] = f"{v:.8g}"
    fng = safe("fear&greed", fetch_fng, {})
    if today in fng:
        r["fng"] = f"{fng[today]:.8g}"
    cmc_now = safe("cmc", fetch_cmc_now, None)
    if cmc_now is not None:
        r["cmc_fng"] = f"{cmc_now:.8g}"
    ls = safe("long/short", fetch_ls_history, {})
    merge(rows, "ls_ratio", ls)  # fills any recent days that were missing
    if not rows:
        sys.exit("No data collected - every source failed.")
    save_daily(rows)

    print(f"daily.csv: {len(rows)} days")


def run_mock():
    """Synthetic data so the dashboard can be tested offline. NOT real."""
    random.seed(7)
    rows, price, mood = {}, 30000.0, 0.0
    start = datetime.now(timezone.utc).date() - timedelta(days=BACKFILL_DAYS)
    rets = []
    for i in range(BACKFILL_DAYS + 1):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        r = random.gauss(0.0005, 0.025)
        rets.append(r)
        price *= 1 + r
        recent = sum(rets[-7:])
        mood = 0.8 * mood + 0.2 * (recent * 8) + random.gauss(0, 0.12)  # lags price
        m = max(-1, min(1, mood))
        rows[d] = {
            "date": d,
            "price": f"{price:.2f}",
            "fng": f"{max(2, min(98, 50 + 45 * m + random.gauss(0, 5))):.0f}",
            "cmc_fng": f"{max(2, min(98, 50 + 40 * m + random.gauss(0, 6))):.0f}" if i > 150 else "",
            "funding": f"{0.0001 + 0.0002 * m + random.gauss(0, 0.00005):.8f}",
            "ls_ratio": f"{math.exp(0.25 * m + random.gauss(0, 0.05)):.4f}",
        }
    save_daily(rows)
    CP.unlink(missing_ok=True)
    (DATA / "MOCK").write_text("synthetic data - delete this file and daily.csv before going live\n")
    print(f"Wrote {len(rows)} MOCK days to {DAILY}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()
    run_mock() if a.mock else run_live(a.backfill)
