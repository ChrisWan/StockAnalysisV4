import unittest

import numpy as np
import pandas as pd

from charts import serialize_chart_data
from indicators import calculate_technical_indicators


class SerializeChartDataTests(unittest.TestCase):
    def _build_frame(self, periods=120):
        dates = pd.date_range('2026-01-01', periods=periods)
        close = pd.Series(100 + np.cumsum(np.random.default_rng(3).normal(0, 1, periods)), index=dates)
        return pd.DataFrame({'Open': close, 'High': close + 1, 'Low': close - 1, 'Close': close})

    def test_dates_and_ohlc_are_serialized(self):
        frame = self._build_frame()
        data = serialize_chart_data(frame)

        self.assertEqual(len(data['dates']), 120)
        self.assertEqual(data['dates'][0], frame.index[0].strftime('%Y-%m-%d'))
        self.assertEqual(len(data['close']), 120)
        self.assertEqual(data['close'][0], float(frame['Close'].iloc[0]))

    def test_indicator_columns_included_once_computed(self):
        frame = calculate_technical_indicators(self._build_frame())
        data = serialize_chart_data(frame)

        for key in ('ma_30', 'ma_90', 'stochastic_k', 'stochastic_d', 'macd', 'macd_signal',
                    'buy_signal', 'sell_signal'):
            self.assertIn(key, data)
            self.assertEqual(len(data[key]), 120)

        self.assertIsInstance(data['buy_signal'][0], bool)
        self.assertIsInstance(data['macd'][-1], float)

    def test_short_history_omits_indicator_columns_without_erroring(self):
        # calculate_technical_indicators only adds indicator columns for 30+ rows
        frame = calculate_technical_indicators(self._build_frame(periods=10))
        data = serialize_chart_data(frame)

        self.assertEqual(len(data['dates']), 10)
        self.assertNotIn('ma_30', data)
        self.assertNotIn('macd', data)

    def test_nan_values_become_json_null(self):
        frame = calculate_technical_indicators(self._build_frame())
        data = serialize_chart_data(frame)

        # 90_Moving_Avg requires 30 periods before it starts producing values
        self.assertIsNone(data['ma_90'][0])

    def test_nan_signal_values_become_false(self):
        frame = self._build_frame(periods=10)
        frame['Buy_Signal'] = [True, False, None, True, False, None, True, False, True, False]
        data = serialize_chart_data(frame)

        self.assertEqual(data['buy_signal'], [True, False, False, True, False, False, True, False, True, False])


if __name__ == '__main__':
    unittest.main()
