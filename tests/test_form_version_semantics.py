# -*- coding: utf-8 -*-
"""Version-chain semantics tests for LectureForm (Phase 2A.2)."""
import tempfile
import unittest
from datetime import datetime, timedelta
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
    ScoreRecord,
    User,
    db,
)
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


def _form(id_value, unique_id, updated_at, listener_number='A1', status='待审核'):
    return LectureForm(
        id=id_value,
        unique_id=unique_id,
        listener_name='Listener',
        listener_number=listener_number,
        lecture_date='2026-01-01',
        class_period='3-4节',
        lecture_location='A101',
        teacher_name='T',
        teacher_college='C',
        course_title='Course',
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
        status=status,
        updated_at=updated_at,
        created_at=updated_at,
    )


class FormVersionModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix='form-version-')

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.db_path = Path(self.temp_dir.name) / f'form-version-{id(self)}.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()

    def test_transient_unflushed_form_returns_self(self):
        transient = _form(None, None, BASE_TIME)
        self.assertIs(transient.get_latest_version(), transient)
        self.assertEqual(transient.get_all_versions(), [transient])

    def test_normal_form_logical_id_and_versions(self):
        form = _form(100, None, BASE_TIME)
        db.session.add(form)
        db.session.commit()
        self.assertEqual(form.logical_id, 100)
        self.assertEqual(form.get_latest_version().id, 100)
        self.assertEqual([v.id for v in form.get_all_versions()], [100])

    def test_higher_id_wins_even_if_updated_at_earlier(self):
        base = _form(100, 100, BASE_TIME + timedelta(hours=2))
        child = _form(101, 100, BASE_TIME + timedelta(hours=1))
        db.session.add_all([base, child])
        db.session.commit()
        self.assertEqual(base.get_latest_version().id, 101)
        self.assertEqual(child.get_latest_version().id, 101)

    def test_same_timestamp_higher_id_wins(self):
        base = _form(100, 100, BASE_TIME)
        child = _form(101, 100, BASE_TIME)
        db.session.add_all([base, child])
        db.session.commit()
        self.assertEqual(base.get_latest_version().id, 101)

    def test_legacy_mixed_chain_includes_base_and_child(self):
        base = _form(100, None, BASE_TIME)
        child = _form(101, 100, BASE_TIME)
        db.session.add_all([base, child])
        db.session.commit()
        self.assertEqual(base.get_latest_version().id, 101)
        self.assertEqual(base.get_all_versions()[0].id, 101)
        self.assertEqual(base.get_all_versions()[1].id, 100)

    def test_all_versions_order_is_id_desc(self):
        base = _form(100, 100, BASE_TIME)
        child = _form(101, 100, BASE_TIME)
        grandchild = _form(102, 100, BASE_TIME)
        db.session.add_all([base, child, grandchild])
        db.session.commit()
        self.assertEqual([v.id for v in base.get_all_versions()], [102, 101, 100])


class FormVersionRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix='form-version-route-')

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.db_path = Path(self.temp_dir.name) / f'form-version-route-{id(self)}.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self._create_org()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()

    def _create_org(self):
        dept_a = Department(name='办公部')
        dept_b = Department(name='策划部')
        db.session.add_all([dept_a, dept_b])
        db.session.flush()
        perm = Permission(name='审表_部门', description='')
        db.session.add(perm)
        db.session.flush()
        group = Group(name='一组', department=dept_a.name)
        db.session.add(group)
        db.session.flush()

        self.info = self._user('A1', 's-a1', '信息员', dept_a.name, group.id, None)
        self.dept_manager = self._user(
            'D1', 's-d1', '管理员', dept_a.name, group.id, {'审表_部门'})
        db.session.commit()

    def _user(self, number, student_id, role, department, group_id, perms):
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
            group='一组',
            group_id=group_id,
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        for name in (perms or set()):
            perm = Permission.query.filter_by(name=name).first()
            db.session.add(RolePermission(
                role=f'特殊角色_{user.id}',
                permission_id=perm.id,
            ))
        return user

    def _form(self, id_value, unique_id, listener_number, updated_at=None, status='待审核'):
        form = _form(
            id_value, unique_id, updated_at or BASE_TIME,
            listener_number=listener_number, status=status,
        )
        db.session.add(form)
        db.session.flush()
        return form

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    def test_submit_form_review_unique_id_null_legacy_behavior(self):
        original = self._form(100, None, self.info.number, status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        response = self.client.post(
            f'/admin/api/review/form/{original.id}',
            json={},
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body['success'])
        self.assertEqual(body['new_status'], '部门已审核')
        db.session.refresh(original)
        self.assertEqual(original.unique_id, original.id)
        versions = original.get_all_versions()
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0].unique_id, original.id)

    def test_stale_action_on_lower_id_returns_400(self):
        base = self._form(100, 100, self.info.number, status='待审核')
        child = self._form(101, 100, self.info.number, status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        response = self.client.post(
            f'/admin/api/review/submit/{base.id}',
            json={},
        )
        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertFalse(body['success'])
        self.assertIn('更新版本', body['message'])

    def _form_with_registration(self, form_id, owner_number, status='待审核'):
        registration = CourseRegistration(
            course_code='C1', selection_code='S1', user_id=self.info.id,
        )
        db.session.add(registration)
        db.session.flush()
        form = self._form(form_id, None, owner_number, status=status)
        form.registration_id = registration.id
        db.session.add(form)
        db.session.commit()
        return form, registration

    def _submit_review_payload(self, form):
        form_data = {
            'listener_name': form.listener_name,
            'course_changes': form.course_changes or '无',
            'lecture_date': form.lecture_date,
            'class_period': form.class_period,
            'lecture_location': form.lecture_location,
            'teacher_name': form.teacher_name,
            'teacher_college': form.teacher_college,
            'course_title': form.course_title,
            'student_grade_class': form.student_grade_class,
            'abnormal_situation': form.abnormal_situation or '无',
            'teaching_method': form.teaching_method,
            'classroom_discipline': form.classroom_discipline,
            'classroom_atmosphere': form.classroom_atmosphere,
            'courseware_quality': form.courseware_quality,
            'overall_effect': form.overall_effect,
            'quality_case': form.quality_case,
            'course_feedback': form.course_feedback,
            'suggestions': form.suggestions or '无',
            'student_signature1': form.student_signature1,
            'contact_phone1': form.contact_phone1,
            'student_signature2': form.student_signature2 or '',
            'contact_phone2': form.contact_phone2 or '',
        }
        return {'form_data': form_data, 'review_comment': 'ok'}

    def test_submit_review_new_version_preserves_registration_id(self):
        original, registration = self._form_with_registration(
            100, self.info.number)
        self._login(self.dept_manager)
        response = self.client.post(
            f'/admin/api/review/submit/{original.id}',
            json=self._submit_review_payload(original),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        new_version = original.get_latest_version()
        self.assertNotEqual(new_version.id, original.id)
        self.assertEqual(new_version.registration_id, registration.id)

    def test_submit_form_review_new_version_preserves_registration_id(self):
        original, registration = self._form_with_registration(
            100, self.info.number)
        self._login(self.dept_manager)
        response = self.client.post(
            f'/admin/api/review/form/{original.id}',
            json={},
        )
        self.assertEqual(response.status_code, 200)
        new_version = original.get_latest_version()
        self.assertNotEqual(new_version.id, original.id)
        self.assertEqual(new_version.registration_id, registration.id)

    def test_reject_new_version_preserves_registration_id(self):
        original, registration = self._form_with_registration(
            100, self.info.number)
        self._login(self.dept_manager)
        response = self.client.post(
            f'/admin/api/review/reject/{original.id}',
            json={'reason': 'x'},
        )
        self.assertEqual(response.status_code, 200)
        new_version = original.get_latest_version()
        self.assertNotEqual(new_version.id, original.id)
        self.assertEqual(new_version.registration_id, registration.id)

    def test_submit_review_invalid_score_swallows_and_commits(self):
        original = self._form(100, None, self.info.number, status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        payload = self._submit_review_payload(original)
        payload['score_data'] = [
            {'reason': 'x', 'department_score': 'abc', 'personal_score': 0}
        ]
        response = self.client.post(
            f'/admin/api/review/submit/{original.id}',
            json=payload,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertIsNone(ScoreRecord.query.filter_by(
            form_id=original.get_latest_version().id).first())

    def test_submit_form_review_invalid_score_rolls_back_500(self):
        original = self._form(100, None, self.info.number, status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        response = self.client.post(
            f'/admin/api/review/form/{original.id}',
            json={
                'form_data': {},
                'review_comment': '',
                'score_data': [
                    {'reason': 'x', 'department_score': 'abc', 'personal_score': 0}
                ],
            },
        )
        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.get_json()['success'])
        self.assertEqual(original.get_latest_version().id, original.id)


if __name__ == '__main__':
    unittest.main()
