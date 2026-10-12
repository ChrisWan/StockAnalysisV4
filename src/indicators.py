"""Technical indicator calculation and momentum scoring."""

import pandas as pd


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


def get_technical_recommendation(stock_data):
    """Get technical analysis recommendation based on latest signals"""
    return build_technical_momentum_summary(stock_data).get('signal', 'HOLD')
