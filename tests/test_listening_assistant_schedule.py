# -*- coding: utf-8 -*-
"""Tests for the batch-linked universal listening-assistant schedule index."""

import tempfile
import unittest
from pathlib import Path

import pandas as pd
from sqlalchemy import inspect

from app.app import app
from app.models import Course, ListeningAssistantScheduleEntry, db
from app.services.listening_assistant_schedule import (
    load_schedule_entries,
    persist_listening_assistant_entries,
)
from app.services.schedule_snapshots import persist_import_snapshot
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

    def _persist(self, frame, filename='schedule.xlsx'):
        batches = persist_import_snapshot(
            frame,
            source_filename=filename,
            source_sha256='a' * 64,
        )
        persist_listening_assistant_entries(frame, batches)
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
            'course_code',
            'selection_code',
            'teacher_name',
            'teacher_college',
            'course_title',
            'student_grade_class',
            'venue_id',
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
        db.drop_all()

        runner = app.test_cli_runner()
        first = runner.invoke(args=['listening-assistant', 'init-schema'])
        second = runner.invoke(args=['listening-assistant', 'init-schema'])

        self.assertEqual(first.exit_code, 0, first.output)
        self.assertEqual(second.exit_code, 0, second.output)
        inspector = inspect(db.engine)
        self.assertTrue(inspector.has_table('listening_assistant_schedule_entries'))
        self.assertFalse(inspector.has_table('schedule_import_batches'))


if __name__ == '__main__':
    unittest.main()
