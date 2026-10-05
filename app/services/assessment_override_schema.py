"""Preserve legacy rule rows while adding explicit semester scope.

SQLite cannot drop the old table UNIQUE constraint with ALTER TABLE. When
that constraint exists, a verified copy/swap runs inside BEGIN IMMEDIATE.
Unknown columns, inbound foreign keys or triggers abort rather than discard
data. No semester is inferred from dates or current administrator settings.
"""
from sqlalchemy import MetaData, inspect
from sqlalchemy.schema import CreateTable

from app.models import AssessmentOverride, db

TABLE = 'assessment_overrides'
LEGACY_UNIQUE = {'user_id', 'start_week', 'end_week', 'override_type'}


def ensure_assessment_override_schema(engine=None):
    engine = engine if engine is not None else db.engine
    with engine.connect() as conn:
        if conn.dialect.name == 'sqlite':
            # Explicit DBAPI transaction also covers SQLite DDL rollback.
            conn.exec_driver_sql('BEGIN IMMEDIATE')
        else:
            conn.begin()
        try:
            inspector = inspect(conn)
            if not inspector.has_table(TABLE):
                AssessmentOverride.__table__.create(conn)
            else:
                columns = [c['name'] for c in inspector.get_columns(TABLE)]
                old_unique = any(set(c['column_names']) == LEGACY_UNIQUE
                                 for c in inspector.get_unique_constraints(TABLE))
                if old_unique:
                    if conn.dialect.name != 'sqlite':
                        raise RuntimeError('Legacy assessment constraint requires an explicit database migration')
                    _migrate_legacy_sqlite_table(conn, inspector, columns)
                elif 'semester' not in columns:
                    conn.exec_driver_sql('ALTER TABLE assessment_overrides ADD COLUMN semester VARCHAR(50)')
                for index in AssessmentOverride.__table__.indexes:
                    index.create(conn, checkfirst=True)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _migrate_legacy_sqlite_table(conn, inspector, columns):
    model_columns = set(AssessmentOverride.__table__.columns.keys())
    if set(columns) - model_columns:
        raise RuntimeError('Unknown assessment columns require a reviewed migration')
    for table in inspector.get_table_names():
        if any(fk.get('referred_table') == TABLE for fk in inspector.get_foreign_keys(table)):
            raise RuntimeError('Inbound assessment foreign keys require a reviewed migration')
    triggers = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (TABLE,)).all()
    if triggers:
        raise RuntimeError('Assessment triggers require a reviewed migration')
    # Preserve any explicit legacy indexes as well as the new semester index.
    indexes = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (TABLE,)).scalars().all()
    temporary_name = TABLE + '__semester_migration'
    if inspector.has_table(temporary_name):
        raise RuntimeError('An assessment migration staging table already exists')
    metadata = MetaData()
    db.metadata.tables['users'].to_metadata(metadata)
    target = AssessmentOverride.__table__.to_metadata(metadata, name=temporary_name)
    conn.execute(CreateTable(target))
    quote = conn.dialect.identifier_preparer.quote
    column_sql = ', '.join(quote(name) for name in columns)
    conn.exec_driver_sql(f'INSERT INTO {quote(temporary_name)} ({column_sql}) SELECT {column_sql} FROM {quote(TABLE)}')
    # Every original field/ID must match before the table is swapped.
    for source, destination in [(TABLE, temporary_name), (temporary_name, TABLE)]:
        mismatch = conn.exec_driver_sql(
            f'SELECT {column_sql} FROM {quote(source)} EXCEPT SELECT {column_sql} FROM {quote(destination)}').first()
        if mismatch is not None:
            raise RuntimeError('Assessment migration verification failed')
    conn.exec_driver_sql(f'DROP TABLE {quote(TABLE)}')
    conn.exec_driver_sql(f'ALTER TABLE {quote(temporary_name)} RENAME TO {quote(TABLE)}')
    for statement in indexes:
        conn.exec_driver_sql(statement)
