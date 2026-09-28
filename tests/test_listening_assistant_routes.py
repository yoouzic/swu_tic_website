import os
import tempfile
import unittest
from datetime import date
from unittest import mock

from werkzeug.security import generate_password_hash

TEST_DB_DIR = tempfile.TemporaryDirectory(prefix='listening-assistant-routes-')
os.environ['SQLITE_DB_PATH'] = os.path.join(TEST_DB_DIR.name, 'routes.sqlite')
os.environ['INSTANCE_DIR'] = TEST_DB_DIR.name
os.environ['SECRET_KEY'] = 'test-secret-key'

from app.app import app
from app.models import LectureForm, ListeningAssistantEvidence, User, db
from app.services.listening_assistant_contracts import ScheduleEntry
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


LOOKUP_DATE = date(2026, 9, 18)
SEMESTER = '2026-2027-1'
PRIMARY_BATCH = 'batch-current'
BACKUP_BATCH = '7'


def schedule_entry(
    *,
    source_kind='primary',
    source_batch_id=PRIMARY_BATCH,
    source_row=1,
    room='8-309',
    teacher_name='张老师',
    course_title='数据结构',
    period=(3, 4),
):
    return ScheduleEntry(
        entry_id=f'{source_kind}:{source_batch_id}:{source_row}',
        lecture_date=LOOKUP_DATE,
        room=room,
        period=period,
        course_code='CS101',
        selection_code=f'S{source_row:03d}',
        course_title=course_title,
        teacher_name=teacher_name,
        teacher_college='计算机学院',
        student_grade_class='2024级计算机1班',
        weekday=5,
        semester=SEMESTER,
        source_kind=source_kind,
        source_batch_id=source_batch_id,
        source_row=source_row,
    )


class ListeningAssistantRoutesTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        TEST_DB_DIR.cleanup()

    def setUp(self):
        configure_sqlite_database(app, db, os.environ['SQLITE_DB_PATH'])
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self.user = self._create_user('1001', 'student-1001', role='信息员')
        self.primary = [
            schedule_entry(source_row=1),
            schedule_entry(source_row=2, teacher_name='李老师', room='9-101'),
        ]
        self.backup = [
            schedule_entry(
                source_kind='backup',
                source_batch_id=BACKUP_BATCH,
                source_row=7,
                teacher_name='备用老师',
                room='10-202',
            )
        ]
        self.loader_patch = mock.patch(
            'app.services.listening_assistant.load_schedule_entries',
            side_effect=self._load_schedule_entries,
        )
        self.loader_patch.start()

    def tearDown(self):
        self.loader_patch.stop()
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()

    def _create_user(self, number, student_id, *, role='信息员', is_active=True):
        user = User(
            number=number,
            department='test',
            name='Test User',
            gender='-',
            grade='-',
            college='Test College',
            major='-',
            dormitory='-',
            phone='13800000000',
            qq='-',
            student_id=student_id,
            password_hash=generate_password_hash('password'),
            role=role,
            group='test',
            is_active=is_active,
        )
        db.session.add(user)
        db.session.commit()
        return user

    def _login(self, user=None):
        user = user or self.user
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    def _load_schedule_entries(self, *, source_kind='primary', **_kwargs):
        return list(self.primary if source_kind == 'primary' else self.backup)

    @staticmethod
    def _assert_envelope(response, *, success=None):
        payload = response.get_json()
        assert payload is not None
        assert set(payload) == {'success', 'data', 'message'}
        assert isinstance(payload['success'], bool)
        assert isinstance(payload['message'], str)
        if success is not None:
            assert payload['success'] is success
        return payload

    def test_all_endpoints_require_an_active_logged_in_user_with_json_errors(self):
        requests = (
            lambda: self.client.get(
                '/user/api/listening-assistant/candidates',
                query_string={'date': '2026-09-18', 'room': '8-309'},
            ),
            lambda: self.client.post('/user/api/listening-assistant/fallback', json={}),
            lambda: self.client.post('/user/api/listening-assistant/confirm', json={}),
        )
        for make_request in requests:
            with self.subTest(endpoint=make_request):
                response = make_request()
                self.assertEqual(response.status_code, 401)
                self._assert_envelope(response, success=False)

    def test_inactive_session_is_rejected_without_personal_fields(self):
        inactive = self._create_user('1002', 'student-1002', is_active=False)
        self._login(inactive)

        response = self.client.get(
            '/user/api/listening-assistant/candidates',
            query_string={'date': '2026-09-18', 'room': '8-309'},
        )

        self.assertEqual(response.status_code, 401)
        payload = self._assert_envelope(response, success=False)
        self.assertNotIn('phone', str(payload))
        self.assertNotIn('student_signature1', str(payload))

    def test_get_candidates_supports_room_teacher_both_rejections_and_always_none(self):
        self._login()

        by_room = self.client.get(
            '/user/api/listening-assistant/candidates',
            query_string=[
                ('date', '2026-09-18'),
                ('room', '8-309'),
                ('rejected_ids', 'not-a-candidate'),
            ],
        )
        room_payload = self._assert_envelope(by_room, success=True)
        self.assertTrue(room_payload['data']['always_show_none'])
        self.assertEqual(len(room_payload['data']['candidates']), 1)
        candidate = room_payload['data']['candidates'][0]

        by_teacher = self.client.get(
            '/user/api/listening-assistant/candidates',
            query_string={'date': '2026-09-18', 'teacher': '李老师'},
        )
        teacher_payload = self._assert_envelope(by_teacher, success=True)
        self.assertEqual(teacher_payload['data']['candidates'][0]['room'], '9-101')

        both = self.client.get(
            '/user/api/listening-assistant/candidates',
            query_string={'date': '2026-09-18', 'room': '8-309', 'teacher': '张老师'},
        )
        both_payload = self._assert_envelope(both, success=True)
        self.assertEqual(
            [item['candidate_id'] for item in both_payload['data']['candidates']],
            [candidate['candidate_id']],
        )

        rejected = self.client.get(
            '/user/api/listening-assistant/candidates',
            query_string=[
                ('date', '2026-09-18'),
                ('room', '8-309'),
                ('rejected_ids', candidate['candidate_id']),
                ('rejected_ids', 'another-rejected-id'),
            ],
        )
        rejected_payload = self._assert_envelope(rejected, success=True)
        self.assertEqual(rejected_payload['data']['candidates'], [])
        self.assertTrue(rejected_payload['data']['always_show_none'])

        no_result = self.client.get(
            '/user/api/listening-assistant/candidates',
            query_string={'date': '2026-09-18', 'teacher': '不存在的老师'},
        )
        no_result_payload = self._assert_envelope(no_result, success=True)
        self.assertEqual(no_result_payload['data']['candidates'], [])
        self.assertTrue(no_result_payload['data']['always_show_none'])

    def test_get_primary_output_is_public_schedule_data_only(self):
        self._login()
        response = self.client.get(
            '/user/api/listening-assistant/candidates',
            query_string={'date': '2026-09-18', 'room': '8-309', 'period': '3-4', 'semester': SEMESTER},
        )

        payload = self._assert_envelope(response, success=True)
        candidate = payload['data']['candidates'][0]
        self.assertNotIn('phone', candidate)
        self.assertNotIn('student_phone', candidate)
        self.assertNotIn('student_signature1', candidate)
        self.assertNotIn('student_signature2', candidate)
        self.assertNotIn('course_feedback', candidate)
        self.assertNotIn('evaluation', candidate)

    def test_get_rejects_invalid_date_or_missing_anchor_as_json(self):
        self._login()
        for query in (
            {'date': 'not-a-date', 'room': '8-309'},
            {'date': '2026-09-18'},
        ):
            with self.subTest(query=query):
                response = self.client.get(
                    '/user/api/listening-assistant/candidates',
                    query_string=query,
                )
                self.assertEqual(response.status_code, 400)
                self._assert_envelope(response, success=False)

    def test_get_rejects_conflicting_aliases_and_accepts_equal_aliases(self):
        self._login()
        conflicting_queries = (
            [
                ('date', '2026-09-18'),
                ('lecture_date', '2026-09-19'),
                ('room', '8-309'),
            ],
            [
                ('date', '2026-09-18'),
                ('room', '8-309'),
                ('teacher', '张老师'),
                ('teacher_name', '李老师'),
            ],
        )
        for query in conflicting_queries:
            with self.subTest(query=query):
                response = self.client.get(
                    '/user/api/listening-assistant/candidates',
                    query_string=query,
                )
                self.assertEqual(response.status_code, 400)
                self._assert_envelope(response, success=False)

        response = self.client.get(
            '/user/api/listening-assistant/candidates',
            query_string=[
                ('date', '2026-09-18'),
                ('lecture_date', '2026-09-18'),
                ('room', '8-309'),
                ('teacher', '张老师'),
                ('teacher_name', '张老师'),
            ],
        )
        payload = self._assert_envelope(response, success=True)
        self.assertEqual(len(payload['data']['candidates']), 1)

    def test_fallback_requires_approved_reason_and_explicit_retired_batch(self):
        self._login()
        base = {
            'date': '2026-09-18',
            'teacher': '备用老师',
            'rejected_ids': [],
            'reason': 'no_result',
            'source_batch_id': BACKUP_BATCH,
            'semester': SEMESTER,
        }

        response = self.client.post('/user/api/listening-assistant/fallback', json=base)
        payload = self._assert_envelope(response, success=True)
        self.assertEqual(payload['data']['source_kind'], 'backup')
        self.assertEqual(payload['data']['source_label'], '备用课表线索 · 需核对')
        self.assertEqual(payload['data']['source_batch_id'], BACKUP_BATCH)
        self.assertEqual(payload['data']['fallback_reason'], 'no_result')
        self.assertTrue(payload['data']['explicit_fallback'])
        self.assertFalse(payload['data']['acknowledged_source'])
        self.assertTrue(payload['data']['needs_confirmation'])
        self.assertTrue(payload['data']['candidates'][0]['needs_confirmation'])

        for reason in ('unsupported', '', None):
            invalid = dict(base)
            invalid['reason'] = reason
            with self.subTest(reason=reason):
                response = self.client.post(
                    '/user/api/listening-assistant/fallback',
                    json=invalid,
                )
                self.assertEqual(response.status_code, 400)
                self._assert_envelope(response, success=False)

        for field in ('teacher', 'date', 'semester', 'source_batch_id'):
            invalid = dict(base)
            invalid[field] = ''
            with self.subTest(field=field):
                response = self.client.post(
                    '/user/api/listening-assistant/fallback',
                    json=invalid,
                )
                self.assertEqual(response.status_code, 400)
                self._assert_envelope(response, success=False)

        for invalid_batch in (0, -1, True, '0', '01', '+7', ' retired-7', 'retired-7'):
            invalid = dict(base)
            invalid['source_batch_id'] = invalid_batch
            with self.subTest(invalid_batch=invalid_batch):
                response = self.client.post(
                    '/user/api/listening-assistant/fallback',
                    json=invalid,
                )
                self.assertEqual(response.status_code, 400)
                self._assert_envelope(response, success=False)

    def test_fallback_wraps_expected_batch_errors_but_not_operational_errors(self):
        self._login()
        payload = {
            'date': '2026-09-18',
            'teacher': '备用老师',
            'rejected_ids': [],
            'reason': 'no_result',
            'source_batch_id': BACKUP_BATCH,
            'semester': SEMESTER,
        }

        for error_message in (
            'backup source batch does not exist',
            'backup source batch must be retired',
            'backup source batch semester does not match requested semester',
        ):
            with self.subTest(error_message=error_message):
                with mock.patch(
                    'app.blueprints.user.listening_assistant.ListeningAssistantService.search_backup',
                    side_effect=ValueError(error_message),
                ):
                    response = self.client.post(
                        '/user/api/listening-assistant/fallback',
                        json=payload,
                    )
                self.assertEqual(response.status_code, 400)
                self._assert_envelope(response, success=False)

        with mock.patch(
            'app.blueprints.user.listening_assistant.ListeningAssistantService.search_backup',
            side_effect=ValueError('database unavailable'),
        ):
            with self.assertRaises(ValueError) as raised:
                self.client.post(
                    '/user/api/listening-assistant/fallback',
                    json=payload,
                )
            self.assertIs(type(raised.exception), ValueError)

    def test_fallback_rejects_an_explicit_false_fallback_flag(self):
        self._login()
        response = self.client.post(
            '/user/api/listening-assistant/fallback',
            json={
                'date': '2026-09-18',
                'teacher': '备用老师',
                'reason': 'no_result',
                'source_batch_id': BACKUP_BATCH,
                'semester': SEMESTER,
                'explicit_fallback': False,
            },
        )
        self.assertEqual(response.status_code, 400)
        self._assert_envelope(response, success=False)

    def _primary_candidate(self):
        response = self.client.get(
            '/user/api/listening-assistant/candidates',
            query_string={'date': '2026-09-18', 'room': '8-309'},
        )
        return self._assert_envelope(response, success=True)['data']['candidates'][0]

    def _backup_candidate(self):
        response = self.client.post(
            '/user/api/listening-assistant/fallback',
            json={
                'date': '2026-09-18',
                'teacher': '备用老师',
                'reason': 'rejected_candidates',
                'source_batch_id': BACKUP_BATCH,
                'semester': SEMESTER,
            },
        )
        return self._assert_envelope(response, success=True)['data']['candidates'][0]

    def _confirm_payload(self, candidate, *, source_kind='primary', **overrides):
        payload = {
            'query': {
                'date': '2026-09-18',
                'room': candidate['room'],
                'teacher': candidate['teacher_name'],
            },
            'candidate_id': candidate['candidate_id'],
            'source_kind': source_kind,
            'semester': SEMESTER,
            'overrides': {},
            'template_version': 'task5-v1',
            'stage': 'confirmed',
        }
        if source_kind == 'backup':
            payload.update({
                'source_batch_id': BACKUP_BATCH,
                'acknowledged_source': True,
                'explicit_fallback': True,
                'reason': 'rejected_candidates',
            })
        payload.update(overrides)
        return payload

    def test_confirm_revalidates_fresh_primary_candidate_and_returns_safe_data_only(self):
        self._login()
        candidate = self._primary_candidate()
        before_forms = LectureForm.query.count()
        before_evidence = ListeningAssistantEvidence.query.count()

        response = self.client.post(
            '/user/api/listening-assistant/confirm',
            json=self._confirm_payload(
                candidate,
                overrides={'room': '9-101', 'period': [5, 6]},
            ),
        )

        payload = self._assert_envelope(response, success=True)
        self.assertEqual(payload['data']['candidate']['candidate_id'], candidate['candidate_id'])
        self.assertEqual(payload['data']['field_snapshot']['lecture_location'], '9-101')
        self.assertEqual(payload['data']['field_snapshot']['class_period'], '第5-6节')
        self.assertTrue(payload['data']['confirmation']['confirmed'])
        self.assertEqual(payload['data']['confirmation']['source_kind'], 'primary')
        self.assertNotIn('phone', str(payload['data']))
        self.assertNotIn('student_signature1', str(payload['data']))
        self.assertNotIn('course_feedback', str(payload['data']))
        self.assertEqual(LectureForm.query.count(), before_forms)
        self.assertEqual(ListeningAssistantEvidence.query.count(), before_evidence)

    def test_confirm_rejects_conflicting_top_level_aliases_and_canonicalizes_equal_aliases(self):
        self._login()
        candidate = self._primary_candidate()
        payload = self._confirm_payload(candidate)
        payload.pop('query')
        payload.update({
            'room': candidate['room'],
            'teacher': candidate['teacher_name'],
            'teacher_name': candidate['teacher_name'],
            'date': '2026-09-18',
            'lecture_date': '2026-09-19',
        })

        response = self.client.post(
            '/user/api/listening-assistant/confirm',
            json=payload,
        )
        self.assertEqual(response.status_code, 400)
        self._assert_envelope(response, success=False)

        payload['lecture_date'] = '2026-09-18'
        payload['teacher'] = '李老师'
        response = self.client.post(
            '/user/api/listening-assistant/confirm',
            json=payload,
        )
        self.assertEqual(response.status_code, 400)
        self._assert_envelope(response, success=False)

        payload['teacher'] = candidate['teacher_name']
        response = self.client.post(
            '/user/api/listening-assistant/confirm',
            json=payload,
        )
        result = self._assert_envelope(response, success=True)
        query = result['data']['query']
        self.assertEqual(query['lecture_date'], '2026-09-18')
        self.assertEqual(query['teacher_name'], candidate['teacher_name'])
        self.assertNotIn('date', query)
        self.assertNotIn('teacher', query)

    def test_confirm_rejects_nested_top_level_field_conflicts_and_canonicalizes_equal_values(self):
        self._login()
        candidate = self._primary_candidate()
        base = self._confirm_payload(candidate)
        base['query'].update({
            'room': '8-309',
            'period': [3, 4],
            'student_grade_class': '2024级计算机1班',
            'semester': SEMESTER,
        })

        for field, value in (
            ('room', '9-101'),
            ('period', [5, 6]),
            ('student_grade_class', '2025级计算机1班'),
            ('semester', '2025-2026-2'),
        ):
            payload = dict(base)
            payload['query'] = dict(base['query'])
            payload[field] = value
            with self.subTest(field=field):
                response = self.client.post(
                    '/user/api/listening-assistant/confirm',
                    json=payload,
                )
                self.assertEqual(response.status_code, 400)
                self._assert_envelope(response, success=False)

        equal = dict(base)
        equal['query'] = dict(base['query'])
        equal['query']['room'] = '08-0309'
        equal['query']['period'] = '第3-4节'
        equal['room'] = '8-309'
        equal['period'] = [3, 4]
        equal['student_grade_class'] = '2024级计算机1班'
        equal['semester'] = SEMESTER

        response = self.client.post(
            '/user/api/listening-assistant/confirm',
            json=equal,
        )
        result = self._assert_envelope(response, success=True)
        query = result['data']['query']
        self.assertEqual(query['room'], '8-309')
        self.assertEqual(query['period'], [3, 4])
        self.assertEqual(query['student_grade_class'], '2024级计算机1班')
        self.assertEqual(query['semester'], SEMESTER)

    def test_confirm_requires_explicit_semester_provenance(self):
        self._login()
        candidate = self._primary_candidate()
        payload = self._confirm_payload(candidate)
        payload.pop('semester')

        response = self.client.post(
            '/user/api/listening-assistant/confirm',
            json=payload,
        )
        self.assertEqual(response.status_code, 400)
        self._assert_envelope(response, success=False)

        payload['query']['semester'] = SEMESTER
        response = self.client.post(
            '/user/api/listening-assistant/confirm',
            json=payload,
        )
        result = self._assert_envelope(response, success=True)
        self.assertEqual(result['data']['confirmation']['semester'], SEMESTER)

    def test_confirm_missing_lookup_anchor_is_a_json_client_error(self):
        self._login()
        candidate = self._primary_candidate()
        payload = self._confirm_payload(candidate)
        payload['query'] = {'date': '2026-09-18', 'semester': SEMESTER}

        response = self.client.post(
            '/user/api/listening-assistant/confirm',
            json=payload,
        )
        self.assertEqual(response.status_code, 400)
        self._assert_envelope(response, success=False)

    def test_confirm_rejects_stale_candidate_against_fresh_search(self):
        self._login()
        candidate = self._primary_candidate()
        self.primary = [schedule_entry(source_row=99, course_title='新课表课程')]

        response = self.client.post(
            '/user/api/listening-assistant/confirm',
            json=self._confirm_payload(candidate),
        )

        self.assertEqual(response.status_code, 404)
        self._assert_envelope(response, success=False)
        self.assertEqual(LectureForm.query.count(), 0)
        self.assertEqual(ListeningAssistantEvidence.query.count(), 0)

    def test_confirm_backup_requires_acknowledgement_and_preserves_source_provenance(self):
        self._login()
        candidate = self._backup_candidate()
        payload = self._confirm_payload(candidate, source_kind='backup')
        payload['acknowledged_source'] = False

        response = self.client.post(
            '/user/api/listening-assistant/confirm',
            json=payload,
        )
        self.assertEqual(response.status_code, 400)
        self._assert_envelope(response, success=False)

        payload['acknowledged_source'] = True
        response = self.client.post(
            '/user/api/listening-assistant/confirm',
            json=payload,
        )
        result = self._assert_envelope(response, success=True)
        self.assertEqual(result['data']['candidate']['source_kind'], 'backup')
        self.assertEqual(result['data']['candidate']['source_batch_id'], BACKUP_BATCH)
        self.assertTrue(result['data']['confirmation']['acknowledged_source'])
        self.assertTrue(result['data']['confirmation']['explicit_fallback'])
        self.assertEqual(result['data']['confirmation']['fallback_reason'], 'rejected_candidates')

        payload['source_batch_id'] = 'retired-7'
        response = self.client.post(
            '/user/api/listening-assistant/confirm',
            json=payload,
        )
        self.assertEqual(response.status_code, 400)
        self._assert_envelope(response, success=False)

        payload['source_batch_id'] = BACKUP_BATCH
        with mock.patch(
            'app.blueprints.user.listening_assistant.ListeningAssistantService.search_backup',
            side_effect=ValueError('backup source batch must be retired'),
        ):
            response = self.client.post(
                '/user/api/listening-assistant/confirm',
                json=payload,
            )
        self.assertEqual(response.status_code, 400)
        self._assert_envelope(response, success=False)

    def test_confirm_rejects_unsafe_overrides_without_db_or_evidence_side_effects(self):
        self._login()
        candidate = self._primary_candidate()
        before_forms = LectureForm.query.count()
        before_evidence = ListeningAssistantEvidence.query.count()

        response = self.client.post(
            '/user/api/listening-assistant/confirm',
            json=self._confirm_payload(
                candidate,
                overrides={'contact_phone1': '13800000000'},
            ),
        )

        self.assertEqual(response.status_code, 400)
        self._assert_envelope(response, success=False)
        self.assertEqual(LectureForm.query.count(), before_forms)
        self.assertEqual(ListeningAssistantEvidence.query.count(), before_evidence)

    def test_unexpected_operational_errors_are_not_reported_as_success(self):
        self._login()
        with mock.patch(
            'app.services.listening_assistant.load_schedule_entries',
            side_effect=ValueError('database unavailable'),
        ):
            with self.assertRaises(ValueError) as raised:
                self.client.get(
                    '/user/api/listening-assistant/candidates',
                    query_string={'date': '2026-09-18', 'room': '8-309'},
                )
            self.assertIs(type(raised.exception), ValueError)

        candidate = self._primary_candidate()
        with mock.patch(
            'app.services.listening_assistant.load_schedule_entries',
            side_effect=TypeError('loader contract failure'),
        ):
            with self.assertRaises(TypeError) as raised:
                self.client.post(
                    '/user/api/listening-assistant/confirm',
                    json=self._confirm_payload(candidate),
                )
            self.assertIs(type(raised.exception), TypeError)


if __name__ == '__main__':
    unittest.main()
