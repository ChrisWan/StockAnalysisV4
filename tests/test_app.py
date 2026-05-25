import unittest
from unittest import mock
import tempfile
import os
from datetime import datetime, timedelta

import pandas as pd

import app as stock_app


class FakeTicker:
    def __init__(self, info):
        self.info = info


class StockAnalysisTests(unittest.TestCase):
    def setUp(self):
        stock_app.app.testing = True
        self.client = stock_app.app.test_client()
        stock_app.last_fetch_meta.clear()
        stock_app.data_cache.clear()
        stock_app.fundamental_cache.clear()
        stock_app.last_update.clear()

    def test_sector_benchmark_median_uses_true_median_for_even_samples(self):
        symbols = ['A', 'B', 'C', 'D', 'E', 'F']
        ticker_payloads = {
            'A': {'trailingPE': 1.0, 'priceToBook': 1.0, 'priceToSalesTrailing12Months': 1.0, 'returnOnEquity': 0.11, 'profitMargins': 0.10},
            'B': {'trailingPE': 2.0, 'priceToBook': 2.0, 'priceToSalesTrailing12Months': 2.0, 'returnOnEquity': 0.12, 'profitMargins': 0.11},
            'C': {'trailingPE': 3.0, 'priceToBook': 3.0, 'priceToSalesTrailing12Months': 3.0, 'returnOnEquity': 0.13, 'profitMargins': 0.12},
            'D': {'trailingPE': 4.0, 'priceToBook': 4.0, 'priceToSalesTrailing12Months': 4.0, 'returnOnEquity': 0.14, 'profitMargins': 0.13},
            'E': {'trailingPE': 5.0, 'priceToBook': 5.0, 'priceToSalesTrailing12Months': 5.0, 'returnOnEquity': 0.15, 'profitMargins': 0.14},
            'F': {'trailingPE': 6.0, 'priceToBook': 6.0, 'priceToSalesTrailing12Months': 6.0, 'returnOnEquity': 0.16, 'profitMargins': 0.15},
        }

        with mock.patch.object(stock_app.yf, 'Ticker', side_effect=lambda symbol: FakeTicker(ticker_payloads[symbol])):
            benchmarks = stock_app.calculate_single_sector_benchmarks('Test Sector', symbols)

        self.assertEqual(benchmarks['pe_ratio_median'], 3.5)
        self.assertEqual(benchmarks['price_to_book_median'], 3.5)

    def test_recommendation_scorecard_exposes_component_scores(self):
        scorecard = stock_app.build_recommendation_scorecard(
            {
                'sector_comparison': {
                    'sector_name': 'Technology',
                    'metrics': {
                        'pe_ratio': {'label': 'Price-to-Earnings Ratio', 'better_than_sector': True},
                        'price_to_book': {'label': 'Price-to-Book Ratio', 'better_than_sector': False},
                        'return_on_equity': {'label': 'Return on Equity', 'better_than_sector': True},
                    }
                },
                'growth_analysis': {
                    'metrics': {
                        'revenue_growth': {'label': 'Revenue Growth', 'raw_value': 0.12},
                        'earnings_growth': {'label': 'Earnings Growth', 'raw_value': 0.08},
                        'ocf_growth': {'label': 'Operating Cash Flow Growth', 'raw_value': -0.02},
                    }
                },
            },
            'BUY'
        )

        self.assertEqual(scorecard['label'], 'BUY')
        self.assertIn('sector_alignment', scorecard['components'])
        self.assertIn('growth_quality', scorecard['components'])
        self.assertIn('technical_momentum', scorecard['components'])
        self.assertEqual(scorecard['components']['technical_momentum']['signal'], 'BUY')
        self.assertEqual(scorecard['components']['growth_quality']['weight'], 0.4)
        self.assertEqual(
            scorecard['components']['growth_quality']['positive_signal_labels'],
            ['Revenue Growth', 'Earnings Growth']
        )
        self.assertEqual(
            scorecard['components']['growth_quality']['negative_signal_labels'],
            ['Operating Cash Flow Growth']
        )
        self.assertEqual(
            scorecard['components']['sector_alignment']['negative_signal_labels'],
            ['Price-to-Book Ratio']
        )
        self.assertIn('signal_rule', scorecard['components']['sector_alignment'])
        self.assertEqual(scorecard['profile']['key'], 'balanced')

    def test_non_default_scoring_profile_changes_component_weights(self):
        scorecard = stock_app.build_recommendation_scorecard(
            {
                'sector_comparison': {
                    'sector_name': 'Technology',
                    'metrics': {
                        'pe_ratio': {'better_than_sector': True},
                        'price_to_book': {'better_than_sector': False},
                        'return_on_equity': {'better_than_sector': True},
                    }
                },
                'growth_analysis': {
                    'metrics': {
                        'revenue_growth': {'raw_value': 0.12},
                        'earnings_growth': {'raw_value': 0.08},
                        'ocf_growth': {'raw_value': -0.02},
                    }
                },
            },
            'BUY',
            profile_name='momentum'
        )

        self.assertEqual(scorecard['profile']['key'], 'momentum')
        self.assertGreater(
            scorecard['components']['technical_momentum']['weight'],
            scorecard['components']['growth_quality']['weight']
        )

    def test_stock_endpoint_returns_json_error_for_missing_symbol(self):
        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=[]):
            response = self.client.get('/api/stock/FAKE')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['error'], 'Stock not found in portfolio')

    def test_chart_endpoint_returns_json_error_for_missing_symbol(self):
        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=[]):
            response = self.client.get('/api/chart/FAKE')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['error'], 'Stock not found in portfolio')

    def test_stock_endpoint_exposes_fetch_status(self):
        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=['AAPL']), \
             mock.patch.object(stock_app, 'fetch_single_stock_data', return_value={
                 'source': 'live',
                 'cached': False,
                 'success': True,
                 'market_data_available': True,
                 'fundamental_data_available': True,
                 'message': 'Fetched fresh market data from Yahoo Finance.'
             }), \
             mock.patch.object(stock_app, 'calculate_fundamental_analysis', return_value={
                 'sector_comparison': {'metrics': {}},
                 'growth_analysis': {'metrics': {}},
                 'financial_health': {},
                 'company_info': {}
             }), \
             mock.patch.object(stock_app, 'build_recommendation_scorecard', return_value={
                 'label': 'HOLD',
                 'total_score': 5.0,
                 'max_score': 10,
                 'components': {}
             }), \
             mock.patch.object(stock_app, 'get_technical_recommendation', return_value='HOLD'):
            response = self.client.get('/api/stock/AAPL')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['fetch_status']['source'], 'live')
        self.assertTrue(payload['fetch_status']['market_data_available'])

    def test_stock_endpoint_respects_profile_query_parameter(self):
        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=['AAPL']), \
             mock.patch.object(stock_app, 'fetch_single_stock_data', return_value={
                 'source': 'live',
                 'cached': False,
                 'success': True,
                 'market_data_available': True,
                 'fundamental_data_available': True,
                 'message': 'Fetched fresh market data from Yahoo Finance.'
             }), \
             mock.patch.object(stock_app, 'calculate_fundamental_analysis', return_value={
                 'sector_comparison': {'metrics': {}, 'sector_name': 'Technology'},
                 'growth_analysis': {'metrics': {}},
                 'financial_health': {},
                 'company_info': {}
             }), \
             mock.patch.object(stock_app, 'build_recommendation_scorecard', return_value={
                 'label': 'HOLD',
                 'total_score': 5.0,
                 'max_score': 10,
                 'profile': {'key': 'growth', 'label': 'Growth', 'weights': {}},
                 'components': {}
             }) as build_scorecard_mock, \
             mock.patch.object(stock_app, 'get_technical_recommendation', return_value='HOLD'):
            response = self.client.get('/api/stock/AAPL?profile=growth')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(build_scorecard_mock.call_args.kwargs['profile_name'], 'growth')
        self.assertTrue(any(profile['key'] == 'growth' for profile in payload['available_scoring_profiles']))

    def test_technical_indicators_require_true_ma_crossover(self):
        dates = pd.date_range('2026-01-01', periods=120)
        close = pd.Series([100.0] * 100 + [94.0] * 9 + [120.0] * 11, index=dates)
        stock_data = pd.DataFrame({
            'Close': close,
            'High': close + 1,
            'Low': close - 1,
        })

        indicators = stock_app.calculate_technical_indicators(stock_data)
        crossover_days = indicators.index[indicators['buy_ma']].tolist()

        self.assertTrue(crossover_days)
        self.assertEqual(len(crossover_days), 1)

    def test_technical_recommendation_uses_recent_composite_signal_counts(self):
        stock_data = pd.DataFrame({
            'Buy_Signal': [False] * 7 + [True, True, False],
            'Sell_Signal': [False] * 10,
            'trend_up': [False] * 9 + [True],
            'trend_down': [False] * 10,
            'MACD': [0.15] * 10,
            'Signal_Line': [0.0] * 10,
            '%K': [22.0] * 9 + [38.0],
            '%D': [28.0] * 9 + [30.0],
        })

        recommendation = stock_app.get_technical_recommendation(stock_data)

        self.assertEqual(recommendation, 'BUY')

    def test_technical_recommendation_returns_hold_when_signals_tie(self):
        stock_data = pd.DataFrame({
            'Buy_Signal': [False] * 8 + [True, False],
            'Sell_Signal': [False] * 8 + [False, True],
            'trend_up': [False] * 10,
            'trend_down': [False] * 10,
            'MACD': [0.0] * 10,
            'Signal_Line': [0.0] * 10,
            '%K': [50.0] * 10,
            '%D': [50.0] * 10,
        })

        recommendation = stock_app.get_technical_recommendation(stock_data)

        self.assertEqual(recommendation, 'HOLD')

    def test_technical_momentum_summary_exposes_weighted_score_details(self):
        stock_data = pd.DataFrame({
            'Buy_Signal': [False] * 8 + [True, True],
            'Sell_Signal': [False] * 10,
            'trend_up': [False] * 9 + [True],
            'trend_down': [False] * 10,
            'MACD': [0.35] * 10,
            'Signal_Line': [0.0] * 10,
            '%K': [18.0] * 9 + [42.0],
            '%D': [24.0] * 9 + [28.0],
        })

        summary = stock_app.build_technical_momentum_summary(stock_data)

        self.assertEqual(summary['signal'], 'BUY')
        self.assertGreater(summary['score'], 7.0)
        self.assertEqual(summary['buy_signals'], 2)
        self.assertEqual(summary['setup_state'], 'Buy signal active')
        self.assertEqual(summary['gap_to_buy_threshold'], 0.0)
        self.assertIn('macd_state', summary['details'])

    def test_recommendation_scorecard_prefers_weighted_technical_summary(self):
        scorecard = stock_app.build_recommendation_scorecard(
            {
                'sector_comparison': {'sector_name': 'Technology', 'metrics': {}},
                'growth_analysis': {'metrics': {}},
            },
            'HOLD',
            technical_summary={
                'signal': 'BUY',
                'score': 7.8,
                'buy_signals': 2,
                'sell_signals': 0,
                'signal_window_days': 10,
                'trend_confirmation': True,
                'latest_buy_age': 1,
                'latest_sell_age': None,
                'details': {'macd_state': 8.0}
            }
        )

        technical_component = scorecard['components']['technical_momentum']
        self.assertEqual(technical_component['signal'], 'BUY')
        self.assertEqual(technical_component['score'], 7.8)
        self.assertTrue(technical_component['trend_confirmation'])
        self.assertEqual(technical_component['setup_state'], 'Neutral setup')
        self.assertIsNotNone(technical_component['gap_to_buy_threshold'])

    def test_run_with_retries_retries_until_success(self):
        attempts = {'count': 0}

        def flaky_operation():
            attempts['count'] += 1
            if attempts['count'] < 3:
                raise RuntimeError('temporary failure')
            return 'ok'

        result = stock_app.run_with_retries(flaky_operation, 'test operation', attempts=3, delay_seconds=0)

        self.assertEqual(result, 'ok')
        self.assertEqual(attempts['count'], 3)

    def test_fetch_single_stock_data_uses_disk_cache_after_live_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_cache_dir = stock_app.STOCK_CACHE_DIR
            stock_app.STOCK_CACHE_DIR = temp_dir

            try:
                cached_frame = pd.DataFrame({'Close': [1.0], 'High': [1.0], 'Low': [1.0]})
                stock_app.save_stock_snapshot('AAPL', cached_frame, {'sector': 'Technology'})

                with mock.patch.object(stock_app, 'run_with_retries', side_effect=RuntimeError('network down')):
                    fetch_meta = stock_app.fetch_single_stock_data('AAPL')

                self.assertEqual(fetch_meta['source'], 'disk-cache')
                self.assertTrue(fetch_meta['market_data_available'])
                self.assertEqual(stock_app.fundamental_cache['AAPL']['sector'], 'Technology')
                self.assertIn('snapshot_saved_at', fetch_meta)
                self.assertTrue(fetch_meta['freshness'])
            finally:
                stock_app.STOCK_CACHE_DIR = original_cache_dir

    def test_fetch_single_stock_data_labels_in_memory_cache_consistently(self):
        stock_app.data_cache['AAPL'] = pd.DataFrame({'Close': [1.0]})
        stock_app.fundamental_cache['AAPL'] = {'sector': 'Technology'}
        stock_app.last_update['AAPL'] = datetime.now()
        stock_app.last_fetch_meta['AAPL'] = {
            'source': 'live',
            'cached': False,
            'success': True,
            'market_data_available': True,
            'fundamental_data_available': True,
            'message': 'Fetched fresh market data from Yahoo Finance.'
        }

        fetch_meta = stock_app.fetch_single_stock_data('AAPL')

        self.assertEqual(fetch_meta['source'], 'cache')
        self.assertTrue(fetch_meta['cached'])
        self.assertEqual(fetch_meta['message'], 'Using recently fetched in-memory cache.')

    def test_prune_stock_cache_removes_old_and_excess_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_cache_dir = stock_app.STOCK_CACHE_DIR
            stock_app.STOCK_CACHE_DIR = temp_dir

            try:
                now = datetime(2026, 5, 6, 12, 0, 0)
                for symbol in ['ONE', 'TWO', 'THREE']:
                    stock_app.save_stock_snapshot(symbol, pd.DataFrame({'Close': [1.0]}), {'sector': 'Technology'})

                old_file = stock_app.get_stock_cache_file('ONE')
                recent_file = stock_app.get_stock_cache_file('TWO')
                newest_file = stock_app.get_stock_cache_file('THREE')

                old_ts = (now - timedelta(days=40)).timestamp()
                recent_ts = (now - timedelta(days=1)).timestamp()
                newest_ts = now.timestamp()
                os.utime(old_file, (old_ts, old_ts))
                os.utime(recent_file, (recent_ts, recent_ts))
                os.utime(newest_file, (newest_ts, newest_ts))

                stock_app.prune_stock_cache(max_age_days=30, max_files=1, now=now)

                self.assertFalse(os.path.exists(old_file))
                self.assertFalse(os.path.exists(recent_file))
                self.assertTrue(os.path.exists(newest_file))
            finally:
                stock_app.STOCK_CACHE_DIR = original_cache_dir

    def test_save_sector_benchmarks_preserves_recent_refresh_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_benchmarks_file = stock_app.SECTOR_BENCHMARKS_FILE
            stock_app.SECTOR_BENCHMARKS_FILE = os.path.join(temp_dir, 'sector_benchmarks.json')

            try:
                self.assertTrue(stock_app.save_sector_benchmarks({'Technology': {'pe_ratio_median': 20}}))
                self.assertTrue(stock_app.save_sector_benchmarks({'Technology': {'pe_ratio_median': 22}}))

                saved_data = stock_app.load_sector_benchmarks()
                history = saved_data['_metadata']['refresh_history']

                self.assertEqual(len(history), 2)
                self.assertEqual(history[0]['total_sectors'], 1)
                self.assertEqual(history[0]['calculation_method'], 'yahoo_finance_percentiles')
            finally:
                stock_app.SECTOR_BENCHMARKS_FILE = original_benchmarks_file

    def test_benchmark_status_exposes_age_and_refresh_history(self):
        stock_app.sector_benchmarks_data = {
            '_metadata': {
                'last_updated': '2026-05-06 10:00:00',
                'calculation_method': 'yahoo_finance_percentiles',
                'refresh_history': [
                    {
                        'timestamp': '2026-05-06 10:00:00',
                        'total_sectors': 2,
                        'calculation_method': 'yahoo_finance_percentiles',
                    }
                ],
            },
            'Technology': {'pe_ratio_median': 20},
            'Healthcare': {'pe_ratio_median': 18},
        }

        with mock.patch.object(stock_app.os.path, 'exists', return_value=True), \
             mock.patch.object(stock_app.os, 'stat', return_value=mock.Mock(st_mtime=datetime(2026, 5, 6, 10, 5, 0).timestamp(), st_size=2048)), \
             mock.patch.object(stock_app, 'get_sector_representative_stocks', return_value={'Technology': ['AAPL'], 'Healthcare': ['LLY']}), \
             mock.patch.object(stock_app, 'describe_age', return_value='2 hour(s) old'):
            response = self.client.get('/api/benchmarks/status')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['benchmark_data']['last_calculated_age'], '2 hour(s) old')
        self.assertEqual(len(payload['benchmark_data']['refresh_history']), 1)


if __name__ == '__main__':
    unittest.main()