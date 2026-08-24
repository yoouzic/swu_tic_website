# -*- coding: utf-8 -*-
"""Round 6C HTTP cutover tests for /admin/api/review/reference_data."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from app.app import app
from app.models import SystemSetting, User, db
from app.services.schedule_snapshots import persist_import_snapshot
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

SEMESTER = '2026-2027-1'


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


class ReferenceDataRouteTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='ref-data-')
        self.db_path = Path(self.temp_dir.name) / 'ref.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self.super_admin = User(
            number='SA-REF',
            department='办公部',
            name='超级管理员',
            gender='-',
            grade='-',
            college='Test',
            major='-',
            dormitory='-',
            phone='-',
            qq='-',
            student_id='SA-REF',
            password_hash='x',
            role='超级管理员',
            group='未分配小组',
            group_id=None,
            is_active=True,
        )
        db.session.add(self.super_admin)
        db.session.commit()
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.super_admin.id
            sess['user_role'] = self.super_admin.role
            sess['user_name'] = self.super_admin.name

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()
        self.temp_dir.cleanup()

    def _post(self, payload):
        return self.client.post('/admin/api/review/reference_data', json=payload)

    def test_route_does_not_instantiate_autoreview_engine(self):
        with mock.patch('app.blueprints.admin.review.AutoReviewEngine') as engine_cls:
            response = self._post({
                'teacher_name': '张三',
                'course_title': '数据结构',
            })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.get_json()['success'])
        engine_cls.assert_not_called()

    def test_canonical_ready_route_returns_schedule_matches_without_legacy_read(self):
        persist_import_snapshot(canonical_source_df(), 'canonical.xlsx', 'a' * 64)
        SystemSetting.set('teaching_current_semester', SEMESTER)
        with mock.patch(
            'app.services.review_schedule_source._read_excel',
            return_value=None,
        ) as read_excel:
            response = self._post({
                'teacher_name': '张三',
                'course_title': '数据结构',
            })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()['result']
        self.assertGreater(len(data['schedule_matches']), 0)
        self.assertEqual(data['schedule_matches'][0]['teacher_name'], '张三')
        read_excel.assert_not_called()

    def test_configured_no_snapshot_route_returns_empty_schedule_but_contacts_work(self):
        SystemSetting.set('teaching_current_semester', SEMESTER)
        with mock.patch(
            'app.services.review_schedule_source._read_excel',
            return_value=None,
        ) as read_excel:
            response = self._post({
                'listener_number': self.super_admin.number,
                'teacher_name': '张三',
                'course_title': '数据结构',
            })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()['result']
        self.assertEqual(data['schedule_matches'], [])
        self.assertEqual(len(data['contact_matches']), 1)
        self.assertEqual(data['contact_matches'][0]['id'], self.super_admin.number)
        read_excel.assert_not_called()

    def test_invalid_authority_route_returns_empty_schedule_without_legacy_read(self):
        from app.models import ScheduleImportBatch, ScheduleSemesterSelection
        other = ScheduleImportBatch(
            semester='OTHER', status='active', row_count=0,
            source_filename='other.xlsx', source_sha256='o' * 64,
        )
        db.session.add(other)
        db.session.flush()
        db.session.add(ScheduleSemesterSelection(
            semester=SEMESTER,
            active_batch_id=other.id,
        ))
        db.session.commit()
        SystemSetting.set('teaching_current_semester', SEMESTER)
        with mock.patch(
            'app.services.review_schedule_source._read_excel',
            return_value=None,
        ) as read_excel:
            response = self._post({
                'listener_number': self.super_admin.number,
                'teacher_name': '张三',
                'course_title': '数据结构',
            })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()['result']
        self.assertEqual(data['schedule_matches'], [])
        self.assertEqual(len(data['contact_matches']), 1)
        read_excel.assert_not_called()

    def test_fresh_deploy_canonical_only_route_works(self):
        persist_import_snapshot(canonical_source_df(), 'canonical.xlsx', 'a' * 64)
        SystemSetting.set('teaching_current_semester', SEMESTER)
        response = self._post({
            'teacher_name': '张三',
            'course_title': '数据结构',
            'student_grade_class': '2023级计算机1班',
            'lecture_location': '32-302',
            'lecture_date': '2026/09/09星期三',
            'class_period': '第3-4节',
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()['result']
        self.assertEqual(data['schedule_matches'][0]['teacher_name'], '张三')
        self.assertIn('_with_location', data['schedule_matches'][0]['match_type'])


if __name__ == '__main__':
    unittest.main()
