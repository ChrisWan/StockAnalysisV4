# Stock Analysis Application V4

Flask-based stock analysis dashboard that combines on-demand market data, peer-relative fundamental checks, and momentum indicators for a manually curated portfolio.

## What The App Does

- Loads stock data only when you select a symbol from the portfolio.
- Caches downloaded market and fundamental data in memory for one hour per symbol.
- Stores the last successful stock payload on disk so a recent snapshot can be reused if live fetches fail.
- Shows fundamental metrics such as valuation, profitability, growth, and financial health.
- Compares selected company metrics against sector benchmark medians stored in `sector_benchmarks.json`.
- Generates an interactive technical chart (candlesticks, moving averages, stochastic, MACD, buy/sell markers) rendered client-side.
- Produces an explicit recommendation scorecard by combining sector-relative fundamentals with recent technical signals.
- Shows the next earnings date and EPS/revenue estimates for the selected stock, cached for 24 hours since these rarely change day to day.
- Records each day's scorecard per symbol/profile and flags when a rating has changed since it was last checked - shown per stock when you open it, per position on the Holdings tab, and in-app for the whole Watchlist at once via a "Check for Changes" button (auto-run once on page load).
- Tracks a separate **Holdings** list (what you actually own - shares, average cost basis) alongside the **Watchlist**, with live unrealized P&L and current rating per position.
- Charts your Holdings' value over time against a benchmark (default SPY) using a dollar-value "shadow portfolio" - the same dollars invested in the benchmark on each position's actual purchase date - so multiple positions bought on different dates don't distort the comparison.
- Lets you **backtest** the recommendation scorecard against history for a chosen symbol and date range, using point-in-time SEC EDGAR data (not today's restated numbers) to avoid lookahead bias, and reports win rate / average forward return per label plus an equity curve.
- For a stock currently rated BUY/STRONG BUY, shows liquid, near-the-money, near-term **cash-secured put candidates** (strike, premium, breakeven, annualized yield) you could sell to open - premium collected now, with the strike as your entry price if assigned. Premium, breakeven, and yield are net of a modeled $1.00 IBKR commission (IBKR Pro Fixed/Lite, 1 contract) for USD contracts, and candidates where the commission would eat more than 25% of the premium are filtered out.

## Current Project Structure

```text
Stock AnalysisV4/
├── src/
│   ├── app.py               # Flask routes + in-memory fetch orchestration
│   ├── config.py            # Paths, cache limits, scoring weights/profiles, backtest defaults
│   ├── data_providers.py    # DataProvider interface, Yahoo provider (incl. put options), composition + selection
│   ├── edgar_provider.py    # SEC EDGAR XBRL fundamentals (validated, default; supports point-in-time as_of queries)
│   ├── ibkr_provider.py     # Interactive Brokers provider (optional, for options work)
│   ├── indicators.py        # Technical indicators and momentum scoring
│   ├── charts.py            # Chart data serialization (rendered client-side)
│   ├── benchmarks.py        # Sector benchmark calculation and persistence
│   ├── scoring.py           # Fundamental analysis + recommendation scorecard
│   ├── backtest.py          # Historical scorecard replay + persistence
│   ├── history.py           # Recommendation history + rating-change detection (SQLite)
│   ├── holdings.py          # Holdings (owned positions) persistence (SQLite)
│   ├── performance.py       # Holdings vs. benchmark (shadow-portfolio comparison)
│   ├── portfolio.py         # Watchlist JSON persistence
│   ├── storage.py           # On-disk stock snapshots
│   └── db.py                # SQLite connection + schema (holdings, recommendation_history, backtest_results)
├── portfolio.json           # (generated at runtime - the Watchlist)
├── sector_benchmarks.json   # (generated at runtime)
├── analysis.db              # (generated at runtime - SQLite)
├── pyproject.toml / uv.lock # Dependencies (uv-managed; see Quick Start)
├── README.md
├── docs/
│   ├── CALCULATION_REVIEW.md
│   ├── EXPANSION_BRAINSTORM.md
│   ├── EXPANSION_EXECUTIVE_SUMMARY.md
│   ├── FINAL_STATUS_REPORT.md
│   ├── PORTFOLIO_MANAGEMENT.md
│   ├── README.md
│   └── stock_analysis.ipynb
├── templates/
│   └── index.html        # Single-page app: Watchlist / Holdings / Benchmarks / Backtesting tabs
├── scripts/
│   └── screenshot.py     # Dev helper: headless-browser screenshot of the running app
└── tests/
    └── test_*.py          # One file per module (imports resolve into src/ via pytest's pythonpath)
```

Application source lives under `src/`; `templates/`, generated data files, and
`tests/` stay at the project root. `config.py`'s `BASE_DIR` resolves two
levels up from `config.py` itself (`src/config.py` → `src/` → repo root), so
`portfolio.json`, `sector_benchmarks.json`, `analysis.db`, and `cache/` always
land at the repo root regardless of where the app is run from. Flask's
`template_folder` is set explicitly (`../templates`) for the same reason.

The UI is a single page with four tabs: **Watchlist** (today's stock-picker/analysis view, unchanged from earlier versions), **Holdings** (positions you actually own, with live P&L and rating), **Benchmarks** (sector benchmark management), and **Backtesting** (run/review historical scorecard replays).

All market data access goes through the `DataProvider` interface in
`data_providers.py`. Three implementations exist - Yahoo Finance (`yfinance`),
SEC EDGAR (`edgar_provider.py`, fundamentals only), and Interactive Brokers
(`ibkr_provider.py`, via a locally running TWS or IB Gateway) - composed by
`CompositeProvider` (EDGAR overlaid on Yahoo, the default) or `FallbackProvider`
(IBKR preferred, Yahoo fallback); see Data Sources below. Yahoo also supplies
options-chain data (`get_put_options`) for the cash-secured put panel - free,
no separate account needed, same as the rest of Yahoo's market data.

## Data Sources

The active provider is selected with the `DATA_PROVIDER` environment variable:

| Value                     | Behavior                                                              |
|---------------------------|-----------------------------------------------------------------------|
| `auto` / `edgar` (default)| SEC EDGAR validated fundamentals overlaid on Yahoo market data        |
| `yahoo`                   | Yahoo Finance only                                                    |
| `ibkr`                    | Interactive Brokers only (TWS or IB Gateway must be running)          |
| `ibkr+yahoo`              | Interactive Brokers preferred, Yahoo fallback                         |

The stock API's `fetch_status` shows which provider served each result, and
`fundamental_metrics.fundamentals_source` shows whether EDGAR data was applied.

### SEC EDGAR (default fundamentals source)

Fundamental metrics for US-listed companies are computed directly from XBRL
company facts filed with the SEC (`data.sec.gov`) - audited primary-source
numbers with no data vendor in between, free of charge. Growth rates
(revenue, operating cash flow, ROE), margins, ROE and current ratio come
from quarterly filings; valuation ratios (P/E, P/S, P/B) are computed from
filed EPS/revenue/equity combined with the current market price. Non-US
symbols and any missing metrics transparently keep their Yahoo values.

The SEC requires an identifying User-Agent: set `EDGAR_USER_AGENT` in `.env`
to `"AppName your-email@example.com"` (quotes required).

### Interactive Brokers setup (optional)

IBKR is no longer the default data source: its API price data works without
subscriptions, but validated fundamentals via `reqFundamentalData` now sit
behind a paid research bundle and the call itself is deprecated by IBKR.
The provider is kept for the planned options-trading integration and can be
activated with `DATA_PROVIDER=ibkr+yahoo`.

1. Create an IBKR account (a free paper trading account is enough for data).
2. Install and run **TWS** (Trader Workstation) or **IB Gateway**.
   Note: the newer "IBKR Desktop" app does **not** expose the API socket.
3. In TWS: File → Global Configuration → API → Settings:
   - Enable *ActiveX and Socket Clients*
   - Optionally enable *Read-Only API* (recommended; this app never trades)
   - Note the socket port (TWS paper: `7497`, TWS live: `7496`,
     Gateway paper: `4002`, Gateway live: `4001`)
4. Configure via environment variables if the defaults do not fit:
   - `IBKR_HOST` (default `127.0.0.1`)
   - `IBKR_PORT` (default `7497`)
   - `IBKR_CLIENT_ID` (default `11`; each API connection needs a unique id)

No credentials are stored in this project: you authenticate by logging into
TWS/Gateway itself (username, password, two-factor via IBKR Mobile), and the
app connects to the already-authenticated TWS over a localhost socket. Copy
`.env.example` to `.env` for local settings and start the app with
`uv run --env-file .env python src/app.py`.

Price history uses `reqHistoricalData` (daily adjusted bars). Fundamentals
use `reqFundamentalData` (`ReportSnapshot`), which returns validated
Refinitiv (LSEG) ratio data — typically more complete and reliable than
Yahoo's scraped fundamentals. European symbols with Yahoo-style suffixes
(e.g. `SAP.DE`) are translated to the matching IBKR contract automatically.

When TWS is not running, `auto` mode detects the missing connection in
milliseconds, serves the request from Yahoo instead, and waits 60 seconds
before probing the connection again.

## Quick Start

### Prerequisites

- [uv](https://docs.astral.sh/uv/) - the only supported way to install and run this project. Dependencies are declared in `pyproject.toml` / pinned in `uv.lock`; there is no `requirements.txt`.
- Python 3.12+ (the project pins 3.13 via `.python-version`; `uv sync` installs it automatically - you don't need Python preinstalled)
- Internet access for market data requests

### Run Locally

```bash
uv sync                              # creates .venv and installs pinned dependencies
uv run python src/app.py             # or: uv run --env-file .env python src/app.py
```

Then open `http://localhost:5000`.

Run the tests with `uv run pytest`.

## How Analysis Works Today

### Fundamental View

- Raw metrics come from the active `DATA_PROVIDER` (see Data Sources above): by default, SEC EDGAR's audited XBRL filings overlaid on Yahoo Finance (`info` plus quarterly statements) for anything EDGAR doesn't cover; optionally Interactive Brokers (Refinitiv ReportSnapshot) instead.
- Growth metrics include quarterly year-over-year revenue growth, operating cash flow growth, and ROE growth.
- Sector comparison uses stored medians for P/E, P/B, P/S, ROE, and profit margin.
- Each sector metric receives a graded 0-10 score based on distance from the sector median (not just a better/worse boolean); the sector alignment component is the average of those scores.
- Negative values (negative P/E, negative ROE or margin) receive an explicit penalty score instead of being silently excluded, and are flagged with a note in the comparison.
- The UI shows raw comparisons plus a recommendation scorecard with weighted component scores.
- Scorecard weights can vary by sector instead of using a single global weighting scheme.

### Technical View

- Price history uses 300 daily bars with adjusted prices.
- Indicators include a 30-day moving average, stochastic oscillator, and MACD.
- Technical recommendation is derived from recent buy and sell signal counts over the last 10 days.

### Recommendation Logic

- Overall recommendation is derived from an explicit scorecard.
- The current scorecard uses sector-specific weight profiles, with a default baseline of 45% sector alignment, 35% growth quality, and 20% technical momentum.
- Each component is exposed by the stock API so the UI can show how the final label was formed.

### Resilience Behavior

- Live requests to the active data provider are retried before the app gives up on a symbol.
- If all retries fail, the app attempts to load the last successful on-disk snapshot for that symbol.
- The UI surfaces whether the result came from a fresh fetch, in-memory cache, or disk snapshot, and which provider served it.

### Backtesting

- Runs on demand from the **Backtesting** tab (or `POST /api/backtest/run`) for a chosen symbol, date range, holding period, and scoring profile - not computed continuously.
- Evaluates the scorecard every `step_days` trading days (default 5) across the window, then measures the realized price return `holding_period_days` later (default 20).
- Technical indicators are computed once on the full price series and read per step by slicing - this is provably lookahead-safe since every indicator (rolling window, EWM, `.shift()`) only ever looks backward; see `tests/test_backtest.py`'s `NoLookaheadProofTests`.
- Fundamentals are point-in-time: `edgar_provider.py` filters XBRL facts to only those filed on or before each evaluation date, so a step never sees a restatement or later filing that hadn't happened yet. Symbols without EDGAR coverage (non-US-listed) still backtest, with the sector/growth component scored neutrally for every step.
- **Known simplification**: sector benchmark medians always use today's `sector_benchmarks.json`, not a historical snapshot (none exists) - this is the one part of the backtest that isn't truly point-in-time, and the approximation gets worse the further back the window goes.
- Reports win rate and average forward return per recommendation label, plus a "follow the signal" (compounds returns only on BUY/STRONG BUY steps) vs. buy-and-hold equity curve. Every run is persisted in `analysis.db` for later review.

## Data Files

### `portfolio.json`

- Stores portfolio entries with `symbol`, `name`, and `date_added`.
- Changes made in the UI are written directly back to this file.

### `sector_benchmarks.json`

- Stores precomputed sector medians and metadata.
- Can be refreshed from the UI with the benchmark management controls.
- The app falls back to static defaults if the file is missing or unreadable.
- Refresh requests are throttled between symbols (default 1.0s, configurable via the `BENCHMARK_FETCH_DELAY` environment variable) to avoid provider rate limits.
- Valuation medians (P/E, P/B, P/S) exclude negative values; profitability medians (ROE, profit margin) include them so weak sectors are represented honestly.

### `analysis.db`

- SQLite database (stdlib `sqlite3`, no separate server) holding three tables: `holdings` (owned positions), `recommendation_history` (one row per symbol/day/profile, used for rating-change detection), and `backtest_results` (persisted backtest reports).
- Created automatically on first run; schema creation is idempotent, no migration framework.

## Important Limitations

- Cache state is held in process memory, so it is not shared across workers or restarts.
- Portfolio/holdings persistence is file- and SQLite-based and assumes a single local user.
- Holdings track a simple aggregate position per symbol (total shares + weighted-average cost basis), not lot-level tax accounting.
- Benchmarks depend on a hard-coded representative stock list rather than a dynamic market universe.
- Backtests use today's sector benchmarks for every historical evaluation date - see the Backtesting section above.
- Automated tests (147, one file per module under `tests/`) cover benchmark math, the recommendation scorecard, JSON error responses, provider fallback/composition, SQLite-backed holdings/history/backtest persistence, and the no-lookahead-bias guarantee for both technical indicators and point-in-time EDGAR fundamentals.
- Flask now defaults to `127.0.0.1:5000` with debug mode off unless `FLASK_HOST`, `FLASK_PORT`, or `FLASK_DEBUG` are set.

## Dependencies

- Flask
- yfinance
- pandas
- numpy
- requests (SEC EDGAR access)
- ib_async (Interactive Brokers API client)

The technical chart is rendered client-side with [Lightweight Charts](https://tradingview.github.io/lightweight-charts/) (loaded via CDN, no build step) instead of a Python plotting library - `/api/chart-data/<symbol>` serves the indicator arrays as JSON.

## Suggested Next Improvements

1. Persist a historical sector-benchmarks snapshot so backtests can compare against the sector median as it actually stood on each evaluation date, instead of today's medians (see the Backtesting known simplification above).
2. Move the watchlist (`portfolio.json`) and the in-memory fetch caches (`data_cache`, `fundamental_cache`, `earnings_cache` - per-process, lost on restart) to the same SQLite store used for holdings/history/backtests; needed for multi-user or hosted use. Consider a lot-level option for holdings if accurate realized-gain accounting matters to you.
3. Add a request-cancellation guard (e.g. `AbortController`) in the frontend for rapid stock/profile switching - confirmed harmless today under real usage pacing, but there's no defense if two `loadStockData()` calls ever overlap.
4. Investigate the "STRONG BUY may be a momentum-chasing/overbought signal rather than an early one" pattern surfaced by backtesting NVDA (2024-08 to 2026-07): its 6 STRONG BUY steps had only a 16.7% forward win rate versus 61.5% for plain BUY.

## Status

- Version: 4.0 (Watchlist/Holdings/Benchmarks/Backtesting tabs, SEC EDGAR point-in-time data, SQLite-backed history/holdings/backtests, `src/` layout, in-app watchlist-wide rating-change alerts, cash-secured put candidates for BUY-rated stocks)
- Last verified: 2026-08-07
- Intended use: local analysis workflow
