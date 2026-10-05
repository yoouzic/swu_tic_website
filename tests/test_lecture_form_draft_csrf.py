"""Drafts retain form input while session credentials remain request-local."""
import json
import os
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path


# Isolate the module-level app before importing it, including generated files.
_IMPORT_RUNTIME = tempfile.TemporaryDirectory(prefix='lecture-draft-csrf-import-')
_ROOT = Path(_IMPORT_RUNTIME.name)
os.environ.update({
    'DATABASE_URL': '', 'SQLITE_DB_PATH': str(_ROOT / 'import.db'),
    'INSTANCE_DIR': str(_ROOT / 'instance'), 'UPLOAD_FOLDER': str(_ROOT / 'uploads'),
    'AUTOMATION_UPLOAD_DIR': str(_ROOT / 'automation'),
    'AUTO_REVIEW_UPLOAD_DIR': str(_ROOT / 'references'),
    'AUTO_REVIEW_REPORT_DIR': str(_ROOT / 'reports'), 'EXPORT_DIR': str(_ROOT / 'exports'),
    'SCHEDULE_TEMPLATE_PATH': str(_ROOT / 'missing-schedule.xlsx'),
    'CONTACT_TEMPLATE_PATH': str(_ROOT / 'missing-contacts.xlsx'),
    'STORAGE_CLEANUP_ENABLED': 'false', 'SECRET_KEY': 'draft-csrf-test-secret',
    'SESSION_COOKIE_SECURE': 'false', 'FLASK_ENV': 'development',
    'DEEPSEEK_API_KEY': '', 'DEEPSEEK_BASE_URL': 'http://127.0.0.1:1/disabled',
    'CELERY_BROKER_URL': 'memory://', 'CELERY_RESULT_BACKEND': 'cache+memory://',
})

from app.app import create_app
from app.models import db, LectureForm, LectureFormDraft, LectureSiteCapture, User
from tests.app_test_utils import cleanup_sqlite_database


class _TokenParser(HTMLParser):
    token = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('name') == 'csrf-token':
            self.token = attrs.get('content')


class LectureFormDraftCsrfTest(unittest.TestCase):
    def setUp(self):
        self.runtime = tempfile.TemporaryDirectory(prefix='lecture-draft-csrf-')
        root = Path(self.runtime.name)
        self.app = create_app({
            'TESTING': True, 'WTF_CSRF_ENABLED': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + str(root / 'test.db'),
            'UPLOAD_FOLDER': str(root / 'uploads'),
            'LECTURE_CAPTURE_FOLDER': str(root / 'photos'),
            'LECTURE_CAPTURE_ENABLED': False,
        })
        with self.app.app_context():
            db.create_all()
            user = User(number='CSRF-OFFICER', department='test', name='Draft User',
                        gender='-', grade='-', college='Test College', major='-',
                        dormitory='-', phone='-', qq='-', student_id='csrf-officer',
                        password_hash='unused', role='信息员', group='test', is_active=True)
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id
        self.client = self.new_session()

    def tearDown(self):
        with self.app.app_context():
            cleanup_sqlite_database(db, drop_all=True)
        self.runtime.cleanup()

    def new_session(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session.update(user_id=self.user_id, user_role='信息员', user_name='Draft User')
        return client

    def page_token(self, client):
        response = client.get('/user/submit_form')
        self.assertEqual(response.status_code, 200)
        parser = _TokenParser()
        parser.feed(response.get_data(as_text=True))
        self.assertTrue(parser.token)
        return parser.token

    def manual_payload(self):
        return dict(lecture_date='2026-10-05', lecture_date_display='2026/10/05星期一',
                    start_period='3', end_period='4', class_period='第3-4节',
                    lecture_location='32-302', teacher_name='Teacher', teacher_college='Test College',
                    course_title='Cross-session Course', student_grade_class='2025 Class',
                    course_changes='无', abnormal_situation='无', teaching_method='PPT演示法',
                    classroom_discipline='非常好', classroom_atmosphere='非常好',
                    courseware_quality='非常好', overall_effect='非常好', quality_case='推荐',
                    course_feedback='教师结合实际案例讲解课程重点，课堂讨论积极，学生能够独立完成练习，知识点安排合理，问题解答细致，教学节奏清晰，课堂秩序良好。',
                    suggestions='无', student_signature1='Student A', contact_phone1='13800000001',
                    student_signature2='Student B', contact_phone2='13800000002')

    def test_save_discards_request_credentials_without_discarding_manual_fields(self):
        token = self.page_token(self.client)
        payload = {'course_feedback': '保留人工评价', 'csrf_token': token,
                   'unique_id': 'old-chain', 'expected_form_id': '7',
                   'expected_form_updated_at': 'old-version',
                   'entry_mode': 'manual', 'manual_entry_allowed': True}
        saved = self.client.put('/user/api/lecture_form_draft', json={'data': payload},
                                headers={'X-CSRFToken': token})
        self.assertEqual(saved.status_code, 200)
        with self.app.app_context():
            stored = json.loads(LectureFormDraft.query.one().payload_json)
        self.assertEqual(set(stored), {'course_feedback'})
        self.assertEqual(stored['course_feedback'], '保留人工评价')

    def test_legacy_draft_restores_and_submits_in_new_csrf_session(self):
        token_a = self.page_token(self.client)
        legacy = dict(self.manual_payload(), csrf_token=token_a)
        with self.app.app_context():
            db.session.add(LectureFormDraft(user_id=self.user_id, draft_key='submit_form',
                                           payload_json=json.dumps(legacy, ensure_ascii=False)))
            db.session.commit()
        fresh_client = self.new_session()
        token_b = self.page_token(fresh_client)
        self.assertNotEqual(token_a, token_b)
        # An actual stale token must still be rejected by CSRF protection.
        rejected = fresh_client.post('/user/submit_form', data=legacy)
        self.assertEqual(rejected.status_code, 400)
        self.assertIn('请求校验失败', rejected.get_data(as_text=True))

        restored = fresh_client.get('/user/api/lecture_form_draft').get_json()['data']
        submitted_fields = {'csrf_token': token_b}
        submitted_fields.update(restored)
        accepted = fresh_client.post('/user/submit_form', data=submitted_fields)
        self.assertEqual(accepted.status_code, 302,
                         'legacy draft must not replace the current request CSRF token')
        with self.app.app_context():
            form = LectureForm.query.one()
            self.assertEqual(form.course_title, legacy['course_title'])
            self.assertEqual(form.course_feedback, legacy['course_feedback'])
            self.assertEqual(LectureFormDraft.query.count(), 0)
        self.assertNotIn('csrf_token', restored)

    def test_legacy_per_photo_resume_strips_session_token_and_keeps_its_evaluation(self):
        token_a = self.page_token(self.client)
        legacy = {'csrf_token': token_a, 'course_feedback': '记录甲独立人工评价',
                  'unique_id': 'old-chain', 'expected_form_id': '8',
                  'expected_form_updated_at': 'old-version',
                  'entry_mode': 'manual', 'manual_entry_allowed': True}
        with self.app.app_context():
            from tests.schedule_fixture import seed_current_schedule
            seed_current_schedule()
            capture = LectureSiteCapture(user_id=self.user_id, photo_key='legacy.jpg',
                received_at='2026-10-05T10:00:00+08:00', draft_json=json.dumps(legacy, ensure_ascii=False))
            db.session.add(capture)
            db.session.commit()
            capture_id = capture.id
        self.app.config['LECTURE_CAPTURE_ENABLED'] = True
        fresh_client = self.new_session()
        token_b = self.page_token(fresh_client)
        self.assertNotEqual(token_a, token_b)
        resumed = fresh_client.post(f'/user/api/site-capture/{capture_id}/resume',
                                    headers={'X-CSRFToken': token_b})
        self.assertEqual(resumed.status_code, 200)
        loaded = fresh_client.get('/user/api/lecture_form_draft').get_json()['data']
        self.assertEqual(set(loaded), {'course_feedback', 'site_capture_id'})
        self.assertEqual(loaded['course_feedback'], legacy['course_feedback'])
        self.assertEqual(loaded['site_capture_id'], capture_id)
        with self.app.app_context():
            self.assertNotIn('csrf_token', json.loads(LectureFormDraft.query.one().payload_json))


if __name__ == '__main__':
    unittest.main()
