"""Semester identity: one composition rule, one resolution contract.

The institution's workbook stores ``学年`` and ``学期`` in separate columns while
the application identifies a semester with a single identifier such as
``2026-2027-1``.  Reading only the raw term cell detaches every imported Course
from the configured semester, which silently closes the registration gate.
"""
import os
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pytest

os.environ.update({
    'SQLITE_DB_PATH': '', 'DATABASE_URL': '',
    'DEEPSEEK_API_KEY': '', 'STORAGE_CLEANUP_ENABLED': '0',
    'SECRET_KEY': 'test-only',
})

from app.app import app  # noqa: E402
from app.models import (  # noqa: E402
    Course, ListeningAssistantScheduleEntry, ListeningBan, ScheduleCourseMapping,
    ScheduleCourseMembership, ScheduleImportBatch, ScheduleSemesterSelection,
    SystemSetting, User, db,
)
from app.services.academic_term import (  # noqa: E402
    clean_term_text,
    compose_semester_identifier,
)
from app.services.schedule_availability import current_schedule_availability  # noqa: E402
from app.services.schedule_snapshots import (  # noqa: E402
    AMBIGUOUS,
    CURRENT_SEMESTER_CONFIGURED_BUT_NO_ACTIVE_SNAPSHOT,
    READY,
    resolve_current_schedule_snapshot,
)
from tests.app_test_utils import (  # noqa: E402
    cleanup_sqlite_database,
    configure_sqlite_database,
)

HEADERS = [
    '教工号', '姓名', '性别', '职称名称', '教师所属学院', '教师联系电话',
    '场地编号', '场地名称', '场地类别名称', '校区', '楼层号', '教学楼', '座位数',
    '课程号', '选课课号', '起始周', '星期几', '上课节次', '课程名称',
    '场地上课起始周', '场地上课节次', '教学班人数', '教学班组成', '学分', '总学时',
    '开课学院', '专业组成', '选课人数', '周学时', '上课时间', '上课地点',
    '课程性质', '学期', '学年',
]


def _row(term, year, code='C001'):
    return [
        'T001', 'Teacher', '男', '教授', '理学院', '139', 'V001', 'A101', '教学楼',
        '北碚', '1', 'A', '100', code, f'{code}-S1', '1-16', '3', '第3-4节', 'Math',
        '1-16', '第3-4节', '30', '行政班1', '2', '32', '理学院', '数学', '25', '2',
        '周三第3-4节', 'A101', '必修', term, year,
    ]


@pytest.fixture
def case(tmp_path):
    """Rebind the app to a per-test SQLite file, as other suites do.

    Relying on module-level environment variables is not enough here: other
    modules in a full-suite run rebind the shared app engine, which left this
    file pointing at a database path that no longer existed.
    """
    configure_sqlite_database(app, db, tmp_path / 'identity.sqlite')
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.create_all()
        admin = User(number='SEM', student_id='sem', department='测试部门', name='学期测试',
                     gender='-', grade='-', college='测试学院', major='-', dormitory='-',
                     phone='-', qq='-', password_hash='unused', role='超级管理员',
                     group='测试组', is_active=True)
        db.session.add(admin)
        db.session.commit()
        client = app.test_client()
        with client.session_transaction() as session:
            session.update(user_id=admin.id, user_role=admin.role, user_name=admin.name)
        yield SimpleNamespace(client=client, root=tmp_path)
        cleanup_sqlite_database(db, drop_all=True)


def _import(case, rows, name='schedule.xlsx'):
    path = case.root / name
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(HEADERS)
    for values in rows:
        sheet.append(values)
    book.save(path)
    os.environ['SCHEDULE_TEMPLATE_PATH'] = str(path)
    with path.open('rb') as handle:
        response = case.client.post('/admin/api/schedule/import',
                                    data={'file': (handle, name)},
                                    content_type='multipart/form-data')
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


# ---------------------------------------------------------------- composition

def test_composition_rule_table():
    assert compose_semester_identifier(1, '2026-2027') == '2026-2027-1'
    assert compose_semester_identifier('1', '2026-2027') == '2026-2027-1'
    assert compose_semester_identifier(2, '2025-2026') == '2025-2026-2'
    assert compose_semester_identifier(2.0, '2025-2026') == '2025-2026-2'
    assert compose_semester_identifier(1, '2026') == '2026-2027-1'
    assert compose_semester_identifier('2025-2026-1', '2025-2026') == '2025-2026-1'
    assert compose_semester_identifier('2', '') == '2'
    assert compose_semester_identifier(None, '2026-2027') == ''
    assert compose_semester_identifier('', '2026-2027') == ''
    assert compose_semester_identifier('第一学期', '2026-2027') == '第一学期'
    assert compose_semester_identifier(1, 'not-a-year') == '1'
    # A blank cell must never become the literal 'nan'.
    assert compose_semester_identifier(float('nan'), '2026-2027') == ''
    assert compose_semester_identifier(float('nan'), float('nan')) == ''


def test_clean_term_text_handles_blank_cells():
    assert clean_term_text(None) == ''
    assert clean_term_text(float('nan')) == ''
    assert clean_term_text('  nan ') == ''
    assert clean_term_text('  1-16周 ') == '1-16周'
    assert clean_term_text(2.0) == '2'
    assert clean_term_text(2) == '2'


# ------------------------------------------------------------------- adoption

def test_real_workbook_semantics_bind_courses_to_composed_semester(case):
    payload = _import(case, [_row('1', '2026-2027')], 'split.xlsx')
    assert payload['success'], payload
    assert payload['stats']['courses_added'] == 1

    batch = ScheduleImportBatch.query.one()
    assert batch.semester == '2026-2027-1'
    assert batch.academic_year == '2026-2027'

    course = Course.query.one()
    assert course.semester == '2026-2027-1'
    assert course.academic_year == '2026-2027'
    assert ListeningAssistantScheduleEntry.query.one().semester == '2026-2027-1'
    assert ScheduleSemesterSelection.query.one().semester == '2026-2027-1'

    SystemSetting.set('teaching_current_semester', '2026-2027-1')
    SystemSetting.set('teaching_first_week_monday', '2026-08-31')
    db.session.commit()

    availability = current_schedule_availability()
    assert availability['status'] == READY, availability
    assert availability['candidates_ready'], availability
    assert availability['registration_ready'], availability


def test_bare_term_configuration_still_resolves(case):
    """An installation configured before composition keeps working."""
    _import(case, [_row('2', '2025-2026')], 'legacy-install.xlsx')
    SystemSetting.set('teaching_current_semester', '2')
    SystemSetting.set('teaching_first_week_monday', '2026-03-02')
    db.session.commit()

    selection = resolve_current_schedule_snapshot(include_rows=False)
    assert selection.status == READY, selection
    assert selection.semester == '2025-2026-2'
    assert selection.batch is not None
    assert current_schedule_availability()['candidates_ready']


def test_bare_term_without_equivalent_authority_fails_closed(case):
    _import(case, [_row('1', '2026-2027')], 'only-autumn.xlsx')
    SystemSetting.set('teaching_current_semester', '2')
    SystemSetting.set('teaching_first_week_monday', '2026-08-31')
    db.session.commit()

    selection = resolve_current_schedule_snapshot(include_rows=False)
    assert selection.status == CURRENT_SEMESTER_CONFIGURED_BUT_NO_ACTIVE_SNAPSHOT, selection


def test_ambiguous_bare_term_fails_closed(case):
    """Two semesters ending in the same term must never be picked arbitrarily."""
    _import(case, [_row('2', '2024-2025')], 'second-a.xlsx')
    _import(case, [_row('2', '2025-2026', code='C002')], 'second-b.xlsx')
    SystemSetting.set('teaching_current_semester', '2')
    SystemSetting.set('teaching_first_week_monday', '2026-03-02')
    db.session.commit()

    selection = resolve_current_schedule_snapshot(include_rows=False)
    assert selection.status == AMBIGUOUS, selection


def test_unique_bare_term_picks_the_only_candidate(case):
    _import(case, [_row('1', '2025-2026')], 'first.xlsx')
    _import(case, [_row('2', '2025-2026', code='C002')], 'second.xlsx')
    SystemSetting.set('teaching_current_semester', '2')
    SystemSetting.set('teaching_first_week_monday', '2026-03-02')
    db.session.commit()

    selection = resolve_current_schedule_snapshot(include_rows=False)
    assert selection.status == READY, selection
    assert selection.semester == '2025-2026-2'


# -------------------------------------------------------------- multi-semester

def test_multi_semester_rows_keep_their_own_semester(case):
    payload = _import(case, [
        _row('1', '2025-2026', code='C001'),
        _row('2', '2025-2026', code='C002'),
    ], 'multi.xlsx')
    assert payload['success'], payload
    assert payload['stats']['courses_added'] == 2

    by_code = {course.course_code: (course.semester, course.academic_year)
               for course in Course.query.all()}
    assert by_code == {'C001': ('2025-2026-1', '2025-2026'),
                       'C002': ('2025-2026-2', '2025-2026')}, by_code
    assert Course.query.count() == 2

    batches = {batch.semester: batch.id for batch in ScheduleImportBatch.query.all()}
    assert set(batches) == {'2025-2026-1', '2025-2026-2'}
    memberships = {(m.batch_id, db.session.get(Course, m.course_id).course_code)
                   for m in ScheduleCourseMembership.query.all()}
    assert memberships == {(batches['2025-2026-1'], 'C001'),
                           (batches['2025-2026-2'], 'C002')}, memberships


def test_repeated_import_is_idempotent_and_keeps_one_selection(case):
    rows = [_row('1', '2026-2027', code='C001'), _row('1', '2026-2027', code='C002')]
    first = _import(case, rows, 'repeat.xlsx')
    assert first['stats']['courses_added'] == 2
    first_selection = ScheduleSemesterSelection.query.one().active_batch_id

    second = _import(case, rows, 'repeat.xlsx')
    assert second['stats']['courses_added'] == 0
    assert second['stats']['courses_skipped_duplicate'] == 2
    assert Course.query.count() == 2

    selections = ScheduleSemesterSelection.query.all()
    assert len(selections) == 1
    assert selections[0].semester == '2026-2027-1'
    assert selections[0].active_batch_id != first_selection
    assert ScheduleCourseMapping.query.filter_by(
        batch_id=selections[0].active_batch_id).count() == 1
    assert ScheduleCourseMembership.query.filter_by(
        batch_id=selections[0].active_batch_id).count() == 2


def test_legacy_bare_term_course_row_is_adopted_not_duplicated(case):
    """A pre-composition Course row is reused and migrated, never duplicated."""
    legacy = Course(course_code='C001', selection_code='C001-S1', start_week='1-16',
                    weekday=3, class_period='第3-4节', course_name='Math',
                    venue_start_week='1-16', venue_class_period='第3-4节', class_size=30,
                    class_composition='行政班1', credits=2.0, total_hours=32.0,
                    offering_college='理学院', major_composition='数学', enrollment_count=25,
                    weekly_hours='2', class_time='周三第3-4节', class_location='A101',
                    course_nature='必修', teacher_id='T001', venue_id='V001', semester='1',
                    academic_year='2026-2027')
    db.session.add(legacy)
    db.session.commit()
    legacy_id = legacy.id

    payload = _import(case, [_row('1', '2026-2027')], 'adopt.xlsx')
    assert payload['success'], payload
    assert payload['stats']['courses_added'] == 0
    assert payload['stats']['courses_skipped_duplicate'] == 1
    assert Course.query.count() == 1

    adopted = db.session.get(Course, legacy_id)
    assert adopted.semester == '2026-2027-1'
    assert adopted.academic_year == '2026-2027'
    assert current_schedule_availability()['registration_ready'] is False  # semester unset
    SystemSetting.set('teaching_current_semester', '2026-2027-1')
    SystemSetting.set('teaching_first_week_monday', '2026-08-31')
    db.session.commit()
    assert current_schedule_availability()['registration_ready'] is True


def test_legacy_row_without_academic_year_is_still_reused(case):
    """A pre-学年-column row must not be duplicated, or its bans stop applying.

    The reuse variant needs an academic year, but a row imported by an older
    importer from a workbook without a 学年 column holds NULL.  Relying only on
    the row's own value created a second Course and left the existing
    ListeningBan pointing at the original, so the banned course reappeared.
    """
    legacy = Course(course_code='C001', selection_code='C001-S1', start_week='1-16',
                    weekday=3, class_period='第3-4节', course_name='Math',
                    venue_start_week='1-16', venue_class_period='第3-4节', class_size=30,
                    class_composition='行政班1', credits=2.0, total_hours=32.0,
                    offering_college='理学院', major_composition='数学', enrollment_count=25,
                    weekly_hours='2', class_time='周三第3-4节', class_location='A101',
                    course_nature='必修', teacher_id='T001', venue_id='V001',
                    semester='1', academic_year=None)
    db.session.add(legacy)
    db.session.commit()
    legacy_id = legacy.id

    listener = User(number='L1', student_id='l1', department='测试部门', name='信息员',
                    gender='-', grade='-', college='测试学院', major='-', dormitory='-',
                    phone='-', qq='-', password_hash='unused', role='信息员',
                    group='测试组', is_active=True)
    db.session.add(listener)
    db.session.commit()
    db.session.add(ListeningBan(course_id=legacy_id, user_id=listener.id, created_by=None))
    db.session.commit()

    payload = _import(case, [_row('1', '2026-2027')], 'null-year.xlsx')
    assert payload['success'], payload
    assert payload['stats']['courses_added'] == 0
    assert payload['stats']['courses_skipped_duplicate'] == 1
    assert Course.query.count() == 1, 'a NULL-year legacy row must be reused'

    adopted = db.session.get(Course, legacy_id)
    assert adopted.semester == '2026-2027-1'
    assert adopted.academic_year == '2026-2027'
    assert ListeningBan.query.one().course_id == legacy_id

    SystemSetting.set('teaching_current_semester', '2026-2027-1')
    SystemSetting.set('teaching_first_week_monday', '2026-08-31')
    db.session.commit()
    with case.client.session_transaction() as session:
        session.update(user_id=listener.id, user_role=listener.role, user_name=listener.name)
    response = case.client.get('/user/api/available_courses')
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()['results'] == [], 'the listening ban must still suppress the course'
