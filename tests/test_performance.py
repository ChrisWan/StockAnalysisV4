import unittest
from unittest import mock

import pandas as pd

import performance


def _linear_price_frame(periods=60, start='2024-01-02', slope=0.5, base=100.0):
    """A perfectly linear price series - values are hand-computable from the
    slope alone. Mirrors test_backtest.py's _linear_price_frame.
    """
    dates = pd.bdate_range(start, periods=periods)
    close = pd.Series([base + slope * i for i in range(periods)], index=dates)
    return pd.DataFrame({
        'Open': close, 'High': close + 1, 'Low': close - 1, 'Close': close, 'Volume': 1_000_000,
    })


class FakeProvider:
    name = 'fake'

    def __init__(self, price_frames):
        self.price_frames = price_frames

    def get_price_history(self, symbol, days=300):
        if symbol not in self.price_frames:
            raise LookupError(f'no data for {symbol!r}')
        return self.price_frames[symbol].copy()


class ComputeHoldingsPerformanceTests(unittest.TestCase):
    def test_single_holding_matches_hand_computed_values(self):
        aapl = _linear_price_frame(periods=60, slope=0.5, base=100.0)   # 100, 100.5, 101, ...
        spy = _linear_price_frame(periods=60, slope=1.0, base=400.0)    # 400, 401, 402, ...
        provider = FakeProvider({'AAPL': aapl, 'SPY': spy})

        holdings = [{'symbol': 'AAPL', 'shares': 10, 'cost_basis_per_share': 100.0,
                     'purchase_date': '2024-01-02'}]

        with mock.patch.object(performance, 'get_provider', return_value=provider):
            result = performance.compute_holdings_performance(holdings, benchmark_symbol='SPY')

        self.assertEqual(result['total_invested'], 1000.0)
        self.assertEqual(result['excluded_holdings'], [])

        # Day 0 (purchase date itself): both curves must exactly equal the
        # invested amount - this is a structural sanity check, not a coincidence.
        self.assertEqual(result['dates'][0], '2024-01-02')
        self.assertEqual(result['portfolio_value'][0], 1000.0)
        self.assertEqual(result['benchmark_value'][0], 1000.0)

        # Day index 10: AAPL = 100 + 0.5*10 = 105 -> 10*105 = 1050
        # SPY = 400 + 1.0*10 = 410; shadow_shares = 1000/400 = 2.5 -> 2.5*410 = 1025
        self.assertEqual(result['portfolio_value'][10], 1050.0)
        self.assertAlmostEqual(result['benchmark_value'][10], 1025.0, places=4)

        # Return % reflects the LAST day of the fixture (index 59), not day 10:
        # AAPL day59 = 100 + 0.5*59 = 129.5 -> value 1295 -> +29.5%
        # SPY day59 = 400 + 1.0*59 = 459.0 -> shadow value 2.5*459 = 1147.5 -> +14.75%
        self.assertAlmostEqual(result['portfolio_return_pct'], 29.5, places=4)
        self.assertAlmostEqual(result['benchmark_return_pct'], 14.75, places=4)

    def test_second_holding_contributes_nothing_before_its_own_purchase_date(self):
        # This is the core anti-money-weighted-distortion check: a later
        # capital injection must not affect the curves before its own date.
        aapl = _linear_price_frame(periods=60, slope=0.5, base=100.0)
        msft = _linear_price_frame(periods=60, slope=0.2, base=300.0)
        spy = _linear_price_frame(periods=60, slope=1.0, base=400.0)
        provider = FakeProvider({'AAPL': aapl, 'MSFT': msft, 'SPY': spy})

        msft_purchase_date = msft.index[20]
        msft_purchase_price = float(msft.loc[msft_purchase_date, 'Close'])  # 300 + 0.2*20 = 304.0

        holdings = [
            {'symbol': 'AAPL', 'shares': 10, 'cost_basis_per_share': 100.0, 'purchase_date': '2024-01-02'},
            {'symbol': 'MSFT', 'shares': 5, 'cost_basis_per_share': msft_purchase_price,
             'purchase_date': msft_purchase_date.date().isoformat()},
        ]

        with mock.patch.object(performance, 'get_provider', return_value=provider):
            result = performance.compute_holdings_performance(holdings, benchmark_symbol='SPY')

        day19 = aapl.index[19]
        idx19 = result['dates'].index(day19.date().isoformat())
        expected_aapl_only = 10 * float(aapl.loc[day19, 'Close'])
        self.assertAlmostEqual(result['portfolio_value'][idx19], expected_aapl_only, places=2)

        idx20 = result['dates'].index(msft_purchase_date.date().isoformat())
        expected_both = 10 * float(aapl.loc[msft_purchase_date, 'Close']) + 5 * msft_purchase_price
        self.assertAlmostEqual(result['portfolio_value'][idx20], expected_both, places=2)

        self.assertAlmostEqual(result['total_invested'], 1000.0 + 5 * msft_purchase_price, places=2)

    def test_holding_without_purchase_date_is_excluded_not_dropped_silently(self):
        aapl = _linear_price_frame(periods=30)
        spy = _linear_price_frame(periods=30, base=400.0)
        provider = FakeProvider({'AAPL': aapl, 'SPY': spy})

        holdings = [
            {'symbol': 'AAPL', 'shares': 10, 'cost_basis_per_share': 100.0, 'purchase_date': '2024-01-02'},
            {'symbol': 'GOOGL', 'shares': 2, 'cost_basis_per_share': 150.0, 'purchase_date': None},
        ]

        with mock.patch.object(performance, 'get_provider', return_value=provider):
            result = performance.compute_holdings_performance(holdings, benchmark_symbol='SPY')

        self.assertEqual(result['excluded_holdings'], ['GOOGL'])
        self.assertGreater(len(result['dates']), 0)

    def test_weekend_purchase_date_aligns_to_next_trading_day(self):
        aapl = _linear_price_frame(periods=30, start='2024-01-02')
        spy = _linear_price_frame(periods=30, start='2024-01-02', base=400.0)
        provider = FakeProvider({'AAPL': aapl, 'SPY': spy})

        # 2024-01-06 is a Saturday; next trading day is Monday 2024-01-08.
        holdings = [{'symbol': 'AAPL', 'shares': 10, 'cost_basis_per_share': 100.0,
                     'purchase_date': '2024-01-06'}]

        with mock.patch.object(performance, 'get_provider', return_value=provider):
            result = performance.compute_holdings_performance(holdings, benchmark_symbol='SPY')

        self.assertEqual(result['dates'][0], '2024-01-08')

    def test_empty_holdings_returns_empty_result_without_calling_provider(self):
        result = performance.compute_holdings_performance([], benchmark_symbol='SPY')

        self.assertEqual(result['dates'], [])
        self.assertIsNone(result['portfolio_return_pct'])
        self.assertEqual(result['total_invested'], 0.0)

    def test_all_holdings_missing_purchase_date_returns_empty_result_with_exclusions(self):
        holdings = [{'symbol': 'AAPL', 'shares': 10, 'cost_basis_per_share': 100.0, 'purchase_date': None}]

        result = performance.compute_holdings_performance(holdings, benchmark_symbol='SPY')

        self.assertEqual(result['dates'], [])
        self.assertEqual(result['excluded_holdings'], ['AAPL'])

    def test_holding_with_unfetchable_price_history_is_excluded_others_continue(self):
        aapl = _linear_price_frame(periods=30)
        spy = _linear_price_frame(periods=30, base=400.0)
        provider = FakeProvider({'AAPL': aapl, 'SPY': spy})  # no data for FAKE123

        holdings = [
            {'symbol': 'AAPL', 'shares': 10, 'cost_basis_per_share': 100.0, 'purchase_date': '2024-01-02'},
            {'symbol': 'FAKE123', 'shares': 1, 'cost_basis_per_share': 50.0, 'purchase_date': '2024-01-02'},
        ]

        with mock.patch.object(performance, 'get_provider', return_value=provider):
            result = performance.compute_holdings_performance(holdings, benchmark_symbol='SPY')

        self.assertIn('FAKE123', result['excluded_holdings'])
        self.assertGreater(len(result['dates']), 0)

    def test_unfetchable_benchmark_raises_value_error(self):
        aapl = _linear_price_frame(periods=30)
        provider = FakeProvider({'AAPL': aapl})  # no benchmark data at all

        holdings = [{'symbol': 'AAPL', 'shares': 10, 'cost_basis_per_share': 100.0,
                     'purchase_date': '2024-01-02'}]

        with mock.patch.object(performance, 'get_provider', return_value=provider):
            with self.assertRaises(ValueError):
                performance.compute_holdings_performance(holdings, benchmark_symbol='NOPE')

    def test_default_benchmark_symbol_used_when_not_specified(self):
        aapl = _linear_price_frame(periods=30)
        spy = _linear_price_frame(periods=30, base=400.0)
        provider = FakeProvider({'AAPL': aapl, 'SPY': spy})

        holdings = [{'symbol': 'AAPL', 'shares': 10, 'cost_basis_per_share': 100.0,
                     'purchase_date': '2024-01-02'}]

        with mock.patch.object(performance, 'get_provider', return_value=provider):
            result = performance.compute_holdings_performance(holdings, benchmark_symbol=None)

        self.assertEqual(result['benchmark_symbol'], 'SPY')


if __name__ == '__main__':
    unittest.main()
