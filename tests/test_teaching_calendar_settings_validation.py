# -*- coding: utf-8 -*-
"""Teaching settings endpoint input-poisoning tests (Phase 2B-P1)."""
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app.app import app
from app.models import SystemSetting, User, db
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


def _valid_payload():
    return {
        'first_week_monday': '2026-09-07',
        'week_start_day': 5,
        'total_weeks': 20,
        'required_submission_count': 1,
        'check_dept_review': False,
        'check_center_review': False,
        'show_auto_review_details': True,
        'enable_typos_check': True,
        'reviewer_display_mode': 'name',
        'course_weekly_limit_enabled': False,
        'course_weekly_limit_count': 1,
        'profile_editable_fields': [],
    }


class TeachingCalendarSettingsValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix='teaching-settings-')

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.db_path = Path(self.temp_dir.name) / f'settings-{id(self)}.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self.super_admin = User(
            number='S1',
            department='办公部',
            name='Super Admin',
            gender='-',
            grade='-',
            college='Test College',
            major='-',
            dormitory='-',
            phone='-',
            qq='-',
            student_id='s-super',
            password_hash=generate_password_hash('password'),
            role='超级管理员',
            group='一组',
            group_id=None,
            is_active=True,
        )
        db.session.add(self.super_admin)
        db.session.commit()
        self._login()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.super_admin.id
            sess['user_role'] = self.super_admin.role
            sess['user_name'] = self.super_admin.name

    def _post(self, payload):
        return self.client.post(
            '/admin/api/settings/teaching',
            json=payload,
        )

    def _post_raw(self, raw):
        return self.client.post(
            '/admin/api/settings/teaching',
            data=raw,
            content_type='application/json',
        )

    def _assert_main_settings_unchanged(self):
        for key in ('teaching_first_week_monday', 'teaching_week_start_day',
                    'teaching_total_weeks', 'teaching_required_submission'):
            self.assertIsNone(SystemSetting.query.filter_by(key=key).first())

    def test_update_rejects_invalid_first_week_date(self):
        payload = _valid_payload()
        payload['first_week_monday'] = 'not-a-date'
        response = self._post(payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_main_settings_unchanged()

    def test_update_rejects_invalid_week_start_day(self):
        payload = _valid_payload()
        payload['week_start_day'] = 'abc'
        response = self._post(payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_main_settings_unchanged()

    def test_update_rejects_invalid_total_weeks(self):
        payload = _valid_payload()
        payload['total_weeks'] = 'abc'
        response = self._post(payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_main_settings_unchanged()

    def test_update_rejects_out_of_range_total_weeks(self):
        payload = _valid_payload()
        payload['total_weeks'] = 53
        response = self._post(payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_main_settings_unchanged()

    def test_update_rejects_invalid_required_submission(self):
        payload = _valid_payload()
        payload['required_submission_count'] = 'abc'
        response = self._post(payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_main_settings_unchanged()

    def test_update_rejects_non_integral_float_week_start(self):
        payload = _valid_payload()
        payload['week_start_day'] = 5.9
        response = self._post(payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_main_settings_unchanged()

    def test_update_rejects_non_integral_float_total_weeks(self):
        payload = _valid_payload()
        payload['total_weeks'] = 20.9
        response = self._post(payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_main_settings_unchanged()

    def test_update_rejects_non_integral_float_required_submission(self):
        payload = _valid_payload()
        payload['required_submission_count'] = 1.9
        response = self._post(payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_main_settings_unchanged()

    def test_update_accepts_integral_float_values(self):
        payload = _valid_payload()
        payload['week_start_day'] = 5.0
        payload['total_weeks'] = 20.0
        payload['required_submission_count'] = 1.0
        response = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertEqual(SystemSetting.get('teaching_week_start_day'), '5')
        self.assertEqual(SystemSetting.get('teaching_total_weeks'), '20')

    def test_update_rejects_non_object_json_env(self):
        for raw in ('null', '[]', '"string"', '1', '{broken'):
            with self.subTest(raw=raw):
                response = self._post_raw(raw)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.get_json()['success'])
                self._assert_main_settings_unchanged()

    def test_update_valid_payload_persists_normalized_values(self):
        response = self._post(_valid_payload())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertEqual(
            SystemSetting.get('teaching_first_week_monday'),
            '2026-09-07',
        )
        self.assertEqual(SystemSetting.get('teaching_week_start_day'), '5')
        self.assertEqual(SystemSetting.get('teaching_total_weeks'), '20')
        self.assertEqual(SystemSetting.get('teaching_required_submission'), '1')

    def test_get_failsafe_with_legacy_invalid_settings(self):
        SystemSetting.set('teaching_first_week_monday', 'not-a-date')
        SystemSetting.set('teaching_week_start_day', 'abc')
        SystemSetting.set('teaching_total_weeks', 'abc')
        SystemSetting.set('teaching_required_submission', 'abc')

        response = self.client.get('/admin/api/settings/teaching')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['success'])
        self.assertIsNone(data['data']['first_week_monday'])
        self.assertEqual(data['data']['week_start_day'], 0)
        self.assertEqual(data['data']['total_weeks'], 20)
        self.assertEqual(data['data']['required_submission_count'], 1)

    def test_get_does_not_rewrite_legacy_bad_settings(self):
        SystemSetting.set('teaching_week_start_day', 'abc')
        self.client.get('/admin/api/settings/teaching')
        self.assertEqual(SystemSetting.get('teaching_week_start_day'), 'abc')


if __name__ == '__main__':
    unittest.main()
