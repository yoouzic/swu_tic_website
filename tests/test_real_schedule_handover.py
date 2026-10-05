"""Real-download blank-cell patterns must preserve Course IDs during handover.

Fixtures contain synthetic people and reproduce the six missing-field patterns
found in the downloaded workbook, without requiring private production data.
"""
from types import SimpleNamespace

import pytest

from app.app import app
from app.models import Course, ListeningBan, SystemSetting, Teacher, User, Venue, db
from app.services.current_courses import current_course_status
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database
from tests.test_schedule_snapshots import DEFAULT_ROW, HEADERS, write_workbook


BLANK_PATTERNS = [
    ('venue_id',),
    ('major_composition',),
    ('class_location', 'venue_id'),
    ('major_composition', 'class_location', 'venue_id'),
    ('major_composition', 'course_nature'),
    ('major_composition', 'venue_id'),
]
BLANK_COLUMNS = {
    'venue_id': '场地编号',
    'major_composition': '专业组成',
    'class_location': '上课地点',
    'course_nature': '课程性质',
}


def _login(client, user):
    with client.session_transaction() as session:
        session.update(user_id=user.id, user_role=user.role, user_name=user.name)


@pytest.fixture
def handover_case(tmp_path, monkeypatch):
    configure_sqlite_database(app, db, tmp_path / 'handover.sqlite')
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        assert str(tmp_path.resolve()) in str(db.engine.url.database)
        db.create_all()
        users = []
        for number, role in [('HANDOVER-ADMIN', '超级管理员'), ('HANDOVER-LISTENER', '信息员')]:
            user = User(
                number=number, student_id=number, department='测试部门', name=number,
                gender='-', grade='-', college='测试学院', major='-', dormitory='-',
                phone='-', qq='-', password_hash='isolated-unused-password',
                role=role, group='测试组', is_active=True,
            )
            db.session.add(user)
            users.append(user)
        db.session.add_all([
            Teacher(teacher_id='T001', name='测试教师'),
            Venue(venue_id='V001', name='32-302'),
        ])
        SystemSetting.set('teaching_current_semester', '2')
        db.session.commit()
        client = app.test_client()
        _login(client, users[0])
        case = SimpleNamespace(client=client, admin=users[0], listener=users[1], root=tmp_path)
        yield case
        cleanup_sqlite_database(db, drop_all=True)


def _legacy_course(**overrides):
    """A pre-snapshot Course record as persisted by the legacy importer."""
    fields = dict(
        course_code='C001', selection_code='S001', start_week='1-16', weekday=3,
        class_period='第3-4节', course_name='数据结构', venue_start_week='1-16',
        venue_class_period='第3-4节', class_size=30,
        class_composition='2023级计算机1班', credits=2.0, total_hours=32.0,
        offering_college='计算机学院', major_composition='计算机', enrollment_count=25,
        weekly_hours='2', class_time='周三第3-4节', class_location='32-302',
        course_nature='必修', teacher_id='T001', venue_id='V001',
        semester='2', academic_year='2025-2026',
    )
    fields.update(overrides)
    course = Course(**fields)
    db.session.add(course)
    db.session.commit()
    return course


def _workbook(case, monkeypatch, **overrides):
    row = dict(DEFAULT_ROW, 学期=2, 学年='2025-2026')
    row.update(overrides)
    path = write_workbook(case.root / 'download-pattern.xlsx', [[row[column] for column in HEADERS]])
    monkeypatch.setenv('SCHEDULE_TEMPLATE_PATH', str(path))
    return path


def _import(case, path):
    _login(case.client, case.admin)
    with path.open('rb') as handle:
        response = case.client.post('/admin/api/schedule/import', data={'file': (handle, path.name)})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'], payload
    return payload['stats']


@pytest.mark.parametrize('missing_fields', BLANK_PATTERNS, ids=lambda fields: '+'.join(fields))
def test_downloaded_blank_cells_reuse_legacy_course_and_ban(handover_case, monkeypatch, missing_fields):
    case = handover_case
    legacy = _legacy_course(**{field: None for field in missing_fields})
    legacy_id = legacy.id
    db.session.add(ListeningBan(course_id=legacy_id, user_id=case.listener.id, created_by=case.admin.id))
    db.session.commit()
    path = _workbook(case, monkeypatch, **{BLANK_COLUMNS[field]: None for field in missing_fields})

    for _ in range(2):
        stats = _import(case, path)
        assert Course.query.count() == 1, 'blank Excel cells must not create a duplicate Course'
        assert stats['courses_added'] == 0
        assert stats['courses_skipped_duplicate'] == 1
        current = current_course_status()
        assert current['mapping_status'] == 'COMPLETE'
        assert current['course_ids'] == [legacy_id]
        assert ListeningBan.query.one().course_id == legacy_id
        _login(case.client, case.listener)
        response = case.client.get('/user/api/available_courses')
        assert response.status_code == 200
        assert response.get_json()['results'] == [], 'handover must preserve the existing listening ban'


@pytest.mark.parametrize('field, column', [('course_code', '课程号'), ('selection_code', '选课课号')])
def test_handover_does_not_merge_identifiers_with_leading_zeroes(handover_case, monkeypatch, field, column):
    case = handover_case
    legacy_id = _legacy_course(**{field: '0012'}).id
    path = _workbook(case, monkeypatch, **{column: '12'})

    first = _import(case, path)
    assert first['courses_added'] == 1
    assert Course.query.count() == 2
    current_id = current_course_status()['course_ids'][0]
    assert current_id != legacy_id
    assert getattr(db.session.get(Course, legacy_id), field) == '0012'
    assert getattr(db.session.get(Course, current_id), field) == '12'
    assert _import(case, path)['courses_added'] == 0
    assert Course.query.count() == 2
