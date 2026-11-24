# -*- coding: utf-8 -*-
"""Characterization and service tests for review scope policy (Phase 2A.1)."""
import sqlite3
import tempfile
import unittest
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event
from werkzeug.security import generate_password_hash

from app.app import app
from app.models import (
    CourseRegistration,
    Department,
    Group,
    LectureForm,
    Permission,
    ReviewReadReceipt,
    RolePermission,
    ScoreRecord,
    User,
    db,
)
from app.review_automation.models import ReviewAssessment, ReviewFinding
from app.services.review_domain import (
    is_form_in_review_scope,
    partition_forms_by_review_scope,
)
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database
from tests.automation_test_utils import make_synthetic_form
from tests.review_request_utils import post_opened_review

ROLE_INFO = '信息员'
ROLE_ADMIN = '管理员'
ROLE_SUPER = '超级管理员'


class _ScopeTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix='review-scope-')

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.db_path = Path(self.temp_dir.name) / f'scope-{id(self)}.sqlite'
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
        self.permissions = {}
        for name in ('审表_小组', '审表_部门', '审表_中心', '管理部门', '管理部门小组', '填表'):
            perm = Permission(name=name, description='test')
            db.session.add(perm)
            self.permissions[name] = perm
        db.session.flush()

        self.dept_a = Department(name='办公部')
        self.dept_b = Department(name='策划部')
        db.session.add_all([self.dept_a, self.dept_b])
        db.session.flush()

        self.g_a = Group(name='一组', department=self.dept_a.name)
        self.g_a2 = Group(name='二组', department=self.dept_a.name)
        self.g_b = Group(name='一组', department=self.dept_b.name)
        self.g_c = Group(name='二组', department=self.dept_b.name)
        db.session.add_all([self.g_a, self.g_a2, self.g_b, self.g_c])
        db.session.flush()

        self.info_same = self._user(
            'A1', 's-a1', ROLE_INFO, self.dept_a.name, self.g_a.name, self.g_a.id)
        self.info_other_group = self._user(
            'A2', 's-a2', ROLE_INFO, self.dept_a.name, self.g_a2.name, self.g_a2.id)
        self.info_cross_dept_same_group = self._user(
            'A3', 's-a3', ROLE_INFO, self.dept_b.name, self.g_a.name, self.g_a.id)
        self.info_cross_dept = self._user(
            'B1', 's-b1', ROLE_INFO, self.dept_b.name, self.g_b.name, self.g_b.id)
        self.inactive_info = self._user(
            'I1', 's-i1', ROLE_INFO, self.dept_a.name, self.g_a.name, self.g_a.id,
            is_active=False,
        )
        self.dept_manager = self._user(
            'D1', 's-d1', ROLE_ADMIN, self.dept_a.name, self.g_a.name, self.g_a.id,
            perms={'审表_部门'},
        )
        self.group_manager = self._user(
            'G1', 's-g1', ROLE_ADMIN, self.dept_a.name, self.g_a.name, self.g_a.id,
            perms={'审表_小组'},
        )
        self.null_group_manager = self._user(
            'N1', 's-n1', ROLE_ADMIN, self.dept_a.name, '未分组', None,
            perms={'审表_小组'},
        )
        self.center_manager = self._user(
            'C1', 's-c1', ROLE_ADMIN, self.dept_a.name, self.g_a.name, self.g_a.id,
            perms={'审表_中心'},
        )
        self.super_admin = self._user(
            'S1', 's-s1', ROLE_SUPER, self.dept_a.name, self.g_a.name, self.g_a.id,
        )
        self.no_perm_admin = self._user(
            'X1', 's-x1', ROLE_ADMIN, self.dept_a.name, self.g_a.name, self.g_a.id,
            perms=set(),
        )
        db.session.commit()

    def _user(self, number, student_id, role, department, group, group_id,
              perms=None, is_active=True):
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
            is_active=is_active,
        )
        db.session.add(user)
        db.session.flush()
        for name in (perms or set()):
            db.session.add(RolePermission(
                role=f'特殊角色_{user.id}',
                permission_id=self.permissions[name].id,
            ))
        return user

    def _form(self, owner_number, status='待审核'):
        form = LectureForm(
            listener_name=f'Listener {owner_number}',
            listener_number=owner_number,
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
        )
        db.session.add(form)
        db.session.flush()
        return form

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name


class ReviewScopeServiceTests(_ScopeTestBase):
    def test_group_same_group_allow_different_group_deny(self):
        form_ok = self._form(self.info_same.number)
        form_bad = self._form(self.info_other_group.number)
        self.assertTrue(is_form_in_review_scope(self.group_manager.id, form_ok))
        self.assertFalse(is_form_in_review_scope(self.group_manager.id, form_bad))

    def test_group_same_group_id_cross_department_deny(self):
        form = self._form(self.info_cross_dept_same_group.number)
        self.assertFalse(is_form_in_review_scope(self.group_manager.id, form))

    def test_group_actor_null_group_id_deny(self):
        form = self._form(self.info_same.number)
        self.assertFalse(is_form_in_review_scope(self.null_group_manager.id, form))

    def test_department_same_allow_different_deny(self):
        form_same = self._form(self.info_same.number)
        form_other = self._form(self.info_cross_dept.number)
        self.assertTrue(is_form_in_review_scope(self.dept_manager.id, form_same))
        self.assertFalse(is_form_in_review_scope(self.dept_manager.id, form_other))

    def test_center_allows_other_users_denies_self(self):
        form_other = self._form(self.info_same.number)
        form_self = self._form(self.center_manager.number)
        self.assertTrue(is_form_in_review_scope(self.center_manager.id, form_other))
        self.assertFalse(is_form_in_review_scope(self.center_manager.id, form_self))

    def test_super_admin_allows_active_information_user(self):
        form = self._form(self.info_same.number)
        self.assertTrue(is_form_in_review_scope(self.super_admin.id, form))

    def test_inactive_owner_deny(self):
        form = self._form(self.inactive_info.number)
        self.assertFalse(is_form_in_review_scope(self.center_manager.id, form))

    def test_unknown_owner_deny(self):
        form = self._form('NO_SUCH_NUMBER')
        self.assertFalse(is_form_in_review_scope(self.center_manager.id, form))

    def test_no_review_permission_all_deny(self):
        form = self._form(self.info_same.number)
        self.assertFalse(is_form_in_review_scope(self.no_perm_admin.id, form))
        allowed, denied = partition_forms_by_review_scope(
            self.no_perm_admin.id, [form])
        self.assertEqual(allowed, [])
        self.assertEqual(denied, [form])

    def test_partition_mixed_preserves_order(self):
        form_allowed_1 = self._form(self.info_same.number)
        form_denied_1 = self._form(self.inactive_info.number)
        form_allowed_2 = self._form(self.info_cross_dept.number)
        form_denied_2 = self._form('NO_SUCH_NUMBER')
        forms = [form_allowed_1, form_denied_1, form_allowed_2, form_denied_2]
        allowed, denied = partition_forms_by_review_scope(
            self.center_manager.id, forms)
        self.assertEqual(allowed, [form_allowed_1, form_allowed_2])
        self.assertEqual(denied, [form_denied_1, form_denied_2])

    def test_empty_input_returns_empty_lists(self):
        self.assertEqual(partition_forms_by_review_scope(1, []), ([], []))

    def test_none_form_denied(self):
        self.assertFalse(is_form_in_review_scope(self.center_manager.id, None))


class ReviewScopeRouteLegacyTests(_ScopeTestBase):
    def test_submit_review_no_permission_keeps_legacy_http_200(self):
        form = self._form(self.info_same.number)
        self._login(self.info_same)
        response = post_opened_review(self.client,
            f'/admin/api/review/submit/{form.id}',
            json={},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()['success'])

    def test_submit_review_invalid_stage_keeps_legacy_http_200(self):
        form = self._form(self.info_same.number, status='中心已审核')
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/submit/{form.id}',
            json={},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()['success'])

    def test_submit_form_review_no_permission_returns_403(self):
        form = self._form(self.info_same.number)
        self._login(self.info_same)
        response = post_opened_review(self.client,
            f'/admin/api/review/form/{form.id}',
            json={},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()['success'])

    def test_submit_form_review_invalid_stage_returns_403(self):
        form = self._form(self.info_same.number, status='中心已审核')
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/form/{form.id}',
            json={},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()['success'])

    def test_reject_no_permission_returns_403(self):
        form = self._form(self.info_same.number)
        self._login(self.info_same)
        response = post_opened_review(self.client,
            f'/admin/api/review/reject/{form.id}',
            json={'reason': 'x'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()['success'])

    def test_reject_invalid_stage_returns_403(self):
        form = self._form(self.info_same.number, status='中心已审核')
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/reject/{form.id}',
            json={'reason': 'x'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()['success'])

    def test_reject_already_rejected_keeps_update_branch(self):
        form = self._form(self.info_same.number, status='已驳回')
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/reject/{form.id}',
            json={'reason': 'updated reason'},
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body['success'])
        self.assertEqual(body['new_status'], '已驳回')

    def test_delete_form_group_mixed_scope_denies_atomically(self):
        logical_id = 90001
        form_a = self._form(self.info_same.number)
        form_a.unique_id = logical_id
        registration = CourseRegistration(
            course_code='C1', selection_code='S1', user_id=self.info_same.id,
        )
        db.session.add(registration)
        db.session.flush()
        form_a.registration_id = registration.id
        db.session.add(form_a)
        db.session.flush()
        db.session.add(ScoreRecord(
            form_id=form_a.id,
            reviewer_id=self.center_manager.id,
        ))

        form_b = self._form('NO_SUCH_NUMBER')
        form_b.unique_id = logical_id
        db.session.add(form_b)
        db.session.commit()

        self._login(self.center_manager)
        response = self.client.delete(f'/admin/api/review/group/{logical_id}')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()['success'])

        db.session.refresh(form_a)
        db.session.refresh(form_b)
        self.assertIsNotNone(LectureForm.query.get(form_a.id))
        self.assertIsNotNone(LectureForm.query.get(form_b.id))
        self.assertIsNotNone(ScoreRecord.query.filter_by(form_id=form_a.id).first())
        self.assertIsNotNone(CourseRegistration.query.get(registration.id))

    def test_bulk_missing_priority_returns_404_before_403(self):
        self._login(self.super_admin)
        foreign_form = self._form('NO_SUCH_NUMBER')
        missing_id = 999999
        response = self.client.post(
            '/admin/api/review/form-tags/preview',
            json={'form_ids': [missing_id, foreign_form.id]},
        )
        self.assertEqual(response.status_code, 404)
        body = response.get_json()
        self.assertFalse(body['success'])
        self.assertIn('不存在', body['message'])

    def test_get_form_for_review_cross_scope_returns_403(self):
        self._login(self.dept_manager)
        foreign_form = self._form(self.info_cross_dept.number)
        response = self.client.get(f'/admin/api/review/form/{foreign_form.id}')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()['success'])

    def test_review_form_draft_cross_scope_get_and_put_return_403(self):
        self._login(self.dept_manager)
        foreign_form = self._form(self.info_cross_dept.number)
        get_response = self.client.get(
            f'/admin/api/review/form/{foreign_form.id}/draft'
        )
        self.assertEqual(get_response.status_code, 403)
        self.assertFalse(get_response.get_json()['success'])

        put_response = self.client.put(
            f'/admin/api/review/form/{foreign_form.id}/draft',
            json={'data': {}},
        )
        self.assertEqual(put_response.status_code, 403)
        self.assertFalse(put_response.get_json()['success'])

    def test_review_queue_group_latest_uses_id_not_updated_at(self):
        # Lower id is newer by activity time, but higher id must still be latest.
        form_low = self._form(self.info_same.number, status='部门已审核')
        form_low.unique_id = 500
        form_low.updated_at = datetime(2030, 1, 1)
        form_high = self._form(self.info_same.number, status='部门已审核')
        form_high.unique_id = 500
        form_high.updated_at = datetime(2020, 1, 1)
        db.session.add_all([form_low, form_high])
        db.session.commit()

        self._login(self.center_manager)
        response = self.client.get('/admin/api/review/forms')
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        group = next(g for g in body['forms'] if g['unique_id'] == 500)
        ids = [f['id'] for f in group['forms']]
        self.assertIn(form_high.id, ids)
        self.assertIn(form_low.id, ids)
        can_review = {f['id']: f['can_review'] for f in group['forms']}
        self.assertTrue(can_review[form_high.id])
        self.assertFalse(can_review[form_low.id])

    def test_review_queue_loads_more_forms_than_sqlite_variable_limit(self):
        forms = [
            make_synthetic_form(
                unique_id=index,
                listener_number=self.info_same.number,
                status='部门已审核',
            )
            for index in range(1, 1102)
        ]
        db.session.add_all(forms)
        db.session.flush()
        # Keep one form unchecked and persist enough assessments to exercise
        # the separate finding-count query past the same limit.
        timestamp = datetime(2026, 4, 15)
        assessments = [
            ReviewAssessment(
                id=f'assessment-{form.id}', form_id=form.id,
                fingerprint=f'{form.id:064x}', created_at=timestamp,
            )
            for form in forms[:-1]
        ]
        db.session.add_all(assessments)
        db.session.add_all([
            ReviewAssessment(id='z-older', form_id=forms[0].id,
                             fingerprint='f' * 64, created_at=datetime(2026, 4, 14)),
            ReviewAssessment(id='a-tied', form_id=forms[0].id,
                             fingerprint='e' * 64, created_at=timestamp),
        ])
        db.session.flush()
        db.session.add_all([
            ReviewFinding(assessment_id=assessment.id, source='schedule',
                          severity='review', title='Synthetic finding', message='Synthetic evidence')
            for assessment in (assessments[0], assessments[-1])
        ])
        db.session.add(ReviewReadReceipt(
            user_id=self.dept_manager.id, form_id=forms[-1].id,
            form_updated_at=forms[-1].updated_at,
        ))
        db.session.commit()
        first_id, last_assessed_id, unchecked_id = forms[0].id, forms[-2].id, forms[-1].id
        expected_assessments = {row.form_id: row.id for row in assessments}

        def enforce_limit(conn, cursor, statement, parameters, context, executemany):
            # Python 3.10 lacks sqlite3.Connection.setlimit().
            setlimit = getattr(conn.connection.driver_connection, 'setlimit', None)
            if setlimit is not None:
                setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
            elif not executemany and len(parameters) > 999:
                raise sqlite3.OperationalError('too many SQL variables')

        engine = db.engine
        event.listen(engine, 'before_cursor_execute', enforce_limit)
        try:
            for reviewer in (self.center_manager, self.dept_manager):
                with self.subTest(reviewer=reviewer.number):
                    self._login(reviewer)
                    response = self.client.get('/admin/api/review/forms')
                    body = response.get_json()
                    self.assertEqual(response.status_code, 200, body.get('message', '')[:180])
                    queue = {group['forms'][0]['id']: group['forms'][0] for group in body['forms']}
                    self.assertEqual(len(queue), len(forms))
                    self.assertEqual({form_id: row['automation']['assessment_id']
                                      for form_id, row in queue.items() if row['automation']},
                                     expected_assessments)
                    self.assertEqual(queue[first_id]['automation']['finding_count'], 1)
                    self.assertEqual(queue[last_assessed_id]['automation']['finding_count'], 1)
                    self.assertIsNone(queue[unchecked_id]['automation'])
                    if reviewer is self.dept_manager:
                        self.assertEqual(queue[first_id]['department_read']['state'], 'unread')
                        self.assertEqual(queue[unchecked_id]['department_read']['state'], 'read')
        finally:
            event.remove(engine, 'before_cursor_execute', enforce_limit)

    def test_review_queue_failure_keeps_database_details_private(self):
        self._login(self.center_manager)
        self._form(self.info_same.number)
        db.session.commit()
        errors = StringIO()
        with patch('app.review_automation.routes.latest_assessment_summaries',
                   side_effect=RuntimeError('synthetic SQL and form parameters')):
            response = self.client.get('/admin/api/review/forms', environ_overrides={'wsgi.errors': errors})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {
            'success': False,
            'message': '加载审核队列失败，请稍后重试。',
        })
        self.assertEqual(errors.getvalue().splitlines(), [
            'review_failed reason=review_queue_load_failed record=-',
        ])

    def test_submit_form_review_effective_lecture_date_change_is_audited(self):
        form = self._form(self.info_same.number, status='待审核')
        form.lecture_date = '原始日期'
        db.session.commit()
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/form/{form.id}',
            json={
                'form_data': {'lecture_date_display': '2026/06/05星期五'},
                'review_comment': '',
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body['success'])
        new_version = form.get_latest_version()
        self.assertEqual(new_version.lecture_date, '2026/06/05星期五')
        self.assertIn('听课时间', new_version.review_comment or '')

    def test_submit_form_review_effective_class_period_change_is_audited(self):
        form = self._form(self.info_same.number, status='待审核')
        form.class_period = '第1-2节'
        db.session.commit()
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/form/{form.id}',
            json={
                'form_data': {'start_period': '3', 'end_period': '4'},
                'review_comment': '',
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body['success'])
        new_version = form.get_latest_version()
        self.assertEqual(new_version.class_period, '第3-4节')
        self.assertIn('第几节', new_version.review_comment or '')


if __name__ == '__main__':
    unittest.main()
