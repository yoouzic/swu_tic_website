# -*- coding: utf-8 -*-
"""Score input validation tests for review endpoints (Phase 2A.3-P2)."""
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app.app import app
from app.models import (
    Department,
    Group,
    LectureForm,
    LectureFormDraft,
    Permission,
    RolePermission,
    ScoreRecord,
    User,
    db,
)
from app.services.review_scores import (
    ScoreValidationError,
    normalize_score_items,
)
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database
from tests.review_request_utils import post_opened_review


class ScoreNormalizerTests(unittest.TestCase):
    def test_none_becomes_empty_list(self):
        self.assertEqual(normalize_score_items(None), [])

    def test_empty_list(self):
        self.assertEqual(normalize_score_items([]), [])

    def test_non_list_raises(self):
        with self.assertRaises(ScoreValidationError):
            normalize_score_items({})

    def test_non_dict_item_raises(self):
        with self.assertRaises(ScoreValidationError):
            normalize_score_items(['x'])

    def test_normal_numeric_string(self):
        result = normalize_score_items([
            {'reason': 'x', 'department_score': '0.5', 'personal_score': '1'}
        ])
        self.assertEqual(result[0]['department_score'], 0.5)
        self.assertEqual(result[0]['personal_score'], 1.0)

    def test_empty_score_is_zero(self):
        result = normalize_score_items([
            {'reason': 'x', 'department_score': None, 'personal_score': ''}
        ])
        self.assertEqual(result[0]['department_score'], 0.0)
        self.assertEqual(result[0]['personal_score'], 0.0)

    def test_non_numeric_raises(self):
        with self.assertRaises(ScoreValidationError):
            normalize_score_items([
                {'reason': 'x', 'department_score': 'abc', 'personal_score': 0}
            ])

    def test_negative_raises(self):
        with self.assertRaises(ScoreValidationError):
            normalize_score_items([
                {'reason': 'x', 'department_score': -1, 'personal_score': 0}
            ])

    def test_nan_raises(self):
        with self.assertRaises(ScoreValidationError):
            normalize_score_items([
                {'reason': 'x', 'department_score': 'nan', 'personal_score': 0}
            ])

    def test_infinity_raises(self):
        with self.assertRaises(ScoreValidationError):
            normalize_score_items([
                {'reason': 'x', 'department_score': 'inf', 'personal_score': 0}
            ])

    def test_missing_reason_raises(self):
        with self.assertRaises(ScoreValidationError):
            normalize_score_items([
                {'department_score': 1, 'personal_score': 0}
            ])

    def test_empty_reason_raises(self):
        with self.assertRaises(ScoreValidationError):
            normalize_score_items([
                {'reason': '', 'department_score': 1, 'personal_score': 0}
            ])

    def test_is_auto_flag(self):
        result = normalize_score_items([
            {'reason': 'x', 'department_score': 1, 'personal_score': 0, 'is_auto': True}
        ])
        self.assertTrue(result[0]['is_auto_generated'])

    def test_is_auto_generated_flag(self):
        result = normalize_score_items([
            {'reason': 'x', 'department_score': 1, 'personal_score': 0, 'is_auto_generated': True}
        ])
        self.assertTrue(result[0]['is_auto_generated'])

    def test_is_auto_generated_precedence_when_both_present(self):
        result = normalize_score_items([
            {
                'reason': 'x',
                'department_score': 1,
                'personal_score': 0,
                'is_auto': False,
                'is_auto_generated': True,
            }
        ])
        self.assertTrue(result[0]['is_auto_generated'])

    def test_zero_zero_normalization(self):
        result = normalize_score_items([
            {'reason': 'x', 'department_score': 0, 'personal_score': 0}
        ])
        self.assertEqual(result[0]['department_score'], 0.0)
        self.assertEqual(result[0]['personal_score'], 0.0)

    def test_boolean_department_score_rejected(self):
        with self.assertRaises(ScoreValidationError):
            normalize_score_items([
                {'reason': 'x', 'department_score': True, 'personal_score': 0}
            ])

    def test_boolean_personal_score_rejected(self):
        with self.assertRaises(ScoreValidationError):
            normalize_score_items([
                {'reason': 'x', 'department_score': 0, 'personal_score': False}
            ])

    def test_auto_false_boolean_is_false(self):
        result = normalize_score_items([
            {
                'reason': 'x',
                'department_score': 1,
                'personal_score': 0,
                'is_auto': False,
            }
        ])
        self.assertFalse(result[0]['is_auto_generated'])

    def test_auto_false_string_is_false(self):
        result = normalize_score_items([
            {
                'reason': 'x',
                'department_score': 1,
                'personal_score': 0,
                'is_auto': 'false',
            }
        ])
        self.assertFalse(result[0]['is_auto_generated'])

    def test_auto_true_string_is_true(self):
        result = normalize_score_items([
            {
                'reason': 'x',
                'department_score': 1,
                'personal_score': 0,
                'is_auto': 'true',
            }
        ])
        self.assertTrue(result[0]['is_auto_generated'])

    def test_auto_numeric_flag_is_normalized(self):
        result = normalize_score_items([
            {
                'reason': 'x',
                'department_score': 1,
                'personal_score': 0,
                'is_auto': 0,
            }
        ])
        self.assertFalse(result[0]['is_auto_generated'])

    def test_auto_invalid_flag_rejected(self):
        with self.assertRaises(ScoreValidationError):
            normalize_score_items([
                {
                    'reason': 'x',
                    'department_score': 1,
                    'personal_score': 0,
                    'is_auto': 'yes',
                }
            ])


class ScoreRouteValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix='score-validation-')

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.db_path = Path(self.temp_dir.name) / f'score-{id(self)}.sqlite'
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
        self.info = self._user(
            'A1', 's-a1', '信息员', dept_a.name, group.id, None)
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

    def _form(self, id_value=None, status='待审核'):
        form = LectureForm(
            id=id_value,
            listener_name='L',
            listener_number=self.info.number,
            lecture_date='2026-01-01',
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

    def _add_review_draft(self, form):
        draft = LectureFormDraft(
            user_id=self.dept_manager.id,
            draft_key=f'review_form:{form.id}',
            payload_json='{"form_data":{},"review_comment":"keep me","score_data":[]}',
        )
        db.session.add(draft)
        db.session.commit()
        return draft

    def _assert_no_mutation(self, form):
        self.assertEqual(LectureForm.query.count(), 1)
        self.assertEqual(form.get_latest_version().id, form.id)
        self.assertEqual(form.status, '待审核')
        self.assertIsNone(ScoreRecord.query.filter_by(form_id=form.id).first())

    def test_submit_review_invalid_score_returns_400_no_mutation(self):
        form = self._form(status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/submit/{form.id}',
            json={
                'score_data': [
                    {'reason': 'x', 'department_score': 'abc', 'personal_score': 0}
                ]
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self.assertEqual(form.get_latest_version().id, form.id)
        self.assertEqual(form.status, '待审核')
        self.assertIsNone(ScoreRecord.query.filter_by(form_id=form.id).first())

    def test_submit_form_review_invalid_score_returns_400_no_mutation(self):
        form = self._form(status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/form/{form.id}',
            json={
                'form_data': {},
                'score_data': [
                    {'reason': 'x', 'department_score': 'nan', 'personal_score': 0}
                ]
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self.assertEqual(form.get_latest_version().id, form.id)
        self.assertEqual(form.status, '待审核')
        self.assertIsNone(ScoreRecord.query.filter_by(form_id=form.id).first())

    def test_submit_form_review_valid_normalized_score_persists(self):
        form = self._form(status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/form/{form.id}',
            json={
                'form_data': {},
                'score_data': [
                    {
                        'reason': '修改教师姓名',
                        'department_score': 0.5,
                        'personal_score': 0.5,
                        'is_auto': True,
                    }
                ]
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        new_version = form.get_latest_version()
        record = ScoreRecord.query.filter_by(form_id=new_version.id).first()
        self.assertIsNotNone(record)
        self.assertAlmostEqual(record.total_department_score, 0.5)
        self.assertAlmostEqual(record.total_personal_score, 0.5)
        self.assertTrue(record.items[0].is_auto_generated)
        self.assertEqual(record.items[0].reason, '修改教师姓名')

    def test_submit_review_legacy_form_malformed_score_json_returns_400_and_preserves_draft(self):
        form = self._form(status='待审核')
        db.session.commit()
        draft = self._add_review_draft(form)
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/submit/{form.id}',
            data={'score_data': '{broken-json'},
            content_type='application/x-www-form-urlencoded',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_no_mutation(form)
        saved_draft = LectureFormDraft.query.filter_by(
            user_id=self.dept_manager.id,
            draft_key=f'review_form:{form.id}',
        ).first()
        self.assertIsNotNone(saved_draft)
        self.assertEqual(saved_draft.payload_json, draft.payload_json)

    def test_submit_review_rejects_boolean_department_score_400(self):
        form = self._form(status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/submit/{form.id}',
            json={
                'score_data': [
                    {'reason': 'x', 'department_score': True, 'personal_score': 0}
                ]
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_no_mutation(form)

    def test_submit_review_rejects_boolean_personal_score_400(self):
        form = self._form(status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/submit/{form.id}',
            json={
                'score_data': [
                    {'reason': 'x', 'department_score': 0, 'personal_score': False}
                ]
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_no_mutation(form)

    def test_submit_form_review_rejects_boolean_department_score_400(self):
        form = self._form(status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/form/{form.id}',
            json={
                'form_data': {},
                'score_data': [
                    {'reason': 'x', 'department_score': True, 'personal_score': 0}
                ]
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_no_mutation(form)

    def test_submit_review_accepts_auto_false_boolean_and_persists_false(self):
        form = self._form(status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/submit/{form.id}',
            json={
                'form_data': {
                    'listener_name': 'L',
                    'lecture_date': '2026-01-01',
                    'class_period': '3-4',
                    'lecture_location': 'A101',
                    'teacher_name': 'T',
                    'teacher_college': 'C',
                    'course_title': 'C',
                    'student_grade_class': 'G',
                    'abnormal_situation': '无',
                    'teaching_method': 'M',
                    'classroom_discipline': 'D',
                    'classroom_atmosphere': 'A',
                    'courseware_quality': 'Q',
                    'overall_effect': 'E',
                    'quality_case': 'Case',
                    'course_feedback': 'F',
                    'suggestions': 'S',
                    'student_signature1': 'sig',
                    'contact_phone1': '123',
                },
                'score_data': [
                    {
                        'reason': 'x',
                        'department_score': 1,
                        'personal_score': 0,
                        'is_auto': False,
                    }
                ]
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        new_version = form.get_latest_version()
        record = ScoreRecord.query.filter_by(form_id=new_version.id).first()
        self.assertIsNotNone(record)
        self.assertFalse(record.items[0].is_auto_generated)

    def test_submit_review_accepts_auto_false_string_and_does_not_make_it_true(self):
        form = self._form(status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/submit/{form.id}',
            json={
                'form_data': {
                    'listener_name': 'L',
                    'lecture_date': '2026-01-01',
                    'class_period': '3-4',
                    'lecture_location': 'A101',
                    'teacher_name': 'T',
                    'teacher_college': 'C',
                    'course_title': 'C',
                    'student_grade_class': 'G',
                    'abnormal_situation': '无',
                    'teaching_method': 'M',
                    'classroom_discipline': 'D',
                    'classroom_atmosphere': 'A',
                    'courseware_quality': 'Q',
                    'overall_effect': 'E',
                    'quality_case': 'Case',
                    'course_feedback': 'F',
                    'suggestions': 'S',
                    'student_signature1': 'sig',
                    'contact_phone1': '123',
                },
                'score_data': [
                    {
                        'reason': 'x',
                        'department_score': 1,
                        'personal_score': 0,
                        'is_auto': 'false',
                    }
                ]
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        new_version = form.get_latest_version()
        record = ScoreRecord.query.filter_by(form_id=new_version.id).first()
        self.assertIsNotNone(record)
        self.assertFalse(record.items[0].is_auto_generated)

    def test_submit_review_json_null_envelope_returns_400(self):
        form = self._form(status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/submit/{form.id}',
            data='null',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_no_mutation(form)

    def test_submit_review_json_array_envelope_returns_400(self):
        form = self._form(status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/submit/{form.id}',
            data='[]',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_no_mutation(form)

    def test_submit_review_malformed_json_returns_400(self):
        form = self._form(status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/submit/{form.id}',
            data='{broken',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_no_mutation(form)

    def test_submit_form_review_json_null_envelope_returns_400(self):
        form = self._form(status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/form/{form.id}',
            data='null',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_no_mutation(form)

    def test_submit_form_review_json_array_envelope_returns_400(self):
        form = self._form(status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/form/{form.id}',
            data='[]',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_no_mutation(form)

    def test_submit_form_review_malformed_json_returns_400(self):
        form = self._form(status='待审核')
        db.session.commit()
        self._login(self.dept_manager)
        response = post_opened_review(self.client,
            f'/admin/api/review/form/{form.id}',
            data='{broken',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self._assert_no_mutation(form)


if __name__ == '__main__':
    unittest.main()
