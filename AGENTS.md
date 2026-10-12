# AGENTS.md

Instructions for AI coding agents working in this repository. This is not
user-facing documentation - see `README.md` for what the app does and how to
use it. This file exists to save you from re-discovering things the hard way.

## Setup, run, test

```bash
uv sync                              # installs deps + pins Python 3.13 (see .python-version)
uv run python src/app.py             # or: uv run --env-file .env python src/app.py
uv run pytest                        # 147 tests, ~1s, no network calls
```

Application source is under `src/`; `templates/`, `tests/`, and generated
data files (`portfolio.json`, `sector_benchmarks.json`, `analysis.db`,
`cache/`) stay at the repo root. There is no `requirements.txt` - `uv` /
`pyproject.toml` / `uv.lock` is the only supported dependency path.

Before any manual/interactive test run, clean generated state so it doesn't
leak between sessions:
```bash
rm -f analysis.db portfolio.json
```

## Architecture in one paragraph

Flask routes in `src/app.py` orchestrate an in-memory fetch/cache layer, then
delegate to single-purpose modules: `data_providers.py` (the `DataProvider`
interface + Yahoo/EDGAR/IBKR implementations + `CompositeProvider`/
`FallbackProvider` composition), `indicators.py` (technical signals),
`scoring.py` (fundamental analysis + recommendation scorecard),
`benchmarks.py` (sector medians), `backtest.py` (historical replay, reuses
scoring/indicators unmodified), `history.py`/`holdings.py`/`db.py` (SQLite
persistence). `charts.py` only serializes data to JSON now - charts render
client-side in `templates/index.html` via Lightweight Charts (CDN, pinned to
v4.1.3; do not upgrade to v5 without checking its breaking API changes, and
do not reintroduce server-side chart image rendering / matplotlib).

Any new market-data source should implement the `DataProvider` protocol in
`data_providers.py` rather than being called directly from `app.py`.

## Traps that will cost you time if you don't know about them

**1. Mocking `get_provider` (or any function) must target the *importing*
module, not the source module.** Every module does `from data_providers
import get_provider` (or similar). This copies the function reference at
import time, so `mock.patch.object(data_providers, 'get_provider')` does
**not** affect calls made from `app.py` or `backtest.py`. You must patch
`stock_app.get_provider` (i.e. patch it on the module that imported it).
Getting this wrong doesn't error - it silently falls through to the real
network call, which is slow and can look like a passing-but-meaningless
test. This bit this project's own test suite three separate times before
the pattern was recognized. If a test mocking a provider/fetch function is
mysteriously making real HTTP calls (check timing - real calls are slow),
this is almost certainly why.

**2. `templates/index.html` is one large file (~2500 lines) with deeply
nested `<div>`s across four tab-panes, followed by a `<script>` block that
builds more HTML as JS string literals (`html += '<div class="...">'`
inside functions like `renderHoldingsTable`).** A single missing or extra
closing `</div>` in the *static markup* will silently misnest later
tab-panes as siblings of the wrong parent - invisible from reading the text,
only visible in the actual parsed DOM. Before and after editing the markup,
check div balance **scoped to the region before the `<script>` tag** (find
its line number first - a whole-file count will be thrown off by the `<div`/
`</div>` substrings inside the JS string literals, which are data, not
structure):
```bash
SCRIPT_LINE=$(grep -n '^    <script>' templates/index.html | head -1 | cut -d: -f1)
awk -v end="$SCRIPT_LINE" 'NR<end && /<div/{n=gsub(/<div/,"<div");d+=n} NR<end && /<\/div>/{m=gsub(/<\/div>/,"<\/div>");d-=m} END{print "final depth (must be 0):", d}' templates/index.html
```
This exact bug happened once already in this repo's history.

**3. Never trust "no console errors" from a `curl`/BeautifulSoup DOM check
as proof a UI change works.** Two real bugs in this project's history
(the div-nesting issue above, and Bootstrap 5.1.3's `Tab` component failing
to deactivate the previous pane) were invisible to static HTML inspection
and only surfaced by actually driving a browser and reading computed
styles/classes after a real click. Playwright is a `[dependency-groups] dev`
dependency (installed by `uv sync`), but the Chromium *binary* is not part
of the Python venv and will not exist in a fresh environment - if
`uv run playwright install --with-deps chromium` hasn't been run yet in this
environment, run it once before trying to drive a browser.
`scripts/screenshot.py` is a minimal helper; for anything beyond a single
screenshot, write a short ad hoc Playwright script, inspect actual DOM
state (`page.eval_on_selector_all`), and check `page.on("console", ...)` /
`page.on("pageerror", ...)` for real errors.

**4. Tab switching in the frontend is hand-rolled vanilla JS, not
Bootstrap's `Tab` component**, because Bootstrap 5.1.3's `Tab` reliably
failed to remove `active`/`show` from the previously active pane in this
environment (a `transitionend`/`prefers-reduced-motion` interaction). The
nav buttons use `data-tab-target` (not `data-bs-toggle="tab"`) on purpose.
Don't "fix" this by reverting to Bootstrap's data-api - re-verify with a
real click-through if you touch this code.

**5. Most frontend JS lives inside a single `DOMContentLoaded` closure**,
not on `window`. Functions invoked via inline `onclick="..."` HTML
attributes (e.g. `addStock()`, `runBacktest()`) *must* be assigned as
`window.functionName = function() {...}` to be reachable - this is why you
see that pattern repeated throughout the script block. Chart instances
(`priceChart`, `backtestEquityChart`, etc.) are closure-scoped on purpose
and are not accessible from outside the closure (e.g. not from
`page.evaluate()` in a test script) unless you deliberately expose them.

**6. Any test that touches SQLite-backed state (holdings, recommendation
history, backtest results) must isolate `db.DB_FILE`** to a
`tempfile.TemporaryDirectory()` in `setUp`/`tearDown`, or it will read/write
the real `analysis.db`. See `tests/test_db.py`, `tests/test_holdings.py`,
or `StockAnalysisTests.setUp` in `tests/test_app.py` for the pattern.

**7. SEC EDGAR requires an identifying `User-Agent`** (`EDGAR_USER_AGENT`
env var, format `"AppName email@example.com"`, quotes matter - an unquoted
value with a space breaks `.env` parsing) or requests get HTTP 403'd.

**8. `config.py`'s `BASE_DIR` resolves two directory levels up** (`src/
config.py` → `src/` → repo root) so generated data files land at the repo
root, not inside `src/`. If you ever move `config.py` again, this needs to
change with it. Same reasoning applies to `app.py`'s explicit
`template_folder='../templates'`.

**9. `holdings.py` intentionally does simple aggregate positions** (total
shares + weighted-average cost basis), not lot-level tracking. Don't add
lot-level accounting as a side effect of an unrelated change - it's a
known, deliberate simplification (see README's Suggested Next
Improvements).

**10. Backtesting's sector-alignment component is not truly point-in-time**
- it always uses today's `sector_benchmarks.json` for every historical
evaluation date (there is no historical benchmark snapshot store). Only the
technical indicators and EDGAR fundamentals (via `as_of` filtering in
`edgar_provider.py`) are genuinely reconstructed as of each historical date.
Don't describe backtest results as fully point-in-time without this caveat.

**11. `YahooFinanceProvider.get_price_history()` must flatten `yf.download`'s
MultiIndex columns** (`('Close', 'SPY')` instead of `'Close'`) before
returning - this is now done in `data_providers.py` itself, matching the
`DataProvider` interface's documented flat-columns contract. Don't remove
that `droplevel` call "to simplify." It exists because every *existing*
caller happened to route through `indicators.calculate_technical_indicators()`
(which flattens defensively as its own first step) - so this bug was latent
and invisible until `performance.py` used `get_price_history()`'s raw output
directly and got a `Series` back from a single-cell `.loc[]` lookup instead
of a scalar. If you add a new consumer of `get_price_history()` that doesn't
go through `calculate_technical_indicators`, don't assume flat columns
without checking - verify with a real (non-mocked) call, the way this bug
was actually found.

**12. `yfinance`'s free options chain (`Ticker.option_chain(expiration).puts`)
frequently reports `bid`/`ask` as `0.0` even for liquid, actively-traded
contracts** - observed live for *every* AAPL put across *every* expiration
during development, not just after-hours. `lastPrice` (the most recent
actual trade) stays populated even then. `YahooFinanceProvider.get_put_options()`
in `data_providers.py` already handles this: it prefers a live `bid` when
there is one and falls back to `lastPrice` otherwise, tagging which one it
used via a `premium_source` field (`'bid'` or `'last_trade'`) so callers can
be transparent about it. If you filter or price options data elsewhere,
don't assume a zero `bid` means "no real market" - verify with a real
(non-mocked) call first, the same lesson as Trap #11.

`get_put_options()`'s `premium` field is **net of a modeled $1.00 IBKR
commission** (1 contract, IBKR Pro Fixed/Lite: $0.65/contract with a $1.00
minimum), not the raw quoted premium - that raw value is preserved
separately as `gross_premium`. Candidates where the commission would eat
more than `OPTIONS_MAX_COMMISSION_DRAG_PCT` (25%) of the gross premium are
dropped entirely. This only applies to USD contracts (`currency` column on
the puts DataFrame) - IBKR's US options fee schedule doesn't apply to a
non-USD chain (e.g. Eurex), so those candidates keep `gross_premium` as
`premium` untouched, with `commission_applicable: False`. If you add
another commission assumption (a different broker, IBKR Pro Tiered, a
UI-editable rate), don't just overwrite `premium` in place again - the
gross/net split here exists so a consumer can always recover the raw
market number.

**13. `templates/index.html`'s stock-detail rendering is split across two
functions that look interchangeable but have different parameters in
scope**: `loadStockData(stock)` does the fetch and calls `displayStockData(data)`
to render it - `displayStockData` only receives `data`, not `stock`/`symbol`.
Code added inside `displayStockData` that needs the current ticker must use
`data.symbol`, not a `stock` variable - referencing `stock` there is a
`ReferenceError` that only shows up in the browser console, never in a
static/curl check (see Trap #3). This exact mistake happened once already
(a new panel's fetch call silently never fired because of it) and was only
caught by checking `page.on("console", ...)` output during Playwright
verification.

## Conventions to follow

- No comments explaining *what* code does; comments only for non-obvious
  *why* (a hidden constraint, a workaround, a subtle invariant). This
  codebase follows that consistently - match it.
- Reuse existing analysis functions rather than duplicating logic -
  `backtest.py` calls the same `scoring.py`/`indicators.py` functions the
  live app uses, unmodified, specifically so backtest results reflect the
  real scoring logic rather than a parallel reimplementation. Within
  `app.py` itself, `_fetch_and_score_symbol(symbol, profile_name)` is the
  single fetch->score->record pipeline shared by the single-stock route,
  Holdings enrichment, and the `/api/portfolio/changes` bulk rating-check -
  add any new per-symbol scoring consumer on top of it rather than
  re-inlining the sequence a fourth time.
- One test file per module under `tests/`, named `test_<module>.py`.
- Prefer exact/hand-computed assertions over "does it run" checks in tests
  where the math is checkable (see `tests/test_backtest.py`'s forward-return
  test for the pattern) - vague assertions have hidden real bugs in this
  project before (e.g. an inequality check that passed by fixture
  coincidence rather than actually verifying behavior).
- Don't add new required paid/subscription dependencies. SEC EDGAR was
  chosen specifically over IBKR's fundamentals precisely because IBKR's
  `reqFundamentalData` now requires a paid research bundle.
