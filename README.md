# Stock Analysis Application V4

Flask-based stock analysis dashboard that combines on-demand market data, peer-relative fundamental checks, and momentum indicators for a manually curated portfolio.

## What The App Does

- Loads stock data only when you select a symbol from the portfolio.
- Caches downloaded market and fundamental data in memory for one hour per symbol.
- Stores the last successful stock payload on disk so a recent snapshot can be reused if live fetches fail.
- Shows fundamental metrics such as valuation, profitability, growth, and financial health.
- Compares selected company metrics against sector benchmark medians stored in `sector_benchmarks.json`.
- Generates a technical chart with a 30-day moving average, stochastic oscillator, and MACD.
- Produces an explicit recommendation scorecard by combining sector-relative fundamentals with recent technical signals.

## Current Project Structure

```text
Stock AnalysisV4/
├── app.py
├── portfolio.json
├── requirements.txt
├── sector_benchmarks.json
├── README.md
├── docs/
│   ├── CALCULATION_REVIEW.md
│   ├── EXPANSION_BRAINSTORM.md
│   ├── EXPANSION_EXECUTIVE_SUMMARY.md
│   ├── FINAL_STATUS_REPORT.md
│   ├── PORTFOLIO_MANAGEMENT.md
│   ├── README.md
│   └── stock_analysis.ipynb
└── templates/
    └── index.html
```

## Quick Start

### Prerequisites

- Python 3.8+
- Internet access for Yahoo Finance requests

### Run Locally

```bash
pip install -r requirements.txt
python app.py
```

Then open `http://localhost:5000`.

## How Analysis Works Today

### Fundamental View

- Raw metrics are pulled from Yahoo Finance `info` and quarterly statements.
- Growth metrics include quarterly year-over-year revenue growth, operating cash flow growth, and ROE growth.
- Sector comparison uses stored medians for P/E, P/B, P/S, ROE, and profit margin.
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

- Live Yahoo Finance requests are retried before the app gives up on a symbol.
- If all retries fail, the app attempts to load the last successful on-disk snapshot for that symbol.
- The UI surfaces whether the result came from a fresh fetch, in-memory cache, or disk snapshot.

## Data Files

### `portfolio.json`

- Stores portfolio entries with `symbol`, `name`, and `date_added`.
- Changes made in the UI are written directly back to this file.

### `sector_benchmarks.json`

- Stores precomputed sector medians and metadata.
- Can be refreshed from the UI with the benchmark management controls.
- The app falls back to static defaults if the file is missing or unreadable.

## Important Limitations

- Cache state is held in process memory, so it is not shared across workers or restarts.
- Portfolio persistence is file-based and assumes a single local user.
- Benchmarks depend on a hard-coded representative stock list rather than a dynamic market universe.
- The current automated tests are focused on benchmark math, the recommendation scorecard, and JSON error responses.
- Flask now defaults to `127.0.0.1:5000` with debug mode off unless `FLASK_HOST`, `FLASK_PORT`, or `FLASK_DEBUG` are set.

## Dependencies

- Flask
- yfinance
- pandas
- matplotlib
- numpy

## Suggested Next Improvements

1. Replace debug mode and `print`-based diagnostics with proper logging and configuration.
2. Add automated tests around benchmark math and recommendation logic.
3. Move portfolio and cache state to a durable backend if you want multi-user or hosted use.
4. Make the recommendation model fully transparent by exposing explicit component scores.

## Status

- Version: 4.0
- Last updated: May 2026
- Intended use: local analysis workflow
