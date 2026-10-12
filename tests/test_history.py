import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

import db
import history


def _scorecard(label, total_score, sector=8.0, growth=6.0, technical=5.0):
    return {
        'label': label,
        'total_score': total_score,
        'components': {
            'sector_alignment': {'score': sector},
            'growth_quality': {'score': growth},
            'technical_momentum': {'score': technical},
        },
    }


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self._original_db_file = db.DB_FILE
        self._temp_dir = tempfile.TemporaryDirectory()
        db.DB_FILE = os.path.join(self._temp_dir.name, 'test.db')

    def tearDown(self):
        db.DB_FILE = self._original_db_file
        self._temp_dir.cleanup()

    def test_record_and_read_back(self):
        history.record_recommendation('AAPL', 'balanced', _scorecard('BUY', 7.5))

        rows = history.get_recommendation_history('AAPL', 'balanced')

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['label'], 'BUY')
        self.assertEqual(rows[0]['total_score'], 7.5)
        self.assertEqual(rows[0]['sector_score'], 8.0)

    def test_repeated_recording_same_day_updates_not_duplicates(self):
        history.record_recommendation('AAPL', 'balanced', _scorecard('HOLD', 5.0))
        history.record_recommendation('AAPL', 'balanced', _scorecard('BUY', 7.0))

        rows = history.get_recommendation_history('AAPL', 'balanced')

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['label'], 'BUY')

    def test_different_profiles_are_tracked_separately(self):
        history.record_recommendation('AAPL', 'balanced', _scorecard('HOLD', 5.0))
        history.record_recommendation('AAPL', 'growth', _scorecard('BUY', 7.5))

        balanced_rows = history.get_recommendation_history('AAPL', 'balanced')
        growth_rows = history.get_recommendation_history('AAPL', 'growth')

        self.assertEqual(balanced_rows[0]['label'], 'HOLD')
        self.assertEqual(growth_rows[0]['label'], 'BUY')

    def test_detect_rating_change_none_when_no_prior_row(self):
        result = history.detect_rating_change('AAPL', 'balanced', 'BUY')
        self.assertIsNone(result)

    def test_detect_rating_change_none_when_label_unchanged(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        with mock.patch.object(history, '_today', return_value=yesterday):
            history.record_recommendation('AAPL', 'balanced', _scorecard('BUY', 7.5))

        result = history.detect_rating_change('AAPL', 'balanced', 'BUY')
        self.assertIsNone(result)

    def test_detect_rating_change_flags_change_from_prior_day(self):
        three_days_ago = (date.today() - timedelta(days=3)).isoformat()
        with mock.patch.object(history, '_today', return_value=three_days_ago):
            history.record_recommendation('AAPL', 'balanced', _scorecard('HOLD', 5.0))

        result = history.detect_rating_change('AAPL', 'balanced', 'BUY')

        self.assertIsNotNone(result)
        self.assertEqual(result['previous_label'], 'HOLD')
        self.assertEqual(result['previous_recorded_date'], three_days_ago)
        self.assertEqual(result['days_since_previous_reading'], 3)

    def test_detect_rating_change_ignores_todays_own_row(self):
        # Recording today's value first must not make detect_rating_change
        # compare against itself (it only looks at rows before today).
        history.record_recommendation('AAPL', 'balanced', _scorecard('BUY', 7.5))

        result = history.detect_rating_change('AAPL', 'balanced', 'SELL')

        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
