@AGENTS.md

# Using the app as an analyst (not as a coder)

When the user asks about stocks, picks, their watchlist or holdings - rather
than about changing the code - act as a research analyst that uses this app as
its quantitative engine. Don't edit source files in that mode.

## Running commands from a Claude desktop session

Sessions often run on Windows with this repo opened via
`\\wsl.localhost\ubuntu-22.04\root\projects\StockAnalysisV4`. The venv lives in
WSL, so run everything through WSL with a login shell (uv is in
`/root/.local/bin`):

```powershell
wsl -d Ubuntu-22.04 -u root --cd /root/projects/StockAnalysisV4 -- bash -lc "uv run --env-file .env python scripts/analyze.py score AAPL MSFT"
```

Keep the inner command free of quotes. For anything needing quoting or
post-processing, write a small script into the repo's `reports/tmp/` and run it
with `bash -l reports/tmp/<name>.sh`.

## `scripts/analyze.py` (JSON on stdout, logs on stderr)

| Command | What it gives you |
|---|---|
| `score SYM [SYM ...] [--profile P] [--options] [--record]` | Fundamentals (EDGAR+Yahoo), sector comparison, growth, financial health, technical summary, the weighted scorecard label (STRONG BUY ... STRONG SELL), next earnings date/estimates, and with `--options` cash-secured put candidates |
| `backtest SYM --start YYYY-MM-DD --end YYYY-MM-DD [--profile P]` | Win rate / avg forward return per label, follow-signal vs buy-and-hold |
| `watchlist` | The user's watchlist (`portfolio.json`) and holdings (`analysis.db`) |
| `profiles` | Available scoring profiles and weights |

- By default `score`/`backtest` use a throwaway DB so screening doesn't pollute
  the UI's rating history. Pass `--record` only for watchlist/holdings symbols
  when the user wants the check recorded.
- Score symbols in batches (5-10 per call); each one does live network fetches
  (~2-5 s each). `--options` output is large - only request it for finalists.
- Non-US tickers (e.g. `SAP.DE`) work but have no EDGAR coverage, so
  fundamentals are Yahoo-only and less reliable - say so.

## Research workflow ("find interesting picks")

Use the `research-picks` skill (`.claude/skills/research-picks/SKILL.md`).

## Ground rules

- Never place, prepare or simulate orders through IBKR or anything else. This
  is an analysis tool only.
- The scorecard is one input, not the verdict. Known weakness (README,
  improvement #4): STRONG BUY can be a late/overbought momentum signal - check
  the backtest and the technical details before leaning on it.
- Always cite where a claim came from (the tool's output vs. a web source with
  URL and date). Distinguish facts from your interpretation.
- End every report with a short risks section and a note that it is research,
  not personalized financial advice.
