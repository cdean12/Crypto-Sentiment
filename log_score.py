#!/usr/bin/env python3
"""Log a hand-made (or Claude-made) sentiment score for a source.

  python log_score.py discord-lounge -0.4 "mostly bearish, TA arguing for new lows"
  python log_score.py x-timeline 0.2 --date 2026-11-08

Score runs from -1 (very bearish) to +1 (very bullish). A manual score for a given
date + source overrides the automatic word-list score for the same file.
"""
import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

PATH = Path(__file__).parent / "data" / "manual_scores.csv"

ap = argparse.ArgumentParser()
ap.add_argument("source")
ap.add_argument("score", type=float)
ap.add_argument("note", nargs="?", default="")
ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
a = ap.parse_args()
if not -1 <= a.score <= 1:
    raise SystemExit("score must be between -1 and 1")
PATH.parent.mkdir(exist_ok=True)
new = not PATH.exists()
with open(PATH, "a", newline="") as f:
    w = csv.writer(f)
    if new:
        w.writerow(["date", "source", "score", "note"])
    w.writerow([a.date, a.source, a.score, a.note])
print(f"logged {a.source} {a.score:+.2f} for {a.date}")
