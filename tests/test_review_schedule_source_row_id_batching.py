# -*- coding: utf-8 -*-
"""Large snapshots must not exceed SQLite's bind-variable limit."""
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from sqlalchemy import event

from app.app import app
from app.models import SystemSetting, db
from app.services.review_schedule_source import resolve_review_schedule_source
from app.services.schedule_snapshots import persist_import_snapshot
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

SEMESTER = '2026-2027-1'
ROW_COUNT = 1200
MAX_BIND_PARAMS = 500


def build_frame(count):
    rows = []
    for index in range(1, count + 1):
        rows.append({
            '学期': SEMESTER,
            '学年': '2026-2027',
            '姓名': f'教师{index}',
            '教师所属学院': '计算机学院',
            '课程名称': f'课程{index}',
            '星期几': 3,
            '上课节次': '第3-4节',
            '场地名称': '32-302',
            '教学班组成': '2023级计算机1班',
            '起始周': '1-16',
        })
    return pd.DataFrame(rows)


class LargeSnapshotQueryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='review-source-large-')
        self.db_path = Path(self.temp_dir.name) / 'review-source-large.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        persist_import_snapshot(
            build_frame(ROW_COUNT),
            source_filename='large.xlsx',
            source_sha256='b' * 64,
        )
        SystemSetting.set('teaching_current_semester', SEMESTER)

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()
        self.temp_dir.cleanup()

    def test_scalar_meta_queries_are_chunked(self):
        query_param_counts = []

        def capture_query(conn, cursor, statement, parameters, context, executemany):
            lowered = statement.lower()
            if 'schedule_import_row_scalar_meta' in lowered and ' in ' in lowered:
                query_param_counts.append(statement.count('?'))

        event.listen(db.engine, 'before_cursor_execute', capture_query)
        try:
            resolution = resolve_review_schedule_source(
                allow_unconfigured_legacy_fallback=False,
            )
        finally:
            event.remove(db.engine, 'before_cursor_execute', capture_query)

        self.assertEqual(resolution.kind, 'canonical_snapshot')
        self.assertEqual(len(resolution.dataframe), ROW_COUNT)
        self.assertEqual(resolution.dataframe.iloc[0]['姓名'], '教师1')
        self.assertEqual(resolution.dataframe.iloc[-1]['姓名'], f'教师{ROW_COUNT}')
        self.assertTrue(query_param_counts)
        self.assertLessEqual(max(query_param_counts), MAX_BIND_PARAMS)


if __name__ == '__main__':
    unittest.main()
