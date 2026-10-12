"""Holdings performance vs. a benchmark, using a dollar-value shadow portfolio.

Holdings store one aggregate position per symbol with a single purchase_date
(see holdings.py - deliberately not lot-level). Naively summing
shares * price(t) from each symbol's own purchase date produces a
money-weighted distortion: adding a new holding makes portfolio value jump
on that date, which looks like a return but isn't.

Instead, on each holding's purchase_date we simulate spending the same
dollar amount (shares * cost_basis_per_share) on the benchmark symbol at
that date's price. Both curves then receive their "capital injections" on
identical dates, so summing shares * close(t) for both is directly
comparable at every point - no percentage chain-linking needed, and it
works correctly with the existing one-purchase-date-per-symbol schema.
"""

import logging
from datetime import date

import pandas as pd

from data_providers import get_provider

LOGGER = logging.getLogger(__name__)

DEFAULT_BENCHMARK_SYMBOL = 'SPY'
LOOKBACK_BUFFER_DAYS = 15


def _as_date(value):
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _align_to_trading_day(price_index, target_date):
    """First available trading day on or after target_date, or None."""
    target_ts = pd.Timestamp(target_date)
    matching = price_index[price_index.normalize() >= target_ts]
    return matching[0] if len(matching) > 0 else None


def _empty_result(benchmark_symbol, excluded_holdings):
    return {
        'dates': [],
        'portfolio_value': [],
        'benchmark_value': [],
        'total_invested': 0.0,
        'current_value': 0.0,
        'portfolio_return_pct': None,
        'benchmark_return_pct': None,
        'benchmark_symbol': benchmark_symbol,
        'excluded_holdings': sorted(set(excluded_holdings)),
    }


def compute_holdings_performance(holdings, benchmark_symbol=None):
    """Compute portfolio value vs. a benchmark shadow portfolio over time.

    `holdings` is the list of dicts returned by holdings.get_holdings().
    Raises ValueError if the benchmark symbol itself has no price history
    (that's fatal - individual holding fetch failures are not, and are
    reported in excluded_holdings instead).
    """
    benchmark_symbol = (benchmark_symbol or DEFAULT_BENCHMARK_SYMBOL).upper().strip()
    if not benchmark_symbol:
        benchmark_symbol = DEFAULT_BENCHMARK_SYMBOL

    excluded_holdings = []
    eligible = []
    for holding in holdings:
        if holding.get('purchase_date'):
            eligible.append(holding)
        else:
            excluded_holdings.append(holding['symbol'])

    if not eligible:
        return _empty_result(benchmark_symbol, excluded_holdings)

    earliest_date = min(_as_date(h['purchase_date']) for h in eligible)
    lookback_days = (date.today() - earliest_date).days + LOOKBACK_BUFFER_DAYS

    provider = get_provider()

    try:
        benchmark_data = provider.get_price_history(benchmark_symbol, days=lookback_days)
    except Exception as error:
        raise ValueError(f"Could not fetch price history for benchmark {benchmark_symbol!r}: {error}")
    if benchmark_data.empty:
        raise ValueError(f"No price history available for benchmark {benchmark_symbol!r}")
    benchmark_data = benchmark_data.sort_index()

    price_data = {}
    fetchable = []
    for holding in eligible:
        symbol = holding['symbol']
        try:
            data = provider.get_price_history(symbol, days=lookback_days)
            if data.empty:
                raise ValueError('empty price history')
        except Exception as error:
            LOGGER.warning(
                "Could not fetch price history for %s (%s); excluding from performance calc",
                symbol, error,
            )
            excluded_holdings.append(symbol)
            continue
        price_data[symbol] = data.sort_index()
        fetchable.append(holding)

    injections = []
    for holding in fetchable:
        symbol = holding['symbol']
        purchase_date = _as_date(holding['purchase_date'])

        aligned = _align_to_trading_day(price_data[symbol].index, purchase_date)
        benchmark_aligned = _align_to_trading_day(benchmark_data.index, purchase_date)
        if aligned is None or benchmark_aligned is None:
            excluded_holdings.append(symbol)
            continue

        invested = holding['shares'] * holding['cost_basis_per_share']
        benchmark_price_at_purchase = float(benchmark_data.loc[benchmark_aligned, 'Close'])
        shadow_shares = invested / benchmark_price_at_purchase

        injections.append({
            'symbol': symbol,
            'shares': holding['shares'],
            'aligned_date': aligned,
            'invested': invested,
            'shadow_shares': shadow_shares,
        })

    if not injections:
        return _empty_result(benchmark_symbol, excluded_holdings)

    earliest_aligned = min(inj['aligned_date'] for inj in injections)
    timeline = benchmark_data.index[benchmark_data.index >= earliest_aligned]

    dates = []
    portfolio_values = []
    benchmark_values = []

    for day in timeline:
        portfolio_value = 0.0
        benchmark_value = 0.0
        for inj in injections:
            if day < inj['aligned_date']:
                continue

            price_on_day = price_data[inj['symbol']]['Close'].asof(day)
            if pd.notna(price_on_day):
                portfolio_value += inj['shares'] * float(price_on_day)

            benchmark_price_on_day = benchmark_data['Close'].asof(day)
            if pd.notna(benchmark_price_on_day):
                benchmark_value += inj['shadow_shares'] * float(benchmark_price_on_day)

        dates.append(day.date().isoformat())
        portfolio_values.append(round(portfolio_value, 2))
        benchmark_values.append(round(benchmark_value, 2))

    total_invested = round(sum(inj['invested'] for inj in injections), 2)
    current_value = portfolio_values[-1]
    current_benchmark_value = benchmark_values[-1]

    return {
        'dates': dates,
        'portfolio_value': portfolio_values,
        'benchmark_value': benchmark_values,
        'total_invested': total_invested,
        'current_value': current_value,
        'portfolio_return_pct': round((current_value - total_invested) / total_invested * 100, 2),
        'benchmark_return_pct': round((current_benchmark_value - total_invested) / total_invested * 100, 2),
        'benchmark_symbol': benchmark_symbol,
        'excluded_holdings': sorted(set(excluded_holdings)),
    }
