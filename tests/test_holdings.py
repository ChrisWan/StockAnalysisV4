import os
import tempfile
import unittest

import db
import holdings


class HoldingsTests(unittest.TestCase):
    def setUp(self):
        self._original_db_file = db.DB_FILE
        self._temp_dir = tempfile.TemporaryDirectory()
        db.DB_FILE = os.path.join(self._temp_dir.name, 'test.db')

    def tearDown(self):
        db.DB_FILE = self._original_db_file
        self._temp_dir.cleanup()

    def test_upsert_creates_new_holding(self):
        result = holdings.upsert_holding('AAPL', 10, 150.0, purchase_date='2026-01-01')

        self.assertEqual(result['shares'], 10)
        self.assertEqual(result['cost_basis_per_share'], 150.0)
        self.assertEqual(result['purchase_date'], '2026-01-01')

    def test_upsert_recomputes_weighted_average_cost(self):
        holdings.upsert_holding('AAPL', 10, 100.0, purchase_date='2026-01-01')
        result = holdings.upsert_holding('AAPL', 10, 200.0)

        # (10*100 + 10*200) / 20 = 150
        self.assertEqual(result['shares'], 20)
        self.assertEqual(result['cost_basis_per_share'], 150.0)

    def test_upsert_keeps_original_purchase_date_on_topup(self):
        holdings.upsert_holding('AAPL', 10, 100.0, purchase_date='2026-01-01')
        result = holdings.upsert_holding('AAPL', 5, 120.0, purchase_date='2026-06-01')

        self.assertEqual(result['purchase_date'], '2026-01-01')

    def test_upsert_rejects_non_positive_values(self):
        with self.assertRaises(ValueError):
            holdings.upsert_holding('AAPL', 0, 100.0)
        with self.assertRaises(ValueError):
            holdings.upsert_holding('AAPL', 10, 0)

    def test_get_holdings_sorted_by_symbol(self):
        holdings.upsert_holding('MSFT', 5, 300.0)
        holdings.upsert_holding('AAPL', 10, 150.0)

        rows = holdings.get_holdings()

        self.assertEqual([row['symbol'] for row in rows], ['AAPL', 'MSFT'])

    def test_remove_holding_full(self):
        holdings.upsert_holding('AAPL', 10, 150.0)

        removed = holdings.remove_holding('AAPL')

        self.assertTrue(removed)
        self.assertIsNone(holdings.get_holding('AAPL'))

    def test_remove_holding_partial(self):
        holdings.upsert_holding('AAPL', 10, 150.0)

        holdings.remove_holding('AAPL', shares=4)

        remaining = holdings.get_holding('AAPL')
        self.assertEqual(remaining['shares'], 6)
        self.assertEqual(remaining['cost_basis_per_share'], 150.0)

    def test_remove_holding_shares_exceeding_position_removes_entirely(self):
        holdings.upsert_holding('AAPL', 10, 150.0)

        holdings.remove_holding('AAPL', shares=999)

        self.assertIsNone(holdings.get_holding('AAPL'))

    def test_remove_holding_missing_symbol_returns_false(self):
        self.assertFalse(holdings.remove_holding('NOPE'))


if __name__ == '__main__':
    unittest.main()
