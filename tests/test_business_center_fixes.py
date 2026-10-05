"""Business regressions use disposable SQLite databases and no external services."""
import os
from datetime import datetime
import tempfile
import unittest
from pathlib import Path
from sqlalchemy import create_engine, event, inspect
from werkzeug.security import generate_password_hash

_BOOT = Path(tempfile.gettempdir()) / 'swu-tic-center-test-bootstrap'
for _name, _part in [('SQLITE_DB_PATH', 'bootstrap.sqlite'), ('INSTANCE_DIR', 'instance'),
                     ('UPLOAD_FOLDER', 'uploads'), ('AUTOMATION_UPLOAD_DIR', 'automation')]:
    os.environ.setdefault(_name, str(_BOOT / _part))
os.environ['DATABASE_URL'] = ''
os.environ['STORAGE_CLEANUP_ENABLED'] = '0'
os.environ['DEEPSEEK_API_KEY'] = ''

from app.app import app
from app.models import (Course, Department, LectureForm, ScheduleCourseMapping,
                        ScheduleCourseMembership, ScheduleImportBatch,
                        ScheduleSemesterSelection, ScheduleImportRow, SystemSetting,
                        Teacher, User, Venue, db)
from app.services.assessment_calc import assessment_latest_form_groups_for_users
from app.services.current_courses import (current_course_query, current_course_status,
                                         ensure_current_course_schema, is_current_course)
from app.services.schedule_snapshots import get_active_schedule_batch
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database
from tests.test_schedule_snapshots import full_row, write_workbook

SEMESTER = '2026-2027-1'


class BusinessCenterFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='business-center-')
        self.root = Path(self.tmp.name)
        configure_sqlite_database(app, db, self.root / 'isolated.sqlite')
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.context = app.app_context()
        self.context.push()
        db.create_all()
        admin = User(number='center-super', department='测试部门', name='测试管理员',
                     gender='-', grade='2026', college='测试学院', major='-', dormitory='-',
                     phone='-', qq='-', student_id='center-super', password_hash='x',
                     role='超级管理员', group='测试组', is_active=True)
        db.session.add(admin)
        db.session.commit()
        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess.update(user_id=admin.id, user_role=admin.role, user_name=admin.name)
        self.old_template = os.environ.get('SCHEDULE_TEMPLATE_PATH')
        self.template = write_workbook(self.root / 'template.xlsx', [full_row(semester=SEMESTER)])
        os.environ['SCHEDULE_TEMPLATE_PATH'] = str(self.template)
        SystemSetting.set('teaching_current_semester', SEMESTER)
        db.session.commit()

    def tearDown(self):
        if self.old_template is None:
            os.environ.pop('SCHEDULE_TEMPLATE_PATH', None)
        else:
            os.environ['SCHEDULE_TEMPLATE_PATH'] = self.old_template
        cleanup_sqlite_database(db, drop_all=True)
        self.context.pop()
        self.tmp.cleanup()

    def upload(self, rows, endpoint='import', name='schedule.xlsx'):
        path = write_workbook(self.root / name, rows)
        with path.open('rb') as source:
            return self.client.post('/admin/api/schedule/' + endpoint,
                                    data={'file': (source, name)},
                                    content_type='multipart/form-data').get_json()

    def test_replacement_lists_only_current_rows_and_keeps_history(self):
        self.assertTrue(self.upload([full_row(semester=SEMESTER)])['success'])
        self.assertTrue(self.upload([full_row(semester=SEMESTER, venue='32-303',
                                            venue_id='V002', **{'上课地点': '32-303'})])['success'])
        listing = self.client.get('/admin/api/courses?semester=' + SEMESTER).get_json()
        self.assertTrue(listing['success'])
        self.assertEqual([r['class_location'] for c in listing['courses'] for r in c['records']], ['32-303'])
        self.assertEqual(Course.query.count(), 2, 'Old Course rows remain available to historical registrations')

    def test_bad_numeric_cell_fails_validation_with_excel_row_and_column(self):
        result = self.upload([full_row(semester=SEMESTER, **{'教学班人数': '未确认'})], 'validate')
        self.assertFalse(result['success'])
        self.assertIn('教学班人数', result['message'])
        self.assertIn('2', result['message'])

    def test_numeric_template_semester_accepts_new_string_semester(self):
        write_workbook(self.template, [full_row(semester=20261)])
        validation = self.upload([full_row(semester=SEMESTER)], endpoint='validate')
        self.assertTrue(validation['success'], validation.get('message'))
        imported = self.upload([full_row(semester=SEMESTER)])
        self.assertTrue(imported['success'], imported.get('message'))
        self.assertEqual(get_active_schedule_batch(SEMESTER).semester, SEMESTER)

    def test_numeric_template_identifiers_and_time_samples_accept_text_values(self):
        write_workbook(self.template, [full_row(semester=20261, **{
            '教工号': 1001, '场地编号': 2001, '课程号': 3001, '选课课号': 4001,
            '教师联系电话': 13900000000, '起始周': 1, '场地上课起始周': 1,
            '上课节次': 12, '场地上课节次': 12, '上课时间': 12, '学年': 2026,
        })])
        validation = self.upload([full_row(semester=SEMESTER)], endpoint='validate')
        self.assertTrue(validation['success'], validation.get('message'))
        imported = self.upload([full_row(semester=SEMESTER)])
        self.assertTrue(imported['success'], imported.get('message'))

    def seed_legacy_courses(self, count=48):
        db.session.bulk_insert_mappings(Teacher, [
            {'teacher_id': f'legacy-T{i}', 'name': f'旧教师{i}'} for i in range(count)
        ])
        db.session.bulk_insert_mappings(Venue, [
            {'venue_id': f'legacy-V{i}', 'name': f'旧场地{i}'} for i in range(count)
        ])
        db.session.bulk_insert_mappings(Course, [
            {'id': i + 1, 'course_code': f'legacy-C{i}', 'selection_code': f'legacy-S{i}',
             'course_name': '同名旧课程', 'semester': SEMESTER, 'weekday': 3,
             'start_week': '1-16', 'class_period': '第3-4节',
             'class_composition': f'旧班级{i}', 'teacher_id': f'legacy-T{i}',
             'venue_id': f'legacy-V{i}', 'class_location': f'旧场地{i}'}
            for i in range(count)
        ])
        batch = ScheduleImportBatch(semester=SEMESTER, source_filename='legacy.xlsx',
                                    source_sha256='legacy', status='active', row_count=count)
        db.session.add(batch)
        db.session.flush()
        db.session.add(ScheduleSemesterSelection(semester=SEMESTER, active_batch_id=batch.id))
        db.session.bulk_insert_mappings(ScheduleImportRow, [
            {'batch_id': batch.id, 'source_row': i + 2, 'teacher_name': f'旧教师{i}',
             'course_name': '同名旧课程', 'weekday_raw': '3', 'start_week_raw': '1-16',
             'class_period_raw': '第3-4节', 'class_composition_raw': f'旧班级{i}',
             'location_raw': f'旧场地{i}'} for i in range(count)
        ])
        db.session.commit()
        db.session.remove()

    def capture_course_query_sql(self, action):
        calls = {'total': 0, 'raw_snapshots': 0}
        engine = db.engine
        def count_sql(connection, cursor, statement, parameters, context, executemany):
            calls['total'] += 1
            if 'FROM schedule_import_rows' in statement:
                calls['raw_snapshots'] += 1
        event.listen(engine, 'before_cursor_execute', count_sql)
        try:
            result = action()
        finally:
            event.remove(engine, 'before_cursor_execute', count_sql)
        return result, calls

    def test_legacy_current_course_query_has_bounded_sql_for_distinct_teachers_and_venues(self):
        self.seed_legacy_courses()
        courses, calls = self.capture_course_query_sql(lambda: current_course_query().order_by(Course.id).limit(5).all())
        self.assertEqual([course.id for course in courses], [1, 2, 3, 4, 5])
        self.assertLessEqual(calls['total'], 20, calls)

    def test_admin_legacy_course_list_resolves_snapshot_once_with_bounded_sql(self):
        self.seed_legacy_courses()
        result, calls = self.capture_course_query_sql(lambda: self.client.get('/admin/api/courses?per_page=64').get_json())
        self.assertTrue(result['success'], result.get('message'))
        self.assertEqual(len(result['courses']), 48)
        self.assertEqual(calls['raw_snapshots'], 1, calls)
        self.assertLessEqual(calls['total'], 20, calls)

    def test_legacy_entry_identity_fields_remain_required_for_unique_match(self):
        self.assertTrue(self.upload([full_row(semester=SEMESTER)])['success'])
        course = Course.query.one()
        fields = {column.name: getattr(course, column.name) for column in Course.__table__.columns
                  if column.name not in {'id', 'created_at', 'updated_at'}}
        original_id = course.id
        for field, replacement in [('course_code', 'other-C'), ('selection_code', 'other-S'),
                                   ('venue_id', 'other-V'), ('venue_start_week', '17-20'),
                                   ('venue_class_period', '第5-6节')]:
            duplicate = dict(fields)
            duplicate[field] = replacement
            db.session.add(Course(**duplicate))
        ScheduleCourseMembership.query.delete()
        ScheduleCourseMapping.query.delete()
        db.session.commit()
        status = current_course_status()
        self.assertEqual(status['mapping_status'], 'LEGACY_SNAPSHOT_MATCH')
        self.assertEqual(status['ambiguous_rows'], 0)
        self.assertEqual(status['course_ids'], [original_id])

    def test_course_management_details_exclude_replaced_course_rows(self):
        self.assertTrue(self.upload([full_row(semester=SEMESTER)])['success'])
        self.assertTrue(self.upload([full_row(semester=SEMESTER, venue='32-303',
                                            venue_id='V002', **{'上课地点': '32-303'})])['success'])
        teacher = self.client.get('/admin/api/teachers/T001').get_json()['teacher']
        self.assertEqual([c['class_location'] for c in teacher['courses']], ['32-303'])
        old_venue = self.client.get('/admin/api/venues/V001').get_json()['venue']
        self.assertEqual(old_venue['courses'], [])

    def test_exact_map_excludes_previous_numeric_only_correction(self):
        self.assertTrue(self.upload([full_row(semester=SEMESTER)])['success'])
        original_id = Course.query.one().id
        self.assertTrue(self.upload([full_row(semester=SEMESTER, **{'教学班人数': 32})])['success'])
        self.assertEqual([c.class_size for c in current_course_query().all()], [32])
        self.assertFalse(is_current_course(original_id))
        self.assertEqual(Course.query.count(), 2)

    def test_explicit_empty_map_never_uses_legacy_fallback(self):
        self.assertTrue(self.upload([full_row(semester=SEMESTER)])['success'])
        batch = get_active_schedule_batch(SEMESTER)
        self.assertIsNotNone(db.session.get(ScheduleCourseMapping, batch.id))
        ScheduleCourseMembership.query.filter_by(batch_id=batch.id).delete()
        db.session.commit()
        self.assertEqual(current_course_status()['mapping_status'], 'COMPLETE')
        self.assertEqual(current_course_query().count(), 0)

    def test_invalid_authority_fails_closed(self):
        self.assertTrue(self.upload([full_row(semester=SEMESTER)])['success'])
        other = ScheduleImportBatch(semester='其他学期', source_filename='other.xlsx',
                                    source_sha256='other', status='active', row_count=0)
        db.session.add(other)
        db.session.flush()
        db.session.get(ScheduleSemesterSelection, SEMESTER).active_batch_id = other.id
        db.session.commit()
        self.assertEqual(current_course_status()['status'], 'INVALID_AUTHORITY')
        self.assertEqual(current_course_query().count(), 0)

    def test_legacy_snapshot_reports_ambiguity_instead_of_exposing_two_revisions(self):
        self.assertTrue(self.upload([full_row(semester=SEMESTER)])['success'])
        self.assertTrue(self.upload([full_row(semester=SEMESTER, **{'教学班人数': 32})])['success'])
        ScheduleCourseMembership.query.delete()
        ScheduleCourseMapping.query.delete()
        db.session.commit()
        status = current_course_status()
        self.assertEqual(status['mapping_status'], 'LEGACY_SNAPSHOT_MATCH')
        self.assertEqual(status['ambiguous_rows'], 1)
        self.assertEqual(current_course_query().count(), 0)

    def test_existing_database_adds_only_mapping_tables_and_keeps_old_entities(self):
        self.assertTrue(self.upload([full_row(semester=SEMESTER)])['success'])
        batch_id = get_active_schedule_batch(SEMESTER).id
        course_id = Course.query.one().id
        db.session.commit()
        ScheduleCourseMembership.__table__.drop(bind=db.engine)
        ScheduleCourseMapping.__table__.drop(bind=db.engine)
        before_tables = set(inspect(db.engine).get_table_names())
        ensure_current_course_schema()
        after_tables = set(inspect(db.engine).get_table_names())
        self.assertEqual(after_tables - before_tables,
                         {'schedule_course_mappings', 'schedule_course_memberships'})
        self.assertEqual(get_active_schedule_batch(SEMESTER).id, batch_id)
        self.assertEqual(Course.query.one().id, course_id)
        self.assertEqual(current_course_status()['mapping_status'], 'LEGACY_SNAPSHOT_MATCH')
        self.assertEqual([c.id for c in current_course_query().all()], [course_id])

    def test_clear_courses_also_clears_membership_and_retains_complete_marker(self):
        self.assertTrue(self.upload([full_row(semester=SEMESTER)])['success'])
        admin = User.query.filter_by(number='center-super').one()
        admin.password_hash = generate_password_hash('test-clear-only')
        db.session.commit()
        result = self.client.post('/admin/api/clear_table', data={
            'table_name': 'courses', 'password': 'test-clear-only',
        }).get_json()
        self.assertTrue(result['success'])
        self.assertEqual(Course.query.count(), 0)
        self.assertEqual(ScheduleCourseMembership.query.count(), 0)
        self.assertEqual(ScheduleCourseMapping.query.count(), 1)

    def test_new_membership_foreign_key_cascades_with_fk_enforcement(self):
        # Verify the new FK independently of unrelated legacy schema FKs.
        engine = create_engine('sqlite:///:memory:')
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql('PRAGMA foreign_keys=ON')
                connection.exec_driver_sql('CREATE TABLE courses (id INTEGER PRIMARY KEY)')
                connection.exec_driver_sql('CREATE TABLE schedule_import_batches (id INTEGER PRIMARY KEY)')
                ScheduleCourseMapping.__table__.create(bind=connection)
                ScheduleCourseMembership.__table__.create(bind=connection)
                connection.exec_driver_sql('INSERT INTO courses VALUES (1)')
                connection.exec_driver_sql('INSERT INTO schedule_import_batches VALUES (1)')
                connection.execute(ScheduleCourseMapping.__table__.insert().values(batch_id=1))
                connection.execute(ScheduleCourseMembership.__table__.insert().values(batch_id=1, course_id=1))
                connection.exec_driver_sql('DELETE FROM courses WHERE id=1')
                self.assertEqual(connection.exec_driver_sql('SELECT count(*) FROM schedule_course_memberships').scalar(), 0)
                self.assertEqual(connection.exec_driver_sql('SELECT count(*) FROM schedule_course_mappings').scalar(), 1)
        finally:
            engine.dispose()

    def test_bad_numeric_import_keeps_formal_batch_and_all_entities(self):
        self.assertTrue(self.upload([full_row(semester=SEMESTER)])['success'])
        original = get_active_schedule_batch(SEMESTER).id
        result = self.upload([full_row(semester=SEMESTER, teacher_id='T999', venue_id='V999',
                                      **{'教学班人数': '未确认'})])
        self.assertFalse(result['success'])
        self.assertEqual(get_active_schedule_batch(SEMESTER).id, original)
        self.assertEqual(ScheduleImportBatch.query.count(), 1)
        self.assertEqual(Course.query.count(), 1)
        self.assertIsNone(db.session.get(Teacher, 'T999'))

    def test_row_error_rolls_back_valid_sibling_and_formal_batch(self):
        self.assertTrue(self.upload([full_row(semester=SEMESTER)])['success'])
        original = get_active_schedule_batch(SEMESTER).id
        result = self.upload([
            full_row(semester=SEMESTER, teacher_id='T999', **{'课程号': 'C999'}),
            full_row(semester=SEMESTER, **{'课程号': None}),
        ])
        self.assertFalse(result['success'])
        self.assertEqual(get_active_schedule_batch(SEMESTER).id, original)
        self.assertEqual(ScheduleImportBatch.query.count(), 1)
        self.assertEqual(Course.query.count(), 1)
        self.assertIsNone(db.session.get(Teacher, 'T999'))

    def add_listener(self):
        listener = User(number='center-listener', department='测试部门', name='测试信息员',
                        gender='-', grade='2026', college='测试学院', major='-', dormitory='-',
                        phone='-', qq='-', student_id='center-listener', password_hash='x',
                        role='信息员', group='测试组', is_active=True)
        db.session.add_all([listener, Department(name='测试部门')])
        db.session.commit()
        return listener

    @staticmethod
    def form(number, status='中心已审核', unique_id=None):
        return LectureForm(listener_name=number, listener_number=number,
                           lecture_date='2026-09-21', class_period='第3-4节',
                           lecture_location='32-302', teacher_name='测试老师',
                           teacher_college='测试学院', course_title='测试课程',
                           student_grade_class='测试班', teaching_method='优',
                           classroom_discipline='优', classroom_atmosphere='优',
                           courseware_quality='优', overall_effect='优', quality_case='无',
                           course_feedback='测试反馈', student_signature1='测试同学',
                           contact_phone1='13900000001', status=status, unique_id=unique_id,
                           created_at=datetime(2026, 9, 22, 10))

    def teaching_settings(self):
        return self.client.post('/admin/api/settings/teaching', json={
            'first_week_monday': '2026-09-07', 'total_weeks': 20,
            'week_start_day': 0, 'required_submission_count': 1,
            'current_semester': SEMESTER,
        })

    def test_month_requirement_counts_each_week_once_and_preserves_penalties(self):
        listener = self.add_listener()
        self.assertTrue(self.teaching_settings().get_json()['success'])
        db.session.add(self.form(listener.number))
        db.session.commit()
        result = self.client.get('/admin/api/review/department-monthly-assessment/stats'
                                 '?start_date=2026-09-07&end_date=2026-10-04&departments=测试部门').get_json()
        month = result['departments'][0]['months'][0]
        self.assertEqual([w['required_submission'] for w in month['weeks']], [1, 1, 1, 1])
        self.assertEqual(month['summary']['required_submission'], 4)
        self.assertEqual(month['summary']['missing_penalty'], 9)
        self.assertEqual(month['summary']['shortage_count'], 3)

    def test_overlapping_month_save_fails_and_keeps_previous_configuration(self):
        valid = [{'start_week': 1, 'end_week': 4, 'label': '原月'},
                 {'start_week': 5, 'end_week': 8, 'label': '下月'}]
        self.assertTrue(self.client.post('/admin/api/teaching-month-definitions', json={'months': valid}).get_json()['success'])
        response = self.client.post('/admin/api/teaching-month-definitions', json={'months': [
            {'start_week': 1, 'end_week': 4, 'label': '月A'},
            {'start_week': 3, 'end_week': 6, 'label': '月B'},
        ]})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self.assertIn('重叠', response.get_json()['message'])
        self.assertEqual(self.client.get('/admin/api/teaching-month-definitions').get_json()['months'], valid)

    def reject_month_configuration_and_keep_saved(self, months):
        original = [{'start_week': 1, 'end_week': 4, 'label': '原月'}]
        self.assertTrue(self.client.post('/admin/api/teaching-month-definitions',
                                         json={'months': original}).get_json()['success'])
        response = self.client.post('/admin/api/teaching-month-definitions', json={'months': months})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])
        self.assertEqual(self.client.get('/admin/api/teaching-month-definitions').get_json()['months'], original)
        return response.get_json()

    def test_months_above_current_calendar_bound_rejected_and_previous_kept(self):
        SystemSetting.set('teaching_total_weeks', '20')
        result = self.reject_month_configuration_and_keep_saved([
            {'start_week': 21, 'end_week': 24, 'label': '越界月'},
        ])
        self.assertIn('20', result['message'])

    def test_months_decimal_endpoints_rejected_without_integer_truncation(self):
        self.reject_month_configuration_and_keep_saved([
            {'start_week': 1.5, 'end_week': 4.5, 'label': '小数月'},
        ])

    def test_months_boolean_endpoints_rejected(self):
        self.reject_month_configuration_and_keep_saved([
            {'start_week': True, 'end_week': 4, 'label': '布尔月'},
        ])

    def test_months_follow_configured_shorter_calendar_bound(self):
        SystemSetting.set('teaching_total_weeks', '12')
        result = self.reject_month_configuration_and_keep_saved([
            {'start_week': 9, 'end_week': 13, 'label': '超出12周'},
        ])
        self.assertIn('12', result['message'])

    def test_months_invalid_total_weeks_uses_existing_safe_default_twenty(self):
        for value in ['未配置', '0', '53']:
            with self.subTest(value=value):
                SystemSetting.set('teaching_total_weeks', value)
                result = self.reject_month_configuration_and_keep_saved([
                    {'start_week': 21, 'end_week': 24, 'label': '越界月'},
                ])
                self.assertIn('20', result['message'])

    def test_adjacent_integer_months_within_calendar_can_be_saved(self):
        SystemSetting.set('teaching_total_weeks', '12')
        valid = [{'start_week': 1, 'end_week': 4, 'label': '首月'},
                 {'start_week': 5, 'end_week': 12, 'label': '次月'}]
        response = self.client.post('/admin/api/teaching-month-definitions', json={'months': valid})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertEqual(self.client.get('/admin/api/teaching-month-definitions').get_json()['months'], valid)

    def test_assessment_uses_physical_latest_id_after_base_timestamp_backfill(self):
        listener = self.add_listener()
        base = self.form(listener.number, status='待审核')
        db.session.add(base)
        db.session.flush()
        final = self.form(listener.number, unique_id=base.id)
        final.updated_at = datetime(2026, 9, 22, 12)
        db.session.add(final)
        db.session.flush()
        base.unique_id = base.id
        base.updated_at = datetime(2026, 9, 22, 13)
        db.session.commit()
        group = assessment_latest_form_groups_for_users([listener.number])[0]
        self.assertEqual(group['latest_form'].id, final.id)
        self.assertEqual([f.id for f in group['forms']], [final.id, base.id])


if __name__ == '__main__':
    unittest.main()
