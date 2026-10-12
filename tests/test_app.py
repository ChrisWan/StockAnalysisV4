import unittest
from unittest import mock
import tempfile
import os
from datetime import datetime, timedelta

import pandas as pd

import app as stock_app
import benchmarks
import data_providers
import db
import holdings as holdings_module
import scoring
import storage


class FakeProvider:
    """Minimal DataProvider stand-in for benchmark calculations."""

    name = 'fake'

    def __init__(self, benchmark_payloads):
        self.benchmark_payloads = benchmark_payloads

    def get_benchmark_metrics(self, symbol):
        return dict(self.benchmark_payloads[symbol])


class StockAnalysisTests(unittest.TestCase):
    def setUp(self):
        stock_app.app.testing = True
        self.client = stock_app.app.test_client()
        stock_app.last_fetch_meta.clear()
        stock_app.data_cache.clear()
        stock_app.fundamental_cache.clear()
        stock_app.last_update.clear()

        self._original_db_file = db.DB_FILE
        self._temp_dir = tempfile.TemporaryDirectory()
        db.DB_FILE = os.path.join(self._temp_dir.name, 'test.db')
        self.addCleanup(self._restore_db_file)

    def _restore_db_file(self):
        db.DB_FILE = self._original_db_file
        self._temp_dir.cleanup()

    def test_sector_benchmark_median_uses_true_median_for_even_samples(self):
        symbols = ['A', 'B', 'C', 'D', 'E', 'F']
        payloads = {
            'A': {'pe_ratio': 1.0, 'price_to_book': 1.0, 'price_to_sales': 1.0, 'return_on_equity': 0.11, 'profit_margin': 0.10},
            'B': {'pe_ratio': 2.0, 'price_to_book': 2.0, 'price_to_sales': 2.0, 'return_on_equity': 0.12, 'profit_margin': 0.11},
            'C': {'pe_ratio': 3.0, 'price_to_book': 3.0, 'price_to_sales': 3.0, 'return_on_equity': 0.13, 'profit_margin': 0.12},
            'D': {'pe_ratio': 4.0, 'price_to_book': 4.0, 'price_to_sales': 4.0, 'return_on_equity': 0.14, 'profit_margin': 0.13},
            'E': {'pe_ratio': 5.0, 'price_to_book': 5.0, 'price_to_sales': 5.0, 'return_on_equity': 0.15, 'profit_margin': 0.14},
            'F': {'pe_ratio': 6.0, 'price_to_book': 6.0, 'price_to_sales': 6.0, 'return_on_equity': 0.16, 'profit_margin': 0.15},
        }

        original_provider = data_providers.get_provider()
        data_providers.set_provider(FakeProvider(payloads))
        try:
            result = benchmarks.calculate_single_sector_benchmarks('Test Sector', symbols, fetch_delay_seconds=0)
        finally:
            data_providers.set_provider(original_provider)

        self.assertEqual(result['pe_ratio_median'], 3.5)
        self.assertEqual(result['price_to_book_median'], 3.5)

    def test_sector_benchmarks_keep_negative_profitability_but_drop_negative_valuations(self):
        symbols = ['A', 'B', 'C', 'D']
        payloads = {
            'A': {'pe_ratio': 10.0, 'price_to_book': 1.0, 'price_to_sales': 1.0, 'return_on_equity': 0.10, 'profit_margin': 0.10},
            'B': {'pe_ratio': 20.0, 'price_to_book': 2.0, 'price_to_sales': 2.0, 'return_on_equity': 0.20, 'profit_margin': 0.20},
            'C': {'pe_ratio': 30.0, 'price_to_book': 3.0, 'price_to_sales': 3.0, 'return_on_equity': -0.30, 'profit_margin': -0.30},
            'D': {'pe_ratio': -5.0, 'price_to_book': 4.0, 'price_to_sales': 4.0, 'return_on_equity': 0.40, 'profit_margin': 0.40},
        }

        original_provider = data_providers.get_provider()
        data_providers.set_provider(FakeProvider(payloads))
        try:
            result = benchmarks.calculate_single_sector_benchmarks('Test Sector', symbols, fetch_delay_seconds=0)
        finally:
            data_providers.set_provider(original_provider)

        # Negative P/E (stock D) excluded from the P/E median: median(10, 20, 30)
        self.assertEqual(result['pe_ratio_median'], 20.0)
        # Negative ROE (stock C) stays in: median(0.10, 0.20, -0.30, 0.40)
        self.assertEqual(result['return_on_equity_median'], 0.15)

    def test_benchmark_fetch_throttles_between_symbols(self):
        payloads = {
            'A': {'pe_ratio': 1.0, 'price_to_book': 1.0, 'price_to_sales': 1.0, 'return_on_equity': 0.11, 'profit_margin': 0.10},
            'B': {'pe_ratio': 2.0, 'price_to_book': 2.0, 'price_to_sales': 2.0, 'return_on_equity': 0.12, 'profit_margin': 0.11},
            'C': {'pe_ratio': 3.0, 'price_to_book': 3.0, 'price_to_sales': 3.0, 'return_on_equity': 0.13, 'profit_margin': 0.12},
        }

        original_provider = data_providers.get_provider()
        data_providers.set_provider(FakeProvider(payloads))
        try:
            with mock.patch.object(benchmarks.time, 'sleep') as sleep_mock:
                benchmarks.calculate_single_sector_benchmarks('Test Sector', ['A', 'B', 'C'], fetch_delay_seconds=0.5)
        finally:
            data_providers.set_provider(original_provider)

        # One delay between each pair of consecutive fetches, none before the first.
        self.assertEqual(sleep_mock.call_count, 2)
        sleep_mock.assert_called_with(0.5)

    def test_score_metric_relative_penalizes_negative_values(self):
        self.assertEqual(
            scoring.score_metric_relative_to_sector(-5.0, 'pe_ratio', 'Technology', higher_is_better=False),
            scoring.NEGATIVE_VALUE_PENALTY_SCORE,
        )
        self.assertEqual(
            scoring.score_metric_relative_to_sector(-0.10, 'return_on_equity', 'Technology', higher_is_better=True),
            scoring.NEGATIVE_VALUE_PENALTY_SCORE,
        )
        self.assertIsNone(
            scoring.score_metric_relative_to_sector(None, 'pe_ratio', 'Technology', higher_is_better=False)
        )

    def test_fundamental_analysis_grades_metrics_and_flags_negatives(self):
        original_data = benchmarks.sector_benchmarks_data
        benchmarks.sector_benchmarks_data = {
            'Technology': {
                'pe_ratio_median': 20.0,
                'price_to_book_median': 4.0,
                'price_to_sales_median': 5.0,
                'return_on_equity_median': 0.15,
                'profit_margin_median': 0.12,
            }
        }
        try:
            analysis = scoring.calculate_fundamental_analysis({
                'sector': 'Technology',
                'pe_ratio': 10.0,        # much cheaper than the median -> top score
                'return_on_equity': -0.05,  # negative -> penalty, not exclusion
            })
        finally:
            benchmarks.sector_benchmarks_data = original_data

        metrics = analysis['sector_comparison']['metrics']
        self.assertEqual(metrics['pe_ratio']['score'], 10)
        self.assertTrue(metrics['pe_ratio']['better_than_sector'])
        self.assertEqual(metrics['return_on_equity']['score'], scoring.NEGATIVE_VALUE_PENALTY_SCORE)
        self.assertFalse(metrics['return_on_equity']['better_than_sector'])
        self.assertIn('note', metrics['return_on_equity'])

    def test_recommendation_scorecard_uses_graded_metric_scores(self):
        scorecard = stock_app.build_recommendation_scorecard(
            {
                'sector_comparison': {
                    'sector_name': 'Technology',
                    'metrics': {
                        'pe_ratio': {'label': 'Price-to-Earnings Ratio', 'better_than_sector': True, 'score': 9.0},
                        'return_on_equity': {'label': 'Return on Equity', 'better_than_sector': False, 'score': 4.0},
                    }
                },
                'growth_analysis': {'metrics': {}},
            },
            'HOLD'
        )

        component = scorecard['components']['sector_alignment']
        self.assertEqual(component['scoring_basis'], 'graded')
        self.assertEqual(component['score'], 6.5)
        self.assertEqual(component['metric_scores']['Price-to-Earnings Ratio'], 9.0)

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
        self.assertEqual(scorecard['components']['sector_alignment']['scoring_basis'], 'binary')
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

    def test_chart_data_endpoint_returns_json_error_for_missing_symbol(self):
        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=[]):
            response = self.client.get('/api/chart-data/FAKE')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['error'], 'Stock not found in portfolio')

    def test_chart_data_endpoint_returns_serialized_indicator_arrays(self):
        import numpy as np
        from indicators import calculate_technical_indicators

        dates = pd.date_range('2026-01-01', periods=120)
        close = pd.Series(100 + np.cumsum(np.random.default_rng(1).normal(0, 1, 120)), index=dates)
        frame = pd.DataFrame({'Open': close, 'High': close + 1, 'Low': close - 1, 'Close': close})
        frame = calculate_technical_indicators(frame)

        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=['AAPL']), \
             mock.patch.object(stock_app, 'fetch_single_stock_data', return_value={'source': 'live'}):
            stock_app.data_cache['AAPL'] = frame
            response = self.client.get('/api/chart-data/AAPL')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload['dates']), 120)
        self.assertEqual(len(payload['close']), 120)
        self.assertIn('ma_30', payload)
        self.assertIn('macd', payload)
        self.assertIn('buy_signal', payload)
        self.assertIsInstance(payload['buy_signal'][0], bool)

    def test_chart_data_endpoint_returns_404_when_no_data(self):
        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=['AAPL']), \
             mock.patch.object(stock_app, 'fetch_single_stock_data', return_value={'source': 'error'}):
            stock_app.data_cache['AAPL'] = pd.DataFrame()
            response = self.client.get('/api/chart-data/AAPL')

        self.assertEqual(response.status_code, 404)

    def test_history_endpoint_returns_json_error_for_missing_symbol(self):
        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=[]):
            response = self.client.get('/api/stock/FAKE/history')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['error'], 'Stock not found in portfolio')

    def test_earnings_endpoint_returns_json_error_for_missing_symbol(self):
        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=[]):
            response = self.client.get('/api/stock/FAKE/earnings')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['error'], 'Stock not found in portfolio')

    def test_earnings_endpoint_returns_provider_data(self):
        stock_app.earnings_cache.clear()
        stock_app.earnings_cache_updated.clear()

        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=['AAPL']), \
             mock.patch.object(stock_app, 'get_provider') as get_provider_mock:
            get_provider_mock.return_value.get_earnings_calendar.return_value = {
                'next_earnings_date': '2026-08-13', 'days_until': 12,
                'eps_estimate': 1.89, 'eps_estimate_low': 1.82,
                'eps_estimate_high': 1.99, 'revenue_estimate': 108_959_817_930,
            }
            response = self.client.get('/api/stock/AAPL/earnings')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['next_earnings_date'], '2026-08-13')
        self.assertEqual(payload['days_until'], 12)

    def test_earnings_endpoint_caches_and_does_not_refetch(self):
        stock_app.earnings_cache.clear()
        stock_app.earnings_cache_updated.clear()

        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=['AAPL']), \
             mock.patch.object(stock_app, 'get_provider') as get_provider_mock:
            get_provider_mock.return_value.get_earnings_calendar.return_value = {
                'next_earnings_date': '2026-08-13', 'days_until': 12,
                'eps_estimate': None, 'eps_estimate_low': None,
                'eps_estimate_high': None, 'revenue_estimate': None,
            }
            self.client.get('/api/stock/AAPL/earnings')
            self.client.get('/api/stock/AAPL/earnings')

            self.assertEqual(get_provider_mock.return_value.get_earnings_calendar.call_count, 1)

    def test_earnings_endpoint_degrades_gracefully_on_provider_failure(self):
        stock_app.earnings_cache.clear()
        stock_app.earnings_cache_updated.clear()

        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=['AAPL']), \
             mock.patch.object(stock_app, 'get_provider') as get_provider_mock:
            get_provider_mock.return_value.get_earnings_calendar.side_effect = RuntimeError('scrape failed')
            response = self.client.get('/api/stock/AAPL/earnings')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(payload['next_earnings_date'])

    def test_put_options_route_404_when_symbol_not_in_portfolio(self):
        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=[]):
            response = self.client.get('/api/stock/FAKE/options')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['error'], 'Stock not found in portfolio')

    def test_put_options_route_returns_provider_candidates(self):
        stock_app.options_cache.clear()
        stock_app.options_cache_updated.clear()
        stock_app.fundamental_cache['AAPL'] = {'current_price': 200.0}

        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=['AAPL']), \
             mock.patch.object(stock_app, 'fetch_single_stock_data', return_value={'source': 'cache'}), \
             mock.patch.object(stock_app, 'get_provider') as get_provider_mock:
            get_provider_mock.return_value.get_put_options.return_value = [
                {'strike': 190.0, 'bid': 2.0, 'expiration': '2026-09-01'}
            ]
            response = self.client.get('/api/stock/AAPL/options')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['current_price'], 200.0)
        self.assertEqual(len(payload['candidates']), 1)
        self.assertEqual(payload['candidates'][0]['strike'], 190.0)
        get_provider_mock.return_value.get_put_options.assert_called_once_with('AAPL', current_price=200.0)

    def test_put_options_route_caches_and_does_not_refetch(self):
        stock_app.options_cache.clear()
        stock_app.options_cache_updated.clear()
        stock_app.fundamental_cache['AAPL'] = {'current_price': 200.0}

        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=['AAPL']), \
             mock.patch.object(stock_app, 'fetch_single_stock_data', return_value={'source': 'cache'}), \
             mock.patch.object(stock_app, 'get_provider') as get_provider_mock:
            get_provider_mock.return_value.get_put_options.return_value = []
            self.client.get('/api/stock/AAPL/options')
            self.client.get('/api/stock/AAPL/options')

            self.assertEqual(get_provider_mock.return_value.get_put_options.call_count, 1)

    def test_put_options_route_degrades_gracefully_on_provider_failure(self):
        stock_app.options_cache.clear()
        stock_app.options_cache_updated.clear()
        stock_app.fundamental_cache['AAPL'] = {'current_price': 200.0}

        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=['AAPL']), \
             mock.patch.object(stock_app, 'fetch_single_stock_data', return_value={'source': 'cache'}), \
             mock.patch.object(stock_app, 'get_provider') as get_provider_mock:
            get_provider_mock.return_value.get_put_options.side_effect = RuntimeError('chain unavailable')
            response = self.client.get('/api/stock/AAPL/options')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['candidates'], [])
        self.assertIn('chain unavailable', payload['error'])

    def test_put_options_route_skips_provider_call_without_current_price(self):
        stock_app.options_cache.clear()
        stock_app.options_cache_updated.clear()
        stock_app.fundamental_cache['AAPL'] = {}

        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=['AAPL']), \
             mock.patch.object(stock_app, 'fetch_single_stock_data', return_value={'source': 'cache'}), \
             mock.patch.object(stock_app, 'get_provider') as get_provider_mock:
            response = self.client.get('/api/stock/AAPL/options')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['candidates'], [])
        get_provider_mock.return_value.get_put_options.assert_not_called()

    def test_run_backtest_route_requires_symbol(self):
        response = self.client.post('/api/backtest/run', json={
            'start_date': '2025-01-01', 'end_date': '2025-06-01',
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['error'], 'Symbol is required')

    def test_run_backtest_route_requires_dates(self):
        response = self.client.post('/api/backtest/run', json={'symbol': 'AAPL'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('start_date', response.get_json()['error'])

    def test_run_backtest_route_returns_error_report_as_400(self):
        with mock.patch.object(stock_app, 'run_backtest', side_effect=ValueError('No price history available')):
            response = self.client.post('/api/backtest/run', json={
                'symbol': 'AAPL', 'start_date': '2025-01-01', 'end_date': '2025-06-01',
            })

        self.assertEqual(response.status_code, 400)
        self.assertIn('No price history', response.get_json()['error'])

    def test_run_backtest_route_persists_and_returns_report_with_id(self):
        fake_report = {
            'symbol': 'AAPL', 'profile': 'balanced', 'sector': 'Technology',
            'start_date': '2025-01-01', 'end_date': '2025-06-01',
            'holding_period_days': 20, 'step_days': 5,
            'fundamentals_available': False, 'steps_evaluated': 1,
            'steps_skipped_insufficient_forward_data': 0,
            'steps': [{'date': '2025-01-02', 'label': 'HOLD', 'total_score': 5.0, 'forward_return': 0.01}],
            'label_performance': {'HOLD': {'count': 1, 'avg_forward_return': 0.01, 'win_rate': 1.0}},
            'follow_signal_cumulative_return': 0.0,
            'buy_and_hold_cumulative_return': 0.01,
            'equity_curve': [{'date': '2025-01-02', 'follow_signal_cumulative': 1.0, 'buy_and_hold_cumulative': 1.01}],
        }

        with mock.patch.object(stock_app, 'run_backtest', return_value=fake_report):
            response = self.client.post('/api/backtest/run', json={
                'symbol': 'aapl', 'start_date': '2025-01-01', 'end_date': '2025-06-01',
            })

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertIn('id', payload)
        self.assertEqual(payload['symbol'], 'AAPL')

        # Persisted for real (not mocked) - should show up in the results list
        listing = self.client.get('/api/backtest/results').get_json()
        self.assertEqual(len(listing['results']), 1)
        self.assertEqual(listing['results'][0]['id'], payload['id'])

    def test_get_backtest_result_route_404_when_missing(self):
        response = self.client.get('/api/backtest/results/999')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['error'], 'Backtest result not found')

    def test_history_endpoint_returns_recorded_rows(self):
        import history
        history.record_recommendation('AAPL', 'balanced', {
            'label': 'BUY', 'total_score': 7.0,
            'components': {
                'sector_alignment': {'score': 8.0},
                'growth_quality': {'score': 6.0},
                'technical_momentum': {'score': 5.0},
            },
        })

        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=['AAPL']):
            response = self.client.get('/api/stock/AAPL/history')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload['history']), 1)
        self.assertEqual(payload['history'][0]['label'], 'BUY')

    def test_stock_endpoint_flags_rating_change(self):
        import history
        from datetime import date, timedelta
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        with mock.patch.object(history, '_today', return_value=yesterday):
            history.record_recommendation('AAPL', 'balanced', {
                'label': 'HOLD', 'total_score': 5.0,
                'components': {
                    'sector_alignment': {'score': 5.0},
                    'growth_quality': {'score': 5.0},
                    'technical_momentum': {'score': 5.0},
                },
            })

        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=['AAPL']), \
             mock.patch.object(stock_app, 'fetch_single_stock_data', return_value={
                 'source': 'live', 'cached': False, 'success': True,
                 'market_data_available': True, 'fundamental_data_available': True,
                 'message': 'Fetched fresh market data.'
             }), \
             mock.patch.object(stock_app, 'calculate_fundamental_analysis', return_value={
                 'sector_comparison': {'metrics': {}, 'sector_name': 'Technology'},
                 'growth_analysis': {'metrics': {}},
                 'financial_health': {},
                 'company_info': {}
             }), \
             mock.patch.object(stock_app, 'build_recommendation_scorecard', return_value={
                 'label': 'BUY', 'total_score': 7.5, 'max_score': 10,
                 'profile': {'key': 'balanced', 'label': 'Balanced', 'weights': {}},
                 'components': {
                     'sector_alignment': {'score': 8.0},
                     'growth_quality': {'score': 6.0},
                     'technical_momentum': {'score': 5.0},
                 },
             }):
            response = self.client.get('/api/stock/AAPL')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(payload['rating_change'])
        self.assertEqual(payload['rating_change']['previous_label'], 'HOLD')

    def test_portfolio_changes_route_empty_portfolio(self):
        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=[]):
            response = self.client.get('/api/portfolio/changes')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['checked'], [])
        self.assertEqual(payload['changes'], [])
        self.assertEqual(payload['errors'], [])

    def test_portfolio_changes_route_flags_changed_symbol(self):
        import history
        from datetime import date, timedelta
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        with mock.patch.object(history, '_today', return_value=yesterday):
            history.record_recommendation('AAPL', 'balanced', {
                'label': 'HOLD', 'total_score': 5.0,
                'components': {
                    'sector_alignment': {'score': 5.0},
                    'growth_quality': {'score': 5.0},
                    'technical_momentum': {'score': 5.0},
                },
            })

        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=['AAPL', 'MSFT']), \
             mock.patch.object(stock_app, 'fetch_single_stock_data', return_value={
                 'source': 'live', 'cached': False, 'success': True,
                 'market_data_available': True, 'fundamental_data_available': True,
                 'message': 'Fetched fresh market data.'
             }), \
             mock.patch.object(stock_app, 'calculate_fundamental_analysis', return_value={
                 'sector_comparison': {'metrics': {}, 'sector_name': 'Technology'},
                 'growth_analysis': {'metrics': {}}, 'financial_health': {}, 'company_info': {}
             }), \
             mock.patch.object(stock_app, 'build_recommendation_scorecard', return_value={
                 'label': 'BUY', 'total_score': 7.5, 'max_score': 10,
                 'profile': {'key': 'balanced', 'label': 'Balanced', 'weights': {}},
                 'components': {
                     'sector_alignment': {'score': 8.0},
                     'growth_quality': {'score': 6.0},
                     'technical_momentum': {'score': 5.0},
                 },
             }):
            response = self.client.get('/api/portfolio/changes')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['checked'], ['AAPL', 'MSFT'])
        # Both symbols score BUY with no prior history for MSFT, so only
        # AAPL (which had a prior HOLD row) surfaces as a change.
        self.assertEqual(len(payload['changes']), 1)
        self.assertEqual(payload['changes'][0]['symbol'], 'AAPL')
        self.assertEqual(payload['changes'][0]['previous_label'], 'HOLD')
        self.assertEqual(payload['changes'][0]['new_label'], 'BUY')
        self.assertEqual(payload['errors'], [])

    def test_portfolio_changes_route_isolates_per_symbol_failures(self):
        import history

        def fetch_side_effect(symbol):
            if symbol == 'BROKEN':
                raise RuntimeError('network unreachable')
            return {
                'source': 'live', 'cached': False, 'success': True,
                'market_data_available': True, 'fundamental_data_available': True,
                'message': 'Fetched fresh market data.'
            }

        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=['BROKEN', 'AAPL']), \
             mock.patch.object(stock_app, 'fetch_single_stock_data', side_effect=fetch_side_effect), \
             mock.patch.object(stock_app, 'calculate_fundamental_analysis', return_value={
                 'sector_comparison': {'metrics': {}, 'sector_name': 'Technology'},
                 'growth_analysis': {'metrics': {}}, 'financial_health': {}, 'company_info': {}
             }), \
             mock.patch.object(stock_app, 'build_recommendation_scorecard', return_value={
                 'label': 'HOLD', 'total_score': 5.0, 'max_score': 10,
                 'profile': {'key': 'balanced', 'label': 'Balanced', 'weights': {}},
                 'components': {
                     'sector_alignment': {'score': 5.0},
                     'growth_quality': {'score': 5.0},
                     'technical_momentum': {'score': 5.0},
                 },
             }):
            response = self.client.get('/api/portfolio/changes')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['checked'], ['BROKEN', 'AAPL'])
        self.assertEqual(len(payload['errors']), 1)
        self.assertEqual(payload['errors'][0]['symbol'], 'BROKEN')
        self.assertIn('network unreachable', payload['errors'][0]['message'])
        # AAPL still gets recorded despite BROKEN failing first.
        self.assertEqual(len(history.get_recommendation_history('AAPL', 'balanced')), 1)

    def test_add_holding_requires_valid_symbol(self):
        with mock.patch.object(stock_app, 'get_provider') as get_provider_mock:
            get_provider_mock.return_value.lookup_symbol.return_value = None
            response = self.client.post('/api/holdings/add', json={
                'symbol': 'FAKE', 'shares': 10, 'cost_basis_per_share': 100.0
            })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['error'], 'Invalid stock symbol')

    def test_add_holding_then_list_shows_it(self):
        with mock.patch.object(stock_app, 'get_provider') as get_provider_mock:
            get_provider_mock.return_value.lookup_symbol.return_value = {
                'symbol': 'AAPL', 'name': 'Apple Inc.'
            }
            add_response = self.client.post('/api/holdings/add', json={
                'symbol': 'AAPL', 'shares': 10, 'cost_basis_per_share': 150.0,
                'purchase_date': '2026-01-01'
            })

        self.assertEqual(add_response.status_code, 200)

        with mock.patch.object(stock_app, 'fetch_single_stock_data', return_value={
                 'source': 'live', 'cached': False, 'success': True,
                 'market_data_available': True, 'fundamental_data_available': True,
                 'message': 'Fetched fresh market data.'
             }), \
             mock.patch.object(stock_app, 'calculate_fundamental_analysis', return_value={
                 'sector_comparison': {'metrics': {}, 'sector_name': 'Technology'},
                 'growth_analysis': {'metrics': {}}, 'financial_health': {}, 'company_info': {}
             }), \
             mock.patch.object(stock_app, 'build_recommendation_scorecard', return_value={
                 'label': 'BUY', 'total_score': 7.0, 'max_score': 10,
                 'profile': {'key': 'balanced', 'label': 'Balanced', 'weights': {}},
                 'components': {
                     'sector_alignment': {'score': 8.0},
                     'growth_quality': {'score': 6.0},
                     'technical_momentum': {'score': 5.0},
                 },
             }):
            stock_app.fundamental_cache['AAPL'] = {'current_price': 180.0}
            list_response = self.client.get('/api/holdings')

        payload = list_response.get_json()
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(payload['holdings']), 1)
        holding = payload['holdings'][0]
        self.assertEqual(holding['symbol'], 'AAPL')
        self.assertEqual(holding['current_price'], 180.0)
        self.assertEqual(holding['market_value'], 1800.0)
        self.assertEqual(holding['unrealized_pnl'], 300.0)  # (180-150)*10
        self.assertEqual(holding['rating'], 'BUY')

    def test_remove_holding_not_found(self):
        response = self.client.post('/api/holdings/remove', json={'symbol': 'NOPE'})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()['error'], 'Holding not found')

    def test_add_then_remove_holding(self):
        with mock.patch.object(stock_app, 'get_provider') as get_provider_mock:
            get_provider_mock.return_value.lookup_symbol.return_value = {
                'symbol': 'AAPL', 'name': 'Apple Inc.'
            }
            self.client.post('/api/holdings/add', json={
                'symbol': 'AAPL', 'shares': 10, 'cost_basis_per_share': 150.0
            })

        remove_response = self.client.post('/api/holdings/remove', json={'symbol': 'AAPL'})

        self.assertEqual(remove_response.status_code, 200)
        self.assertIsNone(holdings_module.get_holding('AAPL'))

    def test_holdings_performance_route_with_no_holdings_needs_no_mocking(self):
        # compute_holdings_performance() short-circuits before touching the
        # provider when there are no eligible holdings, so this exercises the
        # real function end-to-end through the route with zero mocks.
        response = self.client.get('/api/holdings/performance')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['dates'], [])
        self.assertIsNone(payload['portfolio_return_pct'])
        self.assertEqual(payload['benchmark_symbol'], 'SPY')

    def test_holdings_performance_route_passes_benchmark_query_param(self):
        with mock.patch.object(stock_app, 'compute_holdings_performance') as compute_mock:
            compute_mock.return_value = {
                'dates': [], 'portfolio_value': [], 'benchmark_value': [],
                'total_invested': 0.0, 'current_value': 0.0,
                'portfolio_return_pct': None, 'benchmark_return_pct': None,
                'benchmark_symbol': 'QQQ', 'excluded_holdings': [],
            }
            response = self.client.get('/api/holdings/performance?benchmark=qqq')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(compute_mock.call_args.kwargs['benchmark_symbol'], 'qqq')

    def test_holdings_performance_route_returns_400_on_value_error(self):
        with mock.patch.object(stock_app, 'compute_holdings_performance',
                                side_effect=ValueError('No price history available for benchmark')):
            response = self.client.get('/api/holdings/performance?benchmark=NOPE')

        self.assertEqual(response.status_code, 400)
        self.assertIn('benchmark', response.get_json()['error'])

    def test_stock_endpoint_exposes_fetch_status(self):
        with mock.patch.object(stock_app, 'get_portfolio_stocks', return_value=['AAPL']), \
             mock.patch.object(stock_app, 'fetch_single_stock_data', return_value={
                 'source': 'live',
                 'cached': False,
                 'success': True,
                 'market_data_available': True,
                 'fundamental_data_available': True,
                 'message': 'Fetched fresh market data.'
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
             }):
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
                 'message': 'Fetched fresh market data.'
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
             }) as build_scorecard_mock:
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

        indicators_frame = stock_app.calculate_technical_indicators(stock_data)
        crossover_days = indicators_frame.index[indicators_frame['buy_ma']].tolist()

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
            original_cache_dir = storage.STOCK_CACHE_DIR
            storage.STOCK_CACHE_DIR = temp_dir

            try:
                cached_frame = pd.DataFrame({'Close': [1.0], 'High': [1.0], 'Low': [1.0]})
                storage.save_stock_snapshot('AAPL', cached_frame, {'sector': 'Technology'})

                with mock.patch.object(stock_app, 'run_with_retries', side_effect=RuntimeError('network down')):
                    fetch_meta = stock_app.fetch_single_stock_data('AAPL')

                self.assertEqual(fetch_meta['source'], 'disk-cache')
                self.assertTrue(fetch_meta['market_data_available'])
                self.assertEqual(stock_app.fundamental_cache['AAPL']['sector'], 'Technology')
                self.assertIn('snapshot_saved_at', fetch_meta)
                self.assertTrue(fetch_meta['freshness'])
            finally:
                storage.STOCK_CACHE_DIR = original_cache_dir

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
            'message': 'Fetched fresh market data.'
        }

        fetch_meta = stock_app.fetch_single_stock_data('AAPL')

        self.assertEqual(fetch_meta['source'], 'cache')
        self.assertTrue(fetch_meta['cached'])
        self.assertEqual(fetch_meta['message'], 'Using recently fetched in-memory cache.')

    def test_fetch_single_stock_data_treats_day_old_cache_as_stale(self):
        stock_app.data_cache['AAPL'] = pd.DataFrame({'Close': [1.0]})
        stock_app.fundamental_cache['AAPL'] = {'sector': 'Technology'}
        # 24h10m old: timedelta.seconds alone would wrongly report this fresh.
        stock_app.last_update['AAPL'] = datetime.now() - timedelta(days=1, minutes=10)

        with mock.patch.object(stock_app, 'run_with_retries', return_value=(pd.DataFrame({'Close': [2.0]}), {'sector': 'Technology'})), \
             mock.patch.object(stock_app, 'save_stock_snapshot'):
            fetch_meta = stock_app.fetch_single_stock_data('AAPL')

        self.assertEqual(fetch_meta['source'], 'live')

    def test_prune_stock_cache_removes_old_and_excess_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_cache_dir = storage.STOCK_CACHE_DIR
            storage.STOCK_CACHE_DIR = temp_dir

            try:
                now = datetime(2026, 5, 6, 12, 0, 0)
                for symbol in ['ONE', 'TWO', 'THREE']:
                    storage.save_stock_snapshot(symbol, pd.DataFrame({'Close': [1.0]}), {'sector': 'Technology'})

                old_file = storage.get_stock_cache_file('ONE')
                recent_file = storage.get_stock_cache_file('TWO')
                newest_file = storage.get_stock_cache_file('THREE')

                old_ts = (now - timedelta(days=40)).timestamp()
                recent_ts = (now - timedelta(days=1)).timestamp()
                newest_ts = now.timestamp()
                os.utime(old_file, (old_ts, old_ts))
                os.utime(recent_file, (recent_ts, recent_ts))
                os.utime(newest_file, (newest_ts, newest_ts))

                storage.prune_stock_cache(max_age_days=30, max_files=1, now=now)

                self.assertFalse(os.path.exists(old_file))
                self.assertFalse(os.path.exists(recent_file))
                self.assertTrue(os.path.exists(newest_file))
            finally:
                storage.STOCK_CACHE_DIR = original_cache_dir

    def test_save_sector_benchmarks_preserves_recent_refresh_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_benchmarks_file = benchmarks.SECTOR_BENCHMARKS_FILE
            benchmarks.SECTOR_BENCHMARKS_FILE = os.path.join(temp_dir, 'sector_benchmarks.json')

            try:
                self.assertTrue(benchmarks.save_sector_benchmarks({'Technology': {'pe_ratio_median': 20}}))
                self.assertTrue(benchmarks.save_sector_benchmarks({'Technology': {'pe_ratio_median': 22}}))

                saved_data = benchmarks.load_sector_benchmarks()
                history = saved_data['_metadata']['refresh_history']

                self.assertEqual(len(history), 2)
                self.assertEqual(history[0]['total_sectors'], 1)
                self.assertEqual(history[0]['calculation_method'], 'yahoo_finance_percentiles')
            finally:
                benchmarks.SECTOR_BENCHMARKS_FILE = original_benchmarks_file

    def test_benchmark_status_exposes_age_and_refresh_history(self):
        original_data = benchmarks.sector_benchmarks_data
        benchmarks.sector_benchmarks_data = {
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

        try:
            with mock.patch.object(stock_app.os.path, 'exists', return_value=True), \
                 mock.patch.object(stock_app.os, 'stat', return_value=mock.Mock(st_mtime=datetime(2026, 5, 6, 10, 5, 0).timestamp(), st_size=2048)), \
                 mock.patch.object(stock_app, 'get_sector_representative_stocks', return_value={'Technology': ['AAPL'], 'Healthcare': ['LLY']}), \
                 mock.patch.object(stock_app, 'describe_age', return_value='2 hour(s) old'):
                response = self.client.get('/api/benchmarks/status')
        finally:
            benchmarks.sector_benchmarks_data = original_data

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['benchmark_data']['last_calculated_age'], '2 hour(s) old')
        self.assertEqual(len(payload['benchmark_data']['refresh_history']), 1)


if __name__ == '__main__':
    unittest.main()
