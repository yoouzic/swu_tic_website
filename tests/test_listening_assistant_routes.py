import os
import tempfile
import unittest
from datetime import date, datetime
from unittest import mock

from werkzeug.security import generate_password_hash

TEST_DB_DIR = tempfile.TemporaryDirectory(prefix='listening-assistant-routes-')
os.environ['SQLITE_DB_PATH'] = os.path.join(TEST_DB_DIR.name, 'routes.sqlite')
os.environ['INSTANCE_DIR'] = TEST_DB_DIR.name
os.environ['SECRET_KEY'] = 'test-secret-key'

from app.app import app
from app.models import (
    LectureForm,
    ListeningAssistantEvidence,
    ListeningAssistantScheduleEntry,
    ScheduleImportBatch,
    User,
    db,
)
from app.services.listening_assistant import ListeningAssistantService
from app.services.listening_assistant_contracts import ScheduleEntry
from app.services.listening_assistant_guide_contracts import GuidedAssistantState
from app.services.listening_assistant_schedule import ScheduleSourceUnavailable
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
        from tests.schedule_fixture import seed_current_schedule
        seed_current_schedule(SEMESTER)
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
        self.current_semester_patch = mock.patch('app.blueprints.user.listening_assistant.get_current_teaching_semester', return_value=SEMESTER, create=True)
        self.current_semester_patch.start()

    def tearDown(self):
        self.current_semester_patch.stop()
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

    def test_form_uses_configured_semester_without_student_settings(self):
        self._login()
        with mock.patch('app.blueprints.user.forms.get_current_teaching_semester', return_value=SEMESTER, create=True):
            response = self.client.get('/user/submit_form')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert f'data-assistant-semester="{SEMESTER}"' in html
        assert '切换课表学期' not in html
        assert '查看课表来源' not in html
        assert 'data-assistant-fallback-panel' not in html
        assert 'data-assistant-semester value=' in html
        assert 'data-assistant-progress hidden' in html
        assert 'data-assistant-history hidden' in html

    def test_guide_uses_admin_semester_without_student_parameter(self):
        self._login()
        with mock.patch('app.blueprints.user.listening_assistant._guide_service', wraps=__import__('app.blueprints.user.listening_assistant', fromlist=['_guide_service'])._guide_service) as service:
            response = self.client.post('/user/api/listening-assistant/guide/start', json={'known_facts': {}})
        assert response.status_code == 200, response.get_json()
        service.assert_called_once_with(SEMESTER)
        assert response.get_json()['data']['backup_rescue_available'] is False

    def test_unconfigured_admin_semester_is_actionable_not_student_input(self):
        self._login()
        with mock.patch('app.blueprints.user.listening_assistant.get_current_teaching_semester', return_value=''):
            response = self.client.post('/user/api/listening-assistant/guide/start', json={'known_facts': {}})
        assert response.status_code == 503
        assert '请联系管理员' in response.get_json()['message']

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
            lambda: self.client.post(
                '/user/api/listening-assistant/guide/start',
                json={'known_facts': {}, 'semester': SEMESTER},
            ),
            lambda: self.client.post(
                '/user/api/listening-assistant/guide/answer',
                json={},
            ),
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

        response = self._guide_start(user=inactive)
        self.assertEqual(response.status_code, 401)
        self._assert_envelope(response, success=False)

    def _guide_start(self, *, known_facts=None, user=None, semester=SEMESTER):
        self._login(user)
        return self.client.post(
            '/user/api/listening-assistant/guide/start',
            json={
                'known_facts': {} if known_facts is None else known_facts,
                'semester': semester,
            },
        )

    def _guide_answer(self, state, *, question_kind, option_code=None, custom_value=None):
        return self.client.post(
            '/user/api/listening-assistant/guide/answer',
            json={
                'state': state,
                'question_kind': question_kind,
                'option_code': option_code,
                'custom_value': custom_value,
                'semester': SEMESTER,
            },
        )

    def test_guide_start_returns_initial_question_in_the_public_envelope(self):
        with mock.patch(
            'app.blueprints.user.listening_assistant.latest_retired_batch_id',
            return_value=BACKUP_BATCH,
        ):
            response = self._guide_start()

        payload = self._assert_envelope(response, success=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(payload['data']),
            {
                'question', 'state', 'candidates', 'needs_confirmation',
                'backup_source_batch_id', 'backup_rescue_available',
            },
        )
        self.assertEqual(payload['data']['question']['kind'], 'memory')
        self.assertEqual(payload['data']['state']['stage'], 'question')
        self.assertEqual(payload['data']['candidates'], [])
        self.assertFalse(payload['data']['needs_confirmation'])
        self.assertIsNone(payload['data']['backup_source_batch_id'])
        self.assertFalse(payload['data']['backup_rescue_available'])

    def test_guide_start_rejects_non_string_semester_and_known_fact_values(self):
        self._login()
        for label, body in (
            (
                'non-string semester',
                {'known_facts': {}, 'semester': 123},
            ),
            (
                'non-string known fact',
                {'known_facts': {'date': 123}, 'semester': SEMESTER},
            ),
        ):
            with self.subTest(label=label):
                response = self.client.post(
                    '/user/api/listening-assistant/guide/start',
                    json=body,
                )
                self.assertEqual(response.status_code, 400)
                self._assert_envelope(response, success=False)

    def test_guide_all_active_roles_are_allowed(self):
        for index, role in enumerate(('信息员', '管理员', '超级管理员'), start=10):
            with self.subTest(role=role):
                user = self._create_user(str(index), f'student-{index}', role=role)
                response = self._guide_start(user=user)
                self.assertEqual(response.status_code, 200)
                self._assert_envelope(response, success=True)

    def test_guide_answer_forwards_date_teacher_and_custom_paths(self):
        initial = self._assert_envelope(self._guide_start(), success=True)['data']

        date_question = self._assert_envelope(
            self._guide_answer(initial['state'], question_kind='memory', option_code='A'),
            success=True,
        )['data']
        self.assertEqual(date_question['question']['kind'], 'date')

        custom_date = self._assert_envelope(
            self._guide_answer(
                date_question['state'],
                question_kind='date',
                custom_value=LOOKUP_DATE.isoformat(),
            ),
            success=True,
        )['data']
        self.assertEqual(custom_date['state']['known_facts']['date'], LOOKUP_DATE.isoformat())
        self.assertEqual(len(custom_date['candidates']), 2)

        teacher_question = self._assert_envelope(
            self._guide_start(known_facts={'date': LOOKUP_DATE.isoformat()}),
            success=True,
        )['data']
        self.assertEqual(teacher_question['question']['kind'], 'teacher')
        teacher_result = self._assert_envelope(
            self._guide_answer(teacher_question['state'], question_kind='teacher', option_code='A'),
            success=True,
        )['data']
        self.assertEqual(teacher_result['state']['stage'], 'confirm')
        self.assertEqual(teacher_result['candidates'][0]['teacher_name'], '张老师')

    def test_guide_answer_rejects_malformed_and_unsafe_payloads(self):
        self._login()
        start = self._assert_envelope(
            self.client.post(
                '/user/api/listening-assistant/guide/start',
                json={'known_facts': {}, 'semester': SEMESTER},
            ),
            success=True,
        )['data']
        valid = {
            'state': start['state'],
            'question_kind': 'memory',
            'option_code': 'A',
            'custom_value': None,
            'semester': SEMESTER,
        }

        cases = (
            ('unknown top-level field', {**valid, 'candidate_ids': ['forged']}),
            ('unknown state field', {**valid, 'state': {**valid['state'], 'unexpected': True}}),
            ('invalid stage', {**valid, 'state': {**valid['state'], 'stage': 'forged'}}),
            ('option and custom conflict', {**valid, 'custom_value': '2026-09-18'}),
            ('invalid question kind', {**valid, 'question_kind': 'phone'}),
            ('budget overflow', {**valid, 'state': {**valid['state'], 'question_count': 5}}),
            ('budget exhausted memory', {**valid, 'state': {**valid['state'], 'question_count': 4}}),
            (
                'budget exhausted ordinary question',
                {
                    **valid,
                    'state': {
                        'known_facts': {'date': LOOKUP_DATE.isoformat()},
                        'candidate_ids': [],
                        'asked_question_kinds': ['teacher'],
                        'question_count': 4,
                        'stage': 'question',
                    },
                    'question_kind': 'teacher',
                },
            ),
            ('overlong semester', {**valid, 'semester': 'x' * 121}),
            ('non-string question kind', {**valid, 'question_kind': 123}),
            ('non-string option code', {**valid, 'option_code': 123}),
            ('non-string custom value', {**valid, 'option_code': None, 'custom_value': 123}),
            ('non-string semester', {**valid, 'semester': 123}),
            (
                'non-string state candidate id',
                {**valid, 'state': {**valid['state'], 'candidate_ids': [123]}},
            ),
        )
        for label, body in cases:
            with self.subTest(label=label):
                response = self.client.post(
                    '/user/api/listening-assistant/guide/answer',
                    json=body,
                )
                self.assertEqual(response.status_code, 400)
                self._assert_envelope(response, success=False)

        malformed = self.client.post(
            '/user/api/listening-assistant/guide/answer',
            data='{"state":',
            content_type='application/json',
        )
        self.assertEqual(malformed.status_code, 400)
        self._assert_envelope(malformed, success=False)

    def test_guide_answer_ignores_forged_candidate_ids_and_keeps_output_private(self):
        self._login()
        started = self._assert_envelope(
            self._guide_start(known_facts={'date': LOOKUP_DATE.isoformat()}),
            success=True,
        )['data']
        forged_state = {**started['state'], 'candidate_ids': ['forged-candidate']}

        response = self._guide_answer(
            forged_state,
            question_kind=started['question']['kind'],
            option_code='A',
        )
        payload = self._assert_envelope(response, success=True)
        serialized = str(payload).lower()
        self.assertNotIn('forged-candidate', serialized)
        for secret in ('phone', 'signature', 'credential', 'evaluation'):
            self.assertNotIn(secret, serialized)
        self.assertTrue(all(item['candidate_id'] != 'forged-candidate' for item in payload['data']['candidates']))

    def test_guide_answer_does_not_convert_operational_value_error_to_json_success(self):
        self._login()
        started = self._assert_envelope(
            self._guide_start(),
            success=True,
        )['data']
        with mock.patch(
            'app.services.listening_assistant.load_schedule_entries',
            side_effect=ValueError('database unavailable'),
        ):
            with self.assertRaises(ValueError) as raised:
                self._guide_answer(
                    started['state'],
                    question_kind='memory',
                    option_code='A',
                )
        self.assertEqual(str(raised.exception), 'database unavailable')

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

    def test_primary_get_exposes_safe_retired_batch_discovery_for_semester(self):
        indexed_batch = ScheduleImportBatch(
            semester=SEMESTER,
            academic_year='2026',
            source_filename='retired.xlsx',
            source_sha256='a' * 64,
            status='retired',
            row_count=1,
            created_at=datetime(2026, 9, 1, 12, 0, 0),
        )
        unindexed_batch = ScheduleImportBatch(
            semester=SEMESTER,
            academic_year='2026',
            source_filename='unindexed.xlsx',
            source_sha256='b' * 64,
            status='retired',
            row_count=1,
            created_at=datetime(2026, 9, 2, 12, 0, 0),
        )
        db.session.add_all([indexed_batch, unindexed_batch])
        db.session.flush()
        db.session.add(ListeningAssistantScheduleEntry(
            batch_id=indexed_batch.id,
            source_row=2,
            semester=SEMESTER,
        ))
        db.session.commit()
        self._login()

        response = self.client.get(
            '/user/api/listening-assistant/candidates',
            query_string={
                'date': '2026-09-18',
                'room': '8-309',
                'semester': SEMESTER,
            },
        )

        payload = self._assert_envelope(response, success=True)
        self.assertFalse(payload['data']['backup_rescue_available'])
        self.assertEqual(
            payload['data'].get('backup_source_batch_id'),
            None,
        )

    def test_primary_get_returns_unavailable_json_when_authoritative_source_is_not_ready(self):
        self._login()
        unavailable = ScheduleSourceUnavailable(
            status='CURRENT_SEMESTER_CONFIGURED_BUT_NO_ACTIVE_SNAPSHOT',
        )
        with mock.patch(
            'app.services.listening_assistant.load_schedule_entries',
            side_effect=unavailable,
        ):
            response = self.client.get(
                '/user/api/listening-assistant/candidates',
                query_string={
                    'date': '2026-09-18',
                    'room': '8-309',
                    'semester': SEMESTER,
                },
            )

        self.assertEqual(response.status_code, 503)
        payload = self._assert_envelope(response, success=False)
        self.assertEqual(
            payload['data'],
            {
                'status': 'CURRENT_SEMESTER_CONFIGURED_BUT_NO_ACTIVE_SNAPSHOT',
                'code': 'schedule_source_unavailable',
            },
        )
        self.assertNotIn('candidates', payload['data'])

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

    def test_retired_lookup_is_disabled_for_student(self):
        self._login()
        response = self.client.post('/user/api/listening-assistant/fallback', json={'date': '2026-09-18', 'teacher': '备用老师', 'semester': SEMESTER, 'source_batch_id': BACKUP_BATCH, 'reason': 'no_result'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('当前课表', response.get_json()['message'])

    def test_retired_lookup_never_calls_backup_search(self):
        self._login()
        with mock.patch('app.blueprints.user.listening_assistant.ListeningAssistantService.search_backup') as search:
            response = self.client.post('/user/api/listening-assistant/fallback', json={'semester': SEMESTER, 'source_batch_id': BACKUP_BATCH, 'date': '2026-09-18', 'teacher': '备用老师'})
        self.assertEqual(response.status_code, 400)
        search.assert_not_called()

    def test_fallback_rejects_malformed_optional_period_and_class_filters(self):
        self._login()
        base = {
            'date': '2026-09-18',
            'teacher': '备用老师',
            'rejected_ids': [],
            'reason': 'no_result',
            'source_batch_id': BACKUP_BATCH,
            'semester': SEMESTER,
        }
        for field, value in (
            ('period', 'not-a-period'),
            ('student_grade_class', 123),
        ):
            payload = dict(base)
            payload[field] = value
            with self.subTest(field=field):
                response = self.client.post(
                    '/user/api/listening-assistant/fallback',
                    json=payload,
                )
                self.assertEqual(response.status_code, 400)
                self._assert_envelope(response, success=False)

    def test_guide_ignores_a_stale_client_semester(self):
        self._login()
        with mock.patch('app.blueprints.user.listening_assistant._guide_service') as service:
            service.return_value.start.return_value = __import__('app.services.listening_assistant_guide_contracts', fromlist=['GuidedResult']).GuidedResult(
                GuidedAssistantState({}, (), (), 0, 'question'), None, (), False)
            response = self.client.post('/user/api/listening-assistant/guide/start', json={'known_facts': {}, 'semester': 'old-semester'})
        self.assertEqual(response.status_code, 200)
        service.assert_called_once_with(SEMESTER)

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

    def test_confirm_returns_unavailable_json_when_primary_authority_is_not_ready(self):
        self._login()
        candidate = self._primary_candidate()
        unavailable = ScheduleSourceUnavailable(
            status='INVALID_AUTHORITY',
        )

        with mock.patch(
            'app.services.listening_assistant.load_schedule_entries',
            side_effect=unavailable,
        ):
            response = self.client.post(
                '/user/api/listening-assistant/confirm',
                json=self._confirm_payload(candidate),
            )

        self.assertEqual(response.status_code, 503)
        payload = self._assert_envelope(response, success=False)
        self.assertEqual(
            payload['data'],
            {
                'status': 'INVALID_AUTHORITY',
                'code': 'schedule_source_unavailable',
            },
        )
        self.assertIn('unavailable', payload['message'])

    def test_confirm_requires_explicit_override_for_fresh_primary_period_conflict(self):
        self._login()
        response = self.client.get(
            '/user/api/listening-assistant/candidates',
            query_string={
                'date': '2026-09-18',
                'room': '8-309',
                'period': '5-6',
                'semester': SEMESTER,
            },
        )
        candidate = self._assert_envelope(response, success=True)['data']['candidates'][0]
        self.assertIn('period_mismatch', candidate['conflicts'])

        payload = self._confirm_payload(candidate)
        payload['query']['period'] = [5, 6]
        before_forms = LectureForm.query.count()
        before_evidence = ListeningAssistantEvidence.query.count()

        rejected = self.client.post(
            '/user/api/listening-assistant/confirm',
            json=payload,
        )
        rejected_payload = self._assert_envelope(rejected, success=False)
        self.assertEqual(rejected.status_code, 400)
        self.assertIn('conflict requires an explicit override', rejected_payload['message'])
        self.assertEqual(LectureForm.query.count(), before_forms)
        self.assertEqual(ListeningAssistantEvidence.query.count(), before_evidence)

        payload['overrides'] = {'period': [3, 4]}
        accepted = self.client.post(
            '/user/api/listening-assistant/confirm',
            json=payload,
        )
        accepted_payload = self._assert_envelope(accepted, success=True)
        self.assertEqual(
            accepted_payload['data']['field_snapshot']['class_period'],
            '第3-4节',
        )

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

    def test_confirm_binds_semester_to_admin_configuration(self):
        self._login()
        payload = self._confirm_payload(self._primary_candidate())
        payload.pop('semester')
        response = self.client.post('/user/api/listening-assistant/confirm', json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['data']['confirmation']['semester'], SEMESTER)

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

    def test_confirm_rejects_backup_even_with_acknowledgement(self):
        self._login()
        payload = self._confirm_payload(self._primary_candidate(), source_kind='backup')
        response = self.client.post('/user/api/listening-assistant/confirm', json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn('当前课表', response.get_json()['message'])

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
