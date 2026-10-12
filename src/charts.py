"""Chart data serialization for client-side rendering.

The technical indicators are already fully computed by indicators.py; this
module just converts that DataFrame into JSON-friendly arrays for the
frontend to render with Lightweight Charts (candlesticks + buy/sell markers,
stochastic, MACD panes).
"""

import pandas as pd


def _clean_floats(series):
    """Convert a pandas Series to a list of floats, with NaN as None (JSON null)."""
    return [None if pd.isna(value) else float(value) for value in series]


def serialize_chart_data(stock_data):
    """Serialize a technical-indicator DataFrame into JSON-safe chart data.

    Indicator columns (moving averages, stochastic, MACD, buy/sell signals)
    are only present once calculate_technical_indicators() has enough rows
    (30+) to compute them; each is included only if present, so short
    histories degrade to price-only data instead of erroring.
    """
    data = {
        'dates': [d.strftime('%Y-%m-%d') for d in stock_data.index],
        'open': _clean_floats(stock_data['Open']),
        'high': _clean_floats(stock_data['High']),
        'low': _clean_floats(stock_data['Low']),
        'close': _clean_floats(stock_data['Close']),
    }

    optional_float_columns = {
        'ma_30': '30_Moving_Avg',
        'ma_90': '90_Moving_Avg',
        'stochastic_k': '%K',
        'stochastic_d': '%D',
        'macd': 'MACD',
        'macd_signal': 'Signal_Line',
    }
    for key, column in optional_float_columns.items():
        if column in stock_data:
            data[key] = _clean_floats(stock_data[column])

    for key, column in [('buy_signal', 'Buy_Signal'), ('sell_signal', 'Sell_Signal')]:
        if column in stock_data:
            data[key] = [bool(value) for value in stock_data[column].fillna(False)]

    return data
