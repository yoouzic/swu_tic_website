import os
import re
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app.app import app
from app.models import (
    CourseRegistration,
    Department,
    Group,
    LectureForm,
    Permission,
    RolePermission,
    User,
    db,
)
from app.utils.review_permissions import get_reviewable_users
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database
from tests.review_request_utils import with_opened_review_revision, post_opened_review

ROLE_INFO = '信息员'
ROLE_ADMIN = '管理员'
ROLE_SUPER = '超级管理员'
PERM_GROUP = '审表_小组'
PERM_DEPARTMENT = '审表_部门'
PERM_CENTER = '审表_中心'


class SecurityAuthorizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix='security-auth-')

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.db_path = Path(self.temp_dir.name) / f'security-{id(self)}.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self._create_organization()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()

    def _create_organization(self):
        self.permission_objects = {}
        for name in (PERM_GROUP, PERM_DEPARTMENT, PERM_CENTER):
            perm = Permission(name=name, description=f'test {name}')
            db.session.add(perm)
            self.permission_objects[name] = perm
        db.session.flush()

        self.dept_a = Department(name='办公部', description='test')
        self.dept_b = Department(name='策划部', description='test')
        db.session.add_all([self.dept_a, self.dept_b])
        db.session.flush()

        self.group_a1 = Group(name='一组', department=self.dept_a.name, max_members=100)
        self.group_a2 = Group(name='二组', department=self.dept_a.name, max_members=100)
        self.group_b1 = Group(name='一组', department=self.dept_b.name, max_members=100)
        db.session.add_all([self.group_a1, self.group_a2, self.group_b1])
        db.session.flush()

        self.officer_a = self._make_user('A100', 'student-a100', ROLE_INFO, self.dept_a.name, self.group_a1.name, self.group_a1.id)
        self.officer_b = self._make_user('B100', 'student-b100', ROLE_INFO, self.dept_b.name, self.group_b1.name, self.group_b1.id)
        self.officer_no_group_a = self._make_user('ANULL', 'student-anull', ROLE_INFO, self.dept_a.name, '待分配', None)
        self.officer_no_group_b = self._make_user('BNULL', 'student-bnull', ROLE_INFO, self.dept_b.name, '待分配', None)

        self.department_admin_a = self._make_user(
            'D100', 'admin-a100', ROLE_ADMIN, self.dept_a.name, self.group_a1.name, self.group_a1.id,
            permissions={PERM_DEPARTMENT},
        )
        self.department_admin_b = self._make_user(
            'D200', 'admin-b100', ROLE_ADMIN, self.dept_b.name, self.group_b1.name, self.group_b1.id,
            permissions={PERM_DEPARTMENT},
        )
        self.group_admin_no_group = self._make_user(
            'GNULL', 'admin-gnull', ROLE_ADMIN, self.dept_a.name, '待分配', None,
            permissions={PERM_GROUP},
        )
        self.center_admin = self._make_user(
            'C100', 'admin-c100', ROLE_ADMIN, self.dept_a.name, self.group_a1.name, self.group_a1.id,
            permissions={PERM_CENTER},
        )
        self.super_admin = self._make_user(
            'S100', 'admin-super', ROLE_SUPER, self.dept_a.name, self.group_a1.name, self.group_a1.id,
            permissions={PERM_CENTER},
        )
        db.session.commit()

    def _make_user(self, number, student_id, role, department, group, group_id, permissions=None):
        user = User(
            number=number,
            department=department,
            name=f'User {number}',
            gender='-',
            grade='-',
            college='Test College',
            major='-',
            dormitory='-',
            phone='-',
            qq='-',
            student_id=student_id,
            password_hash=generate_password_hash('password'),
            role=role,
            group=group,
            group_id=group_id,
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        for name in (permissions or set()):
            db.session.add(RolePermission(
                role=f'特殊角色_{user.id}',
                permission_id=self.permission_objects[name].id,
            ))
        return user

    def _make_form(self, owner, status='待审核', unique_id=None):
        form = LectureForm(
            listener_name=f'{owner.name}（{owner.college}）',
            listener_number=owner.number,
            course_changes='无',
            lecture_date='2026/06/04星期四',
            class_period='第3-4节',
            lecture_location='32-302',
            teacher_name='Teacher A',
            teacher_college='Test College',
            course_title='Database Systems',
            student_grade_class='2024 Test Class',
            abnormal_situation='无',
            teaching_method='PPT演示法',
            classroom_discipline='好',
            classroom_atmosphere='好',
            courseware_quality='好',
            overall_effect='好',
            quality_case='推荐',
            course_feedback='This is a sufficiently long feedback text for automated review.',
            suggestions='无',
            student_signature1='Student One',
            contact_phone1='13800000001',
            student_signature2='Student Two',
            contact_phone2='13800000002',
            status=status,
            audit_tag='需要人工审核',
        )
        db.session.add(form)
        db.session.flush()
        if unique_id is None:
            form.unique_id = form.id
        else:
            form.unique_id = unique_id
        db.session.commit()
        return form

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    def _review_payload(self, form, extra_listener_number=None):
        return with_opened_review_revision(self.client, form.id, {
            'form_data': {
                'listener_name': form.listener_name,
                'listener_number': extra_listener_number or form.listener_number,
                'course_changes': '无',
                'lecture_date': '2026/06/04',
                'class_period': '第3-4节',
                'lecture_location': '32-302',
                'teacher_name': 'Teacher A',
                'teacher_college': 'Test College',
                'course_title': 'Database Systems',
                'student_grade_class': '2024 Test Class',
                'abnormal_situation': '无',
                'teaching_method': 'PPT演示法',
                'classroom_discipline': '好',
                'classroom_atmosphere': '好',
                'courseware_quality': '好',
                'overall_effect': '好',
                'quality_case': '推荐',
                'course_feedback': 'This is a sufficiently long feedback text for automated review.',
                'suggestions': '无',
                'student_signature1': 'Student One',
                'contact_phone1': '13800000001',
                'student_signature2': 'Student Two',
                'contact_phone2': '13800000002',
            },
            'review_comment': '通过',
            'score_data': [],
        })

    # ---- P0-1 submit review ----
    def test_submit_review_cross_department_forbidden(self):
        self._login(self.department_admin_a)
        form = self._make_form(self.officer_b, status='待审核')
        response = self.client.post(f'/admin/api/review/submit/{form.id}', json=self._review_payload(form))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json().get('success', True))
        self.assertEqual(db.session.get(LectureForm, form.id).status, '待审核')

    def test_submit_review_same_department_success_and_freezes_listener_number(self):
        self._login(self.department_admin_a)
        form = self._make_form(self.officer_a, status='待审核')
        payload = self._review_payload(form, extra_listener_number=self.officer_b.number)
        response = self.client.post(f'/admin/api/review/submit/{form.id}', json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get('success'))
        new_id = data.get('form_id')
        new_form = db.session.get(LectureForm, new_id)
        self.assertEqual(new_form.listener_number, self.officer_a.number)
        self.assertEqual(new_form.status, '部门已审核')

    def test_submit_review_unknown_owner_forbidden(self):
        self._login(self.department_admin_a)
        form = self._make_form(self.officer_a, status='待审核')
        # Remove the owner to simulate unparseable/inactive historical data (fail-closed).
        db.session.delete(db.session.get(User, self.officer_a.id))
        db.session.commit()
        response = self.client.post(f'/admin/api/review/submit/{form.id}', json=self._review_payload(form))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json().get('success', True))

    # ---- P0-2 reject ----
    def test_reject_cross_department_forbidden(self):
        self._login(self.department_admin_a)
        form = self._make_form(self.officer_b, status='待审核')
        response = self.client.post(f'/admin/api/review/reject/{form.id}', json={'reason': 'no'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(db.session.get(LectureForm, form.id).status, '待审核')

    def test_reject_same_department_pending_success(self):
        self._login(self.department_admin_a)
        form = self._make_form(self.officer_a, status='待审核')
        response = post_opened_review(self.client, f'/admin/api/review/reject/{form.id}', json={'reason': '需要补充'})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data.get('success'))
        self.assertEqual(data.get('new_status'), '已驳回')

    def test_reject_state_forbidden_for_department_on_department_approved(self):
        self._login(self.department_admin_a)
        form = self._make_form(self.officer_a, status='部门已审核')
        response = self.client.post(f'/admin/api/review/reject/{form.id}', json={'reason': 'no'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(db.session.get(LectureForm, form.id).status, '部门已审核')

    def test_reject_unknown_owner_fail_closed(self):
        self._login(self.department_admin_a)
        form = self._make_form(self.officer_a, status='待审核')
        db.session.delete(db.session.get(User, self.officer_a.id))
        db.session.commit()
        response = self.client.post(f'/admin/api/review/reject/{form.id}', json={'reason': 'no'})
        self.assertEqual(response.status_code, 403)

    # ---- P0-3 delete ----
    def test_delete_form_requires_center_for_department_admin(self):
        self._login(self.department_admin_a)
        form = self._make_form(self.officer_a, status='待审核')
        response = self.client.delete(f'/admin/api/review/form/{form.id}')
        self.assertEqual(response.status_code, 403)
        self.assertIsNotNone(db.session.get(LectureForm, form.id))

    def test_delete_form_group_requires_center_for_department_admin(self):
        self._login(self.department_admin_a)
        form = self._make_form(self.officer_a, status='待审核')
        response = self.client.delete(f'/admin/api/review/group/{form.unique_id}')
        self.assertEqual(response.status_code, 403)
        self.assertIsNotNone(db.session.get(LectureForm, form.id))

    def test_delete_form_center_can_delete_with_scope(self):
        self._login(self.center_admin)
        form = self._make_form(self.officer_a, status='待审核')
        response = self.client.delete(f'/admin/api/review/form/{form.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(db.session.get(LectureForm, form.id))

    # ---- P0-4 NULL group fail-closed ----
    def test_group_null_reviewer_has_no_reviewable_users(self):
        self.assertEqual(get_reviewable_users(self.group_admin_no_group.id), [])
        self._login(self.group_admin_no_group)
        form = self._make_form(self.officer_no_group_a, status='待审核')
        response = self.client.post(f'/admin/api/review/form/{form.id}', json={'form_data': {}, 'review_comment': 'x'})
        self.assertEqual(response.status_code, 403)

    # ---- P0-5 path traversal ----
    def test_download_passwords_rejects_traversal_and_bad_names(self):
        self._login(self.super_admin)
        for value in (
            '../secret.xlsx',
            '..%2fsecret.xlsx',
            '..\\secret.xlsx',
            '..%5csecret.xlsx',
            '/etc/passwd',
            'absolute.xlsx',
            'contacts:ads.xlsx',
        ):
            with self.subTest(value=value):
                response = self.client.get(f'/admin/download_passwords/{value}')
                self.assertIn(response.status_code, (302, 404))

    def test_download_passwords_allows_legitimate_password_and_contact_names(self):
        self._login(self.super_admin)
        export_dir = os.path.join(self.temp_dir.name, 'exports')
        os.makedirs(export_dir, exist_ok=True)
        import openpyxl
        previous_export_dir = os.environ.get('EXPORT_DIR')
        os.environ['EXPORT_DIR'] = export_dir
        try:
            for filename in ('passwords_20260822_120000.xlsx', 'contacts_办公部_20260822_120000.xlsx'):
                wb = openpyxl.Workbook()
                wb.save(os.path.join(export_dir, filename))
                response = self.client.get(f'/admin/download_passwords/{filename}')
                self.assertEqual(response.status_code, 200)
                _ = response.data  # consume binary body so Windows file handles are released
                response.close()
        finally:
            if previous_export_dir is None:
                os.environ.pop('EXPORT_DIR', None)
            else:
                os.environ['EXPORT_DIR'] = previous_export_dir
        # The actual containment path uses configured EXPORT_DIR; ensure validator not over-strict on Chinese.
        from app.blueprints.admin import _safe_export_filename
        self.assertEqual(_safe_export_filename('contacts_办公部_20260822_120000.xlsx'), 'contacts_办公部_20260822_120000.xlsx')

    # ---- P0-6 registration IDOR ----
    def test_submit_form_foreign_registration_id_forbidden_and_not_mutated(self):
        self._login(self.officer_a)
        registration_b = CourseRegistration(
            course_code='C001', selection_code='S001',
            user_id=self.officer_b.id, listening_info='第1周星期一第1节',
            is_used=False,
        )
        db.session.add(registration_b)
        db.session.commit()
        form_payload = {
            'lecture_date': '2026/06/04',
            'lecture_date_display': '2026/06/04',
            'start_period': '3',
            'end_period': '4',
            'class_period': '第3-4节',
            'lecture_location': '32-302',
            'teacher_name': 'Teacher A',
            'teacher_college': 'Test College',
            'course_title': 'Database Systems',
            'student_grade_class': '2024 Test Class',
            'course_changes': '无',
            'abnormal_situation': '无',
            'teaching_method': 'PPT演示法',
            'classroom_discipline': '好',
            'classroom_atmosphere': '好',
            'courseware_quality': '好',
            'overall_effect': '好',
            'quality_case': '推荐',
            'course_feedback': 'This is a sufficiently long feedback text for automated review.',
            'suggestions': '无',
            'student_signature1': 'Student One',
            'contact_phone1': '13800000001',
            'student_signature2': 'Student Two',
            'contact_phone2': '13800000002',
            'registration_id': str(registration_b.id),
        }
        response = self.client.post('/user/submit_form', data=form_payload)
        self.assertEqual(response.status_code, 403)
        db.session.refresh(registration_b)
        self.assertFalse(registration_b.is_used)
        self.assertEqual(LectureForm.query.filter_by(registration_id=registration_b.id).count(), 0)

    # ---- P0-7 CSRF / cookie / login session ----
    def test_csrf_rejects_unsafe_post_without_token(self):
        app.config['WTF_CSRF_ENABLED'] = True
        self._login(self.super_admin)
        response = self.client.post('/admin/api/review/form/1', json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json().get('code'), 'csrf_failed')

    def test_csrf_allows_post_with_token(self):
        app.config['WTF_CSRF_ENABLED'] = True
        self._login(self.super_admin)
        page = self.client.get('/admin/review_forms')
        match = re.search(r'name="csrf-token" content="([^"]+)"', page.get_data(as_text=True))
        self.assertIsNotNone(match)
        token = match.group(1)
        response = self.client.post('/auth/logout', headers={'X-CSRFToken': token})
        self.assertEqual(response.status_code, 302)

    def test_login_clears_stale_session_fields(self):
        app.config['WTF_CSRF_ENABLED'] = False
        with self.client.session_transaction() as sess:
            sess['stale'] = 'keep-me-away'
            sess['user_id'] = 999999
        response = self.client.post('/auth/login', data={
            'student_id': self.officer_a.student_id,
            'password': 'password',
        })
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as sess:
            self.assertNotIn('stale', sess)
            self.assertEqual(sess['user_id'], self.officer_a.id)

    def test_session_cookie_attributes_explicit(self):
        self.assertTrue(app.config['SESSION_COOKIE_HTTPONLY'])
        self.assertEqual(app.config['SESSION_COOKIE_SAMESITE'], 'Lax')
        self.assertIn(app.config['SESSION_COOKIE_SECURE'], (True, False))


if __name__ == '__main__':
    unittest.main()
