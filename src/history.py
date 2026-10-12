"""Recommendation history tracking and rating-change detection.

One row is kept per (symbol, day, profile) - repeated views on the same day
just refresh that row rather than accumulating duplicates. This turns a
point-in-time scorecard into a trend ("been HOLD for 3 weeks, drifting down")
and lets callers detect when today's label differs from the last recorded one.
"""

from datetime import date

import db


def _today():
    return date.today().isoformat()


def record_recommendation(symbol, profile, scorecard):
    """Upsert today's scorecard result for (symbol, profile)."""
    components = scorecard.get('components', {})
    connection = db.get_connection()
    try:
        connection.execute(
            """
            INSERT INTO recommendation_history
                (symbol, recorded_date, label, total_score,
                 sector_score, growth_score, technical_score, profile)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, recorded_date, profile) DO UPDATE SET
                label = excluded.label,
                total_score = excluded.total_score,
                sector_score = excluded.sector_score,
                growth_score = excluded.growth_score,
                technical_score = excluded.technical_score
            """,
            (
                symbol,
                _today(),
                scorecard.get('label'),
                scorecard.get('total_score'),
                components.get('sector_alignment', {}).get('score'),
                components.get('growth_quality', {}).get('score'),
                components.get('technical_momentum', {}).get('score'),
                profile,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def get_recommendation_history(symbol, profile, limit=30):
    """Return the most recent recorded rows for (symbol, profile), oldest first."""
    connection = db.get_connection()
    try:
        rows = connection.execute(
            """
            SELECT recorded_date, label, total_score, sector_score, growth_score, technical_score
            FROM recommendation_history
            WHERE symbol = ? AND profile = ?
            ORDER BY recorded_date DESC
            LIMIT ?
            """,
            (symbol, profile, limit),
        ).fetchall()
    finally:
        connection.close()

    return [dict(row) for row in reversed(rows)]


def detect_rating_change(symbol, profile, new_label):
    """Compare new_label to the most recent prior day's stored label.

    Returns None when there's no prior row or the label is unchanged.
    The exact day the rating changed isn't known - only that it was still
    `previous_label` as of `previous_recorded_date`, the last time it was
    checked before today.
    """
    connection = db.get_connection()
    try:
        row = connection.execute(
            """
            SELECT recorded_date, label
            FROM recommendation_history
            WHERE symbol = ? AND profile = ? AND recorded_date < ?
            ORDER BY recorded_date DESC
            LIMIT 1
            """,
            (symbol, profile, _today()),
        ).fetchone()
    finally:
        connection.close()

    if row is None or row['label'] == new_label:
        return None

    previous_recorded_date = date.fromisoformat(row['recorded_date'])
    return {
        'previous_label': row['label'],
        'previous_recorded_date': row['recorded_date'],
        'days_since_previous_reading': (date.today() - previous_recorded_date).days,
    }
