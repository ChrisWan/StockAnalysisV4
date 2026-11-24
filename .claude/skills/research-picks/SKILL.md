---
name: research-picks
description: Autonomously research the market for interesting stock candidates, screen them with this repo's scoring engine (scripts/analyze.py), dig into the best ones, and write a report. Use when the user asks for new picks, ideas, a screen, a market scan, or a research report on their watchlist/holdings.
---

# Research picks

Goal: a short, well-sourced list of candidates the user may want to look at,
each backed by this app's quantitative scorecard *and* qualitative research.
Read CLAUDE.md first for how to run `scripts/analyze.py` from Windows.

Arguments the user may give (otherwise use these defaults): theme/sector
(none - broad scan), region (US, since EDGAR coverage is best), number of
final picks (5), scoring profile (`balanced`), include options ideas (no).

## 1. Context (1 call)

Run `analyze.py watchlist` so you know what the user already watches/owns.
Treat those as known - favor new names, but flag if a holding looks weak.

## 2. Generate a candidate universe (15-30 tickers)

Use web search for current, dated sources (last ~30 days). Mix angles so the
list isn't just whatever is trending:
- Earnings beats / guidance raises in the latest reporting season
- Analyst upgrades, notable estimate revisions
- Sector/theme momentum (if the user gave a theme, focus there)
- Quality names that pulled back (good fundamentals, recent drawdown)
- Insider buying, buybacks, spin-offs or other catalysts

Record for each ticker *why* it entered the list and the source URL.
Skip micro-caps (< ~$2B market cap) and illiquid names unless asked.

## 3. Screen with the app

`analyze.py score` the universe in batches. Build a table: symbol, sector,
label, total score, the three component scores, P/E vs sector, revenue
growth, technical setup, days to earnings. Drop SELL/STRONG SELL and names
where the fetch failed (note them).

## 4. Deep dive on the top ~8

For each:
- `analyze.py backtest SYM --start <~2 years ago> --end <today>` - does the
  scorecard have a track record on this name? Note win rate of BUY vs HOLD.
- Read 2-3 recent sources: latest earnings/guidance, the bull and bear case,
  key risks (valuation, debt, concentration, regulation).
- Check the earnings date - flag picks reporting within ~2 weeks.
- If the user asked for options ideas: `score SYM --options` and summarize at
  most 3 put candidates (prefer 20-45 days to expiry; mention `premium_source`
  when it's `last_trade`).

## 5. Report

Write `reports/YYYY-MM-DD-<slug>.md` (create `reports/` if needed) with:
1. Summary - the final picks in one table (symbol, label/score, one-line thesis,
   main risk, next earnings)
2. One section per pick: thesis, what the scorecard says and why, backtest
   evidence, catalysts, risks, sources
3. Names screened out and why (brief)
4. Method and caveats (data sources, scorecard limitations, date of data)
5. "Research, not personalized financial advice" note

Then reply in chat with the summary table and the file path, and invite
follow-ups ("dig deeper into X", "compare X and Y", "add X to watchlist").
Only add to the watchlist or holdings if the user explicitly asks.

Work autonomously through steps 1-5 without stopping to ask, unless the
request is ambiguous in a way that changes the whole scan.
