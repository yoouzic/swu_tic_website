# -*- coding: utf-8 -*-
"""Phase 2B-P2 profile convergence integration tests."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app.app import app
from app.models import LectureForm, SystemSetting, User, db
from app.blueprints.admin.users import _build_user_profile_stats
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


def _frozen_datetime(target):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls):
            return cls(target.year, target.month, target.day, 12, 0, 0)

    return FrozenDateTime


AFTER_TERM = datetime(2027, 1, 25)


class TeachingCalendarProfileIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix='teaching-profile-')

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.db_path = Path(self.temp_dir.name) / f'profile-{id(self)}.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self.user = User(
            number='U001',
            department='办公部',
            name='测试用户',
            gender='-',
            grade='-',
            college='Test College',
            major='-',
            dormitory='-',
            phone='-',
            qq='-',
            student_id='user001',
            password_hash=generate_password_hash('password'),
            role='信息员',
            group='一组',
            group_id=None,
            is_active=True,
        )
        db.session.add(self.user)
        SystemSetting.set('teaching_first_week_monday', '2026-09-07')
        SystemSetting.set('teaching_week_start_day', '0')
        SystemSetting.set('teaching_total_weeks', '20')
        SystemSetting.set('teaching_required_submission', '1')
        db.session.commit()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    def _form(self, lecture_date, audit_tag=''):
        form = LectureForm(
            listener_name='测试用户',
            listener_number=self.user.number,
            lecture_date=lecture_date,
            class_period='3-4',
            lecture_location='A101',
            teacher_name='T',
            teacher_college='C',
            course_title='C',
            student_grade_class='G',
            teaching_method='M',
            classroom_discipline='D',
            classroom_atmosphere='A',
            courseware_quality='Q',
            overall_effect='E',
            quality_case='Case',
            course_feedback='F',
            suggestions='S',
            student_signature1='sig',
            contact_phone1='123',
            audit_tag=audit_tag,
        )
        db.session.add(form)
        db.session.commit()
        return form

    def test_user_profile_after_term_does_not_render_week_21(self):
        self._login(self.user)
        with patch('app.blueprints.user.profile.datetime', _frozen_datetime(AFTER_TERM)):
            response = self.client.get('/user/profile')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('第21周', response.get_data(as_text=True))

    def test_admin_user_profile_stats_after_term_uses_non_teaching_label(self):
        with patch('app.blueprints.admin.users.datetime', _frozen_datetime(AFTER_TERM)):
            stats = _build_user_profile_stats(self.user)
        self.assertIsNotNone(stats)
        self.assertIsNone(stats['current_week'])
        self.assertEqual(stats['current_week_label'], '当前不在教学周内')

    def test_admin_user_profile_stats_before_term_uses_before_label(self):
        before_term = datetime(2026, 9, 1)
        with patch('app.blueprints.admin.users.datetime', _frozen_datetime(before_term)):
            stats = _build_user_profile_stats(self.user)
        self.assertIsNotNone(stats)
        self.assertIsNone(stats['current_week'])
        self.assertEqual(stats['current_week_label'], '本学期教学周尚未开始')

    def test_admin_user_profile_stats_invalid_week_start_falls_back_and_no_500(self):
        SystemSetting.set('teaching_week_start_day', 'abc')
        with patch('app.blueprints.admin.users.datetime', _frozen_datetime(datetime(2026, 9, 7))):
            stats = _build_user_profile_stats(self.user)
        self.assertIsNotNone(stats)
        # historical invalid week_start falls back to Monday, so the date is week 1.
        self.assertEqual(stats['current_week'], 1)
        self.assertEqual(stats['current_week_label'], '当前教学周（第 1 周）')

    def test_admin_profile_counts_legacy_late_form_in_corrected_week(self):
        # Week 2 date (2026-09-14) with legacy 晚交 should be business week 1.
        self._form('2026-09-14', '需要人工审核;晚交')
        with patch('app.blueprints.admin.users.datetime', _frozen_datetime(datetime(2026, 9, 7))):
            stats = _build_user_profile_stats(self.user)
        self.assertEqual(stats['current_week'], 1)
        self.assertEqual(stats['week_submitted'], 1)

    def test_admin_profile_respects_explicit_week_correction(self):
        # Explicit 第3周 should not be counted as week 1.
        self._form('2026-09-07', '需要人工审核;第3周')
        with patch('app.blueprints.admin.users.datetime', _frozen_datetime(datetime(2026, 9, 7))):
            stats = _build_user_profile_stats(self.user)
        self.assertEqual(stats['current_week'], 1)
        self.assertEqual(stats['week_submitted'], 0)


if __name__ == '__main__':
    unittest.main()
