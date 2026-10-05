"""Keep existing manual drafts usable when a timetable enables photo entry."""
import json
from pathlib import Path
from sqlalchemy import inspect
from app.models import db


def ensure_lecture_form_draft_entry_schema(engine=None):
    """Add policy provenance to existing tables without rewriting draft data.

    Existing drafts receive a legacy policy in one transaction. Later SQL/ORM
    inserts default to photo, and ordinary saves assign policy on the server.
    Legacy drafts with retained photo evidence never receive a manual grant.
    """
    engine = engine if engine is not None else db.engine
    if engine.dialect.name == 'sqlite':
        database = engine.url.database
        if not database or database == ':memory:' or not Path(database).is_file() or Path(database).stat().st_size == 0:
            return False  # An app factory must not open/create an empty database.
    with engine.connect() as connection:
        inspector = inspect(connection)
        if not inspector.has_table('lecture_form_drafts'):
            return False
        if 'entry_mode' in {column['name'] for column in inspector.get_columns('lecture_form_drafts')}:
            return False
        connection.commit()
        if connection.dialect.name == 'sqlite':
            connection.exec_driver_sql('BEGIN IMMEDIATE')
        else:
            connection.begin()
        try:
            # Another startup may have upgraded the table before this lock.
            columns = {column['name'] for column in inspect(connection).get_columns('lecture_form_drafts')}
            if 'entry_mode' not in columns:
                connection.exec_driver_sql("ALTER TABLE lecture_form_drafts ADD COLUMN entry_mode VARCHAR(16) NOT NULL DEFAULT 'photo'")
                connection.exec_driver_sql("UPDATE lecture_form_drafts SET entry_mode='legacy'")
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise


def manual_draft_entry_allowed(draft):
    if draft is None or draft.entry_mode not in ('manual', 'legacy'):
        return False
    try:
        payload = json.loads(draft.payload_json or '{}')
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and not payload.get('site_capture_id')
