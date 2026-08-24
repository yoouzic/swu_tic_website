# -*- coding: utf-8 -*-
"""Round 6C-R1 explicit schedule_df sentinel and source-freeze tests."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from app.app import app
from app.models import SystemSetting, db
from app.services.review_reference_data import search_review_reference_data
from app.services.schedule_snapshots import persist_import_snapshot
from app.utils.auto_review import AutoReviewEngine
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

SEMESTER = '2026-2027-1'

LEGACY_COLUMNS = [
    '姓名', '教师所属学院', '课程名称', '星期几', '上课节次',
    '场地名称', '教学班组成', '起始周',
]


def canonical_source_df():
    return pd.DataFrame([{
        '姓名': '张三',
        '教师所属学院': '计算机学院',
        '课程名称': '数据结构',
        '星期几': '三',
        '上课节次': '第3-4节',
        '场地名称': '32-302',
        '教学班组成': '2023级计算机1班',
        '起始周': '1',
        '学期': SEMESTER,
        '学年': '2025',
    }])


def make_legacy_df():
    return pd.DataFrame([{
        '姓名': '李四',
        '教师所属学院': '外国语学院',
        '课程名称': '完全不同课程',
        '星期几': '四',
        '上课节次': '第1-2节',
        '场地名称': '10-101',
        '教学班组成': '2023级外语1班',
        '起始周': '2',
    }], columns=LEGACY_COLUMNS)


class ReferenceSourceSemanticsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='ref-sem-')
        self.db_path = Path(self.temp_dir.name) / 'ref-sem.sqlite'
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

    def _make_canonical_ready(self):
        persist_import_snapshot(canonical_source_df(), 'canonical.xlsx', 'a' * 64)
        SystemSetting.set('teaching_current_semester', SEMESTER)

    def test_omitted_schedule_df_uses_global_ready_source(self):
        self._make_canonical_ready()
        result = search_review_reference_data({
            'teacher_name': '张三',
            'course_title': '数据结构',
        })
        self.assertEqual(len(result['schedule_matches']), 1)

    def test_explicit_none_does_not_reresolve_global_source(self):
        self._make_canonical_ready()
        result = search_review_reference_data(
            {'teacher_name': '张三', 'course_title': '数据结构'},
            schedule_df=None,
        )
        self.assertEqual(result['schedule_matches'], [])

    def test_explicit_empty_dataframe_is_used_directly(self):
        empty_df = pd.DataFrame(columns=LEGACY_COLUMNS)
        result = search_review_reference_data(
            {'teacher_name': '张三', 'course_title': '数据结构'},
            schedule_df=empty_df,
        )
        self.assertEqual(result['schedule_matches'], [])

    def test_engine_explicit_missing_path_does_not_reroute_to_canonical(self):
        self._make_canonical_ready()
        engine = AutoReviewEngine(schedule_path=str(Path(self.temp_dir.name) / 'missing.xlsx'))
        self.assertEqual(engine.schedule_source_kind, 'none')
        self.assertIsNone(engine.schedule_df)
        result = engine.search_reference_data({
            'teacher_name': '张三',
            'course_title': '数据结构',
        })
        self.assertEqual(result['schedule_matches'], [])

    def test_engine_explicit_missing_path_does_not_use_unset_legacy_fallback(self):
        make_legacy_df().to_excel(self.legacy_path, index=False)
        SystemSetting.set('auto_review_schedule_path', str(self.legacy_path))
        engine = AutoReviewEngine(schedule_path=str(Path(self.temp_dir.name) / 'missing.xlsx'))
        self.assertEqual(engine.schedule_source_kind, 'none')
        self.assertIsNone(engine.schedule_df)
        result = engine.search_reference_data({
            'teacher_name': '张三',
            'course_title': '数据结构',
        })
        self.assertEqual(result['schedule_matches'], [])

    def test_instance_source_frozen_after_construction(self):
        engine = AutoReviewEngine()
        self.assertIsNone(engine.schedule_df)

        self._make_canonical_ready()
        result = engine.search_reference_data({
            'teacher_name': '张三',
            'course_title': '数据结构',
        })
        self.assertEqual(result['schedule_matches'], [])

        new_engine = AutoReviewEngine()
        self.assertEqual(new_engine.schedule_source_kind, 'canonical_snapshot')
        result = new_engine.search_reference_data({
            'teacher_name': '张三',
            'course_title': '数据结构',
        })
        self.assertEqual(len(result['schedule_matches']), 1)

    def test_compatibility_wrapper_with_dataframe_uses_it(self):
        self._make_canonical_ready()
        engine = AutoReviewEngine(schedule_path=str(self.legacy_path))
        engine.schedule_df = make_legacy_df()
        result = engine.search_reference_data({
            'teacher_name': '李四',
            'course_title': '完全不同课程',
        })
        self.assertEqual(len(result['schedule_matches']), 1)
        self.assertEqual(result['schedule_matches'][0]['teacher_name'], '李四')


if __name__ == '__main__':
    unittest.main()
