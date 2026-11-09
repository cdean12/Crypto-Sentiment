"""Rough crypto-aware sentiment scoring for pasted chat text. Standard library only.

This is a word list, not understanding: it misses sarcasm and context. Use it for a
consistent, free baseline, and override a day with your own (or Claude's) score in
data/manual_scores.csv when you want something more accurate.
"""
import re

BULL = [
    "bullish", "bull run", "bull market", "moon", "mooning", "pump", "pumping", "breakout", "broke out",
    "rally", "rallying", "send it", "long", "longing", "buy", "buying", "accumulate", "accumulating",
    "higher high", "higher low", "all time high", "ath", "uptrend", "reversal up", "bottom is in",
    "strong", "support held", "reclaim", "reclaimed", "green", "lfg", "gains", "profit", "undervalued",
    "bounce", "bounces", "bullish cross", "golden cross", "upside", "recover", "recovery",
    "hh", "hl", "inverse head and shoulders", "double bottom", "above the",
]
BEAR = [
    "bearish", "bear market", "bear", "dump", "dumping", "crash", "crashing", "rekt", "liquidated",
    "short", "shorting", "sell", "selling", "capitulation", "lower low", "lower high", "new atl", "atl",
    "downtrend", "downside", "breakdown", "broke down", "rejected", "rejection", "weak", "red",
    "lost support", "dead", "rug", "scam", "fear", "panic", "overvalued", "bearish cross", "death cross",
    "new lows", "major lows", "lower lows", "going lower", "more pain", "bleeding", "hit and fail",
    "ll", "lh", "double top", "head and shoulders", "under the", "below the", "losing", "incoming lows",
]
NEGATORS = {"not", "no", "isn't", "isnt", "wont", "won't", "never", "without", "doubt"}

_PAT = lambda words: re.compile(r"\b(" + "|".join(sorted(map(re.escape, words), key=len, reverse=True)) + r")\b")
_BULL, _BEAR = _PAT(BULL), _PAT(BEAR)


def score_line(line):
    """Return (-1..1, hits) for one message line, or (None, 0) if no sentiment words found."""
    t = line.lower()
    # "X, but Y": the clause after the last "but" is what the writer means
    if " but " in t:
        t = t.rsplit(" but ", 1)[1]
    bull = bear = 0
    for pat, is_bull in ((_BULL, True), (_BEAR, False)):
        for m in pat.finditer(t):
            before = t[: m.start()].split()[-4:]
            flipped = any(w in NEGATORS for w in before)
            if is_bull != flipped:
                bull += 1
            else:
                bear += 1
    hits = bull + bear
    return ((bull - bear) / hits, hits) if hits else (None, 0)


def score_text(text, min_lines=5):
    """Average line scores. Returns (score, scored_lines) or (None, scored_lines) if too few."""
    vals = []
    for line in text.splitlines():
        s, hits = score_line(line)
        if s is not None:
            vals.append(s)
    if len(vals) < min_lines:
        return None, len(vals)
    return sum(vals) / len(vals), len(vals)
