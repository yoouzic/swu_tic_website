import json
import os
import tempfile
import unittest
from unittest import mock

from werkzeug.security import generate_password_hash

TEST_DB_DIR = tempfile.TemporaryDirectory()
os.environ['SQLITE_DB_PATH'] = os.path.join(TEST_DB_DIR.name, 'lecture_form_draft_test.db')
os.environ['INSTANCE_DIR'] = TEST_DB_DIR.name
os.environ['SECRET_KEY'] = 'test-secret-key'

from app.app import app
from app.models import db, LectureForm, LectureFormDraft, User
from app.services.listening_assistant_schedule import ScheduleSourceUnavailable
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


INFO_MEMBER = '\u4fe1\u606f\u5458'
PPT_METHOD = 'PPT\u6f14\u793a\u6cd5'
GOOD = '\u597d'
RECOMMENDED = '\u63a8\u8350'


class LectureFormDraftTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        TEST_DB_DIR.cleanup()

    def setUp(self):
        configure_sqlite_database(app, db, os.environ['SQLITE_DB_PATH'])
        app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
        )
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self.user = self._create_user('1001', 'student-1001')
        self.other_user = self._create_user('1002', 'student-1002')

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()

    def _create_user(self, number, student_id):
        user = User(
            number=number,
            department='test',
            name='Test User',
            gender='-',
            grade='-',
            college='Test College',
            major='-',
            dormitory='-',
            phone='-',
            qq='-',
            student_id=student_id,
            password_hash=generate_password_hash('password'),
            role=INFO_MEMBER,
            group='test',
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user

    def _login_as(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    def _valid_form_payload(self):
        return {
            'lecture_date': '2026-06-04',
            'lecture_date_display': '2026/06/04',
            'start_period': '3',
            'end_period': '4',
            'class_period': '\u7b2c3-4\u8282',
            'lecture_location': '32-302',
            'teacher_name': 'Teacher A',
            'teacher_college': 'Test College',
            'course_title': 'Database Systems',
            'student_grade_class': '2024 Test Class',
            'course_changes': '\u65e0',
            'abnormal_situation': '\u65e0',
            'teaching_method': PPT_METHOD,
            'classroom_discipline': GOOD,
            'classroom_atmosphere': GOOD,
            'courseware_quality': GOOD,
            'overall_effect': GOOD,
            'quality_case': RECOMMENDED,
            'course_feedback': 'This course feedback is intentionally longer than fifty characters for testing submit.',
            'suggestions': '\u65e0',
            'student_signature1': 'Student One',
            'contact_phone1': '13800000001',
            'student_signature2': 'Student Two',
            'contact_phone2': '13800000002',
        }

    def test_save_load_and_delete_current_user_draft(self):
        self._login_as(self.user)
        payload = {
            'lecture_location': '32-302',
            'course_title': 'Database Systems',
            'teaching_method': PPT_METHOD,
        }

        save_response = self.client.put('/user/api/lecture_form_draft', json={'data': payload})
        self.assertEqual(save_response.status_code, 200)
        self.assertTrue(save_response.get_json()['success'])

        load_response = self.client.get('/user/api/lecture_form_draft')
        load_json = load_response.get_json()
        self.assertEqual(load_response.status_code, 200)
        self.assertTrue(load_json['exists'])
        self.assertEqual(load_json['data'], payload)
        self.assertIn('updated_at', load_json)

        delete_response = self.client.delete('/user/api/lecture_form_draft')
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(self.client.get('/user/api/lecture_form_draft').get_json()['exists'])

    def test_draft_is_scoped_to_current_user(self):
        self._login_as(self.user)
        self.client.put('/user/api/lecture_form_draft', json={'data': {'course_title': 'User One Draft'}})

        self._login_as(self.other_user)
        response = self.client.get('/user/api/lecture_form_draft')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()['exists'])

    def test_assistant_draft_is_strictly_namespaced_and_legacy_fields_are_preserved(self):
        self._login_as(self.user)
        payload = {
            'course_title': 'Legacy Course',
            'rejected_ids': ['legacy-id', 7, {'drop': 'nested'}],
            'assistant': {
                'stage': 'confirmed',
                'query': {
                    'lecture_date': '2026-09-18',
                    'room': '8-309',
                },
                'rejected_ids': ['candidate-1', {'phone': '13800000000'}],
                'source_kind': 'primary',
                'source_batch_id': 'batch-current',
                'semester': '2026-2027-1',
                'candidate_id': 'primary:batch-current:abc',
                'overrides': {
                    'lecture_location': '9-101',
                },
                'template_version': 'task4-v1',
                'assistant_filled_fields': [
                    'lecture_date',
                    'course_title',
                    'contact_phone1',
                    {'drop': 'nested'},
                ],
                'assistant_filled_groups': ['period', 'phones'],
            },
        }

        response = self.client.put('/user/api/lecture_form_draft', json={'data': payload})
        self.assertEqual(response.status_code, 200)
        saved = self.client.get('/user/api/lecture_form_draft').get_json()['data']
        self.assertEqual(saved['course_title'], 'Legacy Course')
        self.assertEqual(saved['rejected_ids'], ['legacy-id', 7])
        self.assertEqual(saved['assistant']['stage'], 'confirmed')
        self.assertEqual(saved['assistant']['overrides']['lecture_location'], '9-101')
        self.assertEqual(
            saved['assistant']['assistant_filled_fields'],
            ['lecture_date', 'course_title'],
        )
        self.assertEqual(saved['assistant']['assistant_filled_groups'], ['period'])

    def test_guided_assistant_draft_round_trips_bounded_state_and_history(self):
        self._login_as(self.user)
        payload = {
            'assistant': {
                'stage': 'candidate',
                'guide_state': {
                    'known_facts': {'date': '2026-09-18', 'room': '8-309'},
                    'candidate_ids': ['primary:batch-current:1'],
                    'asked_question_kinds': ['memory', 'date'],
                    'question_count': 1,
                    'stage': 'candidate',
                },
                'history': [
                    {'kind': 'memory', 'answer_code': 'A', 'custom_value': None},
                    {'kind': 'date', 'answer_code': 'D', 'custom_value': '2026-09-18'},
                ],
                'template_version': 'task4-v1',
            },
        }

        response = self.client.put('/user/api/lecture_form_draft', json={'data': payload})

        self.assertEqual(response.status_code, 200)
        saved = self.client.get('/user/api/lecture_form_draft').get_json()['data']
        self.assertEqual(saved['assistant']['guide_state'], payload['assistant']['guide_state'])
        self.assertEqual(saved['assistant']['history'], payload['assistant']['history'])

    def test_assistant_draft_rejects_unknown_nested_guide_keys(self):
        self._login_as(self.user)
        payload = {
            'assistant': {
                'guide_state': {
                    'known_facts': {},
                    'candidate_ids': [],
                    'asked_question_kinds': [],
                    'question_count': 0,
                    'stage': 'question',
                    'candidate_snapshot': 'must not be accepted',
                },
            },
        }

        response = self.client.put('/user/api/lecture_form_draft', json={'data': payload})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.client.get('/user/api/lecture_form_draft').get_json()['exists'])

    def test_assistant_draft_rejects_unsafe_candidate_ids_and_sensitive_history(self):
        self._login_as(self.user)
        base_state = {
            'known_facts': {},
            'candidate_ids': ['primary/batch/1'],
            'asked_question_kinds': [],
            'question_count': 0,
            'stage': 'question',
        }
        for state, history in (
            (base_state, []),
            ({**base_state, 'candidate_ids': ['primary:batch:1']}, [
                {'kind': 'room', 'answer_code': 'D', 'custom_value': '13800000000'},
            ]),
            ({**base_state, 'candidate_ids': ['primary:batch:1']}, [
                {'kind': 'room', 'answer_code': 'D', 'custom_value': '138 0000 0000'},
            ]),
            ({**base_state, 'candidate_ids': ['primary:batch:1']}, [
                {'kind': 'room', 'answer_code': 'D', 'custom_value': '１３８－００００－００００'},
            ]),
        ):
            with self.subTest(state=state, history=history):
                response = self.client.put(
                    '/user/api/lecture_form_draft',
                    json={'data': {'assistant': {'guide_state': state, 'history': history}}},
                )
                self.assertEqual(response.status_code, 400)

    def test_assistant_draft_rejects_known_nested_values_with_unsupported_types(self):
        self._login_as(self.user)
        response = self.client.put(
            '/user/api/lecture_form_draft',
            json={
                'data': {
                    'assistant': {
                        'overrides': {'course_title': {'secret': 'x'}},
                    },
                },
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_legacy_assistant_unknown_fields_do_not_hide_form_draft(self):
        self._login_as(self.user)
        db.session.add(LectureFormDraft(
            user_id=self.user.id,
            draft_key='submit_form',
            payload_json=json.dumps({
                'course_title': 'Keep this form draft',
                'assistant': {'stage': 'confirmed', 'obsolete_key': 'drop only this snapshot'},
            }),
        ))
        db.session.commit()

        response = self.client.get('/user/api/lecture_form_draft')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()['data']
        self.assertEqual(payload['course_title'], 'Keep this form draft')
        self.assertNotIn('assistant', payload)

    def test_rewound_guide_draft_clears_old_confirmation_and_fill_provenance(self):
        self._login_as(self.user)
        payload = {
            'assistant': {
                'stage': 'confirmed',
                'source_kind': 'primary',
                'source_batch_id': 'batch-old',
                'candidate_id': 'primary:batch-old:9',
                'overrides': {'lecture_location': '9-101'},
                'assistant_filled_fields': ['course_title'],
                'assistant_filled_groups': ['period'],
                'guide_state': {
                    'known_facts': {'date': '2026-09-18'},
                    'candidate_ids': [],
                    'asked_question_kinds': ['memory'],
                    'question_count': 0,
                    'stage': 'question',
                },
                'history': [],
            },
        }

        response = self.client.put('/user/api/lecture_form_draft', json={'data': payload})

        self.assertEqual(response.status_code, 200)
        saved = self.client.get('/user/api/lecture_form_draft').get_json()['data']['assistant']
        self.assertEqual(saved['stage'], 'question')
        for stale_key in (
            'source_kind',
            'source_batch_id',
            'candidate_id',
            'overrides',
            'assistant_filled_fields',
            'assistant_filled_groups',
        ):
            self.assertNotIn(stale_key, saved)

    def test_assistant_draft_without_guide_payload_remains_legacy_compatible(self):
        self._login_as(self.user)
        payload = {'course_title': 'Legacy direct draft'}

        response = self.client.put('/user/api/lecture_form_draft', json={'data': payload})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.client.get('/user/api/lecture_form_draft').get_json()['data'],
            payload,
        )

    def test_successful_form_submit_clears_current_user_draft(self):
        self._login_as(self.user)
        self.client.put('/user/api/lecture_form_draft', json={'data': {'course_title': 'Old Draft'}})
        self.assertEqual(LectureFormDraft.query.filter_by(user_id=self.user.id).count(), 1)

        response = self.client.post('/user/submit_form', data=self._valid_form_payload())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(LectureForm.query.filter_by(listener_number=self.user.number).count(), 1)
        self.assertEqual(LectureFormDraft.query.filter_by(user_id=self.user.id).count(), 0)

    def test_assistant_source_unavailable_uses_normal_form_error_path(self):
        self._login_as(self.user)
        payload = self._valid_form_payload()
        payload['assistant_payload'] = '{"stage":"confirmed"}'

        with mock.patch(
            'app.blueprints.user.forms.revalidate_selection',
            side_effect=ScheduleSourceUnavailable(status='INVALID_AUTHORITY'),
        ):
            response = self.client.post('/user/submit_form', data=payload)

        self.assertEqual(response.status_code, 400)
        self.assertIn('听课助手信息已失效', response.get_data(as_text=True))
        self.assertEqual(
            LectureForm.query.filter_by(listener_number=self.user.number).count(),
            0,
        )


if __name__ == '__main__':
    unittest.main()
