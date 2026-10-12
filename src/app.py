"""
Stock Portfolio Analysis Web Service

Flask routes and the in-memory fetch orchestration. Analysis logic lives in
the sibling modules: data_providers (market data access), indicators,
scoring, benchmarks, charts, portfolio and storage.
"""

import logging
import os
from datetime import datetime

import pandas as pd
from flask import Flask, render_template, jsonify, request

from backtest import get_backtest_result, get_backtest_results, run_backtest, save_backtest_result
import benchmarks
from benchmarks import get_sector_representative_stocks, refresh_all_sector_benchmarks
from charts import serialize_chart_data
from config import (
    BACKTEST_HOLDING_PERIOD_DAYS,
    BACKTEST_STEP_DAYS,
    CACHE_FRESH_SECONDS,
    EARNINGS_CACHE_SECONDS,
    OPTIONS_CACHE_SECONDS,
    SCORING_PROFILES,
)
from data_providers import get_provider, run_with_retries
from indicators import (
    build_technical_momentum_summary,
    calculate_technical_indicators,
    get_technical_recommendation,
)
from history import detect_rating_change, get_recommendation_history, record_recommendation
from holdings import get_holdings, remove_holding, upsert_holding
from performance import compute_holdings_performance
from portfolio import get_portfolio_stocks, load_portfolio, save_portfolio
from scoring import build_recommendation_scorecard, calculate_fundamental_analysis
from storage import describe_age, load_stock_snapshot, save_stock_snapshot

# templates/ lives at the project root, one level up from src/ where this
# module lives - Flask resolves template_folder relative to this file.
app = Flask(__name__, template_folder='../templates')
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO').upper(),
    format='%(asctime)s %(levelname)s %(name)s: %(message)s'
)
LOGGER = logging.getLogger(__name__)

# Global variables to cache data
data_cache = {}
fundamental_cache = {}
last_update = {}
last_fetch_meta = {}
earnings_cache = {}
earnings_cache_updated = {}
options_cache = {}
options_cache_updated = {}


def load_live_stock_payload(symbol):
    """Fetch the live market and fundamental payload for a stock symbol."""
    provider = get_provider()
    data = provider.get_price_history(symbol, days=300)
    fundamental_data = provider.get_fundamentals(symbol)
    data = calculate_technical_indicators(data)
    return data, fundamental_data


def fetch_single_stock_data(symbol):
    """Fetch data for a single stock on demand"""
    # Check if we have cached data for this stock and if it's still fresh (cache for 1 hour)
    if (symbol in data_cache and symbol in last_update and
            (datetime.now() - last_update[symbol]).total_seconds() < CACHE_FRESH_SECONDS):
        LOGGER.info("Using cached data for %s", symbol)
        fetch_meta = last_fetch_meta.get(symbol, {}).copy()
        fetch_meta['source'] = 'cache'
        fetch_meta['cached'] = True
        fetch_meta['freshness'] = describe_age(last_update.get(symbol))
        fetch_meta['message'] = 'Using recently fetched in-memory cache.'
        last_fetch_meta[symbol] = fetch_meta
        return fetch_meta

    LOGGER.info("Fetching fresh data for %s", symbol)

    try:
        data, fundamental_data = run_with_retries(
            lambda: load_live_stock_payload(symbol),
            f"Live fetch for {symbol}"
        )

        # Cache the data
        data_cache[symbol] = data
        fundamental_cache[symbol] = fundamental_data
        last_update[symbol] = datetime.now()
        save_stock_snapshot(symbol, data, fundamental_data)
        last_fetch_meta[symbol] = {
            'source': 'live',
            'provider': get_provider().name,
            'cached': False,
            'success': True,
            'market_data_available': not data.empty,
            'fundamental_data_available': bool(fundamental_data),
            'snapshot_saved_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'freshness': 'just updated',
            'message': f'Fetched fresh market data ({get_provider().name}).'
        }

        LOGGER.info("Data fetch completed for %s", symbol)
        return last_fetch_meta[symbol]

    except Exception as e:
        LOGGER.warning("Error fetching data for %s: %s", symbol, e)
        snapshot = load_stock_snapshot(symbol)
        if snapshot:
            saved_dt = snapshot['saved_at']
            data_cache[symbol] = snapshot['data']
            fundamental_cache[symbol] = snapshot['fundamental_data']
            last_update[symbol] = saved_dt
            last_fetch_meta[symbol] = {
                'source': 'disk-cache',
                'cached': True,
                'success': True,
                'market_data_available': not data_cache[symbol].empty,
                'fundamental_data_available': bool(fundamental_cache[symbol]),
                'snapshot_saved_at': saved_dt.strftime('%Y-%m-%d %H:%M:%S'),
                'freshness': describe_age(saved_dt),
                'message': (
                    f"Live fetch failed; using the last successful snapshot instead. Original error: {e}"
                ),
            }
            return last_fetch_meta[symbol]

        # Store empty data to avoid repeated failed requests
        data_cache[symbol] = pd.DataFrame()
        fundamental_cache[symbol] = {}
        last_update[symbol] = datetime.now()
        last_fetch_meta[symbol] = {
            'source': 'error',
            'cached': False,
            'success': False,
            'market_data_available': False,
            'fundamental_data_available': False,
            'message': f'Unable to fetch fresh data: {e}'
        }
        return last_fetch_meta[symbol]


def _fetch_and_score_symbol(symbol, profile_name):
    """Fetch, score, and record today's rating for one symbol.

    Shared by the single-stock view, Holdings enrichment, and the
    watchlist change-check endpoint so the fetch->score->record pipeline
    is defined once.
    """
    fetch_status = fetch_single_stock_data(symbol)

    fundamental_metrics = fundamental_cache.get(symbol, {})
    fundamental_analysis = calculate_fundamental_analysis(fundamental_metrics)

    stock_data = data_cache.get(symbol, pd.DataFrame())
    technical_summary = build_technical_momentum_summary(stock_data)
    scorecard = build_recommendation_scorecard(
        fundamental_analysis,
        technical_summary['signal'],
        profile_name=profile_name,
        technical_summary=technical_summary,
    )

    rating_change = detect_rating_change(symbol, profile_name, scorecard['label'])
    record_recommendation(symbol, profile_name, scorecard)

    return {
        'fetch_status': fetch_status,
        'fundamental_metrics': fundamental_metrics,
        'fundamental_analysis': fundamental_analysis,
        'technical_summary': technical_summary,
        'scorecard': scorecard,
        'rating_change': rating_change,
    }


@app.route('/')
def index():
    """Main page"""
    portfolio = load_portfolio()
    portfolio_sorted = sorted(portfolio, key=lambda x: x['symbol'])
    return render_template('index.html', stocks=portfolio_sorted)


@app.route('/api/stock/<symbol>')
def get_stock_data(symbol):
    """API endpoint to get stock data"""
    portfolio_stocks = get_portfolio_stocks()
    if symbol not in portfolio_stocks:
        return jsonify({'error': 'Stock not found in portfolio'}), 404

    profile_name = request.args.get('profile', 'balanced')

    result = _fetch_and_score_symbol(symbol, profile_name)
    fetch_status = result['fetch_status']
    fundamental_metrics = result['fundamental_metrics']
    fundamental_analysis = result['fundamental_analysis']
    recommendation_scorecard = result['scorecard']
    technical_recommendation = result['technical_summary']['signal']
    overall_recommendation = recommendation_scorecard['label']
    rating_change = result['rating_change']

    # Format fundamental metrics for display
    formatted_metrics = {}
    for key, value in fundamental_metrics.items():
        if value is not None:
            if key in ['market_cap', 'enterprise_value', 'total_debt', 'total_cash']:
                formatted_metrics[key] = f"${value:,.0f}"
            elif key in ['profit_margin', 'operating_margin', 'return_on_equity', 'return_on_assets',
                         'revenue_growth', 'earnings_growth', 'dividend_yield', 'revenue_growth_yoy',
                         'ocf_growth_yoy', 'roe_growth_yoy']:
                formatted_metrics[key] = f"{value:.1%}"
            elif key in ['pe_ratio', 'forward_pe', 'price_to_book', 'price_to_sales', 'debt_to_equity', 'current_ratio', 'beta']:
                formatted_metrics[key] = f"{value:.2f}"
            else:
                formatted_metrics[key] = value
        else:
            formatted_metrics[key] = 'N/A'

    # Get last update time for this specific stock
    stock_last_update = last_update.get(symbol)

    return jsonify({
        'symbol': symbol,
        'fundamental_metrics': formatted_metrics,
        'fundamental_analysis': fundamental_analysis,
        'fetch_status': fetch_status,
        'technical_recommendation': technical_recommendation,
        'recommendation_scorecard': recommendation_scorecard,
        'available_scoring_profiles': [
            {'key': key, 'label': value['label']}
            for key, value in SCORING_PROFILES.items()
        ],
        'overall_recommendation': overall_recommendation,
        'rating_change': rating_change,
        'last_updated': stock_last_update.strftime('%Y-%m-%d %H:%M:%S') if stock_last_update else 'N/A'
    })


@app.route('/api/stock/<symbol>/history')
def get_stock_history(symbol):
    """API endpoint to get recorded recommendation history for a stock"""
    portfolio_stocks = get_portfolio_stocks()
    if symbol not in portfolio_stocks:
        return jsonify({'error': 'Stock not found in portfolio'}), 404

    profile_name = request.args.get('profile', 'balanced')
    history = get_recommendation_history(symbol, profile_name)

    return jsonify({'symbol': symbol, 'profile': profile_name, 'history': history})


@app.route('/api/stock/<symbol>/earnings')
def get_stock_earnings(symbol):
    """API endpoint to get the next earnings date and estimates for a stock.

    Cached far longer than price/fundamental data (EARNINGS_CACHE_SECONDS)
    since earnings dates rarely change day to day. Failures degrade to an
    'unavailable' payload rather than a 500, since this is a secondary
    enrichment that should never block the main stock view.
    """
    portfolio_stocks = get_portfolio_stocks()
    if symbol not in portfolio_stocks:
        return jsonify({'error': 'Stock not found in portfolio'}), 404

    if (symbol in earnings_cache and symbol in earnings_cache_updated and
            (datetime.now() - earnings_cache_updated[symbol]).total_seconds() < EARNINGS_CACHE_SECONDS):
        return jsonify(earnings_cache[symbol])

    try:
        earnings_data = get_provider().get_earnings_calendar(symbol)
    except Exception as e:
        LOGGER.warning("Failed to fetch earnings calendar for %s: %s", symbol, e)
        earnings_data = {
            'next_earnings_date': None,
            'days_until': None,
            'eps_estimate': None,
            'eps_estimate_low': None,
            'eps_estimate_high': None,
            'revenue_estimate': None,
        }

    earnings_cache[symbol] = earnings_data
    earnings_cache_updated[symbol] = datetime.now()
    return jsonify(earnings_data)


@app.route('/api/stock/<symbol>/options')
def get_stock_put_options(symbol):
    """API endpoint for cash-secured put candidates (near-the-money,
    near-term, liquid puts) on a stock.

    Cached for OPTIONS_CACHE_SECONDS since premiums move faster than
    earnings dates. Like the earnings endpoint, a provider failure degrades
    to an empty/error payload rather than a 500 - this is a secondary
    enrichment panel that should never block the main stock view.
    """
    portfolio_stocks = get_portfolio_stocks()
    if symbol not in portfolio_stocks:
        return jsonify({'error': 'Stock not found in portfolio'}), 404

    if (symbol in options_cache and symbol in options_cache_updated and
            (datetime.now() - options_cache_updated[symbol]).total_seconds() < OPTIONS_CACHE_SECONDS):
        return jsonify(options_cache[symbol])

    fetch_single_stock_data(symbol)
    current_price = fundamental_cache.get(symbol, {}).get('current_price')

    try:
        candidates = get_provider().get_put_options(symbol, current_price=current_price) if current_price else []
        payload = {'symbol': symbol, 'current_price': current_price, 'candidates': candidates}
    except Exception as e:
        LOGGER.warning("Failed to fetch put options for %s: %s", symbol, e)
        payload = {'symbol': symbol, 'current_price': current_price, 'candidates': [], 'error': str(e)}

    options_cache[symbol] = payload
    options_cache_updated[symbol] = datetime.now()
    return jsonify(payload)


@app.route('/api/chart-data/<symbol>')
def get_chart_data(symbol):
    """API endpoint to get technical analysis chart data as JSON"""
    portfolio_stocks = get_portfolio_stocks()
    if symbol not in portfolio_stocks:
        return jsonify({'error': 'Stock not found in portfolio'}), 404

    # Ensure we have data for this stock
    fetch_single_stock_data(symbol)
    stock_data = data_cache.get(symbol, pd.DataFrame())

    if stock_data.empty:
        return jsonify({'error': 'No data available'}), 404

    return jsonify(serialize_chart_data(stock_data))


@app.route('/api/portfolio', methods=['GET'])
def get_portfolio():
    """Get current portfolio sorted alphabetically"""
    portfolio = load_portfolio()
    portfolio_sorted = sorted(portfolio, key=lambda x: x['symbol'])
    return jsonify({'portfolio': portfolio_sorted})


@app.route('/api/portfolio/add', methods=['POST'])
def add_to_portfolio():
    """Add stock to portfolio"""
    data = request.get_json()
    symbol = data.get('symbol', '').upper().strip()

    if not symbol:
        return jsonify({'error': 'Symbol is required'}), 400

    # Validate stock exists
    try:
        lookup = run_with_retries(
            lambda: get_provider().lookup_symbol(symbol),
            f"Symbol lookup for {symbol}",
            attempts=2,
        )
    except Exception:
        return jsonify({'error': 'Unable to fetch stock data'}), 400

    if not lookup:
        return jsonify({'error': 'Invalid stock symbol'}), 400

    # Load current portfolio
    portfolio = load_portfolio()

    # Check if stock already exists
    for stock in portfolio:
        if stock['symbol'] == symbol:
            return jsonify({'error': 'Stock already in portfolio'}), 400

    # Add new stock
    new_stock = {
        'symbol': symbol,
        'name': lookup['name'],
        'date_added': datetime.now().strftime('%Y-%m-%d')
    }
    portfolio.append(new_stock)

    # Save portfolio
    if save_portfolio(portfolio):
        _clear_stock_caches(symbol)
        return jsonify({'message': f'Successfully added {symbol} to portfolio', 'stock': new_stock})
    else:
        return jsonify({'error': 'Failed to save portfolio'}), 500


@app.route('/api/portfolio/remove', methods=['POST'])
def remove_from_portfolio():
    """Remove stock from portfolio"""
    data = request.get_json()
    symbol = data.get('symbol', '').upper().strip()

    if not symbol:
        return jsonify({'error': 'Symbol is required'}), 400

    # Load current portfolio
    portfolio = load_portfolio()

    # Find and remove stock
    original_length = len(portfolio)
    portfolio = [stock for stock in portfolio if stock['symbol'] != symbol]

    if len(portfolio) == original_length:
        return jsonify({'error': 'Stock not found in portfolio'}), 404

    # Save portfolio
    if save_portfolio(portfolio):
        _clear_stock_caches(symbol)
        return jsonify({'message': f'Successfully removed {symbol} from portfolio'})
    else:
        return jsonify({'error': 'Failed to save portfolio'}), 500


@app.route('/api/portfolio/changes', methods=['GET'])
def get_portfolio_changes():
    """Check every watchlist symbol for a rating change since it was last
    checked. A fetch failure on one symbol is isolated to `errors` rather
    than aborting the whole batch.
    """
    profile_name = request.args.get('profile', 'balanced')
    symbols = get_portfolio_stocks()
    changes = []
    errors = []
    for symbol in symbols:
        try:
            result = _fetch_and_score_symbol(symbol, profile_name)
        except Exception as e:
            LOGGER.warning("Could not check %s for rating changes: %s", symbol, e)
            errors.append({'symbol': symbol, 'message': str(e)})
            continue
        if result['rating_change'] is not None:
            changes.append({
                'symbol': symbol,
                'new_label': result['scorecard']['label'],
                **result['rating_change'],
            })
    return jsonify({'checked': symbols, 'changes': changes, 'errors': errors, 'profile': profile_name})


def _clear_stock_caches(symbol):
    """Drop any in-memory cached state for a symbol."""
    data_cache.pop(symbol, None)
    fundamental_cache.pop(symbol, None)
    last_update.pop(symbol, None)
    last_fetch_meta.pop(symbol, None)
    options_cache.pop(symbol, None)
    options_cache_updated.pop(symbol, None)


HOLDINGS_PROFILE = 'balanced'


def _enrich_holding(holding):
    """Add live price, P&L, and current rating to a stored holding row."""
    symbol = holding['symbol']
    shares = holding['shares']
    cost_basis = holding['cost_basis_per_share']

    enriched = dict(holding)
    enriched['cost_total'] = round(shares * cost_basis, 2)

    result = _fetch_and_score_symbol(symbol, HOLDINGS_PROFILE)
    enriched['fetch_status'] = result['fetch_status']

    current_price = result['fundamental_metrics'].get('current_price')

    if current_price is not None:
        market_value = shares * current_price
        enriched['current_price'] = current_price
        enriched['market_value'] = round(market_value, 2)
        enriched['unrealized_pnl'] = round(market_value - shares * cost_basis, 2)
        enriched['unrealized_pnl_pct'] = round(
            (current_price - cost_basis) / cost_basis * 100, 2
        ) if cost_basis else None
    else:
        enriched['current_price'] = None
        enriched['market_value'] = None
        enriched['unrealized_pnl'] = None
        enriched['unrealized_pnl_pct'] = None

    enriched['rating'] = result['scorecard']['label']
    enriched['rating_change'] = result['rating_change']

    return enriched


@app.route('/api/holdings', methods=['GET'])
def get_holdings_route():
    """Get current holdings enriched with live price, P&L, and rating"""
    enriched_holdings = [_enrich_holding(holding) for holding in get_holdings()]
    return jsonify({'holdings': enriched_holdings})


@app.route('/api/holdings/add', methods=['POST'])
def add_holding():
    """Add or top up a holding"""
    data = request.get_json()
    symbol = data.get('symbol', '').upper().strip()

    if not symbol:
        return jsonify({'error': 'Symbol is required'}), 400

    try:
        shares = float(data.get('shares'))
        cost_basis_per_share = float(data.get('cost_basis_per_share'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Shares and cost basis must be numbers'}), 400

    purchase_date = data.get('purchase_date') or None
    notes = data.get('notes') or None

    try:
        lookup = run_with_retries(
            lambda: get_provider().lookup_symbol(symbol),
            f"Symbol lookup for {symbol}",
            attempts=2,
        )
    except Exception:
        return jsonify({'error': 'Unable to fetch stock data'}), 400

    if not lookup:
        return jsonify({'error': 'Invalid stock symbol'}), 400

    try:
        holding = upsert_holding(symbol, shares, cost_basis_per_share, purchase_date, notes)
    except ValueError as error:
        return jsonify({'error': str(error)}), 400

    _clear_stock_caches(symbol)
    return jsonify({'message': f'Successfully added {symbol} to holdings', 'holding': holding})


@app.route('/api/holdings/remove', methods=['POST'])
def remove_holding_route():
    """Remove a holding entirely, or reduce it if `shares` is given"""
    data = request.get_json()
    symbol = data.get('symbol', '').upper().strip()

    if not symbol:
        return jsonify({'error': 'Symbol is required'}), 400

    shares = data.get('shares')
    if shares is not None:
        try:
            shares = float(shares)
        except (TypeError, ValueError):
            return jsonify({'error': 'Shares must be a number'}), 400

    try:
        removed = remove_holding(symbol, shares)
    except ValueError as error:
        return jsonify({'error': str(error)}), 400

    if not removed:
        return jsonify({'error': 'Holding not found'}), 404

    return jsonify({'message': f'Successfully updated {symbol} in holdings'})


@app.route('/api/holdings/performance', methods=['GET'])
def get_holdings_performance_route():
    """Get portfolio value vs. a benchmark shadow portfolio over time.

    Holdings without a purchase_date, or whose price history can't be
    fetched, are excluded (reported in `excluded_holdings`) rather than
    failing the whole request. Only a fetch failure for the benchmark
    itself is treated as a hard error.
    """
    benchmark_symbol = request.args.get('benchmark', 'SPY')

    try:
        result = compute_holdings_performance(get_holdings(), benchmark_symbol=benchmark_symbol)
    except ValueError as error:
        return jsonify({'error': str(error)}), 400

    return jsonify(result)


@app.route('/api/benchmarks/refresh', methods=['POST'])
def refresh_sector_benchmarks():
    """Calculate and save sector benchmarks to JSON file"""
    try:
        LOGGER.info("Starting sector benchmark calculation via API")

        new_benchmarks = refresh_all_sector_benchmarks()

        if new_benchmarks:
            metadata = new_benchmarks.get('_metadata', {})
            sectors_calculated = [k for k in new_benchmarks.keys() if not k.startswith('_')]

            return jsonify({
                'message': 'Sector benchmarks calculated and saved successfully',
                'sectors_calculated': sectors_calculated,
                'total_sectors': len(sectors_calculated),
                'last_updated': metadata.get('last_updated'),
                'file_saved': 'sector_benchmarks.json'
            })
        else:
            return jsonify({'error': 'Failed to calculate sector benchmarks'}), 500

    except Exception as e:
        return jsonify({'error': f'Failed to refresh benchmarks: {str(e)}'}), 500


@app.route('/api/benchmarks/status', methods=['GET'])
def get_benchmark_status():
    """Get status of sector benchmark file"""
    try:
        # Check if file exists
        benchmarks_file = benchmarks.SECTOR_BENCHMARKS_FILE
        file_exists = os.path.exists(benchmarks_file)

        if file_exists:
            file_stat = os.stat(benchmarks_file)
            file_modified = datetime.fromtimestamp(file_stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            file_size = f"{file_stat.st_size / 1024:.1f} KB"
        else:
            file_modified = "Never"
            file_size = "0 KB"

        # Get metadata from loaded data
        benchmarks_data = benchmarks.sector_benchmarks_data
        metadata = benchmarks_data.get('_metadata', {})
        sectors_available = [k for k in benchmarks_data.keys() if not k.startswith('_')]
        last_calculated = metadata.get('last_updated')
        last_calculated_dt = None
        if last_calculated and last_calculated != 'fallback':
            try:
                last_calculated_dt = datetime.strptime(last_calculated, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                last_calculated_dt = None

        # Check representative stocks status
        representative_stocks = get_sector_representative_stocks()

        return jsonify({
            'file_status': {
                'exists': file_exists,
                'path': os.path.basename(benchmarks_file),
                'last_modified': file_modified,
                'size': file_size
            },
            'benchmark_data': {
                'total_sectors': len(sectors_available),
                'sectors_available': sectors_available,
                'last_calculated': last_calculated or 'Unknown',
                'last_calculated_age': describe_age(last_calculated_dt) if last_calculated_dt else 'Static fallback data',
                'calculation_method': metadata.get('calculation_method', 'Unknown'),
                'using_fallback': metadata.get('last_updated') == 'fallback',
                'refresh_history': metadata.get('refresh_history', [])
            },
            'representative_stocks': {
                'total_stocks': sum(len(stocks) for stocks in representative_stocks.values()),
                'stocks_per_sector': {sector: len(stocks) for sector, stocks in representative_stocks.items()}
            }
        })

    except Exception as e:
        return jsonify({'error': f'Failed to get benchmark status: {str(e)}'}), 500


@app.route('/api/backtest/run', methods=['POST'])
def run_backtest_route():
    """Run a backtest for a symbol/date range and persist the result.

    Synchronous by design: this is a manually-triggered, occasional action
    (not something run continuously), and a multi-month backtest with a
    5-day step completes in well under a second of computation.
    """
    data = request.get_json() or {}
    symbol = (data.get('symbol') or '').upper().strip()
    if not symbol:
        return jsonify({'error': 'Symbol is required'}), 400

    start_date = data.get('start_date')
    end_date = data.get('end_date')
    if not start_date or not end_date:
        return jsonify({'error': 'start_date and end_date are required'}), 400

    profile = data.get('profile', 'balanced')
    try:
        holding_period_days = int(data.get('holding_period_days') or BACKTEST_HOLDING_PERIOD_DAYS)
        step_days = int(data.get('step_days') or BACKTEST_STEP_DAYS)
    except (TypeError, ValueError):
        return jsonify({'error': 'holding_period_days and step_days must be integers'}), 400

    try:
        report = run_backtest(
            symbol, start_date, end_date, profile=profile,
            holding_period_days=holding_period_days, step_days=step_days,
        )
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except Exception as error:
        LOGGER.exception("Backtest failed for %s", symbol)
        return jsonify({'error': f'Backtest failed: {error}'}), 500

    report['id'] = save_backtest_result(report)
    return jsonify(report)


@app.route('/api/backtest/results', methods=['GET'])
def list_backtest_results_route():
    """List summary metadata for all past backtest runs, newest first"""
    return jsonify({'results': get_backtest_results()})


@app.route('/api/backtest/results/<int:run_id>', methods=['GET'])
def get_backtest_result_route(run_id):
    """Get the full report (including per-step detail and equity curve) for one past run"""
    result = get_backtest_result(run_id)
    if result is None:
        return jsonify({'error': 'Backtest result not found'}), 404
    return jsonify(result)


if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', '').lower() in {'1', 'true', 'yes', 'on'}
    host = os.getenv('FLASK_HOST', '127.0.0.1')
    port = int(os.getenv('FLASK_PORT', '5000'))
    app.run(debug=debug_mode, host=host, port=port)
