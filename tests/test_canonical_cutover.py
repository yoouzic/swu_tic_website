# -*- coding: utf-8 -*-
"""Round 6B production source cutover tests for AutoReviewEngine."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pandas as pd
from openpyxl import Workbook

from app.app import app
from app.models import SystemSetting, db
from app.services.academic_term import get_current_teaching_semester
from app.services.schedule_snapshots import persist_import_snapshot
from app.utils.auto_review import AutoReviewEngine
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

SEMESTER = '2026-2027-1'

LEGACY_COLUMNS = [
    '姓名',
    '教师所属学院',
    '课程名称',
    '星期几',
    '上课节次',
    '场地名称',
    '教学班组成',
    '起始周',
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


def write_legacy_schedule(path):
    wb = Workbook()
    ws = wb.active
    ws.append(LEGACY_COLUMNS)
    ws.append(['李四', '外国语学院', '完全不同课程', '四', '第1-2节', '10-101', '2023级外语1班', '2'])
    wb.save(path)
    return path


class CanonicalCutoverTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='cutover-')
        self.db_path = Path(self.temp_dir.name) / 'cutover.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.legacy_path = write_legacy_schedule(Path(self.temp_dir.name) / 'legacy.xlsx')

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()
        self.temp_dir.cleanup()

    def _make_canonical_ready(self):
        persist_import_snapshot(
            canonical_source_df(),
            source_filename='canonical.xlsx',
            source_sha256='a' * 64,
        )
        SystemSetting.set('teaching_current_semester', SEMESTER)

    def test_canonical_ready_is_preferred_without_explicit_path(self):
        self._make_canonical_ready()
        engine = AutoReviewEngine()
        self.assertEqual(engine.schedule_source_kind, 'canonical_snapshot')
        self.assertEqual(str(engine.schedule_df.iloc[0]['姓名']), '张三')
        self.assertEqual(str(engine.schedule_df.iloc[0]['课程名称']), '数据结构')

    def test_explicit_legacy_path_still_wins_over_canonical(self):
        self._make_canonical_ready()
        engine = AutoReviewEngine(schedule_path=str(self.legacy_path))
        self.assertEqual(engine.schedule_source_kind, 'explicit_legacy')
        self.assertEqual(str(engine.schedule_df.iloc[0]['姓名']), '李四')
        self.assertEqual(str(engine.schedule_df.iloc[0]['课程名称']), '完全不同课程')

    def test_canonical_ready_does_not_read_legacy_excel(self):
        self._make_canonical_ready()
        with mock.patch('app.utils.auto_review._read_excel', return_value=None) as read_excel:
            engine = AutoReviewEngine()
        self.assertEqual(engine.schedule_source_kind, 'canonical_snapshot')
        read_excel.assert_not_called()

    def test_non_ready_falls_back_to_legacy_excel(self):
        SystemSetting.set('auto_review_schedule_path', str(self.legacy_path))
        engine = AutoReviewEngine()
        self.assertEqual(engine.schedule_source_kind, 'legacy_fallback')
        self.assertEqual(str(engine.schedule_df.iloc[0]['姓名']), '李四')

    def test_fresh_deploy_with_canonical_only_works(self):
        self._make_canonical_ready()
        engine = AutoReviewEngine()
        result = engine.search_reference_data({
            'teacher_name': '张三',
            'course_title': '数据结构',
        })
        self.assertGreater(len(result['schedule_matches']), 0)
        self.assertEqual(result['schedule_matches'][0]['teacher_name'], '张三')

        form = SimpleNamespace(
            listener_name='张三（计算机学院）',
            listener_number='S001',
            teacher_name='张三',
            teacher_college='计算机学院',
            course_title='数据结构',
            student_grade_class='2023级计算机1班',
            lecture_date='2026/09/09星期三',
            lecture_location='32-302',
            class_period='第3-4节',
            teaching_method='PPT演示法',
            classroom_discipline='好',
            classroom_atmosphere='好',
            courseware_quality='无',
            overall_effect='好',
            quality_case='推荐',
            course_feedback='该老师的课程反馈内容，内容足够长，用于测试评价规则。',
            suggestions='无',
            abnormal_situation='无',
            phone='13900000000',
        )
        review = engine.review_any(form)
        self.assertIsNotNone(review['course_info'])
        self.assertEqual(review['course_info']['teacher_name'], '张三')


if __name__ == '__main__':
    unittest.main()
