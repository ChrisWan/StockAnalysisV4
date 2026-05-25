"""
Stock Portfolio Analysis Web Service
A Flask application to display fundamental and technical analysis for your stock portfolio
"""

from flask import Flask, render_template, jsonify, request
import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
import logging
import json
import os
import time
from datetime import datetime
matplotlib.use('Agg')  # Use non-interactive backend for web
import io
import base64
import json
import os
from datetime import datetime, timedelta
import numpy as np

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO').upper(),
    format='%(asctime)s %(levelname)s %(name)s: %(message)s'
)
LOGGER = logging.getLogger(__name__)

# Portfolio configuration file
PORTFOLIO_FILE = os.path.join(BASE_DIR, 'portfolio.json')
SECTOR_BENCHMARKS_FILE = os.path.join(BASE_DIR, 'sector_benchmarks.json')
STOCK_CACHE_DIR = os.path.join(BASE_DIR, 'cache', 'stocks')
CACHE_MAX_AGE_DAYS = 30
CACHE_MAX_FILES = 100
MAX_BENCHMARK_HISTORY = 12
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


def ensure_stock_cache_dir():
    """Ensure the stock cache directory exists."""
    os.makedirs(STOCK_CACHE_DIR, exist_ok=True)


def describe_age(reference_time, now=None):
    """Return a compact human-readable age for a timestamp."""
    if reference_time is None:
        return None

    now = now or datetime.now()
    delta = now - reference_time
    total_seconds = max(int(delta.total_seconds()), 0)

    if total_seconds < 60:
        return 'less than a minute old'
    if total_seconds < 3600:
        minutes = total_seconds // 60
        return f'{minutes} minute(s) old'
    if total_seconds < 86400:
        hours = total_seconds // 3600
        return f'{hours} hour(s) old'

    days = total_seconds // 86400
    return f'{days} day(s) old'


def normalize_weights(weights, fallback):
    """Normalize a weight mapping so it sums to 1."""
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return fallback.copy()

    return {
        key: round(value / total_weight, 4)
        for key, value in weights.items()
    }


def get_stock_cache_file(symbol):
    """Get the on-disk cache path for a stock symbol."""
    ensure_stock_cache_dir()
    return os.path.join(STOCK_CACHE_DIR, f'{symbol.upper()}.pkl')


def prune_stock_cache(max_age_days=CACHE_MAX_AGE_DAYS, max_files=CACHE_MAX_FILES, now=None):
    """Remove stale or excess stock snapshot files from disk."""
    ensure_stock_cache_dir()
    now = now or datetime.now()
    max_age = timedelta(days=max_age_days)

    cache_entries = []
    for name in os.listdir(STOCK_CACHE_DIR):
        if not name.endswith('.pkl'):
            continue

        file_path = os.path.join(STOCK_CACHE_DIR, name)
        modified_at = datetime.fromtimestamp(os.path.getmtime(file_path))
        age = now - modified_at

        if age > max_age:
            os.remove(file_path)
            continue

        cache_entries.append((modified_at, file_path))

    cache_entries.sort(reverse=True)
    for _, file_path in cache_entries[max_files:]:
        os.remove(file_path)


def save_stock_snapshot(symbol, stock_data, fundamental_data):
    """Persist the latest successful stock payload to disk."""
    if stock_data.empty and not fundamental_data:
        return

    snapshot = {
        'data': stock_data,
        'fundamental_data': fundamental_data,
        'saved_at': datetime.now().isoformat(),
    }
    pd.to_pickle(snapshot, get_stock_cache_file(symbol))
    prune_stock_cache()


def load_stock_snapshot(symbol):
    """Load the last successful stock payload from disk if available."""
    cache_file = get_stock_cache_file(symbol)
    if not os.path.exists(cache_file):
        return None

    snapshot = pd.read_pickle(cache_file)
    saved_at = snapshot.get('saved_at')
    saved_dt = datetime.fromisoformat(saved_at) if saved_at else datetime.now()

    data_cache[symbol] = snapshot.get('data', pd.DataFrame())
    fundamental_cache[symbol] = snapshot.get('fundamental_data', {})
    last_update[symbol] = saved_dt

    return {
        'source': 'disk-cache',
        'cached': True,
        'success': True,
        'market_data_available': not data_cache[symbol].empty,
        'fundamental_data_available': bool(fundamental_cache[symbol]),
        'snapshot_saved_at': saved_dt.strftime('%Y-%m-%d %H:%M:%S'),
        'freshness': describe_age(saved_dt),
        'message': f'Loaded the last successful snapshot saved at {saved_dt.strftime("%Y-%m-%d %H:%M:%S")}.',
    }


def run_with_retries(operation, description, attempts=3, delay_seconds=1.0):
    """Run a network-bound operation with simple retry and backoff."""
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as error:
            last_error = error
            if attempt == attempts:
                break

            LOGGER.warning(
                "%s failed on attempt %s/%s: %s. Retrying in %.1fs",
                description,
                attempt,
                attempts,
                error,
                delay_seconds,
            )
            time.sleep(delay_seconds)

    raise last_error


def load_live_stock_payload(symbol):
    """Fetch the live market and fundamental payload for a stock symbol."""
    data = yf.download(symbol, period='300d', interval='1d', auto_adjust=True)
    data = data.dropna()

    ticker = yf.Ticker(symbol)
    info = ticker.info
    basic_metrics = extract_fundamental_metrics(symbol, info)
    enhanced_metrics = extract_enhanced_growth_metrics(ticker)

    if basic_metrics:
        basic_metrics.update(enhanced_metrics)
        fundamental_data = basic_metrics
    else:
        fundamental_data = enhanced_metrics

    data = calculate_technical_indicators(data)
    return data, fundamental_data


def get_scorecard_weights(sector):
    """Return normalized scorecard weights for a sector."""
    weights = SECTOR_SCORECARD_WEIGHTS.get(sector, DEFAULT_SCORECARD_WEIGHTS).copy()
    return normalize_weights(weights, DEFAULT_SCORECARD_WEIGHTS)

def get_scoring_profile(profile_name):
    """Return scoring profile metadata and normalized weights."""
    profile_key = (profile_name or 'balanced').strip().lower()
    if profile_key not in SCORING_PROFILES:
        profile_key = 'balanced'

    profile = SCORING_PROFILES[profile_key]
    return {
        'key': profile_key,
        'label': profile['label'],
        'weights': normalize_weights(profile['weights'], SCORING_PROFILES['balanced']['weights']),
    }


def combine_weight_profiles(sector, profile_name):
    """Blend sector-specific weights with a user-selected scoring profile."""
    sector_weights = get_scorecard_weights(sector)
    scoring_profile = get_scoring_profile(profile_name)
    if scoring_profile['key'] == 'balanced':
        return sector_weights, scoring_profile

    combined = {
        key: sector_weights[key] * 0.5 + scoring_profile['weights'][key] * 0.5
        for key in DEFAULT_SCORECARD_WEIGHTS.keys()
    }
    return normalize_weights(combined, DEFAULT_SCORECARD_WEIGHTS), scoring_profile

def load_portfolio():
    """Load portfolio from JSON file"""
    try:
        if os.path.exists(PORTFOLIO_FILE):
            with open(PORTFOLIO_FILE, 'r') as f:
                data = json.load(f)
                return data.get('portfolio', [])
        else:
            # Create default portfolio if file doesn't exist
            default_portfolio = []
            save_portfolio(default_portfolio)
            return default_portfolio
    except Exception as e:
        LOGGER.exception("Error loading portfolio")
        return []

def save_portfolio(portfolio):
    """Save portfolio to JSON file"""
    try:
        data = {'portfolio': portfolio}
        with open(PORTFOLIO_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        return True
    except Exception as e:
        LOGGER.exception("Error saving portfolio")
        return False

def get_portfolio_stocks():
    """Get list of stock symbols from portfolio"""
    portfolio = load_portfolio()
    return [stock['symbol'] for stock in portfolio]

def get_company_name_from_yf(symbol):
    """Get company name from Yahoo Finance"""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        return info.get('longName', info.get('shortName', symbol))
    except:
        return symbol

# Global variables to cache data
data_cache = {}
fundamental_cache = {}
last_update = {}
last_fetch_meta = {}

def fetch_single_stock_data(symbol):
    """Fetch data for a single stock on demand"""
    global data_cache, fundamental_cache, last_update, last_fetch_meta
    
    # Check if we have cached data for this stock and if it's still fresh (cache for 1 hour)
    if (symbol in data_cache and symbol in last_update and 
        (datetime.now() - last_update[symbol]).seconds < 3600):
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
            'cached': False,
            'success': True,
            'market_data_available': not data.empty,
            'fundamental_data_available': bool(fundamental_data),
            'snapshot_saved_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'freshness': 'just updated',
            'message': 'Fetched fresh market data from Yahoo Finance.'
        }
        
        LOGGER.info("Data fetch completed for %s", symbol)
        return last_fetch_meta[symbol]
        
    except Exception as e:
        LOGGER.warning("Error fetching data for %s: %s", symbol, e)
        cached_snapshot = load_stock_snapshot(symbol)
        if cached_snapshot:
            cached_snapshot['message'] = (
                f"Live fetch failed; using the last successful snapshot instead. Original error: {e}"
            )
            last_fetch_meta[symbol] = cached_snapshot
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

def fetch_stock_data():
    """Legacy function - kept for backward compatibility but now does nothing"""
    # This function is now obsolete since we load stocks on demand
    # Keeping it to avoid breaking existing code that might call it
    pass

def extract_fundamental_metrics(stock, info):
    """Extract key fundamental metrics from Yahoo Finance info"""
    metrics = {}
    
    try:
        # Market Metrics
        metrics['market_cap'] = info.get('marketCap', None)
        metrics['enterprise_value'] = info.get('enterpriseValue', None)
        metrics['shares_outstanding'] = info.get('sharesOutstanding', None)
        
        # Valuation Ratios
        metrics['pe_ratio'] = info.get('trailingPE', None)
        metrics['forward_pe'] = info.get('forwardPE', None)
        metrics['peg_ratio'] = info.get('pegRatio', None)
        metrics['price_to_book'] = info.get('priceToBook', None)
        metrics['price_to_sales'] = info.get('priceToSalesTrailing12Months', None)
        metrics['ev_to_revenue'] = info.get('enterpriseToRevenue', None)
        metrics['ev_to_ebitda'] = info.get('enterpriseToEbitda', None)
        
        # Profitability Metrics
        metrics['profit_margin'] = info.get('profitMargins', None)
        metrics['operating_margin'] = info.get('operatingMargins', None)
        metrics['return_on_assets'] = info.get('returnOnAssets', None)
        metrics['return_on_equity'] = info.get('returnOnEquity', None)
        
        # Financial Health
        metrics['total_debt'] = info.get('totalDebt', None)
        metrics['total_cash'] = info.get('totalCash', None)
        metrics['debt_to_equity'] = info.get('debtToEquity', None)
        metrics['current_ratio'] = info.get('currentRatio', None)
        metrics['quick_ratio'] = info.get('quickRatio', None)
        
        # Growth Metrics
        metrics['revenue_growth'] = info.get('revenueGrowth', None)
        metrics['earnings_growth'] = info.get('earningsGrowth', None)
        
        # Dividend Information
        metrics['dividend_yield'] = info.get('dividendYield', None)
        metrics['payout_ratio'] = info.get('payoutRatio', None)
        
        # Other Key Metrics
        metrics['beta'] = info.get('beta', None)
        metrics['52_week_high'] = info.get('fiftyTwoWeekHigh', None)
        metrics['52_week_low'] = info.get('fiftyTwoWeekLow', None)
        metrics['avg_volume'] = info.get('averageVolume', None)
        
        # Company Information
        metrics['sector'] = info.get('sector', None)
        metrics['industry'] = info.get('industry', None)
        metrics['country'] = info.get('country', None)
        metrics['employees'] = info.get('fullTimeEmployees', None)
        
        # Current price
        metrics['current_price'] = info.get('currentPrice', None)
        metrics['previous_close'] = info.get('previousClose', None)
        
    except Exception as e:
        LOGGER.warning("Error extracting metrics for %s: %s", stock, e)
        return None
    
    return metrics

def calculate_technical_indicators(data):
    """Calculate technical indicators"""
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.droplevel(1)
    
    if len(data) < 30:
        return data
    
    data = data.sort_index()
    
    # 30-Day Moving Average
    data['30_Moving_Avg'] = data['Close'].rolling(window=30, min_periods=1).mean()
    data['90_Moving_Avg'] = data['Close'].rolling(window=90, min_periods=30).mean()
    
    # Stochastic Oscillator
    high_14 = data['High'].rolling(window=14, min_periods=1).max()
    low_14 = data['Low'].rolling(window=14, min_periods=1).min()
    
    denominator = (high_14 - low_14)
    denominator = denominator.replace(0, 1e-10)
    data['%K'] = (data['Close'] - low_14) * 100 / denominator
    data['%K'] = data['%K'].rolling(window=3, min_periods=1).mean()
    data['%D'] = data['%K'].rolling(window=3, min_periods=1).mean()
    
    # MACD
    data['12_EMA'] = data['Close'].ewm(span=12, adjust=False).mean()
    data['26_EMA'] = data['Close'].ewm(span=26, adjust=False).mean()
    data['MACD'] = data['12_EMA'] - data['26_EMA']
    data['Signal_Line'] = data['MACD'].ewm(span=9, adjust=False).mean()
    
    # Generate buy/sell signals from true crossovers and a simple trend filter.
    data['trend_up'] = (data['30_Moving_Avg'] > data['90_Moving_Avg']).fillna(False)
    data['trend_down'] = (data['30_Moving_Avg'] < data['90_Moving_Avg']).fillna(False)

    data['buy_ma'] = (
        (data['Close'] > data['30_Moving_Avg']) &
        (data['Close'].shift(1) <= data['30_Moving_Avg'].shift(1))
    )
    data['buy_stochastic'] = (
        (data['%K'] > data['%D']) &
        (data['%K'].shift(1) <= data['%D'].shift(1)) &
        ((data['%K'].shift(1) < 30) | (data['%D'].shift(1) < 30))
    )
    data['buy_macd'] = (
        (data['MACD'] > data['Signal_Line']) &
        (data['MACD'].shift(1) <= data['Signal_Line'].shift(1))
    )
    
    data['sell_ma'] = (
        (data['Close'] < data['30_Moving_Avg']) &
        (data['Close'].shift(1) >= data['30_Moving_Avg'].shift(1))
    )
    data['sell_stochastic'] = (
        (data['%K'] < data['%D']) &
        (data['%K'].shift(1) >= data['%D'].shift(1)) &
        ((data['%K'].shift(1) > 70) | (data['%D'].shift(1) > 70))
    )
    data['sell_macd'] = (
        (data['MACD'] < data['Signal_Line']) &
        (data['MACD'].shift(1) >= data['Signal_Line'].shift(1))
    )
    
    window = 10
    buy_signal = (
        data['trend_up'] &
        data['buy_ma'].rolling(window=window, min_periods=window).max().fillna(False).astype(bool) &
        data['buy_stochastic'].rolling(window=window, min_periods=window).max().fillna(False).astype(bool) &
        data['buy_macd'].rolling(window=window, min_periods=window).max().fillna(False).astype(bool)
    )
    data['Buy_Signal'] = buy_signal
    
    sell_signal = (
        data['trend_down'] &
        data['sell_ma'].rolling(window=window, min_periods=window).max().fillna(False).astype(bool) &
        data['sell_stochastic'].rolling(window=window, min_periods=window).max().fillna(False).astype(bool) &
        data['sell_macd'].rolling(window=window, min_periods=window).max().fillna(False).astype(bool)
    )
    data['Sell_Signal'] = sell_signal
    
    return data

def get_sector_representative_stocks():
    """Get representative stocks for each sector to calculate benchmarks"""
    return {
        'Technology': ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'NFLX', 'ADBE', 'CRM'],
        'Healthcare': ['JNJ', 'UNH', 'PFE', 'ABBV', 'TMO', 'ABT', 'MRK', 'DHR', 'BMY', 'LLY'],
        'Financial Services': ['JPM', 'BAC', 'WFC', 'GS', 'MS', 'C', 'AXP', 'BLK', 'SCHW', 'USB'],
        'Consumer Cyclical': ['HD', 'MCD', 'NKE', 'SBUX', 'TGT', 'LOW', 'TJX', 'MAR', 'GM', 'F'],
        'Consumer Defensive': ['PG', 'KO', 'PEP', 'WMT', 'COST', 'CL', 'KMB', 'GIS', 'K', 'CPB'],
        'Utilities': ['NEE', 'DUK', 'SO', 'D', 'AEP', 'EXC', 'SRE', 'PEG', 'XEL', 'ED'],
        'Energy': ['XOM', 'CVX', 'COP', 'EOG', 'SLB', 'PXD', 'KMI', 'OKE', 'WMB', 'VLO'],
        'Industrials': ['BA', 'HON', 'UPS', 'CAT', 'GE', 'MMM', 'LMT', 'RTX', 'UNP', 'CSX'],
        'Materials': ['LIN', 'APD', 'SHW', 'FCX', 'NEM', 'DOW', 'DD', 'PPG', 'ECL', 'IFF'],
        'Real Estate': ['AMT', 'PLD', 'CCI', 'EQIX', 'SPG', 'O', 'WELL', 'DLR', 'PSA', 'EQR'],
        'Communication Services': ['T', 'VZ', 'CMCSA', 'DIS', 'CHTR', 'TMUS', 'NFLX', 'EA', 'ATVI', 'TTWO']
    }

def load_sector_benchmarks():
    """Load sector benchmarks from JSON file"""
    benchmarks_file = SECTOR_BENCHMARKS_FILE
    
    try:
        if os.path.exists(benchmarks_file):
            with open(benchmarks_file, 'r') as f:
                data = json.load(f)
                LOGGER.info("Loaded sector benchmarks from %s", benchmarks_file)
                return data
        else:
            LOGGER.warning("Benchmarks file %s not found, using fallback benchmarks", benchmarks_file)
            return get_fallback_sector_benchmarks_all()
    except Exception as e:
        LOGGER.exception("Error loading sector benchmarks")
        return get_fallback_sector_benchmarks_all()

def save_sector_benchmarks(benchmarks_data):
    """Save sector benchmarks to JSON file"""
    benchmarks_file = SECTOR_BENCHMARKS_FILE
    
    try:
        existing_metadata = load_sector_benchmarks().get('_metadata', {}) if os.path.exists(benchmarks_file) else {}
        refresh_history = existing_metadata.get('refresh_history', [])
        refresh_entry = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_sectors': len([k for k in benchmarks_data.keys() if not k.startswith('_')]),
            'calculation_method': 'yahoo_finance_percentiles',
        }
        refresh_history = [refresh_entry] + refresh_history[:MAX_BENCHMARK_HISTORY - 1]

        # Add metadata
        benchmarks_data['_metadata'] = {
            'last_updated': refresh_entry['timestamp'],
            'total_sectors': len([k for k in benchmarks_data.keys() if not k.startswith('_')]),
            'calculation_method': 'yahoo_finance_percentiles',
            'representative_stocks_per_sector': 10,
            'refresh_history': refresh_history,
        }
        
        with open(benchmarks_file, 'w') as f:
            json.dump(benchmarks_data, f, indent=2)
        
        LOGGER.info("Sector benchmarks saved to %s", benchmarks_file)
        return True
    except Exception as e:
        LOGGER.exception("Error saving sector benchmarks")
        return False

def calculate_all_sector_benchmarks():
    """Calculate benchmarks for all sectors and save to file"""
    LOGGER.info("Starting calculation of all sector benchmarks")
    
    representative_stocks = get_sector_representative_stocks()
    all_benchmarks = {}
    
    for sector, stocks in representative_stocks.items():
        LOGGER.info("Calculating benchmarks for %s sector", sector)
        try:
            sector_benchmarks = calculate_single_sector_benchmarks(sector, stocks)
            if sector_benchmarks:
                all_benchmarks[sector] = sector_benchmarks
                LOGGER.info("%s: %s benchmarks calculated", sector, len(sector_benchmarks))
            else:
                LOGGER.warning("%s: Failed to calculate benchmarks, using fallback", sector)
                all_benchmarks[sector] = get_fallback_sector_benchmarks(sector)
        except Exception as e:
            LOGGER.warning("%s: Error calculating benchmarks: %s", sector, e)
            all_benchmarks[sector] = get_fallback_sector_benchmarks(sector)
    
    # Save to file
    if save_sector_benchmarks(all_benchmarks):
        LOGGER.info("All sector benchmarks calculated and saved")
        return all_benchmarks
    else:
        LOGGER.error("Failed to save sector benchmarks to file")
        return None

def calculate_single_sector_benchmarks(sector, stocks):
    """Calculate simplified benchmarks (median only) for a single sector"""
    metrics_data = []
    
    LOGGER.info("Fetching benchmark data for %s stocks in %s", len(stocks), sector)
    
    # Fetch data for representative stocks
    for i, symbol in enumerate(stocks, 1):
        try:
            LOGGER.info("Benchmark fetch %s/%s: %s", i, len(stocks), symbol)
            ticker = yf.Ticker(symbol)
            info = ticker.info
            
            # Extract key metrics
            stock_metrics = {
                'symbol': symbol,
                'pe_ratio': info.get('trailingPE'),
                'price_to_book': info.get('priceToBook'),
                'price_to_sales': info.get('priceToSalesTrailing12Months'),
                'return_on_equity': info.get('returnOnEquity'),
                'profit_margin': info.get('profitMargins')
            }
            
            # Only include stocks with valid core data
            if (stock_metrics['pe_ratio'] and stock_metrics['pe_ratio'] > 0 and
                stock_metrics['return_on_equity'] and stock_metrics['return_on_equity'] > 0):
                metrics_data.append(stock_metrics)
            else:
                LOGGER.info("Skipping %s due to insufficient core benchmark data", symbol)
                
        except Exception as e:
            LOGGER.warning("Error fetching benchmark inputs for %s: %s", symbol, str(e)[:120])
            continue
    
    if len(metrics_data) < 3:
        LOGGER.warning("Insufficient valid benchmark data for %s (%s stocks), using fallback", sector, len(metrics_data))
        return None
    
    LOGGER.info("Calculating medians from %s valid stocks for %s", len(metrics_data), sector)
    
    # Calculate ONLY medians for each metric (simplified!)
    benchmarks = {}
    metrics_to_calculate = ['pe_ratio', 'price_to_book', 'price_to_sales', 'return_on_equity', 'profit_margin']
    
    for metric in metrics_to_calculate:
        values = [stock[metric] for stock in metrics_data 
                 if stock[metric] is not None and stock[metric] > 0]
        
        if len(values) >= 3:
            median_value = float(np.median(values))
            
            # Store only the median value (simplified!)
            benchmarks[f'{metric}_median'] = round(median_value, 3)
    
    # Add metadata
    benchmarks['_sector_info'] = {
        'total_stocks_analyzed': len(metrics_data),
        'representative_stocks': [stock['symbol'] for stock in metrics_data],
        'calculation_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    return benchmarks

def get_fallback_sector_benchmarks_all():
    """Get fallback benchmarks for all sectors"""
    return {
        'Technology': get_fallback_sector_benchmarks('Technology'),
        'Healthcare': get_fallback_sector_benchmarks('Healthcare'),
        'Financial Services': get_fallback_sector_benchmarks('Financial Services'),
        'Consumer Cyclical': get_fallback_sector_benchmarks('Consumer Cyclical'),
        'Consumer Defensive': get_fallback_sector_benchmarks('Consumer Defensive'),
        'Utilities': get_fallback_sector_benchmarks('Utilities'),
        'Energy': get_fallback_sector_benchmarks('Energy'),
        'Industrials': get_fallback_sector_benchmarks('Industrials'),
        'Materials': get_fallback_sector_benchmarks('Materials'),
        'Real Estate': get_fallback_sector_benchmarks('Real Estate'),
        'Communication Services': get_fallback_sector_benchmarks('Communication Services'),
        '_metadata': {
            'last_updated': 'fallback',
            'total_sectors': 11,
            'calculation_method': 'static_fallback'
        }
    }

def get_fallback_sector_benchmarks(sector):
    """Simplified fallback benchmarks (median only) for a single sector"""
    fallback_benchmarks = {
        'Technology': {
            'pe_ratio_median': 25.0,
            'price_to_book_median': 4.0,
            'price_to_sales_median': 6.0,
            'return_on_equity_median': 0.15,
            'profit_margin_median': 0.12
        },
        'Healthcare': {
            'pe_ratio_median': 18.0,
            'price_to_book_median': 3.0,
            'price_to_sales_median': 4.0,
            'return_on_equity_median': 0.12,
            'profit_margin_median': 0.15
        },
        'Financial Services': {
            'pe_ratio_median': 12.0,
            'price_to_book_median': 1.2,
            'price_to_sales_median': 2.5,
            'return_on_equity_median': 0.10,
            'profit_margin_median': 0.25
        },
        'Utilities': {
            'pe_ratio_median': 15.0,
            'price_to_book_median': 1.5,
            'price_to_sales_median': 2.0,
            'return_on_equity_median': 0.09,
            'profit_margin_median': 0.10
        }
    }
    
    # Add basic fallback for unlisted sectors
    default_benchmarks = {
        'pe_ratio_median': 20.0,
        'price_to_book_median': 3.5,
        'price_to_sales_median': 5.0,
        'return_on_equity_median': 0.12,
        'profit_margin_median': 0.10
    }
    
    return fallback_benchmarks.get(sector, default_benchmarks)

# Load sector benchmarks on startup
sector_benchmarks_data = load_sector_benchmarks()

def get_sector_benchmarks(sector):
    """Get sector-specific benchmark values from loaded data"""
    global sector_benchmarks_data
    
    if sector in sector_benchmarks_data:
        return sector_benchmarks_data[sector]
    else:
        LOGGER.warning("Sector '%s' not found in benchmarks, using fallback", sector)
        return get_fallback_sector_benchmarks(sector)

def score_metric_relative_to_sector(value, metric_type, sector, higher_is_better=True):
    """Simplified scoring: compare to sector median with magnitude consideration"""
    if value is None or value <= 0:
        return 0
    
    benchmarks = get_sector_benchmarks(sector)
    
    # Get median value for this metric type
    median_key = f"{metric_type}_median"
    
    # Handle legacy naming for some metrics
    if metric_type == "pb" and median_key not in benchmarks:
        median_key = "price_to_book_median"
    elif metric_type == "ps" and median_key not in benchmarks:
        median_key = "price_to_sales_median"
    elif metric_type == "roe" and median_key not in benchmarks:
        median_key = "return_on_equity_median"
    
    if median_key not in benchmarks:
        # Fallback to absolute scoring if metric not in benchmarks
        return score_metric_absolute(value, metric_type)
    
    median = benchmarks[median_key]
    
    # Calculate percentage difference from median
    if higher_is_better:
        # For metrics like ROE, profit margin (higher = better)
        # value > median is good
        if value >= median * 1.5:      # 50% better than median
            return 10
        elif value >= median * 1.25:   # 25% better than median  
            return 9
        elif value >= median * 1.1:    # 10% better than median
            return 8
        elif value >= median:          # At or above median
            return 7
        elif value >= median * 0.9:    # Within 10% of median
            return 6
        elif value >= median * 0.75:   # 25% below median
            return 4
        elif value >= median * 0.5:    # 50% below median
            return 2
        else:                          # More than 50% below median
            return 1
    else:
        # For metrics like P/E, P/B (lower = better)
        # value < median is good
        if value <= median * 0.67:     # 33% better than median (much lower)
            return 10
        elif value <= median * 0.8:    # 20% better than median
            return 9
        elif value <= median * 0.9:    # 10% better than median
            return 8
        elif value <= median:          # At or below median
            return 7
        elif value <= median * 1.1:    # Within 10% of median
            return 6
        elif value <= median * 1.25:   # 25% above median
            return 4
        elif value <= median * 1.5:    # 50% above median
            return 2
        else:                          # More than 50% above median
            return 1

def score_metric_absolute(value, metric_type):
    """Fallback absolute scoring for metrics without sector benchmarks"""
    if metric_type == "pe_ratio":
        if value < 15: return 10
        elif value < 25: return 7
        elif value < 35: return 4
        else: return 1
    elif metric_type == "price_to_book":
        if value < 1.5: return 10
        elif value < 3: return 7
        elif value < 5: return 4
        else: return 1
    elif metric_type == "return_on_equity":
        if value > 0.20: return 10
        elif value > 0.15: return 8
        elif value > 0.10: return 6
        elif value > 0.05: return 3
        else: return 1
    elif metric_type == "profit_margin":
        if value > 0.20: return 10
        elif value > 0.15: return 8
        elif value > 0.10: return 6
        elif value > 0.05: return 3
        else: return 1
    else:
        return 5  # Default middle score

def calculate_fundamental_analysis(metrics):
    """Calculate fundamental analysis with clear sector comparison and growth metrics"""
    if not metrics:
        return {
            'sector_comparison': {},
            'growth_analysis': {},
            'company_info': {}
        }
    
    # Get company sector for comparison
    sector = metrics.get('sector', 'Technology')  # Default to Technology if no sector
    sector_benchmarks = get_sector_benchmarks(sector)
    LOGGER.info("Analyzing %s sector comparison", sector)
    
    # Part 1: Sector Comparison - Company vs Peers
    sector_comparison = {
        'sector_name': sector,
        'metrics': {}
    }
    
    # P/E Ratio Comparison
    company_pe = metrics.get('pe_ratio')
    sector_pe_median = sector_benchmarks.get('pe_ratio_median')
    if company_pe and sector_pe_median:
        sector_comparison['metrics']['pe_ratio'] = {
            'label': 'Price-to-Earnings Ratio',
            'company_value': round(company_pe, 2),
            'sector_median': round(sector_pe_median, 2),
            'comparison': 'Lower is Better',
            'better_than_sector': company_pe < sector_pe_median
        }
    
    # Price-to-Book Comparison
    company_pb = metrics.get('price_to_book')
    sector_pb_median = sector_benchmarks.get('price_to_book_median')
    if company_pb and sector_pb_median:
        sector_comparison['metrics']['price_to_book'] = {
            'label': 'Price-to-Book Ratio',
            'company_value': round(company_pb, 2),
            'sector_median': round(sector_pb_median, 2),
            'comparison': 'Lower is Better',
            'better_than_sector': company_pb < sector_pb_median
        }
    
    # Price-to-Sales Comparison
    company_ps = metrics.get('price_to_sales')
    sector_ps_median = sector_benchmarks.get('price_to_sales_median')
    if company_ps and sector_ps_median:
        sector_comparison['metrics']['price_to_sales'] = {
            'label': 'Price-to-Sales Ratio',
            'company_value': round(company_ps, 2),
            'sector_median': round(sector_ps_median, 2),
            'comparison': 'Lower is Better',
            'better_than_sector': company_ps < sector_ps_median
        }
    
    # Return on Equity Comparison
    company_roe = metrics.get('return_on_equity')
    sector_roe_median = sector_benchmarks.get('return_on_equity_median')
    if company_roe and sector_roe_median:
        sector_comparison['metrics']['return_on_equity'] = {
            'label': 'Return on Equity',
            'company_value': f"{round(company_roe * 100, 1)}%",
            'sector_median': f"{round(sector_roe_median * 100, 1)}%",
            'comparison': 'Higher is Better',
            'better_than_sector': company_roe > sector_roe_median
        }
    
    # Profit Margin Comparison
    company_margin = metrics.get('profit_margin')
    sector_margin_median = sector_benchmarks.get('profit_margin_median')
    if company_margin and sector_margin_median:
        sector_comparison['metrics']['profit_margin'] = {
            'label': 'Profit Margin',
            'company_value': f"{round(company_margin * 100, 1)}%",
            'sector_median': f"{round(sector_margin_median * 100, 1)}%",
            'comparison': 'Higher is Better',
            'better_than_sector': company_margin > sector_margin_median
        }
    
    # Part 2: Growth Analysis - Company's Own Growth Metrics
    growth_analysis = {
        'metrics': {}
    }
    
    # Revenue Growth
    revenue_growth = metrics.get('revenue_growth_yoy') or metrics.get('revenue_growth')
    if revenue_growth is not None:
        growth_analysis['metrics']['revenue_growth'] = {
            'label': 'Revenue Growth',
            'value': f"{round(revenue_growth * 100, 1)}%",
            'raw_value': revenue_growth,
            'interpretation': get_growth_interpretation(revenue_growth, 'revenue')
        }
    
    # Earnings Growth
    earnings_growth = metrics.get('earnings_growth')
    if earnings_growth is not None:
        growth_analysis['metrics']['earnings_growth'] = {
            'label': 'Earnings Growth',
            'value': f"{round(earnings_growth * 100, 1)}%",
            'raw_value': earnings_growth,
            'interpretation': get_growth_interpretation(earnings_growth, 'earnings')
        }
    
    # Operating Cash Flow Growth
    ocf_growth = metrics.get('ocf_growth_yoy')
    if ocf_growth is not None:
        growth_analysis['metrics']['ocf_growth'] = {
            'label': 'Operating Cash Flow Growth',
            'value': f"{round(ocf_growth * 100, 1)}%",
            'raw_value': ocf_growth,
            'interpretation': get_growth_interpretation(ocf_growth, 'ocf')
        }
    
    # ROE Growth
    roe_growth = metrics.get('roe_growth_yoy')
    if roe_growth is not None:
        growth_analysis['metrics']['roe_growth'] = {
            'label': 'ROE Growth',
            'value': f"{round(roe_growth * 100, 1)}%",
            'raw_value': roe_growth,
            'interpretation': get_growth_interpretation(roe_growth, 'roe')
        }
    
    # Company Information
    company_info = {
        'name': metrics.get('company_name', 'Unknown'),
        'sector': sector,
        'industry': metrics.get('industry', 'Unknown'),
        'current_price': metrics.get('current_price'),
        'market_cap': metrics.get('market_cap')
    }
    
    # Financial Health Metrics (simple display)
    financial_health = {}
    
    debt_to_equity = metrics.get('debt_to_equity')
    if debt_to_equity is not None:
        financial_health['debt_to_equity'] = {
            'label': 'Debt-to-Equity Ratio',
            'value': round(debt_to_equity, 2),
            'interpretation': get_debt_interpretation(debt_to_equity)
        }
    
    current_ratio = metrics.get('current_ratio')
    if current_ratio is not None:
        financial_health['current_ratio'] = {
            'label': 'Current Ratio',
            'value': round(current_ratio, 2),
            'interpretation': get_liquidity_interpretation(current_ratio)
        }
    
    return {
        'sector_comparison': sector_comparison,
        'growth_analysis': growth_analysis,
        'financial_health': financial_health,
        'company_info': company_info
    }

def get_growth_interpretation(growth_rate, metric_type):
    """Get simple interpretation for growth rates"""
    if growth_rate is None:
        return "Data not available"
    
    if metric_type in ['revenue', 'earnings']:
        if growth_rate >= 0.20:
            return "Excellent - Strong growth"
        elif growth_rate >= 0.10:
            return "Good - Solid growth"
        elif growth_rate >= 0.05:
            return "Moderate - Steady growth"
        elif growth_rate >= 0:
            return "Slow - Minimal growth"
        else:
            return "Declining - Negative growth"
    
    elif metric_type == 'ocf':
        if growth_rate >= 0.15:
            return "Excellent - Strong cash generation"
        elif growth_rate >= 0.05:
            return "Good - Healthy cash flow"
        elif growth_rate >= 0:
            return "Stable - Positive cash flow"
        elif growth_rate >= -0.05:
            return "Concerning - Slight decline"
        else:
            return "Weak - Declining cash flow"
    
    elif metric_type == 'roe':
        if growth_rate >= 0.10:
            return "Excellent - Improving efficiency"
        elif growth_rate >= 0.05:
            return "Good - Better returns"
        elif growth_rate >= 0:
            return "Stable - Maintaining efficiency"
        elif growth_rate >= -0.05:
            return "Watch - Slight decline"
        else:
            return "Concerning - Declining efficiency"
    
    return "Unknown"

def get_debt_interpretation(debt_to_equity):
    """Get interpretation for debt-to-equity ratio"""
    if debt_to_equity is None:
        return "Data not available"
    
    if 0.3 <= debt_to_equity <= 1.2:
        return "Optimal - Balanced capital structure"
    elif 0.1 <= debt_to_equity < 0.3:
        return "Conservative - Low debt usage"
    elif 1.2 < debt_to_equity <= 1.8:
        return "Aggressive - Higher leverage"
    elif debt_to_equity < 0.1:
        return "Very Conservative - Minimal debt"
    elif 1.8 < debt_to_equity <= 2.5:
        return "High Leverage - Monitor closely"
    else:
        return "Very High - Potential concern"

def get_liquidity_interpretation(current_ratio):
    """Get interpretation for current ratio"""
    if current_ratio is None:
        return "Data not available"
    
    if 1.5 <= current_ratio <= 3.0:
        return "Good - Healthy liquidity"
    elif 1.2 <= current_ratio < 1.5:
        return "Adequate - Sufficient liquidity"
    elif 3.0 < current_ratio <= 4.0:
        return "High - Excess cash (could be more efficient)"
    elif 1.0 <= current_ratio < 1.2:
        return "Tight - Monitor cash flow"
    elif current_ratio > 4.0:
        return "Very High - Inefficient cash usage"
    else:
        return "Concerning - Liquidity issues"

def get_simple_overall_recommendation(fundamental_analysis, technical_recommendation):
    """Get simple overall recommendation based on clear metrics"""
    # Count positive indicators from sector comparison
    sector_positives = 0
    sector_metrics = fundamental_analysis.get('sector_comparison', {}).get('metrics', {})
    
    for metric_data in sector_metrics.values():
        if metric_data.get('better_than_sector', False):
            sector_positives += 1
    
    # Count positive growth indicators
    growth_positives = 0
    growth_metrics = fundamental_analysis.get('growth_analysis', {}).get('metrics', {})
    
    for metric_data in growth_metrics.values():
        if metric_data.get('raw_value', 0) > 0.05:  # 5% or higher growth
            growth_positives += 1
    
    # Simple recommendation logic
    total_sector_metrics = len(sector_metrics)
    total_growth_metrics = len(growth_metrics)
    
    # Strong fundamentals: majority of metrics better than sector AND positive growth
    if (total_sector_metrics > 0 and sector_positives >= total_sector_metrics * 0.6 and
        total_growth_metrics > 0 and growth_positives >= total_growth_metrics * 0.5):
        if technical_recommendation == 'BUY':
            return 'STRONG BUY'
        else:
            return 'BUY'
    
    # Decent fundamentals: some metrics better than sector OR good growth
    elif (sector_positives > 0 or growth_positives >= 2):
        if technical_recommendation == 'BUY':
            return 'BUY'
        elif technical_recommendation == 'SELL':
            return 'HOLD'
        else:
            return 'HOLD'
    
    # Weak fundamentals
    else:
        if technical_recommendation == 'SELL':
            return 'SELL'
        else:
            return 'HOLD'

def calculate_component_ratio(positive_count, total_count):
    """Convert a positive/total count into a 0-10 component score."""
    if total_count <= 0:
        return 5.0

    return round((positive_count / total_count) * 10, 1)


def score_boolean_signal(is_positive, positive_score=8.0, neutral_score=4.0):
    """Convert a boolean condition into a bounded 0-10 score contribution."""
    return positive_score if bool(is_positive) else neutral_score


def get_latest_signal_age(signal_series, lookback=7):
    """Return how many sessions ago a signal last fired inside the lookback window."""
    recent = signal_series.tail(lookback)
    active_positions = [index for index, is_active in enumerate(recent.tolist()) if bool(is_active)]
    if not active_positions:
        return None

    return len(recent) - 1 - active_positions[-1]


def build_technical_momentum_summary(stock_data, lookback=7):
    """Build a weighted technical momentum summary from recent signals and current indicator state."""
    buy_threshold = 6.8
    sell_threshold = 3.4

    if stock_data.empty:
        return {
            'signal': 'HOLD',
            'score': 5.0,
            'max_score': 10,
            'buy_signals': 0,
            'sell_signals': 0,
            'signal_window_days': lookback,
            'latest_buy_age': None,
            'latest_sell_age': None,
            'trend_confirmation': False,
            'setup_state': 'No setup',
            'gap_to_buy_threshold': round(buy_threshold - 5.0, 1),
            'gap_to_sell_threshold': round(5.0 - sell_threshold, 1),
            'details': {
                'signal_balance': 5.0,
                'trend_state': 5.0,
                'macd_state': 5.0,
                'stochastic_state': 5.0,
                'recency_bias': 5.0,
            }
        }

    latest_data = stock_data.tail(lookback).copy()
    latest_row = latest_data.iloc[-1]

    buy_signals = int(latest_data['Buy_Signal'].fillna(False).sum()) if 'Buy_Signal' in latest_data else 0
    sell_signals = int(latest_data['Sell_Signal'].fillna(False).sum()) if 'Sell_Signal' in latest_data else 0
    signal_window = max(len(latest_data), 1)
    signal_balance = max(min((buy_signals - sell_signals) / signal_window, 1.0), -1.0)
    signal_balance_score = round(5.0 + signal_balance * 5.0, 1)

    trend_up = bool(latest_row.get('trend_up', False))
    trend_down = bool(latest_row.get('trend_down', False))
    if trend_up:
        trend_state_score = 8.5
    elif trend_down:
        trend_state_score = 2.0
    else:
        trend_state_score = 5.0

    macd_value = latest_row.get('MACD')
    signal_line = latest_row.get('Signal_Line')
    if pd.notna(macd_value) and pd.notna(signal_line):
        macd_gap = float(macd_value - signal_line)
        macd_score = max(min(5.0 + macd_gap * 12.0, 10.0), 0.0)
        if macd_value > 0:
            macd_score = min(macd_score + 0.5, 10.0)
    else:
        macd_score = 5.0

    percent_k = latest_row.get('%K')
    percent_d = latest_row.get('%D')
    if pd.notna(percent_k) and pd.notna(percent_d):
        stochastic_gap = float(percent_k - percent_d)
        stochastic_score = max(min(5.0 + stochastic_gap / 4.0, 10.0), 0.0)
        if percent_k < 25 and percent_k > percent_d:
            stochastic_score = min(stochastic_score + 1.0, 10.0)
        elif percent_k > 75 and percent_k < percent_d:
            stochastic_score = max(stochastic_score - 1.0, 0.0)
    else:
        stochastic_score = 5.0

    latest_buy_age = get_latest_signal_age(stock_data.get('Buy_Signal', pd.Series(dtype=bool)), lookback=lookback)
    latest_sell_age = get_latest_signal_age(stock_data.get('Sell_Signal', pd.Series(dtype=bool)), lookback=lookback)

    recency_bias_score = 5.0
    if latest_buy_age is not None:
        recency_bias_score += max(3.0 - latest_buy_age, 0.0)
    if latest_sell_age is not None:
        recency_bias_score -= max(3.0 - latest_sell_age, 0.0)
    recency_bias_score = max(min(recency_bias_score, 10.0), 0.0)

    technical_score = round(
        signal_balance_score * 0.30 +
        trend_state_score * 0.15 +
        macd_score * 0.25 +
        stochastic_score * 0.15 +
        recency_bias_score * 0.15,
        1
    )

    if technical_score >= buy_threshold:
        signal = 'BUY'
    elif technical_score <= sell_threshold:
        signal = 'SELL'
    else:
        signal = 'HOLD'

    if signal == 'BUY':
        setup_state = 'Buy signal active'
    elif signal == 'SELL':
        setup_state = 'Sell signal active'
    elif technical_score >= 6.0:
        setup_state = 'Approaching buy setup'
    elif technical_score <= 4.0:
        setup_state = 'Approaching sell setup'
    else:
        setup_state = 'Neutral setup'

    return {
        'signal': signal,
        'score': technical_score,
        'max_score': 10,
        'buy_signals': buy_signals,
        'sell_signals': sell_signals,
        'signal_window_days': signal_window,
        'latest_buy_age': latest_buy_age,
        'latest_sell_age': latest_sell_age,
        'trend_confirmation': trend_up,
        'setup_state': setup_state,
        'gap_to_buy_threshold': round(max(buy_threshold - technical_score, 0.0), 1),
        'gap_to_sell_threshold': round(max(technical_score - sell_threshold, 0.0), 1),
        'details': {
            'signal_balance': round(signal_balance_score, 1),
            'trend_state': round(trend_state_score, 1),
            'macd_state': round(macd_score, 1),
            'stochastic_state': round(stochastic_score, 1),
            'recency_bias': round(recency_bias_score, 1),
        }
    }

def build_recommendation_scorecard(fundamental_analysis, technical_recommendation, profile_name='balanced', technical_summary=None):
    """Build an explicit scorecard for the overall recommendation."""
    sector_name = fundamental_analysis.get('sector_comparison', {}).get('sector_name', 'Unknown')
    sector_metrics = fundamental_analysis.get('sector_comparison', {}).get('metrics', {})
    growth_metrics = fundamental_analysis.get('growth_analysis', {}).get('metrics', {})
    weights, scoring_profile = combine_weight_profiles(sector_name, profile_name)

    def metric_label(metric_key, metric_data):
        label = metric_data.get('label') if isinstance(metric_data, dict) else None
        if label:
            return label
        return str(metric_key).replace('_', ' ').title()

    sector_positive_labels = []
    sector_negative_labels = []
    for metric_key, metric_data in sector_metrics.items():
        label = metric_label(metric_key, metric_data)
        if metric_data.get('better_than_sector', False):
            sector_positive_labels.append(label)
        else:
            sector_negative_labels.append(label)

    growth_positive_labels = []
    growth_negative_labels = []
    for metric_key, metric_data in growth_metrics.items():
        label = metric_label(metric_key, metric_data)
        if metric_data.get('raw_value', 0) > 0.05:
            growth_positive_labels.append(label)
        else:
            growth_negative_labels.append(label)

    sector_positives = sum(
        1 for metric_data in sector_metrics.values()
        if metric_data.get('better_than_sector', False)
    )
    growth_positives = sum(
        1 for metric_data in growth_metrics.values()
        if metric_data.get('raw_value', 0) > 0.05
    )

    sector_score = calculate_component_ratio(sector_positives, len(sector_metrics))
    growth_score = calculate_component_ratio(growth_positives, len(growth_metrics))
    technical_summary = technical_summary or {
        'signal': technical_recommendation,
        'score': {'BUY': 8.0, 'HOLD': 5.0, 'SELL': 2.0}.get(technical_recommendation, 5.0),
        'buy_signals': 0,
        'sell_signals': 0,
        'signal_window_days': 0,
        'trend_confirmation': False,
        'latest_buy_age': None,
        'latest_sell_age': None,
        'setup_state': 'Neutral setup',
        'distance_to_buy': None,
        'distance_to_sell': None,
        'details': {}
    }
    technical_score = technical_summary.get('score', 5.0)
    technical_summary.setdefault('gap_to_buy_threshold', round(max(6.8 - technical_score, 0.0), 1))
    technical_summary.setdefault('gap_to_sell_threshold', round(max(technical_score - 3.4, 0.0), 1))
    technical_summary.setdefault('setup_state', 'Neutral setup')

    weighted_score = round(
        sector_score * weights['sector_alignment'] +
        growth_score * weights['growth_quality'] +
        technical_score * weights['technical_momentum'],
        1
    )

    if weighted_score >= 8.0:
        label = 'STRONG BUY'
    elif weighted_score >= 6.5:
        label = 'BUY'
    elif weighted_score >= 4.5:
        label = 'HOLD'
    elif weighted_score >= 2.5:
        label = 'SELL'
    else:
        label = 'STRONG SELL'

    return {
        'label': label,
        'total_score': weighted_score,
        'max_score': 10,
        'sector': sector_name,
        'profile': scoring_profile,
        'components': {
            'sector_alignment': {
                'label': 'Sector Alignment',
                'score': sector_score,
                'weight': weights['sector_alignment'],
                'positive_signals': sector_positives,
                'total_signals': len(sector_metrics),
                'signal_rule': 'Positive when the company beats the sector median for that metric.',
                'positive_signal_labels': sector_positive_labels,
                'negative_signal_labels': sector_negative_labels
            },
            'growth_quality': {
                'label': 'Growth Quality',
                'score': growth_score,
                'weight': weights['growth_quality'],
                'positive_signals': growth_positives,
                'total_signals': len(growth_metrics),
                'signal_rule': 'Positive when growth is above 5% for that metric.',
                'positive_signal_labels': growth_positive_labels,
                'negative_signal_labels': growth_negative_labels
            },
            'technical_momentum': {
                'label': 'Technical Momentum',
                'score': technical_score,
                'weight': weights['technical_momentum'],
                'signal': technical_summary.get('signal', technical_recommendation),
                'buy_signals': technical_summary.get('buy_signals', 0),
                'sell_signals': technical_summary.get('sell_signals', 0),
                'signal_window_days': technical_summary.get('signal_window_days', 0),
                'trend_confirmation': technical_summary.get('trend_confirmation', False),
                'latest_buy_age': technical_summary.get('latest_buy_age'),
                'latest_sell_age': technical_summary.get('latest_sell_age'),
                'setup_state': technical_summary.get('setup_state', 'Neutral setup'),
                'gap_to_buy_threshold': technical_summary.get('gap_to_buy_threshold'),
                'gap_to_sell_threshold': technical_summary.get('gap_to_sell_threshold'),
                'details': technical_summary.get('details', {})
            }
        }
    }

def get_technical_recommendation(stock_data):
    """Get technical analysis recommendation based on latest signals"""
    return build_technical_momentum_summary(stock_data).get('signal', 'HOLD')

def create_technical_chart(stock, stock_data):
    """Create technical analysis chart"""
    fig, axs = plt.subplots(3, 1, figsize=(12, 10), facecolor='white')
    
    # Plot 1: Price and Moving Average
    axs[0].plot(stock_data.index, stock_data['Close'], label='Close Price', color='blue', alpha=0.7)
    axs[0].plot(stock_data.index, stock_data['30_Moving_Avg'], label='30-Day MA', color='red', linewidth=2)
    if '90_Moving_Avg' in stock_data:
        axs[0].plot(stock_data.index, stock_data['90_Moving_Avg'], label='90-Day MA', color='teal', linewidth=1.5, alpha=0.9)
    axs[0].set_title(f'{stock} - Price and Moving Average', fontsize=14, fontweight='bold')
    axs[0].set_ylabel('Price ($)')
    axs[0].legend()
    axs[0].grid(True, alpha=0.3)
    
    # Plot 2: Stochastic Oscillator
    axs[1].plot(stock_data.index, stock_data['%K'], label='Stochastic %K', color='purple', linewidth=1.6)
    axs[1].plot(stock_data.index, stock_data['%D'], label='Stochastic %D', color='orange', linewidth=1.8)
    axs[1].axhline(70, color='red', linestyle='--', alpha=0.7, label='Overbought (70)')
    axs[1].axhline(30, color='green', linestyle='--', alpha=0.7, label='Oversold (30)')
    axs[1].set_title(f'{stock} - Stochastic Oscillator', fontsize=14, fontweight='bold')
    axs[1].set_ylabel('Stochastic')
    axs[1].legend()
    axs[1].grid(True, alpha=0.3)
    axs[1].set_ylim(0, 100)
    
    # Plot 3: MACD
    axs[2].plot(stock_data.index, stock_data['MACD'], label='MACD', color='blue', linewidth=2)
    axs[2].plot(stock_data.index, stock_data['Signal_Line'], label='Signal Line', color='red', linewidth=2)
    axs[2].axhline(0, color='black', linestyle='-', alpha=0.3)
    axs[2].set_title(f'{stock} - MACD', fontsize=14, fontweight='bold')
    axs[2].set_ylabel('MACD')
    axs[2].set_xlabel('Date')
    axs[2].legend()
    axs[2].grid(True, alpha=0.3)
    
    plt.tight_layout()
    
    # Convert plot to base64 string
    img_buffer = io.BytesIO()
    plt.savefig(img_buffer, format='png', dpi=100, bbox_inches='tight')
    img_buffer.seek(0)
    img_str = base64.b64encode(img_buffer.getvalue()).decode()
    plt.close()
    
    return img_str

def get_company_name(symbol):
    """Get company name from Yahoo Finance"""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        return info.get('longName', info.get('shortName', symbol))
    except:
        return symbol

@app.route('/')
def index():
    """Main page"""
    # No longer fetch all stock data at startup - load on demand instead
    # Get current portfolio and sort alphabetically by symbol
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
    
    # Fetch data for this specific stock on demand
    fetch_status = fetch_single_stock_data(symbol)
    
    # Get fundamental data
    fundamental_metrics = fundamental_cache.get(symbol, {})
    fundamental_analysis = calculate_fundamental_analysis(fundamental_metrics)
    
    # Get technical data
    stock_data = data_cache.get(symbol, pd.DataFrame())
    technical_summary = build_technical_momentum_summary(stock_data)
    technical_recommendation = technical_summary['signal']
    recommendation_scorecard = build_recommendation_scorecard(
        fundamental_analysis,
        technical_recommendation,
        profile_name=profile_name,
        technical_summary=technical_summary,
    )
    overall_recommendation = recommendation_scorecard['label']
    
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
        'last_updated': stock_last_update.strftime('%Y-%m-%d %H:%M:%S') if stock_last_update else 'N/A'
    })

@app.route('/api/chart/<symbol>')
def get_chart(symbol):
    """API endpoint to get technical analysis chart"""
    portfolio_stocks = get_portfolio_stocks()
    if symbol not in portfolio_stocks:
        return jsonify({'error': 'Stock not found in portfolio'}), 404
    
    # Ensure we have data for this stock
    fetch_single_stock_data(symbol)
    stock_data = data_cache.get(symbol, pd.DataFrame())
    
    if stock_data.empty:
        return jsonify({'error': 'No data available'}), 404
    
    chart_img = create_technical_chart(symbol, stock_data)
    
    return jsonify({'chart': chart_img})

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
        ticker = yf.Ticker(symbol)
        info = ticker.info
        if not info or 'symbol' not in info:
            return jsonify({'error': 'Invalid stock symbol'}), 400
        
        company_name = info.get('longName', info.get('shortName', symbol))
    except:
        return jsonify({'error': 'Unable to fetch stock data'}), 400
    
    # Load current portfolio
    portfolio = load_portfolio()
    
    # Check if stock already exists
    for stock in portfolio:
        if stock['symbol'] == symbol:
            return jsonify({'error': 'Stock already in portfolio'}), 400
    
    # Add new stock
    new_stock = {
        'symbol': symbol,
        'name': company_name,
        'date_added': datetime.now().strftime('%Y-%m-%d')
    }
    portfolio.append(new_stock)
    
    # Save portfolio
    if save_portfolio(portfolio):
        # Clear cache for the specific stock only (if it exists)
        global data_cache, fundamental_cache, last_update
        if symbol in data_cache:
            del data_cache[symbol]
        if symbol in fundamental_cache:
            del fundamental_cache[symbol]
        if symbol in last_update:
            del last_update[symbol]
        
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
        # Clear cache for the specific stock only
        global data_cache, fundamental_cache, last_update
        if symbol in data_cache:
            del data_cache[symbol]
        if symbol in fundamental_cache:
            del fundamental_cache[symbol]
        if symbol in last_update:
            del last_update[symbol]
        
        return jsonify({'message': f'Successfully removed {symbol} from portfolio'})
    else:
        return jsonify({'error': 'Failed to save portfolio'}), 500

@app.route('/api/benchmarks/refresh', methods=['POST'])
def refresh_sector_benchmarks():
    """Calculate and save sector benchmarks to JSON file"""
    try:
        LOGGER.info("Starting sector benchmark calculation via API")
        
        # Calculate all sector benchmarks
        new_benchmarks = calculate_all_sector_benchmarks()
        
        if new_benchmarks:
            # Reload the global benchmarks data
            global sector_benchmarks_data
            sector_benchmarks_data = new_benchmarks
            
            # Get metadata for response
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
        global sector_benchmarks_data
        
        # Check if file exists
        benchmarks_file = SECTOR_BENCHMARKS_FILE
        file_exists = os.path.exists(benchmarks_file)
        
        if file_exists:
            file_stat = os.stat(benchmarks_file)
            file_modified = datetime.fromtimestamp(file_stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            file_size = f"{file_stat.st_size / 1024:.1f} KB"
        else:
            file_modified = "Never"
            file_size = "0 KB"
        
        # Get metadata from loaded data
        metadata = sector_benchmarks_data.get('_metadata', {})
        sectors_available = [k for k in sector_benchmarks_data.keys() if not k.startswith('_')]
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

def validate_growth_data(quarterly_data, metric_name, required_quarters=4):
    """Validate data quality for growth calculations"""
    if quarterly_data.empty:
        return False, f"No quarterly data available for {metric_name}"
    
    if quarterly_data.shape[1] < required_quarters:
        return False, f"Insufficient quarters for {metric_name} (need {required_quarters}, got {quarterly_data.shape[1]})"
    
    # Check if metric exists
    metric_rows = [idx for idx in quarterly_data.index if metric_name.lower() in str(idx).lower()]
    if not metric_rows:
        return False, f"Metric '{metric_name}' not found in quarterly data"
    
    return True, "Data valid"

def calculate_yoy_growth(quarterly_data, metric_name):
    """Calculate year-over-year growth from quarterly data"""
    try:
        # Validate data quality first
        is_valid, message = validate_growth_data(quarterly_data, metric_name)
        if not is_valid:
            LOGGER.info("Data validation failed for %s: %s", metric_name, message)
            return None
        
        # Find the row with the specified metric
        metric_rows = [idx for idx in quarterly_data.index if metric_name.lower() in str(idx).lower()]
        metric_data = quarterly_data.loc[metric_rows[0]]
        
        # Get most recent quarter vs same quarter last year (4 quarters ago)
        current_q = metric_data.iloc[0]  # Most recent quarter
        year_ago_q = metric_data.iloc[3] if len(metric_data) >= 4 else None
        
        if (year_ago_q is None or year_ago_q == 0 or 
            pd.isna(current_q) or pd.isna(year_ago_q)):
            return None
        
        growth_rate = (current_q - year_ago_q) / abs(year_ago_q)
        return growth_rate
        
    except Exception as e:
        LOGGER.warning("Error calculating YoY growth for %s: %s", metric_name, e)
        return None

def calculate_roe_growth(quarterly_financials, quarterly_balance_sheet):
    """Calculate ROE growth rate over time"""
    try:
        if quarterly_financials.empty or quarterly_balance_sheet.empty:
            return None
        
        # Find Net Income
        income_rows = [idx for idx in quarterly_financials.index 
                      if 'net income' in str(idx).lower() and 'common' not in str(idx).lower()]
        if not income_rows:
            return None
        
        # Find Stockholders Equity
        equity_rows = [idx for idx in quarterly_balance_sheet.index 
                      if 'stockholder' in str(idx).lower() and 'equity' in str(idx).lower()]
        if not equity_rows:
            return None
        
        income_data = quarterly_financials.loc[income_rows[0]]
        equity_data = quarterly_balance_sheet.loc[equity_rows[0]]
        
        # Calculate ROE for recent quarter and year-ago quarter using TTM approach
        if len(income_data) >= 4 and len(equity_data) >= 4:
            # Current TTM (Trailing Twelve Months) ROE
            current_ttm_income = income_data.iloc[0:4].sum()  # Sum of last 4 quarters
            current_equity = equity_data.iloc[0]  # Most recent equity
            
            # Year-ago TTM ROE (if we have enough data)
            if len(income_data) >= 7 and len(equity_data) >= 4:
                year_ago_ttm_income = income_data.iloc[3:7].sum()  # Sum of quarters 4-7
                year_ago_equity = equity_data.iloc[3]  # Equity 4 quarters ago
            else:
                # Fallback: annualize the year-ago quarter
                year_ago_ttm_income = income_data.iloc[3] * 4
                year_ago_equity = equity_data.iloc[3]
            
            if (current_equity > 0 and year_ago_equity > 0 and
                not pd.isna(current_ttm_income) and not pd.isna(year_ago_ttm_income)):
                
                current_roe = current_ttm_income / current_equity
                year_ago_roe = year_ago_ttm_income / year_ago_equity
                
                if year_ago_roe != 0:
                    roe_growth = (current_roe - year_ago_roe) / abs(year_ago_roe)
                    return roe_growth
        
        return None
        
    except Exception as e:
        LOGGER.warning("Error calculating ROE growth: %s", e)
        return None

def extract_enhanced_growth_metrics(ticker):
    """Extract enhanced growth metrics from quarterly data"""
    try:
        # Get quarterly data
        quarterly_financials = ticker.quarterly_financials
        quarterly_cashflow = ticker.quarterly_cashflow
        quarterly_balance_sheet = ticker.quarterly_balance_sheet
        
        enhanced_metrics = {}
        
        # 1. Enhanced Revenue Growth (YoY quarterly)
        revenue_growth_yoy = calculate_yoy_growth(quarterly_financials, 'Total Revenue')
        enhanced_metrics['revenue_growth_yoy'] = revenue_growth_yoy
        
        # 2. Operating Cash Flow Growth
        ocf_growth_yoy = calculate_yoy_growth(quarterly_cashflow, 'Operating Cash Flow')
        enhanced_metrics['ocf_growth_yoy'] = ocf_growth_yoy
        
        # 3. ROE Growth Rate
        roe_growth_yoy = calculate_roe_growth(quarterly_financials, quarterly_balance_sheet)
        enhanced_metrics['roe_growth_yoy'] = roe_growth_yoy
        
        return enhanced_metrics
        
    except Exception as e:
        LOGGER.warning("Error extracting enhanced growth metrics: %s", e)
        return {}

if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', '').lower() in {'1', 'true', 'yes', 'on'}
    host = os.getenv('FLASK_HOST', '127.0.0.1')
    port = int(os.getenv('FLASK_PORT', '5000'))
    app.run(debug=debug_mode, host=host, port=port)
