# -*- coding: utf-8 -*-
"""Round 6A current teaching semester control-plane tests."""
import tempfile
import unittest
from pathlib import Path

from app.app import app
from app.models import SystemSetting, User, db
from app.services.academic_term import (
    SETTING_KEY_CURRENT_TEACHING_SEMESTER,
    get_current_teaching_semester,
    normalize_semester_identifier,
)
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

VALID_BASE_PAYLOAD = {
    'first_week_monday': '2026-09-07',
    'week_start_day': 0,
    'total_weeks': 20,
    'required_submission_count': 1,
}


class CurrentTeachingSemesterTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='current-semester-')
        self.db_path = Path(self.temp_dir.name) / 'current-semester.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()

        self.client = app.test_client()
        super_admin = User(
            number='SA-CS',
            department='办公部',
            name='超级管理员',
            gender='-',
            grade='-',
            college='Test',
            major='-',
            dormitory='-',
            phone='-',
            qq='-',
            student_id='SA-CS',
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

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()
        self.temp_dir.cleanup()

    def _get(self):
        return self.client.get('/admin/api/settings/teaching')

    def _post(self, payload):
        return self.client.post('/admin/api/settings/teaching', json=payload)

    def test_unset_get_returns_empty_semester(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertEqual(response.get_json()['data']['current_semester'], '')
        self.assertEqual(get_current_teaching_semester(), '')

    def test_post_saves_value_and_get_returns_it(self):
        payload = dict(VALID_BASE_PAYLOAD)
        payload['current_semester'] = '2026-2027-1'
        response = self._post(payload)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.get_json()['success'])

        get_response = self._get()
        self.assertEqual(get_response.get_json()['data']['current_semester'], '2026-2027-1')
        self.assertEqual(get_current_teaching_semester(), '2026-2027-1')

    def test_old_post_without_current_semester_succeeds_and_preserves_existing(self):
        first_payload = dict(VALID_BASE_PAYLOAD)
        first_payload['current_semester'] = '2025-2026-2'
        self._post(first_payload)

        old_payload = dict(VALID_BASE_PAYLOAD)
        old_payload['total_weeks'] = 18
        response = self._post(old_payload)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(get_current_teaching_semester(), '2025-2026-2')
        self.assertEqual(SystemSetting.get('teaching_total_weeks'), '18')

    def test_whitespace_is_trimmed_when_saving(self):
        payload = dict(VALID_BASE_PAYLOAD)
        payload['current_semester'] = '  2026-2027-1  '
        response = self._post(payload)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(get_current_teaching_semester(), '2026-2027-1')

    def test_max_length_50_is_accepted_and_51_is_rejected(self):
        max_payload = dict(VALID_BASE_PAYLOAD)
        max_payload['current_semester'] = 'x' * 50
        response = self._post(max_payload)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(get_current_teaching_semester(), 'x' * 50)

        overlong_payload = dict(VALID_BASE_PAYLOAD)
        overlong_payload['current_semester'] = 'x' * 51
        response = self._post(overlong_payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])

        bad_type_payload = dict(VALID_BASE_PAYLOAD)
        bad_type_payload['current_semester'] = 123
        response = self._post(bad_type_payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self.assertEqual(get_current_teaching_semester(), 'x' * 50)

    def test_normalize_semester_identifier_allows_non_regex_identifiers(self):
        self.assertEqual(normalize_semester_identifier(' 2026-2027-1 '), '2026-2027-1')
        self.assertEqual(normalize_semester_identifier('2026春'), '2026春')
        with self.assertRaises(ValueError):
            normalize_semester_identifier(123)
        with self.assertRaises(ValueError):
            normalize_semester_identifier('a\nb')


if __name__ == '__main__':
    unittest.main()
