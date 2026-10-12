"""Backtesting: replay the recommendation scorecard against history to check
whether its BUY/SELL labels have any relationship to what actually happened
next.

Reuses the existing, unmodified indicator and scoring functions - the only
new logic here is the historical walk, point-in-time fundamentals via
edgar_provider's as_of filtering, and result aggregation/persistence.

Sector benchmarks are NOT reconstructed historically (no historical
benchmark store exists); every step uses today's sector_benchmarks.json.
This is a known simplification - reasonable for medium-term backtests,
increasingly approximate the further back the window goes.
"""

import json
import logging
from collections import defaultdict
from datetime import date, datetime

import pandas as pd

import db
from data_providers import get_provider
from edgar_provider import EdgarFundamentalsProvider, compute_fundamentals
from indicators import build_technical_momentum_summary, calculate_technical_indicators
from scoring import build_recommendation_scorecard, calculate_fundamental_analysis
from config import BACKTEST_HOLDING_PERIOD_DAYS, BACKTEST_STEP_DAYS, BACKTEST_WARMUP_DAYS

LOGGER = logging.getLogger(__name__)

BULLISH_LABELS = ('BUY', 'STRONG BUY')

# Shared across backtest runs so repeated runs on the same symbol don't
# re-fetch the (large) companyfacts document.
_edgar = EdgarFundamentalsProvider()


def _as_date(value):
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _resolve_sector(provider, symbol):
    """Best-effort current sector classification, used for every historical
    step (sector classification changes are rare enough for this to be a
    reasonable simplification, unlike the financial metrics themselves).
    """
    try:
        fundamentals = provider.get_fundamentals(symbol)
        return fundamentals.get('sector') or 'Technology'
    except Exception as error:
        LOGGER.info("Could not resolve sector for %s (%s); defaulting to Technology", symbol, error)
        return 'Technology'


def _aggregate_by_label(steps):
    by_label = defaultdict(list)
    for step in steps:
        by_label[step['label']].append(step['forward_return'])

    performance = {}
    for label, returns in by_label.items():
        wins = sum(1 for r in returns if r > 0)
        performance[label] = {
            'count': len(returns),
            'avg_forward_return': round(sum(returns) / len(returns), 4),
            'win_rate': round(wins / len(returns), 4),
        }
    return performance


def _build_equity_curve(steps, price_data):
    """Two comparable cumulative-return series sharing the steps' x-axis:

    - follow_signal: compounds each step's forward return only when that
      step's label was bullish (BUY/STRONG BUY), else holds flat (no
      position) for that step. Steps are treated as independent sequential
      decisions, not an overlapping multi-position portfolio.
    - buy_and_hold: price at each step date relative to the first step's
      price, for a direct comparison over the same window.
    """
    if not steps:
        return []

    start_price = float(price_data.loc[pd.Timestamp(steps[0]['date']), 'Close'])
    follow_cumulative = 1.0
    curve = []
    for step in steps:
        if step['label'] in BULLISH_LABELS:
            follow_cumulative *= (1 + step['forward_return'])
        step_price = float(price_data.loc[pd.Timestamp(step['date']), 'Close'])
        curve.append({
            'date': step['date'],
            'follow_signal_cumulative': round(follow_cumulative, 4),
            'buy_and_hold_cumulative': round(step_price / start_price, 4),
        })
    return curve


def run_backtest(symbol, start_date, end_date, profile='balanced',
                  holding_period_days=BACKTEST_HOLDING_PERIOD_DAYS,
                  step_days=BACKTEST_STEP_DAYS):
    """Walk the recommendation scorecard across [start_date, end_date] and
    score each step's label against its realized forward return.

    Returns a plain-dict report; raises ValueError for user-fixable problems
    (bad symbol, no data, window too narrow for even one evaluable step).
    """
    symbol = symbol.upper().strip()
    start_date = _as_date(start_date)
    end_date = _as_date(end_date)
    if end_date <= start_date:
        raise ValueError('end_date must be after start_date')

    provider = get_provider()
    lookback_days = (date.today() - start_date).days + BACKTEST_WARMUP_DAYS
    price_data = provider.get_price_history(symbol, days=max(lookback_days, BACKTEST_WARMUP_DAYS))
    if price_data.empty:
        raise ValueError(f'No price history available for {symbol!r}')

    price_data = calculate_technical_indicators(price_data.sort_index())

    try:
        company_facts = _edgar.get_company_facts(symbol)
    except Exception as error:
        LOGGER.info(
            "EDGAR fundamentals unavailable for %s backtest (%s); "
            "sector/growth component will be neutral for every step", symbol, error)
        company_facts = None

    sector = _resolve_sector(provider, symbol)

    trading_dates = [d for d in price_data.index if start_date <= d.date() <= end_date]
    step_dates = trading_dates[::step_days]

    steps = []
    skipped_insufficient_forward_data = 0
    for eval_date in step_dates:
        eval_position = price_data.index.get_loc(eval_date)
        forward_position = eval_position + holding_period_days
        if forward_position >= len(price_data):
            skipped_insufficient_forward_data += 1
            continue

        history_slice = price_data.iloc[:eval_position + 1]
        technical_summary = build_technical_momentum_summary(history_slice)

        entry_price = float(price_data['Close'].iloc[eval_position])
        fundamental_metrics = {'sector': sector}
        if company_facts is not None:
            try:
                fundamental_metrics.update(compute_fundamentals(
                    company_facts, current_price=entry_price, as_of=eval_date.date()))
            except Exception as error:
                LOGGER.warning("compute_fundamentals failed for %s as_of %s: %s",
                               symbol, eval_date.date(), error)

        fundamental_analysis = calculate_fundamental_analysis(fundamental_metrics)
        scorecard = build_recommendation_scorecard(
            fundamental_analysis, technical_summary['signal'],
            profile_name=profile, technical_summary=technical_summary)

        exit_price = float(price_data['Close'].iloc[forward_position])
        steps.append({
            'date': eval_date.date().isoformat(),
            'label': scorecard['label'],
            'total_score': scorecard['total_score'],
            'forward_return': round((exit_price - entry_price) / entry_price, 4),
        })

    if not steps:
        raise ValueError(
            'No evaluable steps in the requested window - try an earlier end_date '
            'or a shorter holding period so at least one step has forward data.')

    equity_curve = _build_equity_curve(steps, price_data)

    return {
        'symbol': symbol,
        'profile': profile,
        'sector': sector,
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'holding_period_days': holding_period_days,
        'step_days': step_days,
        'fundamentals_available': company_facts is not None,
        'steps_evaluated': len(steps),
        'steps_skipped_insufficient_forward_data': skipped_insufficient_forward_data,
        'steps': steps,
        'label_performance': _aggregate_by_label(steps),
        'follow_signal_cumulative_return': round(equity_curve[-1]['follow_signal_cumulative'] - 1, 4),
        'buy_and_hold_cumulative_return': round(equity_curve[-1]['buy_and_hold_cumulative'] - 1, 4),
        'equity_curve': equity_curve,
    }


def save_backtest_result(report):
    """Persist a backtest report; returns the new row's id."""
    connection = db.get_connection()
    try:
        cursor = connection.execute(
            """
            INSERT INTO backtest_results
                (symbol, profile, start_date, end_date, holding_period_days, run_at, summary_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (report['symbol'], report['profile'], report['start_date'], report['end_date'],
             report['holding_period_days'], datetime.now().isoformat(), json.dumps(report)),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def get_backtest_results():
    """Return summary metadata for all past runs, newest first."""
    connection = db.get_connection()
    try:
        rows = connection.execute(
            "SELECT id, symbol, profile, start_date, end_date, holding_period_days, run_at, summary_json "
            "FROM backtest_results ORDER BY run_at DESC"
        ).fetchall()
    finally:
        connection.close()

    results = []
    for row in rows:
        summary = json.loads(row['summary_json'])
        results.append({
            'id': row['id'],
            'symbol': row['symbol'],
            'profile': row['profile'],
            'start_date': row['start_date'],
            'end_date': row['end_date'],
            'holding_period_days': row['holding_period_days'],
            'run_at': row['run_at'],
            'label_performance': summary.get('label_performance'),
            'follow_signal_cumulative_return': summary.get('follow_signal_cumulative_return'),
            'buy_and_hold_cumulative_return': summary.get('buy_and_hold_cumulative_return'),
        })
    return results


def get_backtest_result(run_id):
    """Return the full report for one past run, or None if not found."""
    connection = db.get_connection()
    try:
        row = connection.execute(
            "SELECT summary_json FROM backtest_results WHERE id = ?", (run_id,)
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return None
    report = json.loads(row['summary_json'])
    report['id'] = run_id
    return report
