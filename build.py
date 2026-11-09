#!/usr/bin/env python3
"""Turn data/*.csv into a composite sentiment score, test it against price,
and write a self-contained dashboard to docs/index.html. Standard library only."""
import csv
import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
DAILY = ROOT / "data" / "daily.csv"
CP = ROOT / "data" / "cryptopanic.csv"
OUT = ROOT / "docs" / "index.html"

# Weights are renormalised over whichever components exist on a given day.
WEIGHTS = {"fng": 0.30, "funding": 0.25, "ls": 0.10, "cp": 0.10, "soc": 0.25}
MANUAL_DIR = ROOT / "manual"
MANUAL_CSV = ROOT / "data" / "manual_scores.csv"
FUNDING_SCALE = 0.0003   # +/-0.03% per 8h maps to +/-1 (extreme crowded long/short)
LS_WINDOW = 90           # long/short is scored vs its own trailing history (it sits above 1 normally)
LS_MIN = 14             # need this many days before scoring it
CP_MIN_VOTES = 20        # ignore days with too few votes to mean anything
HORIZONS = [1, 7, 30]    # forward-return horizons in days
LEVEL_THRESHOLD = 0.10   # |score| below this counts as neutral for hit-rate


def clip(x):
    return max(-1.0, min(1.0, x))


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


FILL_DAYS = 2  # carry a value forward this many days when a source hasn't published yet


def load_daily():
    with open(DAILY, newline="") as fh:
        rows = sorted(csv.DictReader(fh), key=lambda r: r["date"])
    # start the series at the first day with a price
    first = next((i for i, r in enumerate(rows) if f(r.get("price")) is not None), len(rows))
    rows = rows[first:]
    for col in ("fng", "funding", "ls_ratio"):
        last, age = None, 0
        for r in rows:
            if f(r.get(col)) is not None:
                last, age = r[col], 0
            elif last is not None and age < FILL_DAYS:
                r[col], age = last, age + 1
            else:
                last = None
    return rows


def load_cp_daily():
    """Mean (pos-neg)/(pos+neg) per day from hourly CryptoPanic snapshots."""
    if not CP.exists():
        return {}
    acc = {}
    with open(CP, newline="") as fh:
        for r in csv.DictReader(fh):
            p, n = f(r["positive"]) or 0, f(r["negative"]) or 0
            if p + n >= CP_MIN_VOTES:
                acc.setdefault(r["timestamp"][:10], []).append((p - n) / (p + n))
    return {d: sum(v) / len(v) for d, v in acc.items()}


def load_social():
    """Daily social score from manual/<source>-YYYY-MM-DD.txt files (word-list scored)
    and data/manual_scores.csv (hand/Claude scores, which win for the same date+source)."""
    import re
    from lexicon import score_text
    per = {}  # (date, source) -> (score, method, lines)
    if MANUAL_DIR.exists():
        for p in sorted(MANUAL_DIR.glob("*.txt")):
            m = re.match(r"(.+)-(\d{4}-\d{2}-\d{2})$", p.stem)
            if not m:
                print(f"[skip] {p.name}: name it <source>-YYYY-MM-DD.txt")
                continue
            s, n = score_text(p.read_text(errors="ignore"))
            if s is None:
                print(f"[skip] {p.name}: only {n} lines with sentiment words (need 5)")
                continue
            per[(m.group(2), m.group(1))] = (s, "word list", n)
    if MANUAL_CSV.exists():
        with open(MANUAL_CSV, newline="") as fh:
            for r in csv.DictReader(fh):
                if f(r["score"]) is not None:
                    per[(r["date"], r["source"])] = (clip(f(r["score"])), "manual", None)
    days, log = {}, []
    for (d, src), (s, how, n) in sorted(per.items()):
        days.setdefault(d, []).append(s)
        log.append({"d": d, "src": src, "s": s, "how": how, "n": n})
    return {d: sum(v) / len(v) for d, v in days.items()}, log[-15:][::-1]


def ls_scores(rows):
    """Rolling z-score of ln(long/short ratio), /2 and clipped, keyed by date."""
    out, hist = {}, []
    for row in rows:
        r = f(row.get("ls_ratio"))
        if not r or r <= 0:
            continue
        hist.append(math.log(r))
        w = hist[-LS_WINDOW:]
        if len(w) < LS_MIN:
            continue
        m = sum(w) / len(w)
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / len(w))
        out[row["date"]] = clip((w[-1] - m) / sd / 2) if sd > 0 else 0.0
    return out


def components(row, cp, soc=None, ls=None):
    c = {}
    if f(row["fng"]) is not None:
        c["fng"] = clip((f(row["fng"]) - 50) / 50)
    if f(row["funding"]) is not None:
        c["funding"] = clip(f(row["funding"]) / FUNDING_SCALE)
    if ls and row["date"] in ls:
        c["ls"] = ls[row["date"]]
    if row["date"] in cp:
        c["cp"] = clip(cp[row["date"]])
    if soc and row["date"] in soc:
        c["soc"] = soc[row["date"]]
    return c


def composite(c):
    if not c:
        return None
    w = sum(WEIGHTS[k] for k in c)
    return sum(WEIGHTS[k] * v for k, v in c.items()) / w


def pearson(xs, ys):
    n = len(xs)
    if n < 10:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def accuracy(series, key="score"):
    """Metrics per horizon for series[i][key] vs forward price returns."""
    series = [dict(s, score=s.get(key)) for s in series]
    by_date = {s["date"]: s for s in series if s["price"]}
    out = []
    for h in HORIZONS:
        lv, ch, ret_l, ret_c = [], [], [], []
        up_all = tot_all = hits = hit_n = 0
        for s in series:
            if s["score"] is None or not s["price"]:
                continue
            d = datetime.strptime(s["date"], "%Y-%m-%d")
            fut = by_date.get((d + timedelta(days=h)).strftime("%Y-%m-%d"))
            if not fut:
                continue
            ret = fut["price"] / s["price"] - 1
            lv.append(s["score"]); ret_l.append(ret)
            tot_all += 1; up_all += ret > 0
            if abs(s["score"]) >= LEVEL_THRESHOLD:
                hit_n += 1; hits += (s["score"] > 0) == (ret > 0)
            prev = by_date.get((d - timedelta(days=7)).strftime("%Y-%m-%d"))
            if prev and prev["score"] is not None:
                ch.append(s["score"] - prev["score"]); ret_c.append(ret)
        out.append({
            "horizon": h,
            "n": len(lv),
            "corr_level": pearson(lv, ret_l),
            "corr_change": pearson(ch, ret_c),
            "hit_rate": hits / hit_n if hit_n else None,
            "hit_n": hit_n,
            "base_up": up_all / tot_all if tot_all else None,
        })
    return out


def label(s):
    if s is None:
        return "n/a"
    for cut, name in [(-0.5, "Extreme fear"), (-0.15, "Fear"), (0.15, "Neutral"), (0.5, "Greed")]:
        if s < cut:
            return name
    return "Extreme greed"


def main():
    rows = load_daily()
    cp = load_cp_daily()
    soc, soc_log = load_social()
    ls = ls_scores(rows)
    series, latest_c = [], {}
    for r in rows:
        c = components(r, cp, soc, ls)
        s = composite(c)
        series.append({"date": r["date"], "price": f(r["price"]), "score": s, "soc": c.get("soc"), "c": c})
        if c:
            latest_c = c
    acc = accuracy(series)
    acc_soc = accuracy(series, "soc")
    last_soc = next((s for s in reversed(series) if s["soc"] is not None), None)
    last = next((s for s in reversed(series) if s["score"] is not None), None)
    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "series": [{"d": s["date"], "p": s["price"], "s": s["score"], "m": s["soc"]} for s in series],
        "components": latest_c,
        "social": {"days": len(soc), "log": soc_log, "accuracy": acc_soc,
                   "latest": {"score": last_soc["soc"], "date": last_soc["date"]} if last_soc else None},
        "weights": WEIGHTS,
        "latest": {"score": last["score"] if last else None, "label": label(last["score"] if last else None),
                   "date": last["date"] if last else None},
        "accuracy": acc,
        "cp_days": len(cp),
        "mock": (ROOT / "data" / "MOCK").exists(),
    }
    OUT.parent.mkdir(exist_ok=True)
    html = (ROOT / "template.html").read_text().replace("__DATA__", json.dumps(payload))
    OUT.write_text(html)
    print(f"Wrote {OUT} ({len(series)} days, latest {payload['latest']['label']})")


if __name__ == "__main__":
    main()
