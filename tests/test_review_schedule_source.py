# -*- coding: utf-8 -*-
"""Round 6B canonical snapshot -> legacy shape adapter fidelity tests."""
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from openpyxl import Workbook

from app.app import app
from app.models import db
from app.services.review_schedule_source import (
    LEGACY_SCHEDULE_COLUMNS,
    current_canonical_legacy_df,
    snapshot_rows_to_legacy_df,
)
from app.services.schedule_snapshots import (
    get_active_schedule_rows,
    persist_import_snapshot,
)
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

HEADERS = [
    '教工号', '姓名', '性别', '职称名称', '教师所属学院', '教师联系电话',
    '场地编号', '场地名称', '场地类别名称', '校区', '楼层号', '教学楼', '座位数',
    '课程号', '选课课号', '起始周', '星期几', '上课节次', '课程名称',
    '场地上课起始周', '场地上课节次', '教学班人数', '教学班组成', '学分', '总学时',
    '开课学院', '专业组成', '选课人数', '周学时', '上课时间', '上课地点',
    '课程性质', '学期', '学年',
]

DEFAULT_ROW = {
    '教工号': 'T001',
    '姓名': '张三',
    '性别': '男',
    '职称名称': '教授',
    '教师所属学院': '计算机学院',
    '教师联系电话': '13900000000',
    '场地编号': 'V001',
    '场地名称': '32-302',
    '场地类别名称': '教学楼',
    '校区': '北碚',
    '楼层号': 1,
    '教学楼': 'A',
    '座位数': 100,
    '课程号': 'C001',
    '选课课号': 'S001',
    '起始周': '1-16',
    '星期几': 3,
    '上课节次': '第3-4节',
    '课程名称': '数据结构',
    '场地上课起始周': '1-16',
    '场地上课节次': '第3-4节',
    '教学班人数': 30,
    '教学班组成': '2023级计算机1班',
    '学分': 2,
    '总学时': 32,
    '开课学院': '计算机学院',
    '专业组成': '计算机',
    '选课人数': 25,
    '周学时': 2,
    '上课时间': '周三第3-4节',
    '上课地点': '32-302',
    '课程性质': '必修',
    '学期': '2026-2027-1',
    '学年': '2025',
}

FIELD_ALIASES = {
    'teacher_name': '姓名',
    'teacher_college': '教师所属学院',
    'course_name': '课程名称',
    'weekday': '星期几',
    'periods': '上课节次',
    'venue': '场地名称',
    'class_comp': '教学班组成',
    'start_week': '起始周',
    'semester': '学期',
    'academic_year': '学年',
}


def full_row(**overrides):
    data = dict(DEFAULT_ROW)
    for key, value in overrides.items():
        if key in FIELD_ALIASES:
            data[FIELD_ALIASES[key]] = value
        else:
            data[key] = value
    return [data[header] for header in HEADERS]


def write_workbook(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for row in rows:
        ws.append(row)
    wb.save(path)


class ReviewScheduleSourceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='review-source-')
        self.db_path = Path(self.temp_dir.name) / 'review-source.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()
        self.temp_dir.cleanup()

    def _snapshot_from_workbook(self, path):
        legacy_df = pd.read_excel(path)
        # Recreate the same DataFrame the admin import endpoint passes to the
        # snapshot service, including raw pandas cell values.
        batches = persist_import_snapshot(
            legacy_df,
            source_filename=path.name,
            source_sha256='a' * 64,
        )
        semester = batches[0].semester
        return legacy_df, get_active_schedule_rows(semester)

    def test_adapter_preserves_legacy_raw_cell_fidelity(self):
        path = Path(self.temp_dir.name) / 'fidelity.xlsx'
        rows = [
            full_row(),
            full_row(
                teacher_name='  李四  ',
                teacher_college='  外国语学院  ',
                course_name='  英语  ',
                weekday='3.0',
                periods=' 第1-2节 ',
                venue='  荣昌1234  ',
                class_comp='  2023级外语1班  ',
                start_week='  2-9  ',
            ),
            full_row(
                teacher_name=None,
                teacher_college=None,
                course_name='缺失课程',
                weekday=None,
                periods=None,
                venue=None,
            ),
        ]
        write_workbook(path, rows)
        legacy_df, snapshot_rows = self._snapshot_from_workbook(path)
        adapter_df = snapshot_rows_to_legacy_df(snapshot_rows)

        self.assertEqual(list(adapter_df.columns), LEGACY_SCHEDULE_COLUMNS)
        self.assertEqual(len(adapter_df), len(legacy_df))
        for index in range(len(legacy_df)):
            for column in LEGACY_SCHEDULE_COLUMNS:
                with self.subTest(index=index, column=column):
                    self.assertEqual(
                        str(adapter_df.iloc[index][column]),
                        str(legacy_df.iloc[index][column]),
                    )

        # Row order preserved.
        self.assertEqual(
            [str(value) for value in legacy_df['姓名']],
            [str(value) for value in adapter_df['姓名']],
        )

    def test_adapter_preserves_source_order_for_many_candidates(self):
        path = Path(self.temp_dir.name) / 'many.xlsx'
        rows = [
            full_row(teacher_name=f'教师{i}', course_name='数据结构')
            for i in range(1, 7)
        ]
        write_workbook(path, rows)
        legacy_df, snapshot_rows = self._snapshot_from_workbook(path)
        adapter_df = snapshot_rows_to_legacy_df(snapshot_rows)
        self.assertEqual(
            [str(value) for value in adapter_df['姓名']],
            [f'教师{i}' for i in range(1, 7)],
        )
        self.assertEqual([str(value) for value in legacy_df['姓名']], [f'教师{i}' for i in range(1, 7)])

    def test_current_canonical_legacy_df_returns_none_when_not_ready(self):
        self.assertIsNone(current_canonical_legacy_df())

    def test_current_canonical_legacy_df_returns_ready_snapshot(self):
        persist_import_snapshot(
            pd.DataFrame([full_row()], columns=HEADERS),
            'ready.xlsx',
            'a' * 64,
        )
        from app.services.academic_term import get_current_teaching_semester
        from app.models import SystemSetting
        SystemSetting.set('teaching_current_semester', get_current_teaching_semester() or DEFAULT_ROW['学期'])
        df = current_canonical_legacy_df()
        self.assertIsNotNone(df)
        self.assertEqual(df.iloc[0]['姓名'], '张三')


if __name__ == '__main__':
    unittest.main()
