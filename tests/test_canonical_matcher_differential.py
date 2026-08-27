# -*- coding: utf-8 -*-
"""Round 6B differential parity: legacy Excel schedule vs canonical snapshot."""
import datetime
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from openpyxl import Workbook

from app.app import app
from app.models import db
from app.services.review_schedule_source import snapshot_rows_to_legacy_df
from app.services.review_schedule_matcher import find_course_in_schedule
from app.services.review_reference_data import search_review_reference_data
from app.services.schedule_snapshots import (
    get_active_schedule_rows,
    persist_import_snapshot,
)
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


def sample_rows():
    return [
        {
            '姓名': '张三',
            '教师所属学院': '计算机学院',
            '课程名称': '数据结构',
            '星期几': '三',
            '上课节次': '第3-4节',
            '场地名称': '32-302',
            '教学班组成': '2023级计算机1班',
            '起始周': '1',
        },
        {
            '姓名': '李四',
            '教师所属学院': '外国语学院',
            '课程名称': '数据结构',
            '星期几': '四',
            '上课节次': '第1-2节',
            '场地名称': '10-101',
            '教学班组成': '2023级外语1班',
            '起始周': '2',
        },
        {
            '姓名': '王五',
            '教师所属学院': '物理学院',
            '课程名称': '大学物理',
            '星期几': '一',
            '上课节次': '第1-2节',
            '场地名称': '20-201',
            '教学班组成': '2023级物理1班',
            '起始周': '1',
        },
        {
            '姓名': '赵六',
            '教师所属学院': '荣昌学院',
            '课程名称': '荣昌课程',
            '星期几': '五',
            '上课节次': '3-4节',
            '场地名称': '荣昌1234',
            '教学班组成': '2023级荣昌1班',
            '起始周': '1',
        },
        {
            '姓名': '钱七',
            '教师所属学院': '数统学院',
            '课程名称': '数学分析',
            '星期几': '2',
            '上课节次': '第5-6节',
            '场地名称': '33-0201',
            '教学班组成': '2023级数学1班',
            '起始周': '3',
        },
    ]


def make_legacy_df():
    return pd.DataFrame(sample_rows(), columns=LEGACY_COLUMNS)


def make_source_df():
    data = []
    for row in sample_rows():
        item = dict(row)
        item['学期'] = SEMESTER
        item['学年'] = '2025'
        data.append(item)
    return pd.DataFrame(data)


class CanonicalMatcherDifferentialTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='matcher-diff-')
        self.db_path = Path(self.temp_dir.name) / 'matcher.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()

        # Build canonical snapshot from the same raw row data.
        persist_import_snapshot(
            make_source_df(),
            source_filename='canonical.xlsx',
            source_sha256='a' * 64,
        )
        self.legacy_df = make_legacy_df()
        self.canonical_df = snapshot_rows_to_legacy_df(
            get_active_schedule_rows(SEMESTER),
        )

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()
        self.temp_dir.cleanup()

    def _engine_with_df(self, df):
        engine = AutoReviewEngine(schedule_path=str(Path(self.temp_dir.name) / 'missing.xlsx'))
        engine.schedule_df = df
        return engine

    def _assert_matcher_equal(self, kwargs):
        legacy_engine = self._engine_with_df(self.legacy_df)
        canonical_engine = self._engine_with_df(self.canonical_df)
        legacy_best, legacy_all = legacy_engine._find_course_in_schedule(
            kwargs.get('teacher_name'),
            kwargs.get('teacher_college'),
            kwargs.get('course_title'),
            kwargs.get('class_composition'),
            kwargs.get('location'),
            kwargs.get('feedback_weekday_cn'),
            kwargs.get('feedback_class_period'),
        )
        canonical_best, canonical_all = canonical_engine._find_course_in_schedule(
            kwargs.get('teacher_name'),
            kwargs.get('teacher_college'),
            kwargs.get('course_title'),
            kwargs.get('class_composition'),
            kwargs.get('location'),
            kwargs.get('feedback_weekday_cn'),
            kwargs.get('feedback_class_period'),
        )
        self.assertEqual(legacy_best, canonical_best, kwargs)
        self.assertEqual(legacy_all, canonical_all, kwargs)

    def test_base_exact(self):
        self._assert_matcher_equal({
            'teacher_name': '张三',
            'course_title': '数据结构',
        })

    def test_teacher_fallback(self):
        self._assert_matcher_equal({
            'teacher_name': '张三',
            'course_title': '不存在的课程',
        })

    def test_course_fallback(self):
        self._assert_matcher_equal({
            'teacher_name': '不存在教师',
            'course_title': '数据结构',
        })

    def test_teacher_college_not_used_for_candidate_selection(self):
        self._assert_matcher_equal({
            'teacher_name': '张三',
            'teacher_college': '错误学院',
            'course_title': '数据结构',
        })

    def test_time_success_and_failure(self):
        self._assert_matcher_equal({
            'teacher_name': '张三',
            'course_title': '数据结构',
            'feedback_weekday_cn': '三',
            'feedback_class_period': '第3-4节',
        })
        self._assert_matcher_equal({
            'teacher_name': '张三',
            'course_title': '数据结构',
            'feedback_weekday_cn': '四',
            'feedback_class_period': '第9-10节',
        })

    def test_location_success_and_failure(self):
        self._assert_matcher_equal({
            'teacher_name': '张三',
            'course_title': '数据结构',
            'location': '32-302',
        })
        self._assert_matcher_equal({
            'teacher_name': '张三',
            'course_title': '数据结构',
            'location': '99-999',
        })

    def test_class_success_and_failure(self):
        self._assert_matcher_equal({
            'teacher_name': '张三',
            'course_title': '数据结构',
            'class_composition': '2023级计算机1班',
        })
        self._assert_matcher_equal({
            'teacher_name': '张三',
            'course_title': '数据结构',
            'class_composition': '不存在的班级',
        })

    def test_combined_suffix(self):
        self._assert_matcher_equal({
            'teacher_name': '张三',
            'teacher_college': '计算机学院',
            'course_title': '数据结构',
            'feedback_weekday_cn': '三',
            'feedback_class_period': '第3-4节',
            'location': '32-302',
            'class_composition': '2023级计算机1班',
        })

    def test_top_five(self):
        # Build 6 same-course rows on both sides.
        base = sample_rows()[0]
        many = []
        for i in range(1, 7):
            row = dict(base)
            row['姓名'] = f'教师{i}'
            row['教学班组成'] = f'2023级班级{i}'
            many.append(row)
        legacy_df = pd.DataFrame(many, columns=LEGACY_COLUMNS)
        source_df = pd.DataFrame([
            {**row, '学期': SEMESTER, '学年': '2025'}
            for row in many
        ])
        # Persist into a second semester to avoid overwriting the main one.
        persist_import_snapshot(source_df, 'many.xlsx', 'b' * 64)
        canonical_df = snapshot_rows_to_legacy_df(
            get_active_schedule_rows(SEMESTER),
        )
        legacy_engine = self._engine_with_df(legacy_df)
        canonical_engine = self._engine_with_df(canonical_df)
        _, legacy_all = legacy_engine._find_course_in_schedule(None, None, '数据结构', None, None, None, None)
        _, canonical_all = canonical_engine._find_course_in_schedule(None, None, '数据结构', None, None, None, None)
        self.assertEqual(legacy_all[:5], canonical_all[:5])
        self.assertEqual(
            [m['teacher_name'] for m in canonical_all[:5]],
            [f'教师{i}' for i in range(1, 6)],
        )

    def test_start_week_payload(self):
        self._assert_matcher_equal({
            'teacher_name': '张三',
            'course_title': '数据结构',
        })

    def test_normalization_variants(self):
        self._assert_matcher_equal({
            'teacher_name': '赵六',
            'course_title': '荣昌课程',
            'location': '荣昌1234',
        })
        self._assert_matcher_equal({
            'teacher_name': '钱七',
            'course_title': '数学分析',
            'location': '33-0201',
        })

    def test_student_grade_class_precedence_and_class_composition_fallback(self):
        self._assert_matcher_equal({
            'teacher_name': '张三',
            'course_title': '数据结构',
            'class_composition': '2023级计算机1班',
        })
        self._assert_matcher_equal({
            'teacher_name': '张三',
            'course_title': '数据结构',
            'class_composition': '不存在',
        })

    def test_empty_anchors(self):
        self._assert_matcher_equal({
            'feedback_weekday_cn': '三',
            'feedback_class_period': '第3-4节',
            'location': '32-302',
        })

    def test_search_reference_data_schedule_matches_identical(self):
        legacy_engine = self._engine_with_df(self.legacy_df)
        canonical_engine = self._engine_with_df(self.canonical_df)
        form_data = {
            'teacher_name': '张三',
            'course_title': '数据结构',
            'student_grade_class': '2023级计算机1班',
            'lecture_location': '32-302',
            'lecture_date': '2026/09/09星期三',
            'class_period': '第3-4节',
        }
        legacy_matches = legacy_engine.search_reference_data(form_data)['schedule_matches']
        canonical_matches = canonical_engine.search_reference_data(form_data)['schedule_matches']
        self.assertEqual(legacy_matches, canonical_matches)

    def test_scalar_anchor_fidelity_numeric_and_nan(self):
        import math
        data = [
            {
                '姓名': 123,
                '教师所属学院': '计算机学院',
                '课程名称': '数据结构',
                '星期几': '三',
                '上课节次': '第3-4节',
                '场地名称': '32-302',
                '教学班组成': '2023级计算机1班',
                '起始周': '1',
            },
            {
                '姓名': '张三',
                '教师所属学院': '计算机学院',
                '课程名称': 456,
                '星期几': '三',
                '上课节次': '第3-4节',
                '场地名称': '32-302',
                '教学班组成': '2023级计算机1班',
                '起始周': '1',
            },
            {
                '姓名': float('nan'),
                '教师所属学院': '计算机学院',
                '课程名称': '缺失课程',
                '星期几': '三',
                '上课节次': '第3-4节',
                '场地名称': '32-302',
                '教学班组成': '2023级计算机1班',
                '起始周': '1',
            },
            {
                '姓名': '王五',
                '教师所属学院': '计算机学院',
                '课程名称': float('nan'),
                '星期几': '三',
                '上课节次': '第3-4节',
                '场地名称': '32-302',
                '教学班组成': '2023级计算机1班',
                '起始周': '1',
            },
        ]
        legacy_df = pd.DataFrame(data, columns=LEGACY_COLUMNS)
        source_df = pd.DataFrame([
            {**row, '学期': SEMESTER, '学年': '2025'}
            for row in data
        ])
        persist_import_snapshot(source_df, 'scalar.xlsx', 'c' * 64)
        canonical_df = snapshot_rows_to_legacy_df(
            get_active_schedule_rows(SEMESTER),
        )

        queries = [
            {'teacher_name': '123', 'course_title': '数据结构'},
            {'teacher_name': '张三', 'course_title': '456'},
            {'teacher_name': 'nan', 'course_title': '缺失课程'},
            {'teacher_name': '王五', 'course_title': 'nan'},
            {'teacher_name': '123.0', 'course_title': '数据结构'},
            {'teacher_name': '张三', 'course_title': '123.0'},
        ]
        for form in queries:
            with self.subTest(form=form):
                legacy_engine = self._engine_with_df(legacy_df)
                canonical_engine = self._engine_with_df(canonical_df)
                _, legacy_all = legacy_engine._find_course_in_schedule(
                    form.get('teacher_name'),
                    None,
                    form.get('course_title'),
                    None,
                    None,
                    None,
                    None,
                )
                _, canonical_all = canonical_engine._find_course_in_schedule(
                    form.get('teacher_name'),
                    None,
                    form.get('course_title'),
                    None,
                    None,
                    None,
                    None,
                )
                self.assertEqual(legacy_all, canonical_all, form)

    def test_complete_excel_scalar_universe_differential(self):
        path = Path(self.temp_dir.name) / 'scalar_universe.xlsx'
        workbook = Workbook()
        ws = workbook.active
        headers = [
            '姓名', '教师所属学院', '课程名称', '星期几', '上课节次',
            '场地名称', '教学班组成', '起始周',
        ]
        ws.append(headers)
        base = {
            '教师所属学院': '计算机学院',
            '星期几': '三',
            '上课节次': '第3-4节',
            '场地名称': '32-302',
            '教学班组成': '2023级计算机1班',
            '起始周': '1',
        }
        rows = [
            ('张三', '数据结构'),
            (123, '数据结构'),
            (123.5, '数据结构'),
            (True, '数据结构'),
            (datetime.datetime(2026, 1, 1, 8, 30), '数据结构'),
            (datetime.time(10, 30), '数据结构'),
            (None, '缺失课程'),
            ('王五', 123),
            ('李四', True),
            ('赵六', datetime.datetime(2026, 2, 2, 9, 0)),
            ('钱七', datetime.time(11, 30)),
            ('孙八', None),
        ]
        for teacher, course in rows:
            ws.append([
                teacher, base['教师所属学院'], course, base['星期几'],
                base['上课节次'], base['场地名称'], base['教学班组成'], base['起始周'],
            ])
        workbook.save(path)

        legacy_df = pd.read_excel(path)
        source_df = legacy_df.copy()
        source_df['学期'] = SEMESTER
        source_df['学年'] = '2025'
        persist_import_snapshot(source_df, 'scalar_universe.xlsx', 'e' * 64)
        canonical_df = snapshot_rows_to_legacy_df(
            get_active_schedule_rows(SEMESTER),
        )

        queries = [
            {'teacher_name': '123', 'course_title': '数据结构'},
            {'teacher_name': '123.5', 'course_title': '数据结构'},
            {'teacher_name': 'True', 'course_title': '数据结构'},
            {'teacher_name': '2026-01-01 08:30:00', 'course_title': '数据结构'},
            {'teacher_name': '10:30:00', 'course_title': '数据结构'},
            {'teacher_name': 'nan', 'course_title': '缺失课程'},
            {'teacher_name': '王五', 'course_title': '123'},
            {'teacher_name': '李四', 'course_title': 'True'},
            {'teacher_name': '赵六', 'course_title': '2026-02-02 09:00:00'},
            {'teacher_name': '钱七', 'course_title': '11:30:00'},
            {'teacher_name': '孙八', 'course_title': 'nan'},
        ]
        for form in queries:
            with self.subTest(form=form):
                legacy_engine = self._engine_with_df(legacy_df)
                canonical_engine = self._engine_with_df(canonical_df)
                _, legacy_all = legacy_engine._find_course_in_schedule(
                    form.get('teacher_name'), None, form.get('course_title'),
                    None, None, None, None,
                )
                _, canonical_all = canonical_engine._find_course_in_schedule(
                    form.get('teacher_name'), None, form.get('course_title'),
                    None, None, None, None,
                )
                self.assertEqual(legacy_all, canonical_all, form)

    def test_nat_scalar_parity_from_xlsx(self):
        path = Path(self.temp_dir.name) / 'nat.xlsx'
        workbook = Workbook()
        ws = workbook.active
        ws.append([
            '姓名', '教师所属学院', '课程名称', '星期几', '上课节次',
            '场地名称', '教学班组成', '起始周',
        ])
        rows = [
            (datetime.datetime(2026, 1, 1, 8, 30), '数据结构'),
            (None, '数据结构'),
            (datetime.datetime(2026, 2, 2, 9, 0), '数据结构'),
        ]
        for teacher, course in rows:
            ws.append([
                teacher, '计算机学院', course, '三', '第3-4节',
                '32-302', '2023级计算机1班', '1',
            ])
        workbook.save(path)
        legacy_df = pd.read_excel(path)
        source_df = legacy_df.copy()
        source_df['学期'] = SEMESTER
        source_df['学年'] = '2025'
        persist_import_snapshot(source_df, 'nat.xlsx', 'f' * 64)
        canonical_df = snapshot_rows_to_legacy_df(
            get_active_schedule_rows(SEMESTER),
        )

        for form in [
            {'teacher_name': 'NaT', 'course_title': '数据结构'},
            {'teacher_name': '2026-01-01 08:30:00', 'course_title': '数据结构'},
        ]:
            with self.subTest(form=form):
                legacy_engine = self._engine_with_df(legacy_df)
                canonical_engine = self._engine_with_df(canonical_df)
                _, legacy_all = legacy_engine._find_course_in_schedule(
                    form.get('teacher_name'), None, form.get('course_title'),
                    None, None, None, None,
                )
                _, canonical_all = canonical_engine._find_course_in_schedule(
                    form.get('teacher_name'), None, form.get('course_title'),
                    None, None, None, None,
                )
                self.assertEqual(legacy_all, canonical_all, form)

    def test_search_reference_data_scalar_anchor_parity(self):
        import math
        data = [{
            '姓名': 123,
            '教师所属学院': '计算机学院',
            '课程名称': '数据结构',
            '星期几': '三',
            '上课节次': '第3-4节',
            '场地名称': '32-302',
            '教学班组成': '2023级计算机1班',
            '起始周': '1',
        }]
        legacy_df = pd.DataFrame(data, columns=LEGACY_COLUMNS)
        source_df = pd.DataFrame([
            {**data[0], '学期': SEMESTER, '学年': '2025'}
        ])
        persist_import_snapshot(source_df, 'scalar2.xlsx', 'd' * 64)
        canonical_df = snapshot_rows_to_legacy_df(
            get_active_schedule_rows(SEMESTER),
        )
        form = {'teacher_name': '123', 'course_title': '数据结构'}
        legacy_engine = self._engine_with_df(legacy_df)
        canonical_engine = self._engine_with_df(canonical_df)
        self.assertEqual(
            legacy_engine.search_reference_data(form)['schedule_matches'],
            canonical_engine.search_reference_data(form)['schedule_matches'],
        )

    def test_service_matcher_identical_to_autoreview_wrapper(self):
        engine = self._engine_with_df(self.legacy_df)
        cases = [
            ('张三', None, '数据结构', None, None, None, None),
            (None, None, '数据结构', None, None, None, None),
            ('不存在教师', None, '数据结构', None, None, None, None),
            ('张三', None, '数据结构', '2023级计算机1班', None, None, None),
        ]
        for case in cases:
            with self.subTest(case=case):
                direct = find_course_in_schedule(self.legacy_df, *case)
                wrapper = engine._find_course_in_schedule(*case)
                self.assertEqual(direct, wrapper)

    def test_reference_data_service_identical_to_autoreview_wrapper(self):
        engine = self._engine_with_df(self.legacy_df)
        form_data = {
            'teacher_name': '张三',
            'course_title': '数据结构',
            'student_grade_class': '2023级计算机1班',
            'lecture_location': '32-302',
            'lecture_date': '2026/09/09星期三',
            'class_period': '第3-4节',
        }
        direct = search_review_reference_data(
            form_data,
            schedule_df=self.legacy_df,
        )
        wrapper = engine.search_reference_data(form_data)
        self.assertEqual(direct, wrapper)

    def test_review_any_schedule_fields_identical(self):
        from types import SimpleNamespace
        legacy_engine = self._engine_with_df(self.legacy_df)
        canonical_engine = self._engine_with_df(self.canonical_df)
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
        legacy_result = legacy_engine.review_any(form)
        canonical_result = canonical_engine.review_any(form)
        self.assertEqual(legacy_result['passed'], canonical_result['passed'])
        self.assertEqual(legacy_result['issues'], canonical_result['issues'])
        self.assertEqual(legacy_result['fixes'], canonical_result['fixes'])
        self.assertEqual(legacy_result['course_info'], canonical_result['course_info'])


if __name__ == '__main__':
    unittest.main()
