# Crypto sentiment dashboard (free sources only)

Builds a composite sentiment score from free data, plots it against BTC price, and measures
how well it has predicted later price moves.

| Input | Source | Key needed |
|---|---|---|
| Fear & Greed Index | alternative.me | no |
| BTC price, perp funding rate, long/short ratio | OKX public API | no |
| Community bullish/bearish votes | CryptoPanic | free token (optional) |

Reddit, StockTwits, X, LunarCrush and Santiment are left out on purpose: their free tiers are
restricted, need approval, or forbid automated access.

## Run on GitHub (free, hourly, viewable anywhere)

1. Create a new GitHub repo and push this folder to it.
2. Repo **Settings > Pages**: source = *Deploy from a branch*, branch `main`, folder `/docs`.
3. Optional: get a free token at https://cryptopanic.com/developers/api/ and add it under
   **Settings > Secrets and variables > Actions** as `CRYPTOPANIC_TOKEN`.
4. **Actions** tab: enable workflows, open "Collect sentiment and rebuild dashboard", click **Run workflow**.
   The first run backfills about a year of history; later runs update today's row each hour.
5. Your dashboard is at `https://<your-username>.github.io/<repo>/`.

## Run locally

```
python collect.py      # fetch data (first run backfills history)
python build.py        # writes docs/index.html
```
Open `docs/index.html` in a browser. No packages to install.

## Try it with fake data first

```
python collect.py --mock && python build.py
```
Delete `data/MOCK` and `data/daily.csv` before running for real.

## Adding what people are saying (manual, by hand)

Copy posts yourself from Discord, X or Reddit (select, copy, paste). Don't use extensions or
scripts that collect them automatically: that breaks those sites' terms.

**Option A - paste a file (scored automatically by a word list)**
1. Make a text file in `manual/` named `<source>-YYYY-MM-DD.txt`,
   e.g. `manual/discord-lounge-2026-11-09.txt` or `manual/x-timeline-2026-11-09.txt`.
2. Paste the messages in, one per line. It needs at least 5 lines containing sentiment words.
3. Commit/upload the file to the repo (GitHub web: *Add file > Upload files*). The next hourly run scores it.

**Option B - log a score (more accurate)**
Paste the chat to Claude and ask for a -1 to +1 score, or judge it yourself, then:
```
python log_score.py discord-lounge -0.6 "bearish, TA calling new lows"
```
or add a line to `data/manual_scores.csv` on GitHub: `2026-11-09,discord-lounge,-0.6,note`.
A logged score replaces the word-list score for the same source and date.

Keep it consistent: same channels, roughly the same time each day. Results mean little
before ~100 logged days.

## Notes
- If CryptoPanic returns errors, check the API URL on their docs page and set `CRYPTOPANIC_URL`.
- If OKX history is shorter than a year, funding and long/short ratio simply have fewer days;
  the score uses whichever inputs exist.
- The weights and scales at the top of `build.py` are starting guesses, not tuned values.
