"""Launch regressions: disposable databases, semester rules and stats parity."""
import importlib
import os
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

_BOOT_DIR = tempfile.TemporaryDirectory(prefix='launch-assessment-bootstrap-')
_BOOT = Path(_BOOT_DIR.name)
_ENV = {
    'DATABASE_URL': '', 'INSTANCE_DIR': str(_BOOT / 'instance'),
    'SQLITE_DB_PATH': str(_BOOT / 'bootstrap.sqlite'),
    'UPLOAD_FOLDER': str(_BOOT / 'uploads'),
    'AUTOMATION_UPLOAD_DIR': str(_BOOT / 'automation'),
    'AUTO_REVIEW_UPLOAD_DIR': str(_BOOT / 'auto-review'),
    'AUTO_REVIEW_REPORT_DIR': str(_BOOT / 'reports'),
    'EXPORT_DIR': str(_BOOT / 'exports'),
    'CONTACT_TEMPLATE_PATH': str(_BOOT / 'contacts.xlsx'),
    'SCHEDULE_TEMPLATE_PATH': str(_BOOT / 'schedule.xlsx'),
    'AUTO_REVIEW_DEFAULT_SCHEDULE_PATH': str(_BOOT / 'missing.xlsx'),
    'STORAGE_CLEANUP_ENABLED': 'false', 'DEEPSEEK_API_KEY': '',
    'DEEPSEEK_BASE_URL': 'http://127.0.0.1:1/disabled',
    'CELERY_BROKER_URL': 'memory://', 'CELERY_RESULT_BACKEND': 'cache+memory://',
    'SECRET_KEY': 'isolated-launch-assessment',
}
os.environ.update(_ENV)

from sqlalchemy import create_engine, inspect
from app.app import app
from app.models import AssessmentOverride, LectureForm, SystemSetting, User, db
from app.services.assessment_calc import build_department_monthly_assessment_payload
from app.utils.leave_management import (get_teaching_settings, get_user_relevant_leave_statuses,
                                        record_leave_makeup_form)
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

AUTUMN = '2026-2027-1'
SPRING = '2025-2026-2'


class _FixedLeaveDatetime(datetime):
    @classmethod
    def now(cls):
        return cls(2026, 10, 5, 12, 0, 0)


class LaunchAssessmentSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='launch-assessment-')
        configure_sqlite_database(app, db, Path(self.tmp.name) / 'test.sqlite')
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.context = app.app_context()
        self.context.push()
        self.leave_clock = patch('app.utils.leave_management.datetime', _FixedLeaveDatetime)
        self.leave_clock.start()
        db.create_all()
        self.admin = self.make_user('super', '超级管理员')
        self.user = self.make_user('member', '信息员')
        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess.update(user_id=self.admin.id, user_role=self.admin.role, user_name=self.admin.name)
        self.set_term(AUTUMN, '2026-09-07')

    def tearDown(self):
        self.leave_clock.stop()
        cleanup_sqlite_database(db, drop_all=True)
        self.context.pop()
        self.tmp.cleanup()

    def make_user(self, number, role):
        user = User(number=number, student_id=number, name=number, role=role,
                    department='测试部门', group='测试组', gender='-', grade='-', college='测试学院',
                    major='-', dormitory='-', phone='-', qq='-', password_hash='x', is_active=True)
        db.session.add(user)
        db.session.commit()
        return user

    def set_term(self, semester, start):
        for key, value in [('teaching_current_semester', semester), ('teaching_first_week_monday', start),
                           ('teaching_week_start_day', '0'), ('teaching_total_weeks', '16'),
                           ('teaching_required_submission', '1')]:
            SystemSetting.set(key, value)
        db.session.commit()

    def create_rule(self, **changes):
        body = dict(user_ids=[self.user.id], start_week=1, end_week=1,
                    override_type='leave', reason='合成请假')
        body.update(changes)
        return self.client.post('/admin/api/assessment-overrides', json=body)

    def monthly_required(self):
        payload, err = build_department_monthly_assessment_payload(
            self.admin.id, datetime(2026, 9, 7), datetime(2026, 10, 5), ['测试部门'])
        self.assertIsNone(err)
        return payload['departments'][0]['months'][0]['summary']['required_submission']

    def legacy_rule(self):
        rule = AssessmentOverride(user_id=self.user.id, start_week=1, end_week=1,
                                  override_type='leave', reason='未归属历史请假', created_by=self.admin.id)
        db.session.add(rule)
        db.session.commit()
        return rule

    def test_fresh_schema_contains_nullable_semester(self):
        columns = {c['name']: c for c in inspect(db.engine).get_columns('assessment_overrides')}
        self.assertIn('semester', columns)
        self.assertTrue(columns['semester']['nullable'])

    def test_invalid_week_types_and_beyond_total_do_not_persist(self):
        for start, end in [(1.9, 3.9), (True, True), (17, 999), (0, 1), (2, 1)]:
            AssessmentOverride.query.delete()
            db.session.commit()
            with self.subTest(start=start, end=end):
                response = self.create_rule(start_week=start, end_week=end)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(AssessmentOverride.query.count(), 0)

    def test_non_object_json_is_controlled_400(self):
        app.config['TESTING'] = False
        for endpoint in ['/admin/api/assessment-overrides', '/admin/api/teaching-month-definitions',
                         '/admin/api/review/submission-count/snapshots',
                         '/admin/api/review/department-monthly-assessment/snapshots',
                         '/admin/api/leave-management/current-week']:
            for body in [[1], True, 'x', None]:
                with self.subTest(endpoint=endpoint, body=body):
                    response = self.client.post(endpoint, json=body)
                    self.assertEqual(response.status_code, 400)
                    self.assertFalse(response.get_json()['success'])

    def test_default_months_stop_at_configured_total_and_can_be_saved(self):
        for total, expected_end in [(16, 16), (18, 18), (1, 1), (52, 52)]:
            SystemSetting.set('teaching_total_weeks', str(total))
            SystemSetting.set('teaching_month_definitions', '')
            result = self.client.get('/admin/api/teaching-month-definitions').get_json()
            self.assertEqual(result['months'][-1]['end_week'], expected_end)
            self.assertEqual(self.client.post('/admin/api/teaching-month-definitions',
                                             json={'months': result['months']}).status_code, 200)

    def test_new_rule_uses_backend_semester_and_same_weeks_can_exist_in_new_term(self):
        self.set_term(SPRING, '2026-03-02')
        first = self.create_rule(semester='client-cannot-choose')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(getattr(AssessmentOverride.query.one(), 'semester', None), SPRING)
        self.set_term(AUTUMN, '2026-09-07')
        second = self.create_rule()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()['created_count'], 1)
        self.assertEqual(AssessmentOverride.query.count(), 2)

    def test_rule_creation_requires_explicit_backend_semester(self):
        SystemSetting.set('teaching_current_semester', '')
        response = self.create_rule()
        self.assertEqual(response.status_code, 400)
        self.assertIn('学期', response.get_json()['message'])

    def test_assignment_requires_super_and_conflict_preserves_legacy_rule(self):
        rule = self.legacy_rule()
        self.assertEqual(self.create_rule().status_code, 200)
        conflict = self.client.put(f'/admin/api/assessment-overrides/{rule.id}/semester',
                                   json={'semester': AUTUMN})
        self.assertEqual(conflict.status_code, 409)
        db.session.expire_all()
        self.assertIsNone(db.session.get(AssessmentOverride, rule.id).semester)
        self.assertEqual(AssessmentOverride.query.count(), 2)
        with self.client.session_transaction() as sess:
            sess.update(user_id=self.user.id, user_role=self.user.role, user_name=self.user.name)
        denied = self.client.put(f'/admin/api/assessment-overrides/{rule.id}/semester',
                                 json={'semester': SPRING})
        self.assertEqual(denied.status_code, 403)
        self.assertIsNone(db.session.get(AssessmentOverride, rule.id).semester)

    def test_assignment_constraint_failure_rolls_back_as_controlled_conflict(self):
        from sqlalchemy import event
        from sqlalchemy.exc import IntegrityError
        rule = self.legacy_rule()
        rule_id = rule.id
        def inject_constraint_failure(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith('UPDATE assessment_overrides SET'):
                raise IntegrityError(statement, parameters, RuntimeError('injected concurrent scope conflict'))
        app.config['TESTING'] = False
        event.listen(db.engine, 'before_cursor_execute', inject_constraint_failure)
        try:
            response = self.client.put(f'/admin/api/assessment-overrides/{rule_id}/semester',
                                       json={'semester': AUTUMN})
        finally:
            event.remove(db.engine, 'before_cursor_execute', inject_constraint_failure)
        self.assertEqual(response.status_code, 409)
        db.session.expire_all()
        self.assertIsNone(db.session.get(AssessmentOverride, rule_id).semester)
        self.assertEqual(AssessmentOverride.query.count(), 1)

    def test_spring_leave_does_not_change_autumn_month_or_makeup(self):
        baseline = self.monthly_required()
        self.set_term(SPRING, '2026-03-02')
        self.assertEqual(self.create_rule().status_code, 200)
        spring_rule = AssessmentOverride.query.one()
        self.set_term(AUTUMN, '2026-09-07')
        self.assertEqual(self.monthly_required(), baseline)
        settings, _ = get_teaching_settings()
        self.assertEqual(get_user_relevant_leave_statuses(self.user, settings, current_week=2), [])
        form = self.make_form(1)
        self.assertIsNone(record_leave_makeup_form(self.user, {'override_id': spring_rule.id}, form))
        db.session.commit()
        self.assertIsNone(spring_rule.override_value)
        self.assertEqual(self.client.get(f'/admin/api/leave-management/{spring_rule.id}/forms').status_code, 409)

    def test_unbound_rule_is_visible_warned_and_not_applied_until_explicit_assignment(self):
        baseline = self.monthly_required()
        rule = self.legacy_rule()
        self.assertEqual(self.monthly_required(), baseline)
        listing = self.client.get('/admin/api/assessment-overrides/list?departments=测试部门').get_json()
        entry = listing['departments'][0]['groups'][0]['members'][0]['overrides'][0]
        self.assertEqual(entry['scope_status'], 'unassigned')
        self.assertFalse(entry['applies_to_current_semester'])
        self.assertIn('未归属', listing['scope_warning'])
        assigned = self.client.put(f'/admin/api/assessment-overrides/{rule.id}/semester',
                                   json={'semester': AUTUMN})
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(self.monthly_required(), baseline - 1)
        settings, _ = get_teaching_settings()
        self.assertEqual(len(get_user_relevant_leave_statuses(self.user, settings, current_week=2)), 1)
        reassign = self.client.put(f'/admin/api/assessment-overrides/{rule.id}/semester',
                                   json={'semester': SPRING})
        self.assertEqual(reassign.status_code, 409)

    def test_current_week_leave_binds_backend_semester(self):
        response = self.client.post('/admin/api/leave-management/current-week',
                                    json={'user_id': self.user.id, 'reason': '合成当前周请假'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(getattr(AssessmentOverride.query.one(), 'semester', None), AUTUMN)

    def test_current_leave_makeup_cannot_select_old_term_forms(self):
        self.assertEqual(self.create_rule().status_code, 200)
        rule = AssessmentOverride.query.one()
        current = self.make_form(1)
        old = self.make_form(2)
        old.lecture_date = '2026-03-12'
        old.audit_tag = '周次修正1'
        db.session.commit()
        options = self.client.get(f'/admin/api/leave-management/{rule.id}/forms').get_json()
        self.assertEqual({f['form_id'] for f in options['forms']}, {current.id})
        response = self.client.put(f'/admin/api/leave-management/{rule.id}/makeup-forms',
                                   json={'unique_ids': [old.unique_id]})
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(rule.override_value)
        self.assertIsNone(record_leave_makeup_form(self.user, {'override_id': rule.id}, old))

    def make_form(self, uid):
        form = LectureForm(unique_id=uid, listener_name=self.user.name, listener_number=self.user.number,
                           lecture_date=f'2026-10-0{uid}', class_period='第1-2节', lecture_location='测试教室',
                           teacher_name='合成教师', teacher_college='测试学院', course_title=f'合成课程{uid}',
                           student_grade_class='测试班级', teaching_method='讲授', classroom_discipline='好',
                           classroom_atmosphere='好', courseware_quality='好', overall_effect='好',
                           quality_case='推荐', course_feedback='测试反馈', student_signature1='合成同学',
                           contact_phone1='13800000001', created_at=datetime(2026, 9, uid),
                           updated_at=datetime(2026, 10, uid, 10))
        db.session.add(form)
        db.session.commit()
        return form

    def test_updated_and_lecture_details_match_count_and_each_record_time(self):
        for uid in [1, 2, 3]:
            self.make_form(uid)
        for kind in ['updated', 'lecture']:
            query = f'?start_date=2026-10-01&end_date=2026-10-02&time_filter_type={kind}'
            result = self.client.get(f'/admin/api/review/submission-count/detail/{self.user.id}' + query).get_json()
            self.assertEqual({g['unique_id'] for g in result['groups']}, {1, 2})
            expected = {'2026-10-01 10:00:00', '2026-10-02 10:00:00'} if kind == 'updated' else {
                '2026-10-01 00:00:00', '2026-10-02 00:00:00'}
            self.assertEqual({g['filter_datetime'] for g in result['groups']}, expected)
            counts = self.client.get('/admin/api/review/submission-count/stats' + query).get_json()
            member = next(r for r in counts['users'] if r['user_id'] == self.user.id)
            self.assertEqual(member['submission_group_count'], len(result['groups']))


class AssessmentSchemaMigrationTests(unittest.TestCase):
    def test_schema_failure_after_copy_rolls_back_original_table(self):
        from sqlalchemy import event
        ensure = importlib.import_module('app.services.assessment_override_schema').ensure_assessment_override_schema
        with tempfile.TemporaryDirectory(prefix='assessment-migration-failure-') as tmp:
            engine = create_engine('sqlite:///' + str(Path(tmp) / 'failure.sqlite'))
            with engine.begin() as conn:
                db.metadata.tables['users'].create(conn)
                legacy_sql = '''CREATE TABLE assessment_overrides (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, start_week INTEGER NOT NULL,
                    end_week INTEGER NOT NULL, override_type VARCHAR(50) NOT NULL,
                    override_value TEXT, reason VARCHAR(200) NOT NULL, created_by INTEGER NOT NULL,
                    created_at DATETIME, updated_at DATETIME,
                    UNIQUE (user_id,start_week,end_week,override_type))'''
                conn.exec_driver_sql(legacy_sql)
                conn.exec_driver_sql("INSERT INTO assessment_overrides VALUES (7,1,1,2,'leave',NULL,'不可丢失',1,NULL,NULL)")
                before = [tuple(r) for r in conn.exec_driver_sql('SELECT * FROM assessment_overrides')]
            def fail_at_swap(connection, cursor, statement, parameters, context, executemany):
                if statement.startswith('ALTER TABLE assessment_overrides__semester_migration'):
                    raise RuntimeError('injected migration swap failure')
            event.listen(engine, 'before_cursor_execute', fail_at_swap)
            try:
                with self.assertRaisesRegex(RuntimeError, 'injected migration'):
                    ensure(engine)
            finally:
                event.remove(engine, 'before_cursor_execute', fail_at_swap)
            with engine.connect() as conn:
                self.assertEqual([tuple(r) for r in conn.exec_driver_sql('SELECT * FROM assessment_overrides')], before)
                self.assertNotIn('semester', {c['name'] for c in inspect(conn).get_columns('assessment_overrides')})
                self.assertFalse(inspect(conn).has_table('assessment_overrides__semester_migration'))
            engine.dispose()

    def test_old_schema_rows_preserved_idempotent_and_cross_semester_insert(self):
        spec = importlib.util.find_spec('app.services.assessment_override_schema')
        self.assertIsNotNone(spec, 'An explicit old-schema migration entrypoint is required')
        ensure = importlib.import_module('app.services.assessment_override_schema').ensure_assessment_override_schema
        with tempfile.TemporaryDirectory(prefix='assessment-old-schema-') as tmp:
            engine = create_engine('sqlite:///' + str(Path(tmp) / 'old.sqlite'))
            with engine.begin() as conn:
                conn.exec_driver_sql('CREATE TABLE users (id INTEGER PRIMARY KEY)')
                conn.exec_driver_sql('INSERT INTO users VALUES (1)')
                conn.exec_driver_sql('''CREATE TABLE assessment_overrides (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, start_week INTEGER NOT NULL,
                    end_week INTEGER NOT NULL, override_type VARCHAR(50) NOT NULL,
                    override_value TEXT, reason VARCHAR(200) NOT NULL, created_by INTEGER NOT NULL,
                    created_at DATETIME, updated_at DATETIME,
                    CONSTRAINT unique_user_week_override UNIQUE (user_id,start_week,end_week,override_type),
                    FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(created_by) REFERENCES users(id))''')
                conn.exec_driver_sql("INSERT INTO assessment_overrides VALUES (7,1,1,2,'leave','{\"makeup_forms\": []}','历史原文',1,'2026-03-02 01:00:00','2026-03-03 02:00:00')")
                conn.exec_driver_sql('CREATE INDEX historical_reason_index ON assessment_overrides(reason)')
                before = [tuple(row) for row in conn.exec_driver_sql('SELECT * FROM assessment_overrides')]
            ensure(engine)
            ensure(engine)
            with engine.begin() as conn:
                columns = [c['name'] for c in inspect(conn).get_columns('assessment_overrides') if c['name'] != 'semester']
                after = [tuple(row) for row in conn.exec_driver_sql('SELECT ' + ','.join(columns) + ' FROM assessment_overrides')]
                self.assertEqual(after, before)
                self.assertIsNone(conn.exec_driver_sql('SELECT semester FROM assessment_overrides WHERE id=7').scalar())
                for semester in [SPRING, AUTUMN]:
                    conn.exec_driver_sql('INSERT INTO assessment_overrides (user_id,start_week,end_week,override_type,reason,created_by,semester) VALUES (1,1,2,\'leave\',\'新规则\',1,?)', (semester,))
                self.assertEqual(conn.exec_driver_sql('SELECT count(*) FROM assessment_overrides').scalar(), 3)
                self.assertEqual(conn.exec_driver_sql('PRAGMA integrity_check').scalar(), 'ok')
                self.assertIn('historical_reason_index', {i['name'] for i in inspect(conn).get_indexes('assessment_overrides')})
                self.assertEqual(conn.exec_driver_sql('PRAGMA foreign_key_check').all(), [])
            engine.dispose()
