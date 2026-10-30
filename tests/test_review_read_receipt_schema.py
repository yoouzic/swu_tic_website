"""Upgrade only initialized databases and preserve their existing records."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest import mock

from sqlalchemy import create_engine, event, inspect

from app.services.review_read_receipt_schema import ensure_review_read_receipt_schema


class ReviewReadReceiptSchemaTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='review-receipt-schema-')
        self.path = Path(self.directory.name) / 'existing.sqlite'
        self.engines = []

    def tearDown(self):
        for engine in self.engines:
            engine.dispose()
        self.directory.cleanup()

    def engine(self, path=None):
        engine = create_engine(f'sqlite:///{path or self.path}')
        self.engines.append(engine)
        return engine

    def initialized_database(self, tables=('users', 'lecture_forms')):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            for table in tables:
                connection.execute(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY, original TEXT NOT NULL)')
                connection.execute(f'INSERT INTO {table} VALUES (7, ?)', (f'preserve {table}',))

    def test_adds_receipt_table_without_changing_original_records(self):
        self.initialized_database(('users', 'lecture_forms', 'audit_records'))
        engine = self.engine()
        self.assertTrue(ensure_review_read_receipt_schema(engine))
        with engine.connect() as connection:
            for table in ('users', 'lecture_forms', 'audit_records'):
                self.assertEqual(connection.exec_driver_sql(f'SELECT * FROM {table}').all(),
                                 [(7, f'preserve {table}')])
            inspector = inspect(connection)
            self.assertEqual(set(inspector.get_pk_constraint('review_read_receipts')['constrained_columns']),
                             {'user_id', 'form_id'})
            self.assertEqual({foreign_key['referred_table'] for foreign_key in
                              inspector.get_foreign_keys('review_read_receipts')},
                             {'users', 'lecture_forms'})
            columns = {column['name']: column for column in inspector.get_columns('review_read_receipts')}
            self.assertTrue(columns['form_updated_at']['nullable'])
            self.assertFalse(columns['read_at']['nullable'])

    def test_app_factory_upgrades_an_existing_database(self):
        from app.app import create_app
        from app.models import db

        self.initialized_database()
        application = create_app({'TESTING': True,
                                  'SQLALCHEMY_DATABASE_URI': f'sqlite:///{self.path}'})
        with application.app_context():
            try:
                self.assertTrue(inspect(db.engine).has_table('review_read_receipts'))
                self.assertEqual(db.session.execute(db.text('SELECT * FROM lecture_forms')).all(),
                                 [(7, 'preserve lecture_forms')])
            finally:
                db.session.remove()
                db.engine.dispose()

    def test_existing_receipts_are_preserved_without_taking_a_write_lock(self):
        self.initialized_database()
        engine = self.engine()
        self.assertTrue(ensure_review_read_receipt_schema(engine))
        with engine.begin() as connection:
            connection.exec_driver_sql(
                'INSERT INTO review_read_receipts (user_id, form_id, form_updated_at, read_at) '
                'VALUES (7, 7, NULL, ?)', ('2026-10-16 12:00:00',))
        before = self.path.read_bytes()
        statements = []
        event.listen(engine, 'before_cursor_execute',
                     lambda conn, cursor, statement, params, context, many: statements.append(statement))
        self.assertFalse(ensure_review_read_receipt_schema(engine))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(any('BEGIN IMMEDIATE' in statement.upper() or
                             'CREATE TABLE' in statement.upper() for statement in statements))
        with engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql(
                'SELECT user_id, form_id, form_updated_at, read_at FROM review_read_receipts').all(),
                             [(7, 7, None, '2026-10-16 12:00:00')])

    def test_absent_database_is_not_created_or_opened(self):
        engine = self.engine()
        with mock.patch.object(engine, 'connect', side_effect=AssertionError('must not open absent DB')):
            self.assertFalse(ensure_review_read_receipt_schema(engine))
        self.assertFalse(self.path.exists())

    def test_zero_byte_database_is_not_opened_or_changed(self):
        self.path.touch()
        engine = self.engine()
        with mock.patch.object(engine, 'connect', side_effect=AssertionError('must not open empty DB')):
            self.assertFalse(ensure_review_read_receipt_schema(engine))
        self.assertEqual(self.path.read_bytes(), b'')

    def test_memory_database_is_not_opened(self):
        engine = create_engine('sqlite:///:memory:')
        self.engines.append(engine)
        with mock.patch.object(engine, 'connect', side_effect=AssertionError('must not open memory DB')):
            self.assertFalse(ensure_review_read_receipt_schema(engine))

    def test_partial_databases_are_not_migrated(self):
        for tables in [('users',), ('lecture_forms',), ('unrelated',)]:
            with self.subTest(tables=tables):
                self.path = Path(self.directory.name) / f'{tables[0]}.sqlite'
                self.initialized_database(tables)
                before = self.path.read_bytes()
                engine = self.engine()
                self.assertFalse(ensure_review_read_receipt_schema(engine))
                self.assertEqual(self.path.read_bytes(), before)
                with engine.connect() as connection:
                    self.assertFalse(inspect(connection).has_table('review_read_receipts'))

    def test_concurrent_startups_create_one_table_once(self):
        self.initialized_database()
        engines = (self.engine(), self.engine())
        barrier = Barrier(2)

        def upgrade(engine):
            barrier.wait(timeout=5)
            return ensure_review_read_receipt_schema(engine)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(upgrade, engines))
        self.assertEqual(sorted(results), [False, True])
        with engines[0].connect() as connection:
            self.assertTrue(inspect(connection).has_table('review_read_receipts'))

    def test_sqlite_ddl_is_rolled_back_when_creation_fails(self):
        from app.models import ReviewReadReceipt

        self.initialized_database()
        engine = self.engine()
        original_create = ReviewReadReceipt.__table__.create

        def fail_after_creation(*args, **kwargs):
            original_create(*args, **kwargs)
            raise RuntimeError('simulated interrupted migration')

        with mock.patch.object(ReviewReadReceipt.__table__, 'create', side_effect=fail_after_creation):
            with self.assertRaisesRegex(RuntimeError, 'simulated interrupted migration'):
                ensure_review_read_receipt_schema(engine)
        with engine.connect() as connection:
            self.assertFalse(inspect(connection).has_table('review_read_receipts'))
            self.assertEqual(connection.exec_driver_sql('SELECT * FROM lecture_forms').all(),
                             [(7, 'preserve lecture_forms')])


if __name__ == '__main__':
    unittest.main()
