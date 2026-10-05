"""Cross-semester reservation identity and legacy FK preservation regressions."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'output/2026-10-05-delivery-fixes/course-identity/unit-runtime'
RUNTIME.mkdir(parents=True, exist_ok=True)
os.environ['SQLITE_DB_PATH'] = str(RUNTIME / 'tests.db')
os.environ['INSTANCE_DIR'] = str(RUNTIME)
os.environ['UPLOAD_FOLDER'] = str(RUNTIME / 'uploads')
os.environ['SECRET_KEY'] = 'course-identity-tests'
os.environ['STORAGE_CLEANUP_ENABLED'] = 'false'

from app.app import app
from app.models import Course, CourseRegistration, LectureBan, LectureForm, ListeningBan, ScheduleCourseMembership, SystemSetting, Teacher, User, db
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database
from tests.schedule_fixture import seed_current_schedule


class ReservationCourseIdentityTest(unittest.TestCase):
    def setUp(self):
        configure_sqlite_database(app, db, RUNTIME / 'tests.db')
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.context = app.app_context()
        self.context.push()
        with db.engine.begin() as connection:
            connection.exec_driver_sql('DROP TABLE IF EXISTS ban_log')
        db.drop_all()
        db.create_all()
        self.batch = seed_current_schedule()
        self.user = User(number='identity-user', student_id='identity-user', name='身份测试',
                         role='信息员', department='测试', gender='-', grade='-', college='-',
                         major='-', dormitory='-', phone='-', qq='-', group='-', password_hash='unused', is_active=True)
        self.old = Course(course_code='SAME', selection_code='001', course_name='历史课程', semester='2025-2026-2', academic_year='2025-2026')
        self.current = Course(course_code='SAME', selection_code='001', course_name='当前课程', semester='2026-2027-1', academic_year='2026-2027')
        db.session.add_all([self.user, self.old, self.current])
        db.session.flush()
        db.session.add(ScheduleCourseMembership(batch_id=self.batch.id, course_id=self.current.id))
        db.session.commit()
        SystemSetting.set('teaching_first_week_monday', '2026-09-07')
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = self.user.id
            session['user_role'] = self.user.role
        self.valid_time = patch('app.blueprints.user.reservations.validate_listening_time', return_value=(True, None))
        self.valid_time.start()

    def tearDown(self):
        self.valid_time.stop()
        cleanup_sqlite_database(db, drop_all=True)
        self.context.pop()

    def create(self, course_id=None):
        data = {'course_code': 'SAME', 'selection_code': '001', 'listening_info': '第8周 星期一 第1-2节'}
        if course_id is not None:
            data['course_id'] = course_id
        return self.client.post('/user/api/create_reservation', json=data)

    def legacy_registration(self):
        registration = CourseRegistration(user_id=self.user.id, course_code='SAME', selection_code='001', listening_info='第8周 星期一 第1-2节')
        db.session.add(registration)
        db.session.commit()
        return registration

    def bound_form(self, registration):
        form = LectureForm(registration_id=registration.id, listener_number=self.user.number, listener_name=self.user.name,
                           course_title=self.old.course_name, teacher_name='历史教师', teacher_college='学院', lecture_date='2026-06-01',
                           class_period='第1-2节', lecture_location='8-309', student_grade_class='测试班', teaching_method='讨论',
                           classroom_discipline='好', classroom_atmosphere='好', courseware_quality='无', overall_effect='好',
                           quality_case='否', course_feedback='测试反馈', student_signature1='同学', contact_phone1='13800000000')
        db.session.add(form)
        db.session.commit()
        return form

    def migrate(self):
        from app.services.registration_course_identity import ensure_registration_course_identity_schema
        db.session.rollback()
        ensure_registration_course_identity_schema()
        db.session.expire_all()

    def test_historical_ban_does_not_block_current_identity(self):
        db.session.add(ListeningBan(course_id=self.old.id, user_id=self.user.id))
        db.session.commit()
        available = self.client.get('/user/api/available_courses').get_json()
        self.assertIn(self.current.id, [course['id'] for course in available['results']])
        response = self.create(self.current.id)
        self.assertEqual(response.status_code, 200, response.get_json())
        registration = CourseRegistration.query.one()
        self.assertEqual(registration.course_id, self.current.id)
        self.assertEqual((registration.semester, registration.academic_year), ('2026-2027-1', '2026-2027'))

    def test_current_ban_blocks_then_cancellation_restores_creation(self):
        ban = ListeningBan(course_id=self.current.id, user_id=self.user.id)
        db.session.add(ban)
        db.session.commit()
        self.assertEqual(self.create(self.current.id).status_code, 403)
        db.session.delete(ban)
        db.session.commit()
        self.assertEqual(self.create(self.current.id).status_code, 200)

    def test_explicit_historical_id_cannot_create_current_registration(self):
        self.assertEqual(self.create(self.old.id).status_code, 400)
        self.assertEqual(CourseRegistration.query.count(), 0)

    def test_selected_id_rejects_boolean_float_and_nonpositive_inputs(self):
        for invalid in (True, False, float(self.current.id), 0, -1, '0', '03'):
            with self.subTest(course_id=invalid):
                self.assertEqual(self.create(invalid).status_code, 400)
        self.assertEqual(CourseRegistration.query.count(), 0)

    def test_deleted_resolved_course_never_reinterprets_same_pair_as_current(self):
        from app.services.registration_course_identity import resolve_registration_course, stamp_registration_course
        registration = self.legacy_registration()
        stamp_registration_course(registration, self.old)
        db.session.commit()
        db.session.delete(self.old)
        db.session.commit()
        self.assertIsNone(registration.course_id)
        self.assertEqual(registration.identity_status, 'resolved')
        self.assertIsNone(resolve_registration_course(registration))
        self.assertEqual(self.client.get('/user/api/unused_reservations').get_json()['data'], [])
        SystemSetting.set('course_registration_weekly_limit_enabled', 'true')
        self.assertEqual(self.create(self.current.id).status_code, 200)

    def test_legacy_cancel_targets_current_id_and_preserves_historical_row(self):
        from app.services.registration_course_identity import stamp_registration_course
        historical = self.legacy_registration()
        stamp_registration_course(historical, self.old)
        db.session.commit()
        created = self.create(self.current.id).get_json()['data']['id']
        response = self.client.post('/user/api/cancel_reservation', json={'course_id': self.current.id, 'course_code': 'SAME', 'selection_code': '001'})
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(db.session.get(CourseRegistration, historical.id))
        self.assertIsNone(db.session.get(CourseRegistration, created))

    def test_admin_history_displays_exact_current_and_restricted_legacy(self):
        self.create(self.current.id)
        legacy = self.legacy_registration()
        self.migrate()
        from app.blueprints.admin.users import _build_user_reservations
        records = {item['id']: item for item in _build_user_reservations(self.user)}
        current = next(item for key, item in records.items() if key != legacy.id)
        self.assertEqual(current['course_name'], '当前课程')
        self.assertEqual(current['course_id'], self.current.id)
        self.assertEqual(records[legacy.id]['course_name'], '历史课程（身份待核实）')
        self.assertTrue(records[legacy.id]['identity_restricted'])

    def test_admin_statistics_separates_exact_terms_teachers_and_unknown_history(self):
        from app.services.registration_course_identity import stamp_registration_course
        old_teacher = Teacher(teacher_id='old-teacher', name='历史教师')
        current_teacher = Teacher(teacher_id='current-teacher', name='当前教师')
        db.session.add_all([old_teacher, current_teacher])
        self.old.teacher = old_teacher
        self.current.teacher = current_teacher
        historical = self.legacy_registration()
        stamp_registration_course(historical, self.old)
        db.session.commit()
        self.create(self.current.id)
        self.legacy_registration()
        self.migrate()
        self.user.role = '超级管理员'
        db.session.commit()
        with self.client.session_transaction() as session:
            session['user_role'] = self.user.role
        response = self.client.get('/admin/api/registration_statistics')
        self.assertEqual(response.status_code, 200)
        teachers = {teacher['teacher_id']: teacher for teacher in response.get_json()['teachers']}
        self.assertEqual(teachers['old-teacher']['total_listen_count'], 1)
        self.assertEqual(teachers['current-teacher']['total_listen_count'], 1)
        self.assertEqual(teachers['current-teacher']['course_ids'], [self.current.id])
        self.assertEqual(teachers['']['total_listen_count'], 1)
        self.assertEqual(teachers['']['course_ids'], [])

    def test_ambiguous_current_pair_requires_selected_id(self):
        another = Course(course_code='SAME', selection_code='001', course_name='当前另一教学班', semester=self.current.semester)
        db.session.add(another)
        db.session.flush()
        db.session.add(ScheduleCourseMembership(batch_id=self.batch.id, course_id=another.id))
        db.session.commit()
        self.assertEqual(self.create().status_code, 400)
        self.assertEqual(self.create(another.id).status_code, 200)
        self.assertEqual(CourseRegistration.query.one().course_id, another.id)

    def test_bound_legacy_evidence_resolves_old_and_is_idempotent(self):
        registration = self.legacy_registration()
        form = self.bound_form(registration)
        self.migrate()
        migrated = db.session.get(CourseRegistration, registration.id)
        self.assertEqual(migrated.course_id, self.old.id)
        self.assertEqual(migrated.identity_status, 'resolved')
        evidence = migrated.identity_evidence_json
        self.assertEqual(json.loads(evidence)['source'], 'bound_form')
        self.assertIn(form.id, json.loads(evidence)['form_ids'])
        self.migrate()
        self.assertEqual(db.session.get(CourseRegistration, registration.id).identity_evidence_json, evidence)

    def test_unbound_ambiguous_history_is_readable_restricted_and_not_current_unused(self):
        registration = self.legacy_registration()
        self.migrate()
        migrated = db.session.get(CourseRegistration, registration.id)
        self.assertIsNone(migrated.course_id)
        self.assertEqual(migrated.identity_status, 'ambiguous')
        self.assertEqual(self.client.get('/user/api/unused_reservations').get_json()['data'], [])
        history = self.client.get('/user/api/my_reservations').get_json()['data']
        self.assertEqual([item['id'] for item in history], [registration.id])
        self.assertTrue(history[0]['identity_restricted'])
        self.assertFalse(history[0]['can_edit'])
        self.assertEqual(self.client.put(f'/user/api/my_reservations/{registration.id}', json={'listening_info': '第9周 星期一 第1-2节'}).status_code, 400)

    def test_unique_legacy_unused_is_pinned_before_future_import(self):
        current_id = self.current.id
        db.session.query(ScheduleCourseMembership).filter_by(course_id=current_id).delete()
        db.session.delete(self.current)
        db.session.commit()
        registration = self.legacy_registration()
        self.migrate()
        self.assertEqual(db.session.get(CourseRegistration, registration.id).course_id, self.old.id)
        replacement = Course(course_code='SAME', selection_code='001', course_name='新学期课程', semester='2026-2027-1')
        db.session.add(replacement)
        db.session.flush()
        db.session.add(ScheduleCourseMembership(batch_id=self.batch.id, course_id=replacement.id))
        db.session.commit()
        self.migrate()
        self.assertEqual(db.session.get(CourseRegistration, registration.id).course_id, self.old.id)
        self.assertEqual(self.client.get('/user/api/unused_reservations').get_json()['data'], [])
        self.assertEqual(self.client.get('/user/api/my_reservations').get_json()['data'][0]['course_name'], '历史课程')

    def test_history_and_weekly_limit_do_not_mix_historical_registrations(self):
        registration = self.legacy_registration()
        self.bound_form(registration)
        self.migrate()
        SystemSetting.set('course_registration_weekly_limit_enabled', 'true')
        SystemSetting.set('course_registration_weekly_limit_count', '1')
        response = self.create(self.current.id)
        self.assertEqual(response.status_code, 200, response.get_json())
        current_registration = CourseRegistration.query.filter(CourseRegistration.id != registration.id).one()
        history = self.client.get('/user/api/course_registration_history', query_string={'course_id': self.current.id, 'course_code': 'SAME', 'selection_code': '001'}).get_json()['data']
        self.assertEqual([item['id'] for item in history], [current_registration.id])

    def test_binding_service_preserves_exact_history_without_current_audit_evidence(self):
        registration = self.legacy_registration()
        self.bound_form(registration)
        self.migrate()
        from app.services.registration_course_identity import resolve_registration_course
        self.assertEqual(resolve_registration_course(registration).id, self.old.id)
        self.assertIsNone(resolve_registration_course(registration, current_only=True))

    def test_new_schema_standard_foreign_key_check_succeeds(self):
        with db.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA foreign_keys=ON')
            self.assertEqual(connection.exec_driver_sql('PRAGMA foreign_key_check').all(), [])

    def test_legacy_ban_schema_rebuild_preserves_rows_indexes_triggers_and_validates(self):
        user_id = self.user.id
        db.session.remove()
        with db.engine.begin() as connection:
            connection.exec_driver_sql('DROP TABLE lecture_bans')
            connection.exec_driver_sql('CREATE TABLE lecture_bans (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id), course_id VARCHAR(50) NOT NULL REFERENCES courses(course_code), created_at DATETIME, created_by INTEGER REFERENCES users(id), "遗留备注" TEXT CHECK(length("遗留备注") < 100))')
            connection.exec_driver_sql('CREATE INDEX legacy_ban_code ON lecture_bans(course_id)')
            connection.exec_driver_sql('CREATE TABLE ban_log (id INTEGER)')
            connection.exec_driver_sql('CREATE TRIGGER legacy_ban_audit AFTER INSERT ON lecture_bans BEGIN INSERT INTO ban_log(id) VALUES(NEW.id); END')
            connection.exec_driver_sql('INSERT INTO lecture_bans(id,user_id,course_id,created_at,created_by,"遗留备注") VALUES(42,?,\'SAME\',\'2026-01-01\',?,\'保持\')', (user_id, user_id))
        with db.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA foreign_keys=ON')
            connection.commit()
        self.migrate()
        self.migrate()
        with db.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql('PRAGMA foreign_keys').scalar(), 1)
            self.assertEqual(connection.exec_driver_sql('PRAGMA foreign_key_check').all(), [])
            row = connection.exec_driver_sql('SELECT * FROM lecture_bans').one()
            self.assertEqual(tuple(row), (42, user_id, 'SAME', '2026-01-01', user_id, '保持'))
            self.assertIn('legacy_ban_code', [row[1] for row in connection.exec_driver_sql('PRAGMA index_list(lecture_bans)').all()])
            self.assertEqual(connection.exec_driver_sql("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name='legacy_ban_audit'").scalar(), 1)
            self.assertEqual(connection.exec_driver_sql('SELECT id FROM ban_log').all(), [(42,)])


if __name__ == '__main__':
    unittest.main()
