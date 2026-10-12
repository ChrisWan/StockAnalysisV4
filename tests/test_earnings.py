import unittest
from datetime import date, timedelta
from unittest import mock

import data_providers


class ParseEarningsCalendarTests(unittest.TestCase):
    def test_single_confirmed_date(self):
        calendar = {
            'Earnings Date': [date(2026, 8, 13)],
            'Earnings Average': 1.89,
            'Earnings Low': 1.82,
            'Earnings High': 1.99,
            'Revenue Average': 108_959_817_930,
        }

        result = data_providers.parse_earnings_calendar(calendar, today=date(2026, 8, 1))

        self.assertEqual(result['next_earnings_date'], '2026-08-13')
        self.assertEqual(result['days_until'], 12)
        self.assertEqual(result['eps_estimate'], 1.89)
        self.assertEqual(result['eps_estimate_low'], 1.82)
        self.assertEqual(result['eps_estimate_high'], 1.99)
        self.assertEqual(result['revenue_estimate'], 108_959_817_930)

    def test_date_range_uses_earliest_date(self):
        calendar = {'Earnings Date': [date(2026, 10, 27), date(2026, 10, 31)]}

        result = data_providers.parse_earnings_calendar(calendar, today=date(2026, 8, 1))

        self.assertEqual(result['next_earnings_date'], '2026-10-27')

    def test_non_list_single_date_value(self):
        calendar = {'Earnings Date': date(2026, 9, 1)}

        result = data_providers.parse_earnings_calendar(calendar, today=date(2026, 8, 1))

        self.assertEqual(result['next_earnings_date'], '2026-09-01')

    def test_past_earnings_date_gives_negative_days_until(self):
        calendar = {'Earnings Date': [date(2026, 7, 20)]}

        result = data_providers.parse_earnings_calendar(calendar, today=date(2026, 8, 1))

        self.assertEqual(result['days_until'], -12)

    def test_missing_earnings_date_returns_empty_shape(self):
        result = data_providers.parse_earnings_calendar({'Dividend Date': date(2026, 5, 1)})

        self.assertIsNone(result['next_earnings_date'])
        self.assertIsNone(result['days_until'])
        self.assertIsNone(result['eps_estimate'])

    def test_empty_calendar_returns_empty_shape(self):
        self.assertIsNone(data_providers.parse_earnings_calendar({})['next_earnings_date'])
        self.assertIsNone(data_providers.parse_earnings_calendar(None)['next_earnings_date'])


class YahooProviderEarningsTests(unittest.TestCase):
    def test_get_earnings_calendar_delegates_to_yfinance_ticker_calendar(self):
        provider = data_providers.YahooFinanceProvider()
        fake_ticker = mock.Mock()
        fake_ticker.calendar = {'Earnings Date': [date(2026, 8, 13)], 'Earnings Average': 1.5}

        with mock.patch.object(data_providers.yf, 'Ticker', return_value=fake_ticker):
            result = provider.get_earnings_calendar('AAPL')

        self.assertEqual(result['next_earnings_date'], '2026-08-13')
        self.assertEqual(result['eps_estimate'], 1.5)


class YahooProviderPriceHistoryTests(unittest.TestCase):
    def test_get_price_history_flattens_multiindex_columns(self):
        # yf.download returns MultiIndex columns like ('Close', 'SPY') even
        # for a single symbol - regression test for a bug where callers
        # bypassing indicators.calculate_technical_indicators (which flattens
        # this defensively) got a Series back from a single-cell .loc lookup
        # instead of a scalar.
        import pandas as pd

        dates = pd.bdate_range('2026-01-02', periods=5)
        raw = pd.DataFrame(
            {
                ('Close', 'SPY'): [400.0, 401.0, 402.0, 403.0, 404.0],
                ('High', 'SPY'): [401.0, 402.0, 403.0, 404.0, 405.0],
                ('Low', 'SPY'): [399.0, 400.0, 401.0, 402.0, 403.0],
                ('Open', 'SPY'): [400.0, 400.5, 401.5, 402.5, 403.5],
                ('Volume', 'SPY'): [1000, 1000, 1000, 1000, 1000],
            },
            index=dates,
        )
        raw.columns = pd.MultiIndex.from_tuples(raw.columns)
        provider = data_providers.YahooFinanceProvider()

        with mock.patch.object(data_providers.yf, 'download', return_value=raw):
            result = provider.get_price_history('SPY', days=5)

        self.assertFalse(isinstance(result.columns, pd.MultiIndex))
        self.assertEqual(list(result.columns), ['Close', 'High', 'Low', 'Open', 'Volume'])
        # The real failure mode this guards against: a single-label .loc
        # lookup on a flat 'Close' column must return a scalar, not a Series.
        value = result.loc[dates[0], 'Close']
        self.assertIsInstance(value, float)
        self.assertEqual(value, 400.0)


class FakeOptionChain:
    def __init__(self, puts):
        self.puts = puts


class FakeOptionsTicker:
    def __init__(self, options, chains, info=None):
        self.options = options
        self._chains = chains
        self.info = info or {}

    def option_chain(self, expiration):
        return FakeOptionChain(self._chains[expiration])


class YahooProviderPutOptionsTests(unittest.TestCase):
    def test_filters_to_near_the_money_liquid_candidates(self):
        import pandas as pd

        near_expiration = (date.today() + timedelta(days=20)).isoformat()
        puts = pd.DataFrame([
            # Too far below current price (>10% discount) - excluded.
            {'strike': 80.0, 'bid': 0.50, 'ask': 0.60, 'lastPrice': 0.55, 'openInterest': 500, 'volume': 100,
             'impliedVolatility': 0.3, 'contractSymbol': 'FAR'},
            # Within 10% below current price, liquid - included.
            {'strike': 95.0, 'bid': 1.00, 'ask': 1.10, 'lastPrice': 0.95, 'openInterest': 50, 'volume': 20,
             'impliedVolatility': 0.28, 'contractSymbol': 'NEAR'},
            # Above current price (ITM) - excluded.
            {'strike': 105.0, 'bid': 6.00, 'ask': 6.20, 'lastPrice': 5.90, 'openInterest': 200, 'volume': 10,
             'impliedVolatility': 0.32, 'contractSymbol': 'ITM'},
            # Illiquid (open interest below threshold) - excluded.
            {'strike': 96.0, 'bid': 1.20, 'ask': 1.30, 'lastPrice': 1.15, 'openInterest': 2, 'volume': 1,
             'impliedVolatility': 0.29, 'contractSymbol': 'ILLIQUID'},
            # No real market at all (zero bid AND zero last trade) - excluded.
            {'strike': 97.0, 'bid': 0.0, 'ask': 0.10, 'lastPrice': 0.0, 'openInterest': 100, 'volume': 5,
             'impliedVolatility': 0.27, 'contractSymbol': 'NOBID'},
        ])
        ticker = FakeOptionsTicker(options=(near_expiration,), chains={near_expiration: puts})

        with mock.patch.object(data_providers.yf, 'Ticker', return_value=ticker):
            provider = data_providers.YahooFinanceProvider()
            candidates = provider.get_put_options('AAPL', current_price=100.0)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate['contract_symbol'], 'NEAR')
        self.assertEqual(candidate['strike'], 95.0)
        self.assertEqual(candidate['gross_premium'], 1.00)
        self.assertEqual(candidate['premium_source'], 'bid')
        # Net of the $1.00 IBKR commission (1 contract): (1.00*100 - 1.00)/100
        self.assertEqual(candidate['premium'], 0.99)
        self.assertTrue(candidate['commission_applicable'])
        self.assertEqual(candidate['commission'], 1.00)
        self.assertEqual(candidate['breakeven'], 94.01)  # 95.0 - 0.99
        self.assertEqual(candidate['discount_to_current_pct'], 5.0)  # (100-95)/100 * 100
        # annualized yield = 0.99 / 95.0 * 365 / 20 * 100
        expected_yield = round(0.99 / 95.0 * 365 / 20 * 100, 2)
        self.assertEqual(candidate['annualized_yield_pct'], expected_yield)

    def test_falls_back_to_last_price_when_bid_is_zero(self):
        # Yahoo's free options feed frequently reports bid/ask as 0 even for
        # liquid, actively-traded contracts (observed live for every AAPL
        # put across every expiration) while lastPrice - the most recent
        # actual trade - stays populated. A contract shouldn't be dropped
        # just because there's no live bid right now.
        import pandas as pd

        expiration = (date.today() + timedelta(days=20)).isoformat()
        puts = pd.DataFrame([
            {'strike': 95.0, 'bid': 0.0, 'ask': 0.0, 'lastPrice': 2.10, 'openInterest': 50, 'volume': 20,
             'impliedVolatility': 0.28, 'contractSymbol': 'STALE_QUOTE'},
        ])
        ticker = FakeOptionsTicker(options=(expiration,), chains={expiration: puts})

        with mock.patch.object(data_providers.yf, 'Ticker', return_value=ticker):
            provider = data_providers.YahooFinanceProvider()
            candidates = provider.get_put_options('AAPL', current_price=100.0)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate['gross_premium'], 2.10)
        self.assertEqual(candidate['premium_source'], 'last_trade')
        # Net of the $1.00 IBKR commission (1 contract): (2.10*100 - 1.00)/100
        self.assertEqual(candidate['premium'], 2.09)
        self.assertEqual(candidate['breakeven'], 92.91)  # 95.0 - 2.09

    def test_excludes_candidates_where_commission_eats_too_much_premium(self):
        # $0.02/share = $2.00/contract gross; the $1.00 commission is a 50%
        # drag, well over OPTIONS_MAX_COMMISSION_DRAG_PCT (25%) - not a
        # trade worth placing, so it should be dropped rather than shown
        # with a misleadingly small net premium.
        import pandas as pd

        expiration = (date.today() + timedelta(days=20)).isoformat()
        puts = pd.DataFrame([
            {'strike': 95.0, 'bid': 0.02, 'ask': 0.05, 'openInterest': 50, 'volume': 20,
             'impliedVolatility': 0.28, 'contractSymbol': 'THIN'},
        ])
        ticker = FakeOptionsTicker(options=(expiration,), chains={expiration: puts})

        with mock.patch.object(data_providers.yf, 'Ticker', return_value=ticker):
            provider = data_providers.YahooFinanceProvider()
            candidates = provider.get_put_options('AAPL', current_price=100.0)

        self.assertEqual(candidates, [])

    def test_non_usd_contracts_keep_gross_premium_uncommissioned(self):
        # IBKR's $0.65/$1.00 schedule is US-options-specific; a non-USD
        # chain (e.g. Eurex) needs a fee schedule that hasn't been
        # researched, so commission is not applied and the candidate is
        # never excluded by the drag filter regardless of how thin it is.
        import pandas as pd

        expiration = (date.today() + timedelta(days=20)).isoformat()
        puts = pd.DataFrame([
            {'strike': 95.0, 'bid': 0.02, 'ask': 0.05, 'openInterest': 50, 'volume': 20,
             'impliedVolatility': 0.28, 'contractSymbol': 'EUR_THIN', 'currency': 'EUR'},
        ])
        ticker = FakeOptionsTicker(options=(expiration,), chains={expiration: puts})

        with mock.patch.object(data_providers.yf, 'Ticker', return_value=ticker):
            provider = data_providers.YahooFinanceProvider()
            candidates = provider.get_put_options('AAPL', current_price=100.0)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate['gross_premium'], 0.02)
        self.assertEqual(candidate['premium'], 0.02)
        self.assertFalse(candidate['commission_applicable'])
        self.assertIsNone(candidate['commission'])
        self.assertIsNone(candidate['commission_drag_pct'])

    def test_excludes_expirations_beyond_max_days_to_expiry(self):
        import pandas as pd

        near_expiration = (date.today() + timedelta(days=10)).isoformat()
        far_expiration = (date.today() + timedelta(days=90)).isoformat()
        near_puts = pd.DataFrame([
            {'strike': 95.0, 'bid': 1.00, 'ask': 1.10, 'openInterest': 50, 'volume': 20,
             'impliedVolatility': 0.28, 'contractSymbol': 'NEAR'},
        ])
        far_puts = pd.DataFrame([
            {'strike': 95.0, 'bid': 2.00, 'ask': 2.10, 'openInterest': 50, 'volume': 20,
             'impliedVolatility': 0.28, 'contractSymbol': 'FAR'},
        ])
        ticker = FakeOptionsTicker(
            options=(near_expiration, far_expiration),
            chains={near_expiration: near_puts, far_expiration: far_puts},
        )

        with mock.patch.object(data_providers.yf, 'Ticker', return_value=ticker):
            provider = data_providers.YahooFinanceProvider()
            candidates = provider.get_put_options('AAPL', current_price=100.0)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['contract_symbol'], 'NEAR')

    def test_falls_back_to_ticker_info_when_current_price_not_supplied(self):
        import pandas as pd

        expiration = (date.today() + timedelta(days=15)).isoformat()
        puts = pd.DataFrame([
            {'strike': 95.0, 'bid': 1.00, 'ask': 1.10, 'openInterest': 50, 'volume': 20,
             'impliedVolatility': 0.28, 'contractSymbol': 'NEAR'},
        ])
        ticker = FakeOptionsTicker(
            options=(expiration,), chains={expiration: puts}, info={'currentPrice': 100.0}
        )

        with mock.patch.object(data_providers.yf, 'Ticker', return_value=ticker):
            provider = data_providers.YahooFinanceProvider()
            candidates = provider.get_put_options('AAPL')

        self.assertEqual(len(candidates), 1)

    def test_returns_empty_list_when_no_current_price_available(self):
        ticker = FakeOptionsTicker(options=(), chains={}, info={})

        with mock.patch.object(data_providers.yf, 'Ticker', return_value=ticker):
            provider = data_providers.YahooFinanceProvider()
            candidates = provider.get_put_options('AAPL')

        self.assertEqual(candidates, [])


class CompositeAndFallbackEarningsDelegationTests(unittest.TestCase):
    def test_composite_provider_delegates_to_market(self):
        market = mock.Mock()
        market.get_earnings_calendar.return_value = {'next_earnings_date': '2026-08-13'}
        edgar = mock.Mock()
        provider = data_providers.CompositeProvider(edgar, market)

        result = provider.get_earnings_calendar('AAPL')

        self.assertEqual(result['next_earnings_date'], '2026-08-13')
        market.get_earnings_calendar.assert_called_once_with('AAPL')

    def test_fallback_provider_falls_back_when_primary_lacks_method(self):
        primary = mock.Mock(spec=['name'])  # no get_earnings_calendar attribute
        primary.name = 'ibkr'
        secondary = mock.Mock()
        secondary.name = 'yahoo'
        secondary.get_earnings_calendar.return_value = {'next_earnings_date': '2026-08-13'}
        provider = data_providers.FallbackProvider(primary, secondary)

        result = provider.get_earnings_calendar('AAPL')

        self.assertEqual(result['next_earnings_date'], '2026-08-13')

    def test_composite_provider_delegates_put_options_to_market(self):
        market = mock.Mock()
        market.get_put_options.return_value = [{'strike': 95.0}]
        edgar = mock.Mock()
        provider = data_providers.CompositeProvider(edgar, market)

        result = provider.get_put_options('AAPL', current_price=100.0)

        self.assertEqual(result, [{'strike': 95.0}])
        market.get_put_options.assert_called_once_with('AAPL', current_price=100.0)


if __name__ == '__main__':
    unittest.main()
