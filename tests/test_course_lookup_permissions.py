"""Role-correct lookup, navigation and owner-history presentation."""
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from flask import render_template

from app.app import app
from app.models import (
    CourseRegistration, LectureForm, LectureFormDraft, ListeningAssistantEvidence,
    LectureSiteCapture, Permission, RolePermission, SystemSetting, User, db,
)
from app.services.listening_assistant_contracts import ScheduleEntry
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


class CourseLookupPermissionsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='course-lookup-permissions-')
        configure_sqlite_database(app, db, Path(self.temp_dir.name) / 'lookup.sqlite')
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self.users = {}
        for index, role in enumerate(('信息员', '管理员', '超级管理员', '教师'), 1):
            user = User(number=f'LOOKUP-{index}', department='test', name=role,
                        gender='-', grade='-', college='Test', major='-', dormitory='-',
                        phone='-', qq='-', student_id=f'lookup-{index}', password_hash='x',
                        role=role, group='test', is_active=True)
            db.session.add(user)
            self.users[role] = user
        db.session.add(SystemSetting(key='teaching_current_semester', value='2026-2027-1'))
        db.session.commit()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.context.pop()
        self.temp_dir.cleanup()

    def login(self, role, *, forged_role=None):
        with self.client.session_transaction() as sess:
            sess.clear()
            sess['user_id'] = self.users[role].id
            sess['user_role'] = forged_role or role

    def assert_no_fill_controls(self, html):
        for marker in ('id="lectureForm"', 'site-capture.js', 'lecture-form-draft.js',
                       'lecture-form-assistant.js', 'id="registration-tab"',
                       'data-activity-panel="registration"', 'id="deleteModal"',
                       'id="editReservationModal"', 'id="deleteReservationModal"',
                       'onclick="deleteForm(', 'onclick="editReservation(',
                       'onclick="deleteReservation(', '/user/submit_form', '/user/form/edit/'):
            self.assertNotIn(marker, html)

    def test_lookup_is_available_to_all_active_roles_without_form_widgets(self):
        for role in self.users:
            with self.subTest(role=role):
                self.login(role)
                response = self.client.get('/user/course_lookup')
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertIn('id="courseLookupForm"', html)
                self.assertIn('course-lookup.js', html)
                self.assertIn('2026-2027-1', html)
                self.assertNotIn('name="semester"', html)
                self.assertNotIn('id="lectureForm"', html)
                for script in ('site-capture.js', 'lecture-form-draft.js', 'lecture-form-assistant.js'):
                    self.assertNotIn(script, html)

    def test_lookup_rejects_anonymous_and_inactive_accounts(self):
        self.assertEqual(self.client.get('/user/course_lookup').status_code, 302)
        self.users['教师'].is_active = False
        db.session.commit()
        self.login('教师')
        response = self.client.get('/user/course_lookup')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/auth/login', response.location)

    def test_legacy_null_active_can_lookup(self):
        self.users['教师'].is_active = None
        db.session.commit()
        self.login('教师')
        self.assertEqual(self.client.get('/user/course_lookup').status_code, 200)

    def test_unconfigured_semester_has_actionable_state_and_no_semester_input(self):
        SystemSetting.query.filter_by(key='teaching_current_semester').delete()
        db.session.commit()
        self.login('教师')
        html = self.client.get('/user/course_lookup').get_data(as_text=True)
        self.assertIn('课表暂未配置，请联系管理员', html)
        self.assertNotIn('name="semester"', html)

    def test_manager_navigation_uses_database_permission_and_ignores_session_role(self):
        self.login('管理员', forged_role='信息员')
        html = self.client.get('/user/profile').get_data(as_text=True)
        self.assertIn('/user/course_lookup', html)
        self.assert_no_fill_controls(html)
        permission = Permission(name='填表')
        db.session.add(permission)
        db.session.flush()
        db.session.add(RolePermission(role='管理员', permission_id=permission.id))
        db.session.commit()
        html = self.client.get('/user/profile').get_data(as_text=True)
        self.assertIn('/user/submit_form', html)
        self.assertIn('听课登记', html)

    def test_super_course_management_has_course_lookup_entry(self):
        self.login('超级管理员')
        html = self.client.get('/admin/course_feedback_management', follow_redirects=True).get_data(as_text=True)
        self.assertIn('查询课表', html)
        self.assertIn('/user/course_lookup', html)
        self.assertNotIn('/user/submit_form', html)
        self.assertNotIn('听课与填报', html)

    def test_read_only_history_keeps_owner_details_and_hides_mutation_controls(self):
        self.login('管理员')
        sample = SimpleNamespace(id=8, unique_id='history-8', lecture_date='2026-09-18',
                                 course_title='历史课程', class_period='3-4', teacher_name='张老师',
                                 teacher_college='学院', lecture_location='8-309', status='已驳回',
                                 updated_at=datetime(2026, 9, 18), created_at=datetime(2026, 9, 18),
                                 review_comment='历史审核意见')
        reservation = SimpleNamespace(id=9, created_at=datetime(2026, 9, 18), course_name='历史预约',
                                      course_code='CS101', selection_code='S001', teacher_name='张老师',
                                      class_time='周五', listening_info='原计划', is_bound=False,
                                      bind_count=0, can_edit=True, can_delete=True)
        with app.test_request_context('/user/listening_registration?tab=records'):
            from flask import session
            session['user_id'] = self.users['管理员'].id
            html = render_template('user/activity_center.html', active_tab='records',
                                   forms=[{'latest_form': sample, 'versions': [sample, sample]}],
                                   my_reservations=[reservation])
        self.assertIn('历史课程', html)
        self.assertIn('历史审核意见', html)
        self.assertIn('历史预约', html)
        self.assertIn('/admin/form/8', html)
        self.assert_no_fill_controls(html)

    def test_candidate_query_is_read_only_for_all_active_roles(self):
        entry = ScheduleEntry(entry_id='primary:demo:1', lecture_date=date(2026, 9, 18),
                              room='8-309', period=(3, 4), course_code='CS101', selection_code='S001',
                              course_title='数据结构', teacher_name='张老师', teacher_college='计算机学院',
                              student_grade_class='2024级1班', weekday=5, semester='2026-2027-1',
                              source_kind='primary', source_batch_id='demo', source_row=1)
        models = (LectureForm, LectureFormDraft, CourseRegistration,
                  ListeningAssistantEvidence, LectureSiteCapture)
        baseline = [model.query.count() for model in models]
        with mock.patch('app.services.listening_assistant.load_schedule_entries', return_value=[entry]):
            for role in self.users:
                self.login(role)
                response = self.client.get('/user/api/listening-assistant/candidates?date=2026-09-18&room=8-309&semester=forged')
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get_json()['success'])
                self.assertEqual(response.get_json()['data']['candidates'][0]['course_title'], '数据结构')
        self.assertEqual([model.query.count() for model in models], baseline)


if __name__ == '__main__':
    unittest.main()
