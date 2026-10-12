import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

import numpy as np
import pandas as pd

import backtest
import db
from indicators import calculate_technical_indicators


def _synthetic_price_frame(periods=400, start='2024-01-01', seed=7, trend=0.15, noise=1.2):
    """A price series with enough history/volatility for indicators to
    produce real (non-degenerate) signals, but fully reproducible.
    """
    dates = pd.bdate_range(start, periods=periods)
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(trend, noise, periods)), index=dates)
    return pd.DataFrame({
        'Open': close.shift(1).fillna(close.iloc[0]),
        'High': close + rng.uniform(0.2, 1.5, periods),
        'Low': close - rng.uniform(0.2, 1.5, periods),
        'Close': close,
        'Volume': 1_000_000,
    })


def _linear_price_frame(periods=400, start='2024-01-01', slope=0.5, base=100.0):
    """A perfectly linear price series - forward returns are hand-computable
    from the slope alone, useful for exact arithmetic assertions.
    """
    dates = pd.bdate_range(start, periods=periods)
    close = pd.Series([base + slope * i for i in range(periods)], index=dates)
    return pd.DataFrame({
        'Open': close, 'High': close + 1, 'Low': close - 1, 'Close': close, 'Volume': 1_000_000,
    })


class NoLookaheadProofTests(unittest.TestCase):
    """The core correctness claim behind computing indicators once on the
    full series and slicing per backtest step: reading row T from indicators
    computed on the full series must exactly match recomputing indicators on
    only the data available up to T. This is true because every indicator
    (rolling/.ewm/.shift) only ever looks backward - proving it here is what
    justifies backtest.py doing the (much cheaper) compute-once-and-slice
    instead of recomputing indicators at every step.
    """

    def test_indicator_values_at_truncation_point_match_recomputed_truncated_series(self):
        full_frame = _synthetic_price_frame(periods=200)
        full_indicators = calculate_technical_indicators(full_frame.copy())

        check_position = 150
        truncated_frame = full_frame.iloc[:check_position + 1].copy()
        truncated_indicators = calculate_technical_indicators(truncated_frame)

        row_from_full = full_indicators.iloc[check_position]
        row_from_truncated = truncated_indicators.iloc[-1]

        for column in ['30_Moving_Avg', '90_Moving_Avg', '%K', '%D', 'MACD', 'Signal_Line']:
            self.assertAlmostEqual(
                row_from_full[column], row_from_truncated[column], places=8,
                msg=f'{column} differs between full-series and truncated computation - lookahead leak')

        for column in ['Buy_Signal', 'Sell_Signal', 'trend_up', 'trend_down']:
            self.assertEqual(
                bool(row_from_full[column]), bool(row_from_truncated[column]),
                msg=f'{column} differs between full-series and truncated computation - lookahead leak')

    def test_holds_at_multiple_truncation_points(self):
        full_frame = _synthetic_price_frame(periods=250, seed=3)
        full_indicators = calculate_technical_indicators(full_frame.copy())

        for check_position in [100, 150, 200, 249]:
            truncated_indicators = calculate_technical_indicators(
                full_frame.iloc[:check_position + 1].copy())
            full_row = full_indicators.iloc[check_position]
            truncated_row = truncated_indicators.iloc[-1]
            self.assertAlmostEqual(full_row['MACD'], truncated_row['MACD'], places=8)
            self.assertAlmostEqual(full_row['30_Moving_Avg'], truncated_row['30_Moving_Avg'], places=8)


class AggregateByLabelTests(unittest.TestCase):
    def test_computes_count_avg_and_win_rate_per_label(self):
        steps = [
            {'label': 'BUY', 'forward_return': 0.10},
            {'label': 'BUY', 'forward_return': -0.02},
            {'label': 'BUY', 'forward_return': 0.05},
            {'label': 'SELL', 'forward_return': -0.03},
            {'label': 'SELL', 'forward_return': 0.01},
        ]

        result = backtest._aggregate_by_label(steps)

        self.assertEqual(result['BUY']['count'], 3)
        self.assertAlmostEqual(result['BUY']['avg_forward_return'], (0.10 - 0.02 + 0.05) / 3, places=4)
        self.assertAlmostEqual(result['BUY']['win_rate'], 2 / 3, places=4)
        self.assertEqual(result['SELL']['count'], 2)
        self.assertAlmostEqual(result['SELL']['win_rate'], 0.5, places=4)


class BuildEquityCurveTests(unittest.TestCase):
    def test_compounds_only_bullish_steps_and_tracks_buy_and_hold(self):
        dates = pd.bdate_range('2024-01-01', periods=5)
        price_data = pd.DataFrame({'Close': [100.0, 110.0, 121.0, 108.9, 130.0]}, index=dates)
        steps = [
            {'date': dates[0].date().isoformat(), 'label': 'BUY', 'forward_return': 0.10},
            {'date': dates[1].date().isoformat(), 'label': 'HOLD', 'forward_return': 0.05},
            {'date': dates[2].date().isoformat(), 'label': 'STRONG BUY', 'forward_return': -0.10},
            {'date': dates[3].date().isoformat(), 'label': 'SELL', 'forward_return': 0.02},
        ]

        curve = backtest._build_equity_curve(steps, price_data)

        # Step 0 (BUY): 1 * 1.10 = 1.10
        self.assertAlmostEqual(curve[0]['follow_signal_cumulative'], 1.10, places=4)
        # Step 1 (HOLD): unchanged, still 1.10
        self.assertAlmostEqual(curve[1]['follow_signal_cumulative'], 1.10, places=4)
        # Step 2 (STRONG BUY): 1.10 * 0.90 = 0.99
        self.assertAlmostEqual(curve[2]['follow_signal_cumulative'], 0.99, places=4)
        # Step 3 (SELL): unchanged, still 0.99
        self.assertAlmostEqual(curve[3]['follow_signal_cumulative'], 0.99, places=4)

        # buy_and_hold tracks price relative to the first step's price (100.0)
        self.assertAlmostEqual(curve[0]['buy_and_hold_cumulative'], 1.0, places=4)
        self.assertAlmostEqual(curve[1]['buy_and_hold_cumulative'], 1.10, places=4)
        self.assertAlmostEqual(curve[3]['buy_and_hold_cumulative'], 1.089, places=4)

    def test_empty_steps_returns_empty_curve(self):
        self.assertEqual(backtest._build_equity_curve([], pd.DataFrame()), [])


class FakeProvider:
    name = 'fake'

    def __init__(self, price_data, sector='Technology'):
        self.price_data = price_data
        self.sector = sector

    def get_price_history(self, symbol, days=300):
        return self.price_data.copy()

    def get_fundamentals(self, symbol):
        return {'sector': self.sector}


class FakeEdgarNoData:
    def get_company_facts(self, symbol):
        raise LookupError('not US-listed')


class RunBacktestTests(unittest.TestCase):
    def setUp(self):
        self._original_db_file = db.DB_FILE
        self._temp_dir = tempfile.TemporaryDirectory()
        db.DB_FILE = os.path.join(self._temp_dir.name, 'test.db')

    def tearDown(self):
        db.DB_FILE = self._original_db_file
        self._temp_dir.cleanup()

    def test_run_backtest_produces_well_formed_report_without_edgar_data(self):
        price_data = _synthetic_price_frame(periods=400)
        provider = FakeProvider(price_data)
        start = (price_data.index[250]).date()
        end = (price_data.index[350]).date()

        with mock.patch.object(backtest, 'get_provider', return_value=provider), \
             mock.patch.object(backtest, '_edgar', FakeEdgarNoData()):
            report = backtest.run_backtest('AAPL', start, end, holding_period_days=20, step_days=5)

        self.assertEqual(report['symbol'], 'AAPL')
        self.assertFalse(report['fundamentals_available'])
        self.assertGreater(report['steps_evaluated'], 0)
        self.assertEqual(len(report['steps']), report['steps_evaluated'])
        self.assertIn('label_performance', report)
        self.assertIn('equity_curve', report)
        self.assertEqual(len(report['equity_curve']), report['steps_evaluated'])

        valid_labels = {'STRONG BUY', 'BUY', 'HOLD', 'SELL', 'STRONG SELL'}
        for step in report['steps']:
            self.assertIn(step['label'], valid_labels)

    def test_run_backtest_forward_return_matches_hand_computed_value(self):
        # Linear series: forward_return at step T with holding_period H and
        # slope S is exactly (H*S) / (base + slope*T) - fully predictable.
        price_data = _linear_price_frame(periods=400, slope=0.5, base=100.0)
        provider = FakeProvider(price_data)
        start = price_data.index[250].date()
        end = price_data.index[300].date()

        with mock.patch.object(backtest, 'get_provider', return_value=provider), \
             mock.patch.object(backtest, '_edgar', FakeEdgarNoData()):
            report = backtest.run_backtest('AAPL', start, end, holding_period_days=20, step_days=5)

        first_step = report['steps'][0]
        eval_position = price_data.index.get_loc(pd.Timestamp(first_step['date']))
        entry_price = 100.0 + 0.5 * eval_position
        expected_return = round((0.5 * 20) / entry_price, 4)
        self.assertAlmostEqual(first_step['forward_return'], expected_return, places=4)

    def test_run_backtest_raises_for_invalid_date_range(self):
        price_data = _synthetic_price_frame(periods=100)
        provider = FakeProvider(price_data)

        with mock.patch.object(backtest, 'get_provider', return_value=provider):
            with self.assertRaises(ValueError):
                backtest.run_backtest('AAPL', date(2026, 1, 10), date(2026, 1, 1))

    def test_run_backtest_raises_when_no_price_data(self):
        provider = FakeProvider(pd.DataFrame())

        with mock.patch.object(backtest, 'get_provider', return_value=provider):
            with self.assertRaises(ValueError):
                backtest.run_backtest('AAPL', date(2025, 1, 1), date(2025, 6, 1))

    def test_run_backtest_uses_edgar_as_of_data_when_available(self):
        price_data = _synthetic_price_frame(periods=400)
        provider = FakeProvider(price_data)
        start = price_data.index[250].date()
        end = price_data.index[300].date()

        fake_facts = {'facts': {'us-gaap': {}, 'dei': {}}}
        fake_edgar = mock.Mock()
        fake_edgar.get_company_facts.return_value = fake_facts

        with mock.patch.object(backtest, 'get_provider', return_value=provider), \
             mock.patch.object(backtest, '_edgar', fake_edgar):
            report = backtest.run_backtest('AAPL', start, end, holding_period_days=20, step_days=10)

        self.assertTrue(report['fundamentals_available'])
        fake_edgar.get_company_facts.assert_called_once_with('AAPL')


class BacktestPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._original_db_file = db.DB_FILE
        self._temp_dir = tempfile.TemporaryDirectory()
        db.DB_FILE = os.path.join(self._temp_dir.name, 'test.db')

    def tearDown(self):
        db.DB_FILE = self._original_db_file
        self._temp_dir.cleanup()

    def _sample_report(self, symbol='AAPL'):
        return {
            'symbol': symbol, 'profile': 'balanced', 'sector': 'Technology',
            'start_date': '2025-01-01', 'end_date': '2025-06-01',
            'holding_period_days': 20, 'step_days': 5,
            'fundamentals_available': True, 'steps_evaluated': 2,
            'steps_skipped_insufficient_forward_data': 0,
            'steps': [{'date': '2025-01-02', 'label': 'BUY', 'total_score': 7.0, 'forward_return': 0.05}],
            'label_performance': {'BUY': {'count': 1, 'avg_forward_return': 0.05, 'win_rate': 1.0}},
            'follow_signal_cumulative_return': 0.05,
            'buy_and_hold_cumulative_return': 0.03,
            'equity_curve': [{'date': '2025-01-02', 'follow_signal_cumulative': 1.05, 'buy_and_hold_cumulative': 1.03}],
        }

    def test_save_and_retrieve_full_result(self):
        run_id = backtest.save_backtest_result(self._sample_report())

        result = backtest.get_backtest_result(run_id)

        self.assertEqual(result['symbol'], 'AAPL')
        self.assertEqual(result['id'], run_id)
        self.assertEqual(result['label_performance']['BUY']['win_rate'], 1.0)

    def test_get_backtest_result_missing_id_returns_none(self):
        self.assertIsNone(backtest.get_backtest_result(999))

    def test_get_backtest_results_lists_newest_first_with_summary_fields(self):
        backtest.save_backtest_result(self._sample_report(symbol='AAPL'))
        backtest.save_backtest_result(self._sample_report(symbol='MSFT'))

        results = backtest.get_backtest_results()

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['symbol'], 'MSFT')  # most recent first
        self.assertIn('follow_signal_cumulative_return', results[0])
        self.assertNotIn('steps', results[0])  # summary listing omits full step detail


if __name__ == '__main__':
    unittest.main()
