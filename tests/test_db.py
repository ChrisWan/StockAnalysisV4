import os
import tempfile
import unittest

import db


class DbTests(unittest.TestCase):
    def setUp(self):
        self._original_db_file = db.DB_FILE
        self._temp_dir = tempfile.TemporaryDirectory()
        db.DB_FILE = os.path.join(self._temp_dir.name, 'test.db')

    def tearDown(self):
        db.DB_FILE = self._original_db_file
        self._temp_dir.cleanup()

    def test_schema_creation_is_idempotent(self):
        db.get_connection().close()
        db.get_connection().close()  # second open must not raise

        connection = db.get_connection()
        tables = {
            row['name'] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")
        }
        connection.close()
        self.assertTrue({'holdings', 'recommendation_history', 'backtest_results'} <= tables)

    def test_holdings_insert_and_query_roundtrip(self):
        connection = db.get_connection()
        connection.execute(
            "INSERT INTO holdings (symbol, shares, cost_basis_per_share, purchase_date, notes, updated_at) "
            "VALUES ('AAPL', 10, 150.0, '2026-01-01', NULL, '2026-01-01T00:00:00')")
        connection.commit()

        row = connection.execute("SELECT * FROM holdings WHERE symbol = 'AAPL'").fetchone()
        connection.close()

        self.assertEqual(row['shares'], 10)
        self.assertEqual(row['cost_basis_per_share'], 150.0)

    def test_recommendation_history_enforces_unique_symbol_date_profile(self):
        connection = db.get_connection()
        connection.execute(
            "INSERT INTO recommendation_history "
            "(symbol, recorded_date, label, total_score, profile) "
            "VALUES ('AAPL', '2026-01-01', 'BUY', 7.0, 'balanced')")
        connection.commit()

        with self.assertRaises(Exception):
            connection.execute(
                "INSERT INTO recommendation_history "
                "(symbol, recorded_date, label, total_score, profile) "
                "VALUES ('AAPL', '2026-01-01', 'HOLD', 5.0, 'balanced')")
            connection.commit()
        connection.close()


if __name__ == '__main__':
    unittest.main()
