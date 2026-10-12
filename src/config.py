"""Central configuration: file paths, cache limits, and scoring weights."""

import os

# Project root (one level up from src/, where this file lives) - keeps
# generated data files (portfolio.json, analysis.db, cache/) at the repo
# root regardless of where the application source lives.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PORTFOLIO_FILE = os.path.join(BASE_DIR, 'portfolio.json')
SECTOR_BENCHMARKS_FILE = os.path.join(BASE_DIR, 'sector_benchmarks.json')
STOCK_CACHE_DIR = os.path.join(BASE_DIR, 'cache', 'stocks')
DB_FILE = os.path.join(BASE_DIR, 'analysis.db')

CACHE_MAX_AGE_DAYS = 30
CACHE_MAX_FILES = 100
CACHE_FRESH_SECONDS = 3600
MAX_BENCHMARK_HISTORY = 12

# Earnings dates rarely change day to day, so they get a much longer cache
# than price/fundamental data.
EARNINGS_CACHE_SECONDS = 24 * 3600

# Cash-secured put candidates: near-the-money (strike within this fraction
# below current price), near-term (between this many and this many days to
# expiration - 0DTE contracts are excluded since they carry near-zero time
# value and aren't a meaningful premium-selling candidate), and liquid (at
# least this much open interest). Cached shorter than earnings since
# premiums move faster, but long enough to avoid re-issuing several
# option_chain calls per stock on every page reload.
OPTIONS_MIN_DAYS_TO_EXPIRY = 1
OPTIONS_MAX_DAYS_TO_EXPIRY = 45
OPTIONS_MIN_OPEN_INTEREST = 10
OPTIONS_MAX_STRIKE_DISCOUNT_PCT = 0.10
OPTIONS_CACHE_SECONDS = 1800

# IBKR Pro Fixed / IBKR Lite options commission: $0.65/contract, $1.00
# minimum per order, all-in (bundles exchange/regulatory/clearing fees -
# unlike IBKR Pro Tiered, which drops the per-contract rate at high volume
# but adds pass-through exchange fees; not modeled here since it doesn't
# apply to a personal account trading a handful of contracts). Modeled for
# a single-contract trade - the conservative default given the app has no
# position-sizing UI: the $1.00 minimum binds at 1 contract, while 2+
# contracts settle to a flat $0.65/contract with no minimum drag.
IBKR_OPTIONS_COMMISSION_PER_CONTRACT = 0.65
IBKR_OPTIONS_MIN_COMMISSION_PER_ORDER = 1.00
OPTIONS_MAX_COMMISSION_DRAG_PCT = 0.25

# Delay between per-symbol requests during a benchmark refresh so we do not
# hammer the data provider with ~100 back-to-back calls.
BENCHMARK_FETCH_DELAY_SECONDS = float(os.getenv('BENCHMARK_FETCH_DELAY', '1.0'))

# Backtesting defaults. Evaluation steps are spaced BACKTEST_STEP_DAYS
# trading days apart; each step's forward return is measured
# BACKTEST_HOLDING_PERIOD_DAYS trading days later. BACKTEST_WARMUP_DAYS of
# extra calendar-day history is fetched before the requested start date so
# the 90-day moving average and 10-day signal window are fully warmed up by
# the first evaluated step (no NaN/neutral scoring on real evaluation dates).
BACKTEST_STEP_DAYS = 5
BACKTEST_HOLDING_PERIOD_DAYS = 20
BACKTEST_WARMUP_DAYS = 180

# Score assigned to a metric whose value is negative (e.g. negative P/E from
# negative earnings, negative ROE). Explicit penalty instead of silently
# excluding the metric from scoring.
NEGATIVE_VALUE_PENALTY_SCORE = 1.0

DEFAULT_SCORECARD_WEIGHTS = {
    'sector_alignment': 0.45,
    'growth_quality': 0.35,
    'technical_momentum': 0.20,
}

SCORING_PROFILES = {
    'balanced': {
        'label': 'Balanced',
        'weights': {
            'sector_alignment': 0.40,
            'growth_quality': 0.35,
            'technical_momentum': 0.25,
        },
    },
    'growth': {
        'label': 'Growth',
        'weights': {
            'sector_alignment': 0.25,
            'growth_quality': 0.50,
            'technical_momentum': 0.25,
        },
    },
    'value': {
        'label': 'Value',
        'weights': {
            'sector_alignment': 0.55,
            'growth_quality': 0.25,
            'technical_momentum': 0.20,
        },
    },
    'momentum': {
        'label': 'Momentum',
        'weights': {
            'sector_alignment': 0.20,
            'growth_quality': 0.25,
            'technical_momentum': 0.55,
        },
    },
}

SECTOR_SCORECARD_WEIGHTS = {
    'Technology': {
        'sector_alignment': 0.35,
        'growth_quality': 0.40,
        'technical_momentum': 0.25,
    },
    'Healthcare': {
        'sector_alignment': 0.40,
        'growth_quality': 0.40,
        'technical_momentum': 0.20,
    },
    'Financial Services': {
        'sector_alignment': 0.50,
        'growth_quality': 0.30,
        'technical_momentum': 0.20,
    },
    'Utilities': {
        'sector_alignment': 0.55,
        'growth_quality': 0.20,
        'technical_momentum': 0.25,
    },
    'Energy': {
        'sector_alignment': 0.50,
        'growth_quality': 0.25,
        'technical_momentum': 0.25,
    },
}
