"""Sector benchmark calculation, persistence and fallbacks."""

import json
import logging
import os
import time
from datetime import datetime

import numpy as np

import config
from config import BENCHMARK_FETCH_DELAY_SECONDS, MAX_BENCHMARK_HISTORY
from data_providers import get_provider

LOGGER = logging.getLogger(__name__)

# Module-level so tests can point persistence at a temporary file.
SECTOR_BENCHMARKS_FILE = config.SECTOR_BENCHMARKS_FILE

# Valuation ratios are meaningless when negative (negative P/E just means
# negative earnings), so they are excluded from medians. Profitability
# metrics keep negative values so the median reflects the real sector.
VALUATION_METRICS = {'pe_ratio', 'price_to_book', 'price_to_sales'}
BENCHMARK_METRICS = ['pe_ratio', 'price_to_book', 'price_to_sales', 'return_on_equity', 'profit_margin']


def get_sector_representative_stocks():
    """Get representative stocks for each sector to calculate benchmarks"""
    return {
        'Technology': ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'NFLX', 'ADBE', 'CRM'],
        'Healthcare': ['JNJ', 'UNH', 'PFE', 'ABBV', 'TMO', 'ABT', 'MRK', 'DHR', 'BMY', 'LLY'],
        'Financial Services': ['JPM', 'BAC', 'WFC', 'GS', 'MS', 'C', 'AXP', 'BLK', 'SCHW', 'USB'],
        'Consumer Cyclical': ['HD', 'MCD', 'NKE', 'SBUX', 'TGT', 'LOW', 'TJX', 'MAR', 'GM', 'F'],
        'Consumer Defensive': ['PG', 'KO', 'PEP', 'WMT', 'COST', 'CL', 'KMB', 'GIS', 'K', 'CPB'],
        'Utilities': ['NEE', 'DUK', 'SO', 'D', 'AEP', 'EXC', 'SRE', 'PEG', 'XEL', 'ED'],
        'Energy': ['XOM', 'CVX', 'COP', 'EOG', 'SLB', 'PSX', 'KMI', 'OKE', 'WMB', 'VLO'],
        'Industrials': ['BA', 'HON', 'UPS', 'CAT', 'GE', 'MMM', 'LMT', 'RTX', 'UNP', 'CSX'],
        'Materials': ['LIN', 'APD', 'SHW', 'FCX', 'NEM', 'DOW', 'DD', 'PPG', 'ECL', 'IFF'],
        'Real Estate': ['AMT', 'PLD', 'CCI', 'EQIX', 'SPG', 'O', 'WELL', 'DLR', 'PSA', 'EQR'],
        'Communication Services': ['T', 'VZ', 'CMCSA', 'DIS', 'CHTR', 'TMUS', 'NFLX', 'EA', 'WBD', 'TTWO']
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
    except Exception:
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
    except Exception:
        LOGGER.exception("Error saving sector benchmarks")
        return False


def calculate_single_sector_benchmarks(sector, stocks, fetch_delay_seconds=None):
    """Calculate simplified benchmarks (median only) for a single sector.

    A short delay between per-symbol requests keeps a full refresh from
    hammering the provider with back-to-back calls.
    """
    if fetch_delay_seconds is None:
        fetch_delay_seconds = BENCHMARK_FETCH_DELAY_SECONDS

    provider = get_provider()
    metrics_data = []

    LOGGER.info("Fetching benchmark data for %s stocks in %s", len(stocks), sector)

    for i, symbol in enumerate(stocks, 1):
        if i > 1 and fetch_delay_seconds > 0:
            time.sleep(fetch_delay_seconds)

        try:
            LOGGER.info("Benchmark fetch %s/%s: %s", i, len(stocks), symbol)
            stock_metrics = provider.get_benchmark_metrics(symbol)
            stock_metrics['symbol'] = symbol

            # Include the stock as long as it has any usable data at all.
            if any(stock_metrics.get(metric) is not None for metric in BENCHMARK_METRICS):
                metrics_data.append(stock_metrics)
            else:
                LOGGER.info("Skipping %s due to missing benchmark data", symbol)

        except Exception as e:
            LOGGER.warning("Error fetching benchmark inputs for %s: %s", symbol, str(e)[:120])
            continue

    if len(metrics_data) < 3:
        LOGGER.warning("Insufficient valid benchmark data for %s (%s stocks), using fallback", sector, len(metrics_data))
        return None

    LOGGER.info("Calculating medians from %s valid stocks for %s", len(metrics_data), sector)

    benchmarks = {}
    for metric in BENCHMARK_METRICS:
        if metric in VALUATION_METRICS:
            values = [stock[metric] for stock in metrics_data
                      if stock.get(metric) is not None and stock[metric] > 0]
        else:
            values = [stock[metric] for stock in metrics_data
                      if stock.get(metric) is not None]

        if len(values) >= 3:
            median_value = float(np.median(values))
            benchmarks[f'{metric}_median'] = round(median_value, 3)

    # Add metadata
    benchmarks['_sector_info'] = {
        'total_stocks_analyzed': len(metrics_data),
        'representative_stocks': [stock['symbol'] for stock in metrics_data],
        'calculation_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }

    return benchmarks


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


def get_fallback_sector_benchmarks_all():
    """Get fallback benchmarks for all sectors"""
    sectors = [
        'Technology', 'Healthcare', 'Financial Services', 'Consumer Cyclical',
        'Consumer Defensive', 'Utilities', 'Energy', 'Industrials', 'Materials',
        'Real Estate', 'Communication Services',
    ]
    fallback = {sector: get_fallback_sector_benchmarks(sector) for sector in sectors}
    fallback['_metadata'] = {
        'last_updated': 'fallback',
        'total_sectors': len(sectors),
        'calculation_method': 'static_fallback'
    }
    return fallback


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
    if sector in sector_benchmarks_data:
        return sector_benchmarks_data[sector]
    else:
        LOGGER.warning("Sector '%s' not found in benchmarks, using fallback", sector)
        return get_fallback_sector_benchmarks(sector)


def refresh_all_sector_benchmarks():
    """Recalculate all benchmarks, persist them, and update in-memory state."""
    global sector_benchmarks_data

    new_benchmarks = calculate_all_sector_benchmarks()
    if new_benchmarks:
        sector_benchmarks_data = new_benchmarks
    return new_benchmarks
