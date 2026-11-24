import math
import unittest
from datetime import date
from unittest import mock

import pandas as pd

import data_providers


def _quarterly(row_name, values_by_date):
    """A yfinance-style quarterly statement: one row, newest quarter first."""
    columns = [pd.Timestamp(d) for d in values_by_date]
    return pd.DataFrame([list(values_by_date.values())], index=[row_name], columns=columns)


class YoyGrowthTests(unittest.TestCase):
    def test_compares_with_quarter_one_year_earlier_not_three_quarters(self):
        # AAPL's real shape: the 2025-12-31 holiday quarter sits three columns
        # back, so a positional iloc[3] lookup reported -20% instead of +11.7%.
        data = _quarterly('Total Revenue', {
            '2026-09-30': 114.0, '2026-06-30': 109.0, '2026-03-31': 111.0,
            '2025-12-31': 143.0, '2025-09-30': 102.0, '2025-06-30': math.nan,
        })

        growth = data_providers.calculate_yoy_growth(data, 'Total Revenue')

        self.assertAlmostEqual(growth, (114.0 - 102.0) / 102.0)

    def test_skips_leading_nan_quarter(self):
        data = _quarterly('Total Revenue', {
            '2026-09-30': math.nan, '2026-06-30': 40.0, '2026-03-31': 45.0,
            '2025-12-31': 39.0, '2025-09-30': 32.0, '2025-06-30': 34.0,
        })

        growth = data_providers.calculate_yoy_growth(data, 'Total Revenue')

        self.assertAlmostEqual(growth, (40.0 - 34.0) / 34.0)

    def test_returns_none_without_a_year_ago_quarter(self):
        data = _quarterly('Total Revenue', {
            '2026-09-30': 10.0, '2026-06-30': 9.0, '2026-03-31': 8.0, '2025-12-31': 7.0,
        })

        self.assertIsNone(data_providers.calculate_yoy_growth(data, 'Total Revenue'))


class RoeGrowthTests(unittest.TestCase):
    QUARTERS = ['2026-09-30', '2026-06-30', '2026-03-31', '2025-12-31',
                '2025-09-30', '2025-06-30', '2025-03-31', '2024-12-31']

    def _statements(self, incomes, equities):
        income = _quarterly('Net Income', dict(zip(self.QUARTERS, incomes)))
        equity = _quarterly('Stockholders Equity', dict(zip(self.QUARTERS, equities)))
        return income, equity

    def test_uses_non_overlapping_ttm_windows_and_year_ago_equity(self):
        income, equity = self._statements([10, 10, 10, 10, 5, 5, 5, 5], [100, 100, 100, 90, 80])

        growth = data_providers.calculate_roe_growth(income, equity)

        # Current ROE 40/100 = 0.4; year-ago ROE 20/80 = 0.25 -> +60%
        self.assertAlmostEqual(growth, 0.6)

    def test_annualizes_year_ago_quarter_when_eight_quarters_unavailable(self):
        income, equity = self._statements([10, 10, 10, 10, 4], [100, 100, 100, 90, 80])

        growth = data_providers.calculate_roe_growth(income, equity)

        # Current ROE 0.4; year-ago ROE (4*4)/80 = 0.2 -> +100%
        self.assertAlmostEqual(growth, 1.0)

    def test_returns_none_without_year_ago_quarter(self):
        income, equity = self._statements([10, 10, 10, 10], [100, 100, 100, 90])

        self.assertIsNone(data_providers.calculate_roe_growth(income, equity))


class YahooPriceHistoryAdjustmentTests(unittest.TestCase):
    def _download_frame(self):
        dates = pd.bdate_range('2026-01-02', periods=2)
        raw = pd.DataFrame({
            ('Adj Close', 'KO'): [49.0, 60.0],
            ('Close', 'KO'): [50.0, 60.0],
            ('High', 'KO'): [51.0, 61.0],
            ('Low', 'KO'): [49.0, 59.0],
            ('Open', 'KO'): [50.0, 60.0],
            ('Volume', 'KO'): [100, 100],
        }, index=dates)
        raw.columns = pd.MultiIndex.from_tuples(raw.columns)
        return dates, raw

    def test_close_is_total_return_and_split_close_keeps_price_level(self):
        dates, raw = self._download_frame()

        with mock.patch.object(data_providers.yf, 'download', return_value=raw):
            result = data_providers.YahooFinanceProvider().get_price_history('KO', days=2)

        first = result.loc[dates[0]]
        self.assertEqual(first['Close'], 49.0)
        self.assertEqual(first['Split Close'], 50.0)
        # OHLC scaled by the same dividend ratio (49/50), as auto_adjust=True did
        self.assertAlmostEqual(first['High'], 51.0 * 0.98)
        self.assertAlmostEqual(first['Low'], 49.0 * 0.98)
        self.assertAlmostEqual(first['Open'], 50.0 * 0.98)
        self.assertNotIn('Adj Close', result.columns)
        self.assertEqual(result.loc[dates[1], 'Close'], 60.0)

    def test_get_splits_returns_date_ratio_pairs(self):
        ticker = mock.Mock()
        ticker.splits = pd.Series(
            [1.5, 1.5],
            index=pd.DatetimeIndex(['2026-03-20', '2026-07-17']).tz_localize('America/New_York'))

        with mock.patch.object(data_providers.yf, 'Ticker', return_value=ticker):
            splits = data_providers.YahooFinanceProvider().get_splits('SNEX')

        self.assertEqual(splits, [(date(2026, 3, 20), 1.5), (date(2026, 7, 17), 1.5)])


if __name__ == '__main__':
    unittest.main()
