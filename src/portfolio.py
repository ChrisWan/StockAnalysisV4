"""Portfolio persistence (JSON file, single local user)."""

import json
import logging
import os

import config

LOGGER = logging.getLogger(__name__)

# Module-level so tests can point persistence at a temporary file.
PORTFOLIO_FILE = config.PORTFOLIO_FILE


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
    except Exception:
        LOGGER.exception("Error loading portfolio")
        return []


def save_portfolio(portfolio):
    """Save portfolio to JSON file"""
    try:
        data = {'portfolio': portfolio}
        with open(PORTFOLIO_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        return True
    except Exception:
        LOGGER.exception("Error saving portfolio")
        return False


def get_portfolio_stocks():
    """Get list of stock symbols from portfolio"""
    return [stock['symbol'] for stock in load_portfolio()]
