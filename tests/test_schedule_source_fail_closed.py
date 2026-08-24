# -*- coding: utf-8 -*-
"""Round 6B-R1 source-resolution fail-closed contract tests."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from app.app import app
from app.models import (
    ScheduleImportBatch,
    ScheduleSemesterSelection,
    SystemSetting,
    db,
)
from app.utils.auto_review import AutoReviewEngine
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

LEGACY_DF = pd.DataFrame([{
    '姓名': '李四',
    '教师所属学院': '外国语学院',
    '课程名称': '完全不同课程',
    '星期几': '四',
    '上课节次': '第1-2节',
    '场地名称': '10-101',
    '教学班组成': '2023级外语1班',
    '起始周': '2',
}])


class ScheduleSourceFailClosedTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='source-fail-')
        self.db_path = Path(self.temp_dir.name) / 'source.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.legacy_path = Path(self.temp_dir.name) / 'legacy.xlsx'

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()
        self.temp_dir.cleanup()

    def test_configured_no_snapshot_with_legacy_file_fails_closed(self):
        SystemSetting.set('teaching_current_semester', '2026-2027-1')
        SystemSetting.set('auto_review_schedule_path', str(self.legacy_path))
        with mock.patch('app.utils.auto_review._read_excel', return_value=LEGACY_DF) as read_excel:
            engine = AutoReviewEngine()
        self.assertEqual(engine.schedule_source_kind, 'none')
        self.assertIsNone(engine.schedule_df)
        read_excel.assert_not_called()

    def test_invalid_authority_with_legacy_file_fails_closed(self):
        other = ScheduleImportBatch(
            semester='OTHER', status='active', row_count=0,
            source_filename='other.xlsx', source_sha256='o' * 64,
        )
        db.session.add(other)
        db.session.flush()
        db.session.add(ScheduleSemesterSelection(
            semester='2026-2027-1',
            active_batch_id=other.id,
        ))
        db.session.commit()
        SystemSetting.set('teaching_current_semester', '2026-2027-1')
        SystemSetting.set('auto_review_schedule_path', str(self.legacy_path))

        with mock.patch('app.utils.auto_review._read_excel', return_value=LEGACY_DF) as read_excel:
            engine = AutoReviewEngine()
        self.assertEqual(engine.schedule_source_kind, 'none')
        self.assertIsNone(engine.schedule_df)
        read_excel.assert_not_called()

    def test_ambiguous_authority_with_legacy_file_fails_closed(self):
        b1 = ScheduleImportBatch(
            semester='2026-2027-1', status='active', row_count=0,
            source_filename='a.xlsx', source_sha256='a' * 64,
        )
        b2 = ScheduleImportBatch(
            semester='2026-2027-1', status='active', row_count=0,
            source_filename='b.xlsx', source_sha256='b' * 64,
        )
        db.session.add_all([b1, b2])
        db.session.commit()
        SystemSetting.set('teaching_current_semester', '2026-2027-1')
        SystemSetting.set('auto_review_schedule_path', str(self.legacy_path))

        with mock.patch('app.utils.auto_review._read_excel', return_value=LEGACY_DF) as read_excel:
            engine = AutoReviewEngine()
        self.assertEqual(engine.schedule_source_kind, 'none')
        self.assertIsNone(engine.schedule_df)
        read_excel.assert_not_called()

    def test_unset_semester_with_legacy_file_falls_back(self):
        SystemSetting.set('auto_review_schedule_path', str(self.legacy_path))
        with mock.patch('app.utils.auto_review._read_excel', return_value=LEGACY_DF) as read_excel:
            engine = AutoReviewEngine()
        self.assertEqual(engine.schedule_source_kind, 'legacy_fallback')
        self.assertIsNotNone(engine.schedule_df)
        read_excel.assert_called_once()

    def test_unset_semester_without_legacy_file_is_none(self):
        with mock.patch('app.utils.auto_review._read_excel', return_value=None) as read_excel:
            engine = AutoReviewEngine()
        self.assertEqual(engine.schedule_source_kind, 'none')
        self.assertIsNone(engine.schedule_df)
        read_excel.assert_called_once()

    def test_ready_canonical_still_preferred_with_legacy_file(self):
        from app.services.schedule_snapshots import persist_import_snapshot
        persist_import_snapshot(
            pd.DataFrame([{
                '姓名': '张三',
                '教师所属学院': '计算机学院',
                '课程名称': '数据结构',
                '星期几': '三',
                '上课节次': '第3-4节',
                '场地名称': '32-302',
                '教学班组成': '2023级计算机1班',
                '起始周': '1',
                '学期': '2026-2027-1',
                '学年': '2025',
            }]),
            'canonical.xlsx',
            'a' * 64,
        )
        SystemSetting.set('teaching_current_semester', '2026-2027-1')
        SystemSetting.set('auto_review_schedule_path', str(self.legacy_path))
        with mock.patch('app.utils.auto_review._read_excel', return_value=LEGACY_DF) as read_excel:
            engine = AutoReviewEngine()
        self.assertEqual(engine.schedule_source_kind, 'canonical_snapshot')
        self.assertEqual(str(engine.schedule_df.iloc[0]['姓名']), '张三')
        read_excel.assert_not_called()

    def test_explicit_schedule_path_remains_highest_priority(self):
        SystemSetting.set('teaching_current_semester', '2026-2027-1')
        SystemSetting.set('auto_review_schedule_path', str(self.legacy_path))
        with mock.patch('app.utils.auto_review._read_excel', return_value=LEGACY_DF) as read_excel:
            engine = AutoReviewEngine(schedule_path=str(self.legacy_path))
        self.assertEqual(engine.schedule_source_kind, 'explicit_legacy')
        self.assertIsNotNone(engine.schedule_df)
        read_excel.assert_called_once()


if __name__ == '__main__':
    unittest.main()
