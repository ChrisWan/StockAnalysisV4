"""Command-line access to the app's analysis pipeline, emitting JSON.

Meant for scripted/agent use: the same fetch -> score path the web UI uses
(`app._fetch_and_score_symbol`), without starting Flask and without the
watchlist-membership check the HTTP routes enforce, so any ticker can be
screened.

    uv run --env-file .env python scripts/analyze.py score AAPL MSFT --options
    uv run --env-file .env python scripts/analyze.py backtest NVDA --start 2024-08-01 --end 2026-06-30
    uv run --env-file .env python scripts/analyze.py watchlist
    uv run --env-file .env python scripts/analyze.py profiles

JSON goes to stdout; logging goes to stderr.
"""

import argparse
import json
import math
import os
import sys
import tempfile
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))

import numpy as np
import pandas as pd

import app as stock_app
import db
from backtest import run_backtest, save_backtest_result
from config import SCORING_PROFILES
from holdings import get_holdings
from portfolio import load_portfolio


def _clean(value):
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, (pd.DataFrame, pd.Series)):
        return None
    return value


def _isolate_db():
    # Screening dozens of research candidates shouldn't flood the real
    # recommendation_history table (it drives the UI's rating-change alerts).
    tmp_dir = tempfile.mkdtemp(prefix='analyze_')
    db.DB_FILE = os.path.join(tmp_dir, 'analysis.db')


def _score_one(symbol, profile, with_options):
    result = stock_app._fetch_and_score_symbol(symbol, profile)
    payload = {'symbol': symbol, 'profile': profile, **result}

    try:
        payload['earnings'] = stock_app.get_provider().get_earnings_calendar(symbol)
    except Exception as error:
        payload['earnings'] = {'error': str(error)}

    if with_options:
        current_price = result['fundamental_metrics'].get('current_price')
        try:
            payload['put_candidates'] = (
                stock_app.get_provider().get_put_options(symbol, current_price=current_price)
                if current_price else []
            )
        except Exception as error:
            payload['put_candidates'] = {'error': str(error)}
    return payload


def cmd_score(args):
    if not args.record:
        _isolate_db()
    results = []
    for symbol in args.symbols:
        symbol = symbol.upper().strip()
        try:
            results.append(_score_one(symbol, args.profile, args.options))
        except Exception as error:
            results.append({'symbol': symbol, 'error': f'{type(error).__name__}: {error}'})
    return results


def cmd_backtest(args):
    if not args.record:
        _isolate_db()
    report = run_backtest(
        args.symbol.upper().strip(), args.start, args.end, profile=args.profile,
        holding_period_days=args.holding_period_days, step_days=args.step_days,
    )
    if args.record:
        report['id'] = save_backtest_result(report)
    if not args.full:
        report = {k: v for k, v in report.items() if k not in ('steps', 'equity_curve')}
    return report


def cmd_watchlist(_args):
    return {'watchlist': load_portfolio(), 'holdings': get_holdings()}


def cmd_profiles(_args):
    return {key: value for key, value in SCORING_PROFILES.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    score = sub.add_parser('score', help='Fetch and score one or more tickers')
    score.add_argument('symbols', nargs='+')
    score.add_argument('--profile', default='balanced', choices=sorted(SCORING_PROFILES))
    score.add_argument('--options', action='store_true', help='Include cash-secured put candidates')
    score.add_argument('--record', action='store_true',
                       help="Write today's rating to analysis.db (as the web UI does)")
    score.set_defaults(func=cmd_score)

    bt = sub.add_parser('backtest', help='Replay the scorecard over a date range')
    bt.add_argument('symbol')
    bt.add_argument('--start', required=True)
    bt.add_argument('--end', required=True)
    bt.add_argument('--profile', default='balanced', choices=sorted(SCORING_PROFILES))
    bt.add_argument('--holding-period-days', type=int, default=stock_app.BACKTEST_HOLDING_PERIOD_DAYS)
    bt.add_argument('--step-days', type=int, default=stock_app.BACKTEST_STEP_DAYS)
    bt.add_argument('--full', action='store_true', help='Include per-step detail and equity curve')
    bt.add_argument('--record', action='store_true', help='Persist the run to analysis.db')
    bt.set_defaults(func=cmd_backtest)

    sub.add_parser('watchlist', help='Show the watchlist and holdings').set_defaults(func=cmd_watchlist)
    sub.add_parser('profiles', help='List scoring profiles').set_defaults(func=cmd_profiles)

    args = parser.parse_args()
    print(json.dumps(_clean(args.func(args)), indent=2, default=str))


if __name__ == '__main__':
    main()
