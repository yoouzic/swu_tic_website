"""Route permission boundaries using isolated SQLite and private photo storage."""
import io
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image
from sqlalchemy import select

from app.app import create_app
from app.models import (
    Course, CourseRegistration, LectureForm, LectureFormDraft, LectureSiteCapture,
    Permission, RolePermission, ScheduleCourseMembership, SystemSetting, User, db,
)
from app.services.registration_course_identity import stamp_registration_course
from tests.review_request_utils import opened_listener_revision
from tests.schedule_fixture import seed_current_schedule


def _photo():
    stream = io.BytesIO()
    Image.new('RGB', (100, 80), 'white').save(stream, 'JPEG')
    stream.seek(0)
    return stream


def _form_payload(lecture_date):
    return {
        'lecture_date': lecture_date, 'class_period': '第3-4节',
        'lecture_location': '32-302', 'teacher_name': '测试教师',
        'teacher_college': '测试学院', 'course_title': '测试课程',
        'student_grade_class': '2025级1班', 'teaching_method': 'PPT演示法',
        'classroom_discipline': '好', 'classroom_atmosphere': '好',
        'courseware_quality': '好', 'overall_effect': '好', 'quality_case': '推荐',
        'course_feedback': '课堂案例清楚，教师解释了相关知识和使用条件，同学们能够跟随教学过程理解课程内容。',
        'student_signature1': '同学甲', 'contact_phone1': '13800000001',
        'student_signature2': '同学乙', 'contact_phone2': '13800000002',
    }


@pytest.fixture
def route_case(tmp_path, monkeypatch):
    # The app's scheduled storage cleanup targets BASE_DIR; disable it before
    # constructing this app so every test remains within disposable storage.
    monkeypatch.setattr('app.utils.storage_cleanup.run_scheduled_storage_cleanup',
                        lambda *args, **kwargs: {'skipped': True})
    storage = tmp_path / 'storage'
    captures = storage / 'captures'
    captures.mkdir(parents=True)
    app = create_app({
        'TESTING': True, 'WTF_CSRF_ENABLED': False, 'SECRET_KEY': 'route-test-secret',
        'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'routes.sqlite'}",
        'UPLOAD_FOLDER': str(storage / 'uploads'),
        'AUTOMATION_UPLOAD_DIR': str(storage / 'automation'),
        'LECTURE_CAPTURE_ENABLED': True, 'LECTURE_CAPTURE_FOLDER': str(captures),
    })
    with app.app_context():
        db.create_all()
        case = SimpleNamespace(app=app, client=app.test_client(), storage=storage, captures=captures)
        permission = Permission(name='填表')
        db.session.add(permission)
        db.session.flush()
        case.fill_permission_id = permission.id
        case.users = {}
        for index, (key, role, permitted) in enumerate([
            ('officer', '信息员', False), ('manager', '管理员', False),
            ('permitted_manager', '管理员', True), ('super', '超级管理员', True),
        ], 1):
            user = User(number=f'R{index}', student_id=f'route-{index}', name=key,
                        department='测试部门', gender='-', grade='2025', college='测试学院',
                        major='测试专业', dormitory='-', phone='-', qq='-',
                        password_hash='unused-test-password', role=role, group='测试组', is_active=True)
            db.session.add(user)
            db.session.flush()
            if permitted:
                db.session.add(RolePermission(role=f'特殊角色_{user.id}', permission_id=permission.id))
            case.users[key] = user
        case.course = Course(course_code='R100', selection_code='R200', course_name='测试课程')
        db.session.add(case.course)
        today = date.today()
        first_monday = today - timedelta(days=today.weekday())
        db.session.add(SystemSetting(key='teaching_first_week_monday', value=first_monday.isoformat()))
        db.session.commit()
        yield case
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def _login(case, key, session_role=None):
    user = case.users[key]
    with case.client.session_transaction() as session:
        session.clear()
        session['user_id'] = user.id
        session['user_role'] = session_role or user.role
        session['user_name'] = user.name
    return user


def _seed_history(case, user):
    instant = datetime.now(timezone(timedelta(hours=8)))
    payload = _form_payload(instant.date().isoformat())
    form = LectureForm(listener_name=user.name, listener_number=user.number, **payload)
    reservation = CourseRegistration(user_id=user.id, course_code='R100', selection_code='R200',
                                     listening_info='第2周星期一第3-4节', is_used=False)
    db.session.add_all([form, reservation])
    db.session.flush()
    photo_key = f'owner-{user.id}.jpg'
    (case.captures / photo_key).write_bytes(_photo().getvalue())
    capture = LectureSiteCapture(user_id=user.id, photo_key=photo_key, received_at=instant.isoformat(),
                                 building='32', room_number='302', period_number=3,
                                 location_json='{}', draft_json=json.dumps(payload), ocr_status='pending')
    db.session.add(capture)
    db.session.flush()
    draft_payload = dict(payload, site_capture_id=capture.id)
    db.session.add(LectureFormDraft(user_id=user.id, draft_key='submit_form',
                                   payload_json=json.dumps(draft_payload)))
    db.session.commit()
    return SimpleNamespace(form_id=form.id, reservation_id=reservation.id, capture_id=capture.id,
                           payload=payload, photo_key=photo_key)


def _snapshot(case):
    db.session.rollback()
    tables = {}
    for table in db.metadata.sorted_tables:
        # Snapshot every table, including evidence, scores and binding flags.
        statement = select(table).order_by(*table.primary_key.columns)
        tables[table.name] = tuple(tuple(row) for row in db.session.execute(statement))
    files = {str(path.relative_to(case.storage)): path.read_bytes()
             for path in case.storage.rglob('*') if path.is_file()}
    return tables, files


WRITE_CASES = [
    ('submit_get', 'GET', '/user/submit_form', 'html'),
    ('submit_post', 'POST', '/user/submit_form', 'html'),
    ('edit_get', 'GET', '/user/form/edit/{form_id}', 'html'),
    ('delete_form', 'POST', '/user/delete_form/{form_id}', 'api'),
    ('draft_put', 'PUT', '/user/api/lecture_form_draft', 'api'),
    ('draft_delete', 'DELETE', '/user/api/lecture_form_draft', 'api'),
    ('reservation_create', 'POST', '/user/api/create_reservation', 'api'),
    ('reservation_cancel', 'POST', '/user/api/cancel_reservation', 'api'),
    ('reservation_update', 'PUT', '/user/api/my_reservations/{reservation_id}', 'api'),
    ('reservation_delete', 'DELETE', '/user/api/my_reservations/{reservation_id}', 'api'),
    ('capture_upload', 'POST', '/user/api/site-capture', 'api'),
    ('capture_patch', 'PATCH', '/user/api/site-capture/{capture_id}', 'api'),
    ('capture_ocr', 'POST', '/user/api/site-capture/{capture_id}/ocr-region', 'api'),
    ('capture_location', 'PATCH', '/user/api/site-capture/{capture_id}/location', 'api'),
    ('capture_confirm', 'POST', '/user/api/site-capture/{capture_id}/confirm', 'api'),
    ('capture_resume', 'POST', '/user/api/site-capture/{capture_id}/resume', 'api'),
    ('registration_history', 'GET', '/user/api/course_registration_history?course_code=R100&selection_code=R200', 'api'),
]


@pytest.mark.parametrize('user_key', ['manager', 'super'])
@pytest.mark.parametrize('name,method,path,response_kind', WRITE_CASES, ids=[row[0] for row in WRITE_CASES])
def test_denied_routes_do_not_write_tables_or_photos(route_case, monkeypatch, user_key,
                                                    name, method, path, response_kind):
    case = route_case
    # Forging the role must not turn either persisted identity into an officer.
    user = _login(case, user_key, session_role='信息员')
    history = _seed_history(case, user)
    before = _snapshot(case)
    ocr = Mock(return_value={'room_number': '302', 'status': 'recognized', 'alternatives': ['302']})
    monkeypatch.setattr('app.services.lecture_site_capture.recognize_door', ocr)
    kwargs = {'json': {'course_code': 'R100', 'selection_code': 'R200',
                       'listening_info': '第2周星期一第3-4节', 'data': {'course_title': '改写'},
                       'building': '32', 'room_number': '303', 'candidate_id': 'fake',
                       'location': {'latitude': 29.82, 'longitude': 106.43, 'accuracy': 15},
                       'region': {'x': .1, 'y': .1, 'width': .5, 'height': .5}}}
    if method == 'GET' or name in {'draft_delete', 'delete_form', 'capture_resume', 'reservation_delete'}:
        kwargs = {}
    elif name == 'submit_post':
        kwargs = {'data': dict(history.payload, unique_id=str(history.form_id))}
    elif name == 'capture_upload':
        kwargs = {'data': {'photo': (_photo(), 'new.jpg'), 'new_record': '1'},
                  'content_type': 'multipart/form-data'}
    response = case.client.open(path.format(**vars(history)), method=method, **kwargs)
    if response_kind == 'html':
        assert response.status_code == 302
        assert response.headers['Location'].endswith('/')
        with case.client.session_transaction() as session:
            assert any(any('\u4e00' <= char <= '\u9fff' for char in message)
                       for _, message in session.get('_flashes', []))
    else:
        assert response.status_code == 403, response.get_data(as_text=True)
        envelope = response.get_json()
        assert envelope['success'] is False
        assert envelope['data'] is None
        assert any('\u4e00' <= char <= '\u9fff' for char in envelope['message'])
    assert _snapshot(case) == before
    ocr.assert_not_called()


@pytest.mark.parametrize('user_key', ['manager', 'super'])
def test_denied_accounts_keep_owned_history_reads_and_readonly_flags(route_case, monkeypatch, user_key):
    case = route_case
    user = _login(case, user_key)
    history = _seed_history(case, user)
    before = _snapshot(case)
    draft = case.client.get('/user/api/lecture_form_draft')
    assert draft.status_code == 200
    # With no current candidates the editable draft is manual-only, while the
    # owned photo and persisted historical binding remain available as history.
    assert 'site_capture_id' not in draft.get_json()['data']
    stored_draft = LectureFormDraft.query.filter_by(user_id=user.id).one()
    assert json.loads(stored_draft.payload_json)['site_capture_id'] == history.capture_id
    for suffix in ('', '/photo', '/map'):
        assert case.client.get(f'/user/api/site-capture/{history.capture_id}{suffix}').status_code == 200
    for method in ('GET', 'POST'):
        unavailable = case.client.open(f'/user/api/site-capture/{history.capture_id}/suggestions',
                                       method=method, json={'facts': {}})
        assert unavailable.status_code == 503
        assert unavailable.get_json()['success'] is False
        assert unavailable.get_json()['data']['source_unavailable'] is True
    assert case.client.get('/user/api/site-capture/settings').status_code == 200
    assert case.client.get('/user/api/site-capture/records').get_json()['data'][0]['id'] == history.capture_id
    reservations = case.client.get('/user/api/my_reservations').get_json()['data']
    assert len(reservations) == 1
    assert reservations[0]['can_edit'] is False
    assert reservations[0]['can_delete'] is False
    captured_context = {}
    def inspect_context(template, **context):
        captured_context.update(context)
        return 'rendered'
    monkeypatch.setattr('app.blueprints.user.reservations.render_template', inspect_context)
    assert case.client.get('/user/listening_registration?tab=registration').status_code == 200
    assert captured_context['active_tab'] == 'records'
    assert captured_context['my_reservations'][0]['can_edit'] is False
    assert captured_context['my_reservations'][0]['can_delete'] is False
    assert _snapshot(case) == before
    # Ownership remains enforced even when only read access is retained.
    _login(case, 'officer')
    assert case.client.get(f'/user/api/site-capture/{history.capture_id}').status_code == 404
    assert case.client.get(f'/user/api/site-capture/{history.capture_id}/photo').status_code == 404
    assert case.client.get('/user/api/lecture_form_draft').get_json()['exists'] is False


@pytest.mark.parametrize('user_key', ['officer', 'permitted_manager'])
def test_capable_accounts_can_keep_mutating_their_records(route_case, monkeypatch, user_key):
    case = route_case
    batch = seed_current_schedule('2026-2027-1')
    case.course.semester = '2026-2027-1'
    db.session.add(ScheduleCourseMembership(batch_id=batch.id, course_id=case.course.id))
    db.session.commit()
    user = _login(case, user_key, session_role='超级管理员')
    history = _seed_history(case, user)
    stamp_registration_course(db.session.get(CourseRegistration, history.reservation_id), case.course)
    db.session.commit()
    assert case.client.get('/user/submit_form').status_code == 200
    assert case.client.get(f'/user/form/edit/{history.form_id}').status_code == 200
    assert case.client.get('/user/api/course_registration_history?course_code=R100&selection_code=R200').status_code == 200
    saved = case.client.put('/user/api/lecture_form_draft', json={'data': {'course_feedback': '继续填写'}})
    assert saved.status_code == 200
    assert case.client.get('/user/api/lecture_form_draft').get_json()['data']['course_feedback'] == '继续填写'
    assert case.client.patch(f'/user/api/site-capture/{history.capture_id}',
                             json={'building': '32', 'room_number': '302'}).status_code == 200
    assert case.client.post(f'/user/api/site-capture/{history.capture_id}/resume').status_code == 200
    monkeypatch.setattr('app.blueprints.user.reservations.validate_listening_time', lambda value: (True, None))
    assert case.client.put(f'/user/api/my_reservations/{history.reservation_id}',
                          json={'listening_info': '第2周星期一第5-6节'}).status_code == 200
    assert db.session.get(CourseRegistration, history.reservation_id).listening_info == '第2周星期一第5-6节'
    assert case.client.delete(f'/user/api/my_reservations/{history.reservation_id}').status_code == 200
    payload = dict(history.payload, unique_id=str(history.form_id))
    payload.update(opened_listener_revision(case.client, history.form_id))
    submitted = case.client.post('/user/submit_form', data=payload)
    assert submitted.status_code == 302
    assert f'/user/success/{history.form_id}' in submitted.headers['Location']
    assert case.client.post(f'/user/delete_form/{history.form_id}').status_code == 200
    assert db.session.get(LectureForm, history.form_id) is None
    assert case.client.delete('/user/api/lecture_form_draft').status_code == 200


def test_manager_permission_changes_take_effect_on_next_write(route_case):
    case = route_case
    user = _login(case, 'permitted_manager')
    assert case.client.put('/user/api/lecture_form_draft', json={'data': {'course_title': '授权时保存'}}).status_code == 200
    RolePermission.query.filter_by(role=f'特殊角色_{user.id}').delete()
    db.session.commit()
    before = _snapshot(case)
    denied = case.client.put('/user/api/lecture_form_draft', json={'data': {'course_title': '撤销后写入'}})
    assert denied.status_code == 403
    assert _snapshot(case) == before
    assert case.client.get('/user/api/lecture_form_draft').get_json()['data']['course_title'] == '授权时保存'
