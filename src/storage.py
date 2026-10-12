"""On-disk snapshot storage for stock payloads plus small time helpers."""

import os
from datetime import datetime, timedelta

import pandas as pd

import config
from config import CACHE_MAX_AGE_DAYS, CACHE_MAX_FILES

# Module-level so tests can point snapshots at a temporary directory.
STOCK_CACHE_DIR = config.STOCK_CACHE_DIR


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


def ensure_stock_cache_dir():
    """Ensure the stock cache directory exists."""
    os.makedirs(STOCK_CACHE_DIR, exist_ok=True)


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
    """Load the last successful stock payload from disk if available.

    Returns a dict with 'data', 'fundamental_data' and 'saved_at' (datetime),
    or None when no snapshot exists. Purely reads the disk; callers decide
    how to populate their caches.
    """
    cache_file = get_stock_cache_file(symbol)
    if not os.path.exists(cache_file):
        return None

    snapshot = pd.read_pickle(cache_file)
    saved_at = snapshot.get('saved_at')
    saved_dt = datetime.fromisoformat(saved_at) if saved_at else datetime.now()

    return {
        'data': snapshot.get('data', pd.DataFrame()),
        'fundamental_data': snapshot.get('fundamental_data', {}),
        'saved_at': saved_dt,
    }
