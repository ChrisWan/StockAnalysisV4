"""Holdings persistence (SQLite, single local user).

A holding is a simple aggregate position per symbol: total shares plus a
weighted-average cost basis, not lot-level tracking. Adding more shares to
an existing symbol recomputes the average cost; the original purchase_date
is kept (it represents when the position was first opened).
"""

import logging
from datetime import datetime

import db

LOGGER = logging.getLogger(__name__)


def get_holdings():
    """Return all holdings, sorted by symbol."""
    connection = db.get_connection()
    try:
        rows = connection.execute(
            "SELECT * FROM holdings ORDER BY symbol"
        ).fetchall()
    finally:
        connection.close()

    return [dict(row) for row in rows]


def get_holding(symbol):
    """Return a single holding, or None if not held."""
    connection = db.get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM holdings WHERE symbol = ?", (symbol,)
        ).fetchone()
    finally:
        connection.close()

    return dict(row) if row else None


def upsert_holding(symbol, shares, cost_basis_per_share, purchase_date=None, notes=None):
    """Add a new holding or add shares to an existing one, recomputing the average cost."""
    if shares <= 0:
        raise ValueError('shares must be positive')
    if cost_basis_per_share <= 0:
        raise ValueError('cost_basis_per_share must be positive')

    connection = db.get_connection()
    try:
        existing = connection.execute(
            "SELECT * FROM holdings WHERE symbol = ?", (symbol,)
        ).fetchone()

        if existing is None:
            connection.execute(
                """
                INSERT INTO holdings
                    (symbol, shares, cost_basis_per_share, purchase_date, notes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (symbol, shares, cost_basis_per_share, purchase_date, notes,
                 datetime.now().isoformat()),
            )
        else:
            total_shares = existing['shares'] + shares
            weighted_cost = (
                existing['shares'] * existing['cost_basis_per_share'] +
                shares * cost_basis_per_share
            ) / total_shares
            connection.execute(
                """
                UPDATE holdings
                SET shares = ?, cost_basis_per_share = ?, notes = COALESCE(?, notes), updated_at = ?
                WHERE symbol = ?
                """,
                (total_shares, weighted_cost, notes, datetime.now().isoformat(), symbol),
            )
        connection.commit()
    finally:
        connection.close()

    return get_holding(symbol)


def remove_holding(symbol, shares=None):
    """Remove a holding entirely, or reduce it by `shares` if given.

    Returns True if a holding existed and was changed/removed, False if the
    symbol wasn't held at all.
    """
    connection = db.get_connection()
    try:
        existing = connection.execute(
            "SELECT * FROM holdings WHERE symbol = ?", (symbol,)
        ).fetchone()
        if existing is None:
            return False

        if shares is None or shares >= existing['shares']:
            connection.execute("DELETE FROM holdings WHERE symbol = ?", (symbol,))
        else:
            if shares <= 0:
                raise ValueError('shares must be positive')
            remaining = existing['shares'] - shares
            connection.execute(
                "UPDATE holdings SET shares = ?, updated_at = ? WHERE symbol = ?",
                (remaining, datetime.now().isoformat(), symbol),
            )
        connection.commit()
        return True
    finally:
        connection.close()
