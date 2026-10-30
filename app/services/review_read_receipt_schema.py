"""Add personal review receipts to an already initialized application database."""
from pathlib import Path

from sqlalchemy import inspect

from app.models import ReviewReadReceipt, db


def ensure_review_read_receipt_schema(engine=None):
    """Create the receipt table once, without opening empty SQLite databases.

    Ordinary startup only inspects the existing table. A missing table is
    created transactionally after checking the parent tables and acquiring
    SQLite's write lock, so simultaneous startups can safely race.
    """
    engine = engine if engine is not None else db.engine
    if engine.dialect.name == 'sqlite':
        database = engine.url.database
        if (not database or database == ':memory:' or
                not Path(database).is_file() or Path(database).stat().st_size == 0):
            return False

    with engine.connect() as connection:
        inspector = inspect(connection)
        if inspector.has_table('review_read_receipts'):
            return False
        if not all(inspector.has_table(table) for table in ('users', 'lecture_forms')):
            return False
        connection.commit()
        if connection.dialect.name == 'sqlite':
            connection.exec_driver_sql('BEGIN IMMEDIATE')
        else:
            connection.begin()
        try:
            # Another startup may have created it before we acquired the lock.
            inspector = inspect(connection)
            changed = (not inspector.has_table('review_read_receipts') and
                       all(inspector.has_table(table) for table in ('users', 'lecture_forms')))
            if changed:
                ReviewReadReceipt.__table__.create(connection, checkfirst=True)
            connection.commit()
            return changed
        except Exception:
            connection.rollback()
            raise
