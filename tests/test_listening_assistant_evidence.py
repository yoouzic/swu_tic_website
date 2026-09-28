import json
import os
import tempfile
import unittest
from datetime import date
from unittest import mock

from werkzeug.security import generate_password_hash

TEST_DB_DIR = tempfile.TemporaryDirectory()
os.environ['SQLITE_DB_PATH'] = os.path.join(TEST_DB_DIR.name, 'listening_assistant_evidence_test.db')
os.environ['INSTANCE_DIR'] = TEST_DB_DIR.name
os.environ['SECRET_KEY'] = 'test-secret-key'

from app.app import app
from app.models import (
    LectureForm,
    ListeningAssistantEvidence,
    User,
    db,
)
from app.services.listening_assistant import ListeningAssistantService
from app.services.listening_assistant_contracts import AssistantQuery, ScheduleEntry
from app.services.listening_assistant_evidence import (
    NormalizedAssistantSelection,
    create_evidence,
    revalidate_selection,
    safe_field_snapshot,
)
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database


INFO_MEMBER = '\u4fe1\u606f\u5458'
LOOKUP_DATE = date(2026, 9, 18)
SEMESTER = '2026-2027-1'


class LoaderSpy:
    def __init__(self, primary=(), backup=()):
        self.primary = list(primary)
        self.backup = list(backup)
        self.calls = []

    def __call__(self, *, source_kind='primary', semester=None, source_batch_id=None, batch_id=None):
        self.calls.append({
            'source_kind': source_kind,
            'semester': semester,
            'source_batch_id': source_batch_id,
            'batch_id': batch_id,
        })
        return list(self.primary if source_kind == 'primary' else self.backup)


def schedule_entry(
    *,
    source_kind='primary',
    source_batch_id='batch-current',
    source_row=1,
    room='8-309',
    course_title='数据结构',
    teacher_name='张老师',
):
    return ScheduleEntry(
        entry_id=f'{source_kind}:{source_batch_id}:{source_row}',
        lecture_date=LOOKUP_DATE,
        room=room,
        period=(3, 4),
        course_code='CS101',
        selection_code='S101',
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


class ListeningAssistantEvidenceTest(unittest.TestCase):
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
        self.user = self._create_user('1001', 'student-1001')
        self._login_as(self.user)

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

    def _service(self, *, primary=(), backup=()):
        loader = LoaderSpy(primary=primary, backup=backup)
        return ListeningAssistantService(schedule_loader=loader), loader

    def _payload(self, service, *, source_kind='primary', source_batch_id='batch-current', **extra):
        query = AssistantQuery(LOOKUP_DATE, room='8-309')
        if source_kind == 'backup':
            result = service.search_backup(
                query,
                source_batch_id=source_batch_id,
                semester=SEMESTER,
                explicit_fallback=True,
                reason='no_result',
            )
        else:
            result = service.search(query, semester=SEMESTER)
        candidate = result.candidates[0]
        payload = {
            'stage': 'confirmed',
            'query': {
                'lecture_date': LOOKUP_DATE.isoformat(),
                'room': '8-309',
            },
            'rejected_ids': [],
            'source_kind': source_kind,
            'source_batch_id': source_batch_id,
            'semester': SEMESTER,
            'candidate_id': candidate.candidate_id,
            'overrides': {},
            'template_version': 'task4-v1',
        }
        if source_kind == 'backup':
            payload.update({
                'explicit_fallback': True,
                'fallback_reason': 'no_result',
                'acknowledged_source': True,
            })
        payload.update(extra)
        return payload, candidate

    def test_confirmed_primary_selection_revalidates_and_creates_safe_evidence(self):
        service, _loader = self._service(primary=[schedule_entry()])
        payload, candidate = self._payload(service)
        payload['overrides'] = {
            'lecture_location': '9-101',
            'period': [5, 6],
        }

        normalized = revalidate_selection(
            self.user,
            payload,
            service=service,
            semester=SEMESTER,
        )
        self.assertIsInstance(normalized, NormalizedAssistantSelection)
        self.assertEqual(normalized.candidate.candidate_id, candidate.candidate_id)
        self.assertEqual(normalized.source_batch_id, 'batch-current')
        self.assertEqual(normalized.field_snapshot['lecture_location'], '9-101')
        self.assertEqual(normalized.field_snapshot['class_period'], '第5-6节')

        form = LectureForm(
            listener_name='Test User（Test College）',
            listener_number=self.user.number,
            lecture_date='2026-09-18',
            class_period='第3-4节',
            lecture_location='9-101',
            teacher_name='张老师',
            teacher_college='计算机学院',
            course_title='数据结构',
            student_grade_class='2024级计算机1班',
            teaching_method='讲授法',
            classroom_discipline='好',
            classroom_atmosphere='好',
            courseware_quality='无',
            overall_effect='好',
            quality_case='推荐',
            course_feedback='足够长的课程反馈内容，用于验证听课助手证据不会保存评价原文。',
            student_signature1='真实签名',
            contact_phone1='13800000000',
        )
        db.session.add(form)
        db.session.flush()
        evidence = create_evidence(self.user, form, normalized)
        db.session.flush()

        self.assertEqual(evidence.user_id, self.user.id)
        self.assertEqual(evidence.lecture_form_id, form.id)
        self.assertEqual(evidence.source_kind, 'primary')
        self.assertEqual(evidence.source_batch_id, 'batch-current')
        self.assertEqual(evidence.template_version, 'task4-v1')
        stored = ' '.join(
            value or ''
            for value in (
                evidence.query_json,
                evidence.candidate_json,
                evidence.overrides_json,
                evidence.confirmation_json,
            )
        )
        self.assertNotIn('13800000000', stored)
        self.assertNotIn('真实签名', stored)
        self.assertNotIn('课程反馈内容', stored)

    def test_revalidation_requires_an_explicit_confirmed_stage(self):
        service, _loader = self._service(primary=[schedule_entry()])
        payload, _candidate = self._payload(service)

        payload.pop('stage')
        with self.assertRaises(ValueError):
            revalidate_selection(self.user, payload, service=service, semester=SEMESTER)

        payload['stage'] = 'review'
        with self.assertRaises(ValueError):
            revalidate_selection(self.user, payload, service=service, semester=SEMESTER)

    def test_revalidation_propagates_operational_search_errors(self):
        service, _loader = self._service(primary=[schedule_entry()])
        payload, _candidate = self._payload(service)

        for error in (
            RuntimeError('database unavailable'),
            ValueError('loader returned an invalid value'),
            TypeError('loader contract failure'),
        ):
            class OperationalFailureService:
                _semester = None

                def search(self, *args, _error=error, **kwargs):
                    raise _error

            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)) as raised:
                    revalidate_selection(
                        self.user,
                        payload,
                        service=OperationalFailureService(),
                        semester=SEMESTER,
                    )
                self.assertIs(type(raised.exception), type(error))

    def test_revalidation_requires_explicit_semester_provenance(self):
        service, _loader = self._service(primary=[schedule_entry()])
        payload, _candidate = self._payload(service)
        payload.pop('semester')

        with self.assertRaises(ValueError):
            revalidate_selection(self.user, payload, service=service)

        configured_service = ListeningAssistantService(
            schedule_loader=LoaderSpy(primary=[schedule_entry()]),
            semester=SEMESTER,
        )
        with self.assertRaises(ValueError):
            revalidate_selection(self.user, payload, service=configured_service)

    def test_stale_or_unknown_candidate_is_rejected_against_fresh_results(self):
        service, loader = self._service(primary=[schedule_entry()])
        payload, _candidate = self._payload(service)
        loader.primary = [schedule_entry(source_batch_id='batch-new')]

        with self.assertRaises(ValueError):
            revalidate_selection(self.user, payload, service=service, semester=SEMESTER)

        payload['candidate_id'] = 'primary:batch-new:not-a-real-candidate'
        with self.assertRaises(ValueError):
            revalidate_selection(self.user, payload, service=service, semester=SEMESTER)

    def test_backup_requires_acknowledgement_and_matches_source_batch_and_semester(self):
        backup = schedule_entry(source_kind='backup', source_batch_id='retired-7')
        service, _loader = self._service(backup=[backup])
        payload, _candidate = self._payload(
            service,
            source_kind='backup',
            source_batch_id='retired-7',
        )
        payload['acknowledged_source'] = False
        with self.assertRaises(ValueError):
            revalidate_selection(self.user, payload, service=service, semester=SEMESTER)

        payload['acknowledged_source'] = True
        payload['source_batch_id'] = 'retired-other'
        with self.assertRaises(ValueError):
            revalidate_selection(self.user, payload, service=service, semester=SEMESTER)

        payload['source_batch_id'] = 'retired-7'
        payload['semester'] = '2025-2026-2'
        with self.assertRaises(ValueError):
            revalidate_selection(self.user, payload, service=service, semester=SEMESTER)

        payload['semester'] = SEMESTER
        normalized = revalidate_selection(
            self.user,
            payload,
            service=service,
            semester=SEMESTER,
        )
        self.assertEqual(normalized.source_kind, 'backup')
        self.assertTrue(normalized.confirmation.acknowledged_source)

    def test_safe_snapshot_rejects_phone_and_signature_overrides(self):
        service, _loader = self._service(primary=[schedule_entry()])
        payload, candidate = self._payload(service)
        payload['overrides'] = {
            'contact_phone1': '13800000000',
        }
        with self.assertRaises(ValueError):
            revalidate_selection(self.user, payload, service=service, semester=SEMESTER)

        snapshot = safe_field_snapshot(
            candidate,
            overrides={
                'room': '9-101',
                'period': '第5-6节',
                'teacher_name': '人工核对教师',
            },
        )
        self.assertEqual(
            set(snapshot),
            {
                'lecture_date',
                'class_period',
                'lecture_location',
                'teacher_name',
                'teacher_college',
                'course_title',
                'student_grade_class',
            },
        )
        self.assertEqual(snapshot['lecture_location'], '9-101')
        self.assertEqual(snapshot['class_period'], '第5-6节')
        self.assertNotIn('contact_phone1', snapshot)
        self.assertNotIn('student_signature1', snapshot)

    def test_evidence_is_scoped_to_user_and_flushed_form(self):
        service, _loader = self._service(primary=[schedule_entry()])
        payload, _candidate = self._payload(service)
        normalized = revalidate_selection(self.user, payload, service=service, semester=SEMESTER)
        form = LectureForm(
            listener_name='Test User（Test College）',
            listener_number=self.user.number,
            lecture_date='2026-09-18',
            class_period='第3-4节',
            lecture_location='8-309',
            teacher_name='张老师',
            teacher_college='计算机学院',
            course_title='数据结构',
            student_grade_class='2024级计算机1班',
            teaching_method='讲授法',
            classroom_discipline='好',
            classroom_atmosphere='好',
            courseware_quality='无',
            overall_effect='好',
            quality_case='推荐',
            course_feedback='足够长的课程反馈内容，用于证据测试。',
            student_signature1='签名',
            contact_phone1='13800000000',
        )
        db.session.add(form)
        db.session.flush()
        evidence = create_evidence(self.user, form, normalized)
        db.session.flush()
        self.assertEqual(ListeningAssistantEvidence.query.filter_by(user_id=self.user.id).count(), 1)
        self.assertEqual(ListeningAssistantEvidence.query.filter_by(lecture_form_id=form.id).count(), 1)

        other_user = self._create_user('1002', 'student-1002')
        with self.assertRaises(ValueError):
            create_evidence(other_user, form, normalized)

    def test_submit_revalidates_assistant_fills_only_empty_schedule_fields_and_records_evidence(self):
        service, _loader = self._service(primary=[schedule_entry()])
        assistant_payload, _candidate = self._payload(service)
        form_data = self._valid_form_payload()
        form_data.update({
            'lecture_date': '',
            'lecture_date_display': '',
            'start_period': '',
            'end_period': '',
            'class_period': '',
            'lecture_location': '',
            'teacher_name': '',
            'teacher_college': '',
            'course_title': '',
            'student_grade_class': '',
            'assistant_payload': json.dumps(assistant_payload, ensure_ascii=False),
        })
        with mock.patch(
            'app.blueprints.user.forms.revalidate_selection',
            side_effect=lambda user, payload, **kwargs: revalidate_selection(
                user,
                payload,
                service=service,
                semester=SEMESTER,
            ),
        ):
            response = self.client.post('/user/submit_form', data=form_data)

        self.assertEqual(response.status_code, 302)
        form = LectureForm.query.filter_by(listener_number=self.user.number).first()
        self.assertEqual(form.lecture_date, '2026-09-18')
        self.assertEqual(form.class_period, '第3-4节')
        self.assertEqual(form.lecture_location, '8-309')
        self.assertEqual(form.teacher_name, '张老师')
        self.assertEqual(form.course_title, '数据结构')
        self.assertEqual(ListeningAssistantEvidence.query.filter_by(user_id=self.user.id).count(), 1)

    def test_submit_preserves_explicitly_cleared_assistant_overrides(self):
        service, _loader = self._service(primary=[schedule_entry()])
        assistant_payload, _candidate = self._payload(service)
        assistant_payload['overrides'] = {
            'lecture_date': None,
            'room': None,
            'period': None,
            'teacher_name': None,
            'teacher_college': None,
            'course_title': None,
            'student_grade_class': None,
        }
        form_data = self._valid_form_payload()
        form_data.update({
            'lecture_date': '',
            'lecture_date_display': '',
            'start_period': '',
            'end_period': '',
            'class_period': '',
            'lecture_location': '',
            'teacher_name': '',
            'teacher_college': '',
            'course_title': '',
            'student_grade_class': '',
            'assistant_payload': json.dumps(assistant_payload, ensure_ascii=False),
        })
        with mock.patch(
            'app.blueprints.user.forms.revalidate_selection',
            side_effect=lambda user, payload, **kwargs: revalidate_selection(
                user,
                payload,
                service=service,
                semester=SEMESTER,
            ),
        ):
            response = self.client.post('/user/submit_form', data=form_data)

        self.assertEqual(response.status_code, 302)
        form = LectureForm.query.filter_by(listener_number=self.user.number).first()
        for field_name in (
            'lecture_date',
            'class_period',
            'lecture_location',
            'teacher_name',
            'teacher_college',
            'course_title',
            'student_grade_class',
        ):
            self.assertEqual(getattr(form, field_name), '')

    def test_submit_does_not_hide_operational_revalidation_errors(self):
        form_data = self._valid_form_payload()
        form_data['assistant_payload'] = json.dumps({'stage': 'confirmed'})

        with mock.patch(
            'app.blueprints.user.forms.revalidate_selection',
            side_effect=RuntimeError('database unavailable'),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post('/user/submit_form', data=form_data)

        self.assertEqual(LectureForm.query.filter_by(listener_number=self.user.number).count(), 0)
        self.assertEqual(ListeningAssistantEvidence.query.filter_by(user_id=self.user.id).count(), 0)

    def test_legacy_submit_without_assistant_keeps_existing_path_without_evidence(self):
        response = self.client.post('/user/submit_form', data=self._valid_form_payload())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ListeningAssistantEvidence.query.count(), 0)

    def _valid_form_payload(self):
        return {
            'lecture_date': '2026-06-04',
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
            'course_feedback': 'This course feedback is intentionally longer than fifty characters for testing submit.',
            'suggestions': '无',
            'student_signature1': 'Student One',
            'contact_phone1': '13800000001',
            'student_signature2': 'Student Two',
            'contact_phone2': '13800000002',
        }


if __name__ == '__main__':
    unittest.main()
