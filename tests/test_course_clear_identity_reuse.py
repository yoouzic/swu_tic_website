"""Formal clear/import routes must not let recycled Course IDs reclaim history."""
import json
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/2026-10-05-delivery-fixes/course-identity/clear-id-reuse'
OUT.mkdir(parents=True, exist_ok=True)
os.environ['SQLITE_DB_PATH'] = str(OUT / 'bootstrap.db')
os.environ['INSTANCE_DIR'] = str(OUT / 'runtime')
os.environ['UPLOAD_FOLDER'] = str(OUT / 'uploads')
os.environ['STORAGE_CLEANUP_ENABLED'] = 'false'

from werkzeug.security import generate_password_hash
from app.app import app
from app.models import Course, CourseRegistration, ListeningBan, SystemSetting, User, db
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database
from tests.test_schedule_snapshots import full_row, write_workbook


class CourseClearIdentityReuseTest(unittest.TestCase):
    def setUp(self):
        configure_sqlite_database(app, db, OUT / 'test.db')
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        def user(number, role):
            return User(number=number, student_id=number, name=number, role=role,
                        department='测试', gender='-', grade='-', college='-', major='-', dormitory='-',
                        phone='-', qq='-', group='-', password_hash=generate_password_hash('isolated-clear-password'), is_active=True)
        self.super_user = user('clear-super', '超级管理员')
        self.info = user('clear-info', '信息员')
        db.session.add_all([self.super_user, self.info])
        db.session.commit()
        self.client = app.test_client()
        self.steps = []
        self.old_template = os.environ.get('SCHEDULE_TEMPLATE_PATH')
        template = write_workbook(OUT / 'template.xlsx', [full_row(semester='2026-2027-1')])
        os.environ['SCHEDULE_TEMPLATE_PATH'] = str(template)
        SystemSetting.set('teaching_current_semester', '2026-2027-1')
        SystemSetting.set('teaching_first_week_monday', '2026-09-07')

    def tearDown(self):
        (OUT / (self._testMethodName + '-steps.json')).write_text(json.dumps(self.steps, ensure_ascii=False, indent=2), encoding='utf-8')
        if self.old_template is None:
            os.environ.pop('SCHEDULE_TEMPLATE_PATH', None)
        else:
            os.environ['SCHEDULE_TEMPLATE_PATH'] = self.old_template
        cleanup_sqlite_database(db, drop_all=True)
        self.context.pop()

    def login(self, user):
        with self.client.session_transaction() as session:
            session.clear()
            session.update(user_id=user.id, user_role=user.role)

    def request(self, method, path, **kwargs):
        response = getattr(self.client, method)(path, **kwargs)
        self.steps.append({'method': method, 'path': path, 'status': response.status_code, 'payload': response.get_json()})
        return response

    def upload(self, course_name, teacher_id):
        self.login(self.super_user)
        row = full_row(semester='2026-2027-1', academic_year='2026-2027', course_name=course_name,
                       teacher_id=teacher_id, teacher_name=teacher_id)
        path = write_workbook(OUT / (teacher_id + '.xlsx'), [row])
        with path.open('rb') as source:
            response = self.request('post', '/admin/api/schedule/import', data={'file': (source, path.name)}, content_type='multipart/form-data')
        self.assertTrue(response.get_json()['success'], response.get_json())
        db.session.expire_all()
        from app.services.current_courses import current_course_query
        return current_course_query().one()

    def create(self, course_id):
        self.login(self.info)
        return self.request('post', '/user/api/create_reservation', json={
            'course_id': course_id, 'course_code': 'C001', 'selection_code': 'S001', 'listening_info': '第8周 星期一 第3-4节'})

    def clear_and_reimport(self):
        self.login(self.super_user)
        clear = self.request('post', '/admin/api/clear_table', data={'table_name': 'courses', 'password': 'isolated-clear-password'})
        self.assertEqual(clear.status_code, 400, clear.get_json())
        self.assertFalse(clear.get_json()['success'])
        self.assertEqual(clear.get_json()['code'], 'historical_course_references')
        self.assertEqual(Course.query.count(), 1, 'Protected clear must leave the original course intact')
        return self.upload('清空后同学期新课程', 'NEW-TEACHER')

    def test_clear_reimport_preserves_history_and_does_not_reclaim_recycled_id(self):
        old = self.upload('清空前原课程', 'OLD-TEACHER')
        old_id = old.id
        created = self.create(old_id)
        self.assertEqual(created.status_code, 200, created.get_json())
        registration_id = created.get_json()['data']['id']
        registration = db.session.get(CourseRegistration, registration_id)
        before = {name: getattr(registration, name) for name in ('course_code', 'selection_code', 'semester', 'academic_year', 'listening_info', 'identity_evidence_json')}
        new = self.clear_and_reimport()
        self.assertNotEqual(new.id, old_id, 'Preserved history prevents SQLite row-ID reuse')
        self.login(self.info)
        unused = self.request('get', '/user/api/unused_reservations').get_json()['data']
        history = self.request('get', '/user/api/my_reservations').get_json()['data']
        self.assertNotIn(registration_id, [item['id'] for item in unused], unused)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['course_name'], '清空前原课程')
        self.assertTrue(history[0]['identity_restricted'])
        db.session.expire_all()
        registration = db.session.get(CourseRegistration, registration_id)
        self.assertEqual(registration.course_id, old_id)
        self.assertEqual({name: getattr(registration, name) for name in before}, before)
        SystemSetting.set('course_registration_weekly_limit_enabled', 'true')
        SystemSetting.set('course_registration_weekly_limit_count', '1')
        self.assertEqual(self.create(new.id).status_code, 200)

    def test_clear_preserves_legacy_ambiguous_and_pending_without_new_inference(self):
        self.upload('清空前原课程', 'OLD-TEACHER')
        records = [CourseRegistration(user_id=self.info.id, course_code='C001', selection_code='S001',
                                      identity_status=status, listening_info='第8周 星期一 第3-4节',
                                      identity_evidence_json=json.dumps({'source': 'legacy-fixture', 'state': status}))
                   for status in ('pending', 'ambiguous')]
        db.session.add_all(records)
        db.session.commit()
        ids = [r.id for r in records]
        evidence = {r.id: r.identity_evidence_json for r in records}
        self.clear_and_reimport()
        self.login(self.info)
        unused = self.request('get', '/user/api/unused_reservations').get_json()['data']
        self.assertFalse(any(item['id'] in ids for item in unused), unused)
        db.session.expire_all()
        for registration_id in ids:
            record = db.session.get(CourseRegistration, registration_id)
            self.assertIsNone(record.course_id)
            self.assertIn(record.identity_status, {'pending', 'ambiguous'})
            self.assertEqual(record.identity_evidence_json, evidence[registration_id])

    def test_recycled_course_id_does_not_transfer_legacy_listening_ban_to_new_row(self):
        old = self.upload('清空前原课程', 'OLD-TEACHER')
        old_id = old.id
        self.login(self.super_user)
        banned = self.request('post', f'/admin/api/courses/{old_id}/ban_users', json={'user_ids': [self.info.id]})
        self.assertTrue(banned.get_json()['success'], banned.get_json())
        new = self.clear_and_reimport()
        self.assertNotEqual(new.id, old_id)
        self.login(self.info)
        listing = self.request('get', '/user/api/available_courses').get_json()['results']
        self.assertIn(new.id, [item['id'] for item in listing], listing)
        self.assertEqual(self.create(new.id).status_code, 200)


if __name__ == '__main__':
    unittest.main()
