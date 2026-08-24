# -*- coding: utf-8 -*-
"""Round 6A canonical schedule snapshot foundation tests."""
import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from sqlalchemy import inspect

from app.app import app
from app.models import (
    ScheduleImportBatch,
    ScheduleImportRow,
    SystemSetting,
    User,
    db,
)
from app.services.academic_term import get_current_teaching_semester
from app.services.schedule_snapshots import (
    get_active_schedule_batch,
    get_active_schedule_rows,
    persist_import_snapshot,
)
from app.utils.auto_review import AutoReviewEngine
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
    '学期': '2025-2026-1',
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
    'teacher_id': '教工号',
    'venue_id': '场地编号',
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
    return path


class ScheduleSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='schedule-snapshot-')
        self.db_path = Path(self.temp_dir.name) / 'snapshots.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()

        self.client = app.test_client()
        super_admin = User(
            number='SA1',
            department='办公部',
            name='超级管理员',
            gender='-',
            grade='-',
            college='Test',
            major='-',
            dormitory='-',
            phone='-',
            qq='-',
            student_id='SA1',
            password_hash='x',
            role='超级管理员',
            group='未分配小组',
            group_id=None,
            is_active=True,
        )
        db.session.add(super_admin)
        db.session.commit()
        with self.client.session_transaction() as sess:
            sess['user_id'] = super_admin.id
            sess['user_role'] = super_admin.role
            sess['user_name'] = super_admin.name

        self._old_template = os.environ.get('SCHEDULE_TEMPLATE_PATH')

    def tearDown(self):
        if self._old_template is None:
            os.environ.pop('SCHEDULE_TEMPLATE_PATH', None)
        else:
            os.environ['SCHEDULE_TEMPLATE_PATH'] = self._old_template
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()
        self.temp_dir.cleanup()

    def _workbook(self, rows, name='schedule.xlsx'):
        path = Path(self.temp_dir.name) / name
        write_workbook(path, rows)
        os.environ['SCHEDULE_TEMPLATE_PATH'] = str(path)
        return path

    def _import(self, path):
        with open(path, 'rb') as handle:
            return self.client.post(
                '/admin/api/schedule/import',
                data={'file': (handle, Path(path).name)},
                content_type='multipart/form-data',
            )

    def test_single_semester_import_creates_active_snapshot_and_preserves_raw_fields(self):
        path = self._workbook([
            full_row(),
            full_row(
                teacher_name='李四',
                teacher_college='外国语学院',
                course_name='英语',
                weekday=4.0,
                periods='第1-2节',
                venue='10-101',
                class_comp='2023级外语1班',
                start_week='2-9',
            ),
        ])
        response = self._import(path)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.get_json()['success'])

        batches = ScheduleImportBatch.query.order_by(ScheduleImportBatch.id).all()
        self.assertEqual(len(batches), 1)
        batch = batches[0]
        self.assertEqual(batch.status, 'active')
        self.assertEqual(batch.semester, '2025-2026-1')
        self.assertEqual(batch.row_count, 2)
        self.assertEqual(batch.source_filename, 'schedule.xlsx')
        self.assertEqual(len(batch.source_sha256), 64)
        self.assertEqual(get_active_schedule_batch('2025-2026-1').id, batch.id)

        rows = get_active_schedule_rows('2025-2026-1')
        self.assertEqual([row.source_row for row in rows], [2, 3])
        self.assertEqual(rows[0].teacher_name, '张三')
        self.assertEqual(rows[0].teacher_college, '计算机学院')
        self.assertEqual(rows[0].course_name, '数据结构')
        self.assertEqual(rows[0].weekday_raw, '3')
        self.assertEqual(rows[0].class_period_raw, '第3-4节')
        self.assertEqual(rows[0].location_raw, '32-302')
        self.assertEqual(rows[0].class_composition_raw, '2023级计算机1班')
        self.assertEqual(rows[0].start_week_raw, '1-16')
        # openpyxl/pandas normalize an integral numeric Excel cell to integer
        # text when read back; the snapshot must mirror that exact pandas view.
        self.assertEqual(rows[1].weekday_raw, '4')

    def test_second_import_same_semester_retires_previous_snapshot(self):
        path = self._workbook([full_row()])
        self._import(path)
        first = ScheduleImportBatch.query.one()

        second_path = self._workbook(
            [full_row(venue='99-999', course_name='新课程')],
            name='second.xlsx',
        )
        self._import(second_path)
        db.session.expire_all()

        first = db.session.get(ScheduleImportBatch, first.id)
        second = ScheduleImportBatch.query.order_by(ScheduleImportBatch.id.desc()).first()
        self.assertEqual(first.status, 'retired')
        self.assertEqual(second.status, 'active')
        self.assertEqual(
            ScheduleImportBatch.query.filter_by(
                semester='2025-2026-1', status='active',
            ).count(),
            1,
        )
        active = get_active_schedule_batch('2025-2026-1')
        self.assertEqual(active.id, second.id)
        self.assertEqual(len(get_active_schedule_rows('2025-2026-1')), 1)

    def test_different_semesters_keep_independent_active_snapshots(self):
        path_a = self._workbook([full_row(semester='2025-2026-1')], name='a.xlsx')
        self._import(path_a)
        path_b = self._workbook([full_row(semester='2025-2026-2')], name='b.xlsx')
        self._import(path_b)

        active_a = get_active_schedule_batch('2025-2026-1')
        active_b = get_active_schedule_batch('2025-2026-2')
        self.assertIsNotNone(active_a)
        self.assertIsNotNone(active_b)
        self.assertEqual(active_a.semester, '2025-2026-1')
        self.assertEqual(active_b.semester, '2025-2026-2')
        self.assertNotEqual(active_a.id, active_b.id)

    def test_multi_semester_workbook_creates_per_semester_batches(self):
        path = self._workbook([
            full_row(semester='2025-2026-1'),
            full_row(semester='2025-2026-2', teacher_name='李四'),
        ])
        response = self._import(path)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.get_json()['success'])

        batches = ScheduleImportBatch.query.order_by(ScheduleImportBatch.id).all()
        self.assertEqual(
            [(batch.semester, batch.row_count, batch.status) for batch in batches],
            [('2025-2026-1', 1, 'active'), ('2025-2026-2', 1, 'active')],
        )

    def test_blank_semester_rows_are_snapshotted_without_rejecting_import(self):
        path = self._workbook([full_row(semester='')])
        response = self._import(path)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.get_json()['success'])

        batch = ScheduleImportBatch.query.one()
        self.assertEqual(batch.semester, '')
        self.assertEqual(batch.row_count, 1)
        self.assertEqual(batch.status, 'active')

    def test_rollback_leaves_no_partial_snapshot(self):
        df = pd.DataFrame([full_row()], columns=HEADERS)
        persist_import_snapshot(df, source_filename='x.xlsx', source_sha256='a' * 64)
        self.assertGreater(ScheduleImportBatch.query.count(), 0)
        self.assertGreater(ScheduleImportRow.query.count(), 0)

        db.session.rollback()
        self.assertEqual(ScheduleImportBatch.query.count(), 0)
        self.assertEqual(ScheduleImportRow.query.count(), 0)

    def test_differential_fidelity_with_legacy_excel_path(self):
        path = self._workbook([
            full_row(
                teacher_name='张三',
                teacher_college='计算机学院',
                course_name='数据结构',
                weekday=3,
                periods='第3-4节',
                venue='32-302',
                class_comp='2023级计算机1班',
                start_week='1-16',
            ),
            full_row(
                teacher_name='李四',
                teacher_college='外国语学院',
                course_name='英语',
                weekday=4.0,
                periods='第1-2节',
                venue='10-101',
                class_comp='2023级外语1班',
                start_week='2-9',
            ),
        ])
        self._import(path)

        legacy_df = pd.read_excel(path)
        rows = get_active_schedule_rows('2025-2026-1')
        self.assertEqual(len(rows), len(legacy_df))

        mapping = {
            'teacher_name': '姓名',
            'teacher_college': '教师所属学院',
            'course_name': '课程名称',
            'weekday_raw': '星期几',
            'class_period_raw': '上课节次',
            'location_raw': '场地名称',
            'class_composition_raw': '教学班组成',
            'start_week_raw': '起始周',
        }
        for index, (_, legacy_row) in enumerate(legacy_df.iterrows()):
            snapshot_row = rows[index]
            for snapshot_field, excel_column in mapping.items():
                with self.subTest(snapshot_field=snapshot_field, index=index):
                    self.assertEqual(
                        getattr(snapshot_row, snapshot_field),
                        str(legacy_row[excel_column]),
                    )
            self.assertEqual(snapshot_row.source_row, index + 2)

        engine = AutoReviewEngine(schedule_path=str(path))
        self.assertIsNotNone(engine.schedule_df)
        self.assertEqual(str(engine.schedule_df.iloc[0]['姓名']), rows[0].teacher_name)
        self.assertEqual(str(engine.schedule_df.iloc[0]['起始周']), rows[0].start_week_raw)

    def test_row_level_teacher_college_and_venue_are_preserved_for_same_teacher(self):
        path = self._workbook([
            full_row(
                teacher_id='T001',
                teacher_name='张三',
                teacher_college='计算机学院',
                venue='32-302',
            ),
            full_row(
                teacher_id='T001',
                teacher_name='张三',
                teacher_college='外国语学院',
                venue='10-101',
            ),
        ])
        self._import(path)
        rows = get_active_schedule_rows('2025-2026-1')
        self.assertEqual([row.teacher_college for row in rows], ['计算机学院', '外国语学院'])
        self.assertEqual([row.location_raw for row in rows], ['32-302', '10-101'])
        self.assertEqual([row.teacher_name for row in rows], ['张三', '张三'])

    def test_whitespace_in_raw_fields_is_preserved(self):
        row = full_row()
        row[HEADERS.index('姓名')] = '  张三  '
        row[HEADERS.index('场地名称')] = '  32-302  '
        path = self._workbook([row])
        self._import(path)

        snapshot_row = get_active_schedule_rows('2025-2026-1')[0]
        self.assertEqual(snapshot_row.teacher_name, '  张三  ')
        self.assertEqual(snapshot_row.location_raw, '  32-302  ')

        legacy_df = pd.read_excel(path)
        self.assertEqual(str(legacy_df.iloc[0]['姓名']), '  张三  ')
        self.assertEqual(str(legacy_df.iloc[0]['场地名称']), '  32-302  ')

    def test_nan_cells_are_snapshotted_as_legacy_nan_text(self):
        row = full_row()
        row[HEADERS.index('姓名')] = None
        row[HEADERS.index('教师所属学院')] = None
        path = self._workbook([row])
        self._import(path)

        snapshot_row = get_active_schedule_rows('2025-2026-1')[0]
        self.assertEqual(snapshot_row.teacher_name, 'nan')
        self.assertEqual(snapshot_row.teacher_college, 'nan')

        legacy_df = pd.read_excel(path)
        self.assertEqual(str(legacy_df.iloc[0]['姓名']), 'nan')
        self.assertEqual(str(legacy_df.iloc[0]['教师所属学院']), 'nan')

    def test_top_five_source_order_is_reconstructable_from_snapshot(self):
        path = self._workbook([
            full_row(teacher_name=f'教师{i}', course_name='数据结构')
            for i in range(1, 7)
        ])
        self._import(path)
        rows = get_active_schedule_rows('2025-2026-1')
        self.assertEqual(
            [row.teacher_name for row in rows],
            ['教师1', '教师2', '教师3', '教师4', '教师5', '教师6'],
        )
        self.assertEqual([row.source_row for row in rows[:5]], [2, 3, 4, 5, 6])

    def test_current_semester_chain_selects_matching_active_batch(self):
        path_a = self._workbook([full_row(semester='2025-2026-1')], name='a.xlsx')
        self._import(path_a)
        path_b = self._workbook([full_row(semester='2025-2026-2', teacher_name='李四')], name='b.xlsx')
        self._import(path_b)

        SystemSetting.set('teaching_current_semester', '2025-2026-1')
        self.assertEqual(
            get_active_schedule_rows(get_current_teaching_semester())[0].teacher_name,
            '张三',
        )

        SystemSetting.set('teaching_current_semester', '2025-2026-2')
        self.assertEqual(
            get_active_schedule_rows(get_current_teaching_semester())[0].teacher_name,
            '李四',
        )

    def test_fresh_database_creates_schedule_snapshot_tables(self):
        inspector = inspect(db.engine)
        self.assertTrue(inspector.has_table('schedule_import_batches'))
        self.assertTrue(inspector.has_table('schedule_import_rows'))
        self.assertTrue(inspector.has_table('schedule_semester_selections'))
        self.assertTrue(inspector.has_table('schedule_import_row_scalar_meta'))


if __name__ == '__main__':
    unittest.main()
