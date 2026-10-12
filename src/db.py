"""SQLite storage for holdings, recommendation history, and backtest results.

Single-user, single-file database - no migration framework needed, schema
creation is idempotent (CREATE TABLE IF NOT EXISTS) and runs on every
connection open.
"""

import sqlite3

import config

# Module-level so tests can point it at a temporary file.
DB_FILE = config.DB_FILE

SCHEMA = """
CREATE TABLE IF NOT EXISTS holdings (
    symbol TEXT PRIMARY KEY,
    shares REAL NOT NULL,
    cost_basis_per_share REAL NOT NULL,
    purchase_date TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    recorded_date TEXT NOT NULL,
    label TEXT NOT NULL,
    total_score REAL NOT NULL,
    sector_score REAL,
    growth_score REAL,
    technical_score REAL,
    profile TEXT NOT NULL,
    UNIQUE(symbol, recorded_date, profile)
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    profile TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    holding_period_days INTEGER NOT NULL,
    run_at TEXT NOT NULL,
    summary_json TEXT NOT NULL
);
"""


def get_connection():
    """Open a connection to the database, creating the schema if needed."""
    connection = sqlite3.connect(DB_FILE)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection
