# -*- coding: utf-8 -*-
"""Tests for the batch-linked universal listening-assistant schedule index."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError

from app.app import app
from app.models import (
    Course,
    ListeningAssistantScheduleEntry,
    ScheduleImportRow,
    db,
)
from app.services.listening_assistant_schedule import (
    load_schedule_entries,
    ensure_listening_assistant_schema,
    persist_listening_assistant_entries,
)
from app.services.schedule_snapshots import (
    DEFAULT_SOURCE_ROW_OFFSET,
    persist_import_snapshot,
)
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


SEMESTER = '2026-2027-1'


def schedule_frame(*, semester=SEMESTER, include_optional_ids=True, teacher='张三'):
    row = {
        '姓名': teacher,
        '教师所属学院': '计算机学院',
        '课程名称': '数据结构',
        '星期几': 3,
        '上课节次': '第3-4节',
        '场地名称': '32-302',
        '教学班组成': '2023级 计算机1班',
        '起始周': '1-16',
        '场地上课起始周': '1-16',
        '场地上课节次': '第3-4节',
        '学期': semester,
        '学年': '2026',
    }
    if include_optional_ids:
        row.update({
            '课程号': 'C001',
            '选课课号': 'S001',
            '场地编号': 'V001',
        })
    return pd.DataFrame([row])


class ListeningAssistantScheduleTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='listening-assistant-schedule-')
        self.db_path = Path(self.temp_dir.name) / 'schedule.sqlite'
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

    def _persist(
        self,
        frame,
        filename='schedule.xlsx',
        source_row_offset=DEFAULT_SOURCE_ROW_OFFSET,
    ):
        batches = persist_import_snapshot(
            frame,
            source_filename=filename,
            source_sha256='a' * 64,
            source_row_offset=source_row_offset,
        )
        persist_listening_assistant_entries(
            frame,
            batches,
            source_row_offset=source_row_offset,
        )
        db.session.commit()
        return batches

    def test_model_table_contains_batch_provenance_and_search_fields(self):
        inspector = inspect(db.engine)
        self.assertTrue(inspector.has_table('listening_assistant_schedule_entries'))

        columns = {column['name'] for column in inspector.get_columns(
            'listening_assistant_schedule_entries'
        )}
        self.assertTrue({
            'id',
            'batch_id',
            'source_row',
            'semester',
            'academic_year',
            'semester_raw',
            'academic_year_raw',
            'course_code',
            'course_code_raw',
            'selection_code',
            'selection_code_raw',
            'teacher_name',
            'teacher_name_raw',
            'teacher_college',
            'teacher_college_raw',
            'course_title',
            'course_title_raw',
            'student_grade_class',
            'student_grade_class_raw',
            'venue_id',
            'venue_id_raw',
            'location_raw',
            'start_week_raw',
            'weekday_raw',
            'period_raw',
            'venue_start_week_raw',
            'venue_period_raw',
        } <= columns)

        unique_constraints = {
            constraint['name']
            for constraint in inspector.get_unique_constraints(
                'listening_assistant_schedule_entries'
            )
        }
        self.assertIn(
            'uq_listening_assistant_schedule_batch_source',
            unique_constraints,
        )

    def test_persist_accepts_optional_identifier_columns_and_is_idempotent(self):
        frame = schedule_frame(include_optional_ids=False)
        batches = self._persist(frame)

        entry = ListeningAssistantScheduleEntry.query.one()
        self.assertEqual(entry.batch_id, batches[0].id)
        self.assertEqual(entry.source_row, 2)
        self.assertIsNone(entry.course_code)
        self.assertIsNone(entry.selection_code)
        self.assertIsNone(entry.venue_id)
        self.assertEqual(entry.location_raw, '32-302')
        self.assertEqual(entry.venue_start_week_raw, '1-16')

        persist_listening_assistant_entries(frame, batches)
        db.session.commit()

        self.assertEqual(
            ListeningAssistantScheduleEntry.query.filter_by(
                batch_id=batches[0].id,
                source_row=2,
            ).count(),
            1,
        )

        loaded = load_schedule_entries(source_kind='primary', semester=SEMESTER)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].room, '32-302')
        self.assertEqual(loaded[0].period, (3, 4))
        self.assertEqual(loaded[0].student_grade_class, '2023级计算机1班')
        self.assertEqual(loaded[0].location_raw, '32-302')
        self.assertEqual(loaded[0].period_raw, '第3-4节')
        self.assertEqual(loaded[0].source_row, 2)

    def test_persist_retains_raw_identity_cells_alongside_normalized_fields(self):
        frame = schedule_frame()
        frame.loc[0, '学期'] = f' {SEMESTER} '
        frame.loc[0, '学年'] = ' 2026 '
        frame.loc[0, '课程号'] = ' C001 '
        frame.loc[0, '选课课号'] = ' S001 '
        frame.loc[0, '姓名'] = ' 张三 '
        frame.loc[0, '教师所属学院'] = ' 计算机学院 '
        frame.loc[0, '课程名称'] = ' 数据结构 '
        frame.loc[0, '场地编号'] = ' V001 '

        batches = self._persist(frame)
        entry = ListeningAssistantScheduleEntry.query.one()

        self.assertEqual(entry.semester_raw, f' {SEMESTER} ')
        self.assertEqual(entry.academic_year_raw, ' 2026 ')
        self.assertEqual(entry.course_code_raw, ' C001 ')
        self.assertEqual(entry.selection_code_raw, ' S001 ')
        self.assertEqual(entry.teacher_name_raw, ' 张三 ')
        self.assertEqual(entry.teacher_college_raw, ' 计算机学院 ')
        self.assertEqual(entry.course_title_raw, ' 数据结构 ')
        self.assertEqual(entry.venue_id_raw, ' V001 ')
        self.assertEqual(entry.student_grade_class_raw, '2023级 计算机1班')
        self.assertEqual(entry.course_code, 'C001')
        self.assertEqual(entry.teacher_name, '张三')
        self.assertEqual(entry.course_title, '数据结构')
        self.assertEqual(entry.venue_id, 'V001')
        self.assertEqual(entry.semester, SEMESTER)
        self.assertEqual(batches[0].semester, SEMESTER)

    def test_non_default_source_row_offset_matches_canonical_snapshot(self):
        source_row_offset = 11
        batches = self._persist(
            schedule_frame(),
            filename='offset.xlsx',
            source_row_offset=source_row_offset,
        )

        canonical_row = ScheduleImportRow.query.one()
        assistant_entry = ListeningAssistantScheduleEntry.query.one()
        self.assertEqual(canonical_row.batch_id, batches[0].id)
        self.assertEqual(canonical_row.source_row, source_row_offset)
        self.assertEqual(assistant_entry.batch_id, batches[0].id)
        self.assertEqual(assistant_entry.source_row, source_row_offset)

    def test_row_academic_year_overrides_batch_value_and_preserves_fallback(self):
        frame = pd.concat(
            [
                schedule_frame(),
                schedule_frame(teacher='李四'),
                schedule_frame(teacher='王五'),
            ],
            ignore_index=True,
        )
        frame.loc[0, '学年'] = 'batch-year'
        frame.loc[1, '学年'] = 'row-year'
        frame.loc[2, '学年'] = None

        batches = self._persist(frame, filename='academic-years.xlsx')
        entries = ListeningAssistantScheduleEntry.query.order_by(
            ListeningAssistantScheduleEntry.source_row.asc(),
        ).all()

        self.assertEqual(batches[0].academic_year, 'batch-year')
        self.assertEqual(entries[0].academic_year, 'batch-year')
        self.assertEqual(entries[0].academic_year_raw, 'batch-year')
        self.assertEqual(entries[1].academic_year, 'row-year')
        self.assertEqual(entries[1].academic_year_raw, 'row-year')
        self.assertEqual(entries[2].academic_year, 'batch-year')
        self.assertIsNone(entries[2].academic_year_raw)

    def test_primary_loader_follows_only_authoritative_current_batch(self):
        first_batch = self._persist(
            schedule_frame(teacher='旧教师'),
            filename='old.xlsx',
        )[0]
        current_batch = self._persist(
            schedule_frame(teacher='当前教师'),
            filename='current.xlsx',
        )[0]

        entries = load_schedule_entries(source_kind='primary', semester=SEMESTER)

        self.assertEqual({entry.source_batch_id for entry in entries}, {str(current_batch.id)})
        self.assertEqual({entry.teacher_name for entry in entries}, {'当前教师'})
        self.assertNotEqual(first_batch.id, current_batch.id)

    def test_backup_loader_requires_explicit_retired_batch_and_never_merges_history(self):
        first_batch = self._persist(
            schedule_frame(teacher='第一历史批次'),
            filename='first.xlsx',
        )[0]
        second_batch = self._persist(
            schedule_frame(teacher='第二历史批次'),
            filename='second.xlsx',
        )[0]
        current_batch = self._persist(
            schedule_frame(teacher='当前批次'),
            filename='current.xlsx',
        )[0]

        with self.assertRaises(ValueError):
            load_schedule_entries(source_kind='backup', semester=SEMESTER)

        backup = load_schedule_entries(
            source_kind='backup',
            semester=SEMESTER,
            source_batch_id=first_batch.id,
        )
        self.assertEqual({entry.source_batch_id for entry in backup}, {str(first_batch.id)})
        self.assertEqual({entry.teacher_name for entry in backup}, {'第一历史批次'})
        self.assertNotIn(str(second_batch.id), {entry.source_batch_id for entry in backup})
        self.assertNotIn(str(current_batch.id), {entry.source_batch_id for entry in backup})

        with self.assertRaises(ValueError):
            load_schedule_entries(
                source_kind='backup',
                semester=SEMESTER,
                source_batch_id=current_batch.id,
            )

    def test_loader_never_falls_back_to_legacy_course_rows(self):
        db.session.add(Course(
            course_code='LEGACY-CODE',
            selection_code='LEGACY-SELECTION',
            course_name='Legacy only course',
            class_location='32-302',
            semester=SEMESTER,
        ))
        db.session.commit()

        self.assertEqual(
            load_schedule_entries(source_kind='primary', semester=SEMESTER),
            [],
        )

    def test_schema_command_creates_only_assistant_table_and_is_idempotent(self):
        ListeningAssistantScheduleEntry.__table__.drop(
            bind=db.engine,
            checkfirst=True,
        )

        runner = app.test_cli_runner()
        first = runner.invoke(args=['listening-assistant', 'init-schema'])
        second = runner.invoke(args=['listening-assistant', 'init-schema'])

        self.assertEqual(first.exit_code, 0, first.output)
        self.assertEqual(second.exit_code, 0, second.output)
        db.session.rollback()
        inspector = inspect(db.engine)
        self.assertTrue(inspector.has_table('listening_assistant_schedule_entries'))

    def test_schema_command_fails_actionably_without_canonical_parent_table(self):
        db.drop_all()

        result = app.test_cli_runner().invoke(
            args=['listening-assistant', 'init-schema'],
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIsNotNone(result.exception)
        self.assertEqual(result.exception.code, 1)
        self.assertIn('Error:', result.output)
        self.assertIn('schedule_import_batches', result.output)
        self.assertFalse(inspect(db.engine).has_table(
            'listening_assistant_schedule_entries',
        ))

    def test_import_auto_ensures_assistant_table_when_canonical_schema_exists(self):
        ListeningAssistantScheduleEntry.__table__.drop(
            bind=db.engine,
            checkfirst=True,
        )
        self.assertFalse(inspect(db.engine).has_table(
            'listening_assistant_schedule_entries',
        ))

        ensure_listening_assistant_schema()
        batches = persist_import_snapshot(
            schedule_frame(),
            source_filename='auto-ensure.xlsx',
            source_sha256='b' * 64,
        )
        persist_listening_assistant_entries(schedule_frame(), batches)
        db.session.commit()

        self.assertTrue(inspect(db.engine).has_table(
            'listening_assistant_schedule_entries',
        ))
        self.assertEqual(ListeningAssistantScheduleEntry.query.count(), 1)

    def test_import_auto_ensures_with_flushed_snapshot_rows_in_same_session(self):
        ListeningAssistantScheduleEntry.__table__.drop(
            bind=db.engine,
            checkfirst=True,
        )
        frame = schedule_frame()
        batches = persist_import_snapshot(
            frame,
            source_filename='flushed-before-index.xlsx',
            source_sha256='d' * 64,
        )

        persist_listening_assistant_entries(frame, batches)
        db.session.commit()

        self.assertEqual(ListeningAssistantScheduleEntry.query.count(), 1)

    def test_cli_wraps_database_ddl_failure_as_actionable_click_error(self):
        from app.services import listening_assistant_cli

        ddl_error = OperationalError(
            'CREATE TABLE failed',
            {},
            PermissionError('permission denied'),
        )
        with patch.object(
            listening_assistant_cli,
            'ensure_listening_assistant_schema',
            side_effect=ddl_error,
        ):
            result = app.test_cli_runner().invoke(
                args=['listening-assistant', 'init-schema'],
            )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn('Error:', result.output)
        self.assertIn('CREATE TABLE failed', result.output)
        self.assertNotIn('Traceback', result.output)

    def test_schema_upgrade_adds_missing_raw_columns_and_indexes(self):
        raw_columns = (
            'semester_raw',
            'academic_year_raw',
            'course_code_raw',
            'selection_code_raw',
            'teacher_name_raw',
            'teacher_college_raw',
            'course_title_raw',
            'venue_id_raw',
        )
        db.session.remove()
        with db.engine.begin() as connection:
            for column in raw_columns:
                connection.exec_driver_sql(
                    'ALTER TABLE listening_assistant_schedule_entries '
                    f'DROP COLUMN "{column}"'
                )
            connection.exec_driver_sql(
                'DROP INDEX IF EXISTS '
                'ix_listening_assistant_schedule_batch_teacher'
            )

        before = {
            column['name']
            for column in inspect(db.engine).get_columns(
                'listening_assistant_schedule_entries'
            )
        }
        self.assertTrue(set(raw_columns).isdisjoint(before))

        ensure_listening_assistant_schema()

        after = {
            column['name']
            for column in inspect(db.engine).get_columns(
                'listening_assistant_schedule_entries'
            )
        }
        self.assertTrue(set(raw_columns) <= after)
        indexes = {
            index['name']
            for index in inspect(db.engine).get_indexes(
                'listening_assistant_schedule_entries'
            )
        }
        self.assertIn(
            'ix_listening_assistant_schedule_batch_teacher',
            indexes,
        )

        batches = persist_import_snapshot(
            schedule_frame(),
            source_filename='upgraded.xlsx',
            source_sha256='c' * 64,
        )
        persist_listening_assistant_entries(schedule_frame(), batches)
        db.session.commit()
        self.assertEqual(ListeningAssistantScheduleEntry.query.count(), 1)


if __name__ == '__main__':
    unittest.main()
