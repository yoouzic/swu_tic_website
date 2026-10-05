"""Isolated route regressions for the 2026-10-02 listener business audit."""
import io
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import pytest
from werkzeug.security import generate_password_hash

from app.app import app
from app.models import (
    Course, CourseRegistration, LectureForm, LectureFormDraft, LectureSiteCapture,
    Permission, RolePermission, ScheduleCourseMapping, ScheduleCourseMembership,
    ScheduleImportBatch, ScheduleSemesterSelection, SystemSetting, User, db,
)
from app.services.listening_assistant_evidence import AssistantSelectionError
from app.services.teaching_calendar import parse_lecture_date
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database
from tests.schedule_fixture import seed_current_schedule


def valid_payload():
    return {
        'lecture_date': '2026-10-02', 'lecture_date_display': '2026/10/02星期五',
        'start_period': '3', 'end_period': '4', 'class_period': '第3-4节',
        'lecture_location': '32-302', 'teacher_name': '测试教师', 'teacher_college': '测试学院',
        'course_title': '测试课程', 'student_grade_class': '2025级1班',
        'course_changes': '无', 'abnormal_situation': '无',
        'teaching_method': 'PPT演示法、讲授法、互动法',
        'classroom_discipline': '非常好', 'classroom_atmosphere': '非常好',
        'courseware_quality': '非常好', 'overall_effect': '非常好', 'quality_case': '推荐',
        'course_feedback': '教师结合实际案例讲解课程重点，课堂讨论积极，学生能够独立完成练习，知识点安排合理，问题解答细致，教学节奏清晰，课堂秩序良好。',
        'suggestions': '无', 'student_signature1': '测试同学甲', 'contact_phone1': '13800000001',
        'student_signature2': '测试同学乙', 'contact_phone2': '13800000002',
    }


def login(client, user):
    with client.session_transaction() as session:
        session.update(user_id=user.id, user_role=user.role, user_name=user.name)


def make_user(number, role='信息员'):
    user = User(number=number, student_id=number, department='测试部门', name=number,
                gender='-', grade='-', college='测试学院', major='-', dormitory='-',
                phone='-', qq='-', password_hash=generate_password_hash('isolated-only'),
                role=role, group='测试组', is_active=True)
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def listener_case(tmp_path):
    configure_sqlite_database(app, db, tmp_path / 'listener.sqlite')
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, LECTURE_CAPTURE_ENABLED=False,
                      LECTURE_CAPTURE_FOLDER=str(tmp_path / 'photos'))
    with app.app_context():
        assert str(tmp_path.resolve()) in str(db.engine.url.database)
        db.create_all()
        user = make_user('LISTENER-BUSINESS')
        db.session.add(Course(course_code='TEST-C', selection_code='TEST-S', course_name='测试课程'))
        db.session.commit()
        client = app.test_client()
        login(client, user)
        yield SimpleNamespace(client=client, user=user, app=app, folder=tmp_path / 'photos')
        app.config['LECTURE_CAPTURE_ENABLED'] = False
        cleanup_sqlite_database(db, drop_all=True)


def registration(case, week=6):
    row = CourseRegistration(course_code='TEST-C', selection_code='TEST-S', user_id=case.user.id,
                             listening_info=f'第{week}周星期一第3-4节', is_used=False)
    db.session.add(row)
    db.session.commit()
    return row


def create_form(case, **overrides):
    payload = valid_payload()
    payload.update(overrides)
    parsed = parse_lecture_date(payload['lecture_date_display'])
    if parsed:
        payload['lecture_date'] = parsed.isoformat()
    response = case.client.post('/user/submit_form', data=payload)
    assert response.status_code == 302
    return LectureForm.query.order_by(LectureForm.id.desc()).first()


def initial_data(response):
    match = re.search(r'var initialFormData = (.*?);', response.get_data(as_text=True))
    assert match, 'the response must retain submitted initial form data'
    return json.loads(match.group(1))


def revision_fields(form):
    return {'expected_form_id': str(form.id),
            'expected_form_updated_at': form.updated_at.isoformat() if form.updated_at else ''}


@pytest.mark.parametrize('status', ['待审核', '已驳回'])
def test_edit_page_restores_registration_hidden_value(listener_case, status):
    case = listener_case
    row = registration(case)
    form = create_form(case, registration_id=str(row.id))
    form.status = status
    db.session.commit()
    response = case.client.get(f'/user/form/edit/{form.id}')
    assert initial_data(response)['registration_id'] == row.id
    hidden = re.search(r'<input[^>]*id="registration_id"[^>]*>', response.get_data(as_text=True)).group(0)
    assert f'value="{row.id}"' in hidden


@pytest.mark.parametrize('status', ['待审核', '已驳回'])
@pytest.mark.parametrize('posted_registration', [None, ''])
def test_edit_or_refill_inherits_binding_when_hidden_field_is_empty(listener_case, status, posted_registration):
    case = listener_case
    row = registration(case)
    form = create_form(case, registration_id=str(row.id))
    logical_id = form.unique_id
    form.status = status
    db.session.commit()
    payload = valid_payload()
    payload.update(**revision_fields(form), unique_id=str(logical_id), course_feedback='补充具体教学案例。' + payload['course_feedback'])
    if posted_registration is not None:
        payload['registration_id'] = posted_registration
    assert case.client.post('/user/submit_form', data=payload).status_code == 302
    latest = LectureForm.query.filter_by(unique_id=logical_id).order_by(LectureForm.id.desc()).first()
    assert latest.registration_id == row.id
    assert db.session.get(CourseRegistration, row.id).is_used is True
    assert all(item['id'] != row.id for item in case.client.get('/user/api/unused_reservations').get_json()['data'])


def test_explicit_registration_change_keeps_rebind_semantics(listener_case):
    case = listener_case
    first, second = registration(case), registration(case, 7)
    form = create_form(case, registration_id=str(first.id))
    payload = valid_payload()
    payload.update(**revision_fields(form), unique_id=str(form.unique_id), registration_id=str(second.id))
    assert case.client.post('/user/submit_form', data=payload).status_code == 302
    assert db.session.get(LectureForm, form.id).registration_id == second.id
    assert db.session.get(CourseRegistration, first.id).is_used is False
    assert db.session.get(CourseRegistration, second.id).is_used is True


@pytest.mark.parametrize('query, expected_offsets', [
    ({'date_from': '2026-10-02', 'date_to': '2026-10-02'}, {0}),
    ({'date_from': '2026-10-02'}, {0, 1}),
    ({'date_to': '2026-10-02'}, {-1, 0}),
])
def test_record_date_filters_parse_display_and_legacy_dates(listener_case, query, expected_offsets):
    case = listener_case
    ids = {}
    for offset, date_text in [(-1, '2026/10/01星期四'), (0, '2026/10/02星期五'), (1, '2026年10月3日')]:
        form = create_form(case, lecture_date_display=date_text, course_title=f'日期测试{offset}')
        ids[offset] = form.id
    invalid = create_form(case, course_title='未识别日期')
    invalid.lecture_date = '不可解析的历史日期'  # Legacy data predates new submission validation.
    db.session.commit()
    html = case.client.get('/user/listening_registration', query_string={'tab': 'records', **query}).get_data(as_text=True)
    visible_ids = {int(value) for value in re.findall(r'data-form-row="(\d+)"', html)}
    assert visible_ids == {ids[offset] for offset in expected_offsets}
    assert invalid.id not in visible_ids


def test_records_latest_version_is_physical_id_even_when_old_timestamp_is_newer(listener_case):
    case = listener_case
    old = create_form(case)
    logical_id = old.unique_id
    old.status = '已驳回'
    db.session.commit()
    payload = valid_payload()
    payload.update(**revision_fields(old), unique_id=str(logical_id), course_feedback='补充课堂案例。' + payload['course_feedback'])
    assert case.client.post('/user/submit_form', data=payload).status_code == 302
    latest = LectureForm.query.order_by(LectureForm.id.desc()).first()
    old.updated_at = datetime.now() + timedelta(days=2)
    latest.updated_at = datetime.now() - timedelta(days=2)
    db.session.commit()
    html = case.client.get('/user/listening_registration?tab=records').get_data(as_text=True)
    assert f'data-form-row="{latest.id}"' in html
    assert f'data-form-row="{old.id}"' not in html


def test_records_search_does_not_promote_a_matching_old_version_to_current(listener_case):
    case = listener_case
    old = create_form(case, course_title='修改前旧课程名称')
    old.status = '已驳回'
    db.session.commit()
    payload = valid_payload()
    payload.update(**revision_fields(old), unique_id=str(old.unique_id), course_title='修改后当前课程名称')
    assert case.client.post('/user/submit_form', data=payload).status_code == 302
    latest = LectureForm.query.order_by(LectureForm.id.desc()).first()
    old_html = case.client.get('/user/listening_registration', query_string={'tab': 'records', 'search': '修改前旧课程名称'}).get_data(as_text=True)
    assert 'data-form-row=' not in old_html
    current_html = case.client.get('/user/listening_registration', query_string={'tab': 'records', 'search': '修改后当前课程名称'}).get_data(as_text=True)
    assert f'data-form-row="{latest.id}"' in current_html
    assert '共2个版本' in current_html


def test_record_search_keeps_case_insensitive_course_matching(listener_case):
    case = listener_case
    form = create_form(case, course_title='Database Systems')
    html = case.client.get('/user/listening_registration', query_string={'tab': 'records', 'search': 'database'}).get_data(as_text=True)
    assert f'data-form-row="{form.id}"' in html


def select_current_courses(case):
    semester = '2026-2027-1'
    old = Course.query.first()
    old.semester, old.class_location = semester, '32-302'
    current = Course(course_code='CURRENT-C', selection_code='CURRENT-S', course_name='当前课程',
                     semester=semester, class_location='32-303')
    db.session.add(current)
    batch = ScheduleImportBatch(semester=semester, source_filename='isolated.xlsx', source_sha256='a' * 64)
    db.session.add(batch)
    db.session.flush()
    db.session.add_all([
        ScheduleSemesterSelection(semester=semester, active_batch_id=batch.id),
        ScheduleCourseMapping(batch_id=batch.id),
        ScheduleCourseMembership(batch_id=batch.id, course_id=current.id),
    ])
    SystemSetting.set('teaching_current_semester', semester)
    SystemSetting.set('teaching_first_week_monday', '2026-09-07')
    db.session.commit()
    return old, current


def test_available_courses_only_current_authority_rows_keep_old_registration(listener_case):
    case = listener_case
    previous = registration(case)
    old, current = select_current_courses(case)
    response = case.client.get('/user/api/available_courses')
    assert response.status_code == 200
    assert [item['id'] for item in response.get_json()['results']] == [current.id]
    historical = case.client.get('/user/api/my_reservations').get_json()['data']
    assert historical[0]['id'] == previous.id
    assert historical[0]['courses'][0]['class_location'] == old.class_location


def test_create_reservation_refuses_retired_course_and_accepts_current(listener_case):
    case = listener_case
    old, current = select_current_courses(case)
    with mock.patch('app.blueprints.user.reservations.validate_listening_time', return_value=(True, None)):
        retired = case.client.post('/user/api/create_reservation', json={
            'course_code': old.course_code, 'selection_code': old.selection_code, 'listening_info': '第6周星期一第3-4节'})
        assert retired.status_code == 400
        accepted = case.client.post('/user/api/create_reservation', json={
            'course_code': current.course_code, 'selection_code': current.selection_code, 'listening_info': '第6周星期一第3-4节'})
    assert accepted.status_code == 200
    assert CourseRegistration.query.count() == 1


def test_formally_configured_missing_schedule_exposes_no_selectable_courses(listener_case):
    case = listener_case
    SystemSetting.set('teaching_current_semester', '2026-2027-1')
    response = case.client.get('/user/api/available_courses')
    assert response.status_code == 503
    assert response.get_json()['code'] == 'current_schedule_unavailable'


def configure_week_limit():
    for key, value in {
        'teaching_first_week_monday': '2026-09-07', 'teaching_week_start_day': '0',
        'teaching_total_weeks': '20', 'course_registration_weekly_limit_enabled': 'true',
        'course_registration_weekly_limit_count': '1',
    }.items():
        SystemSetting.set(key, value)


def test_reservation_update_obeys_same_weekly_limit_as_creation(listener_case):
    case = listener_case
    select_current_courses(case)
    # Include the original course in the authoritative mapping for this limit test.
    old = Course.query.filter_by(course_code='TEST-C').one()
    batch = ScheduleImportBatch.query.one()
    db.session.add(ScheduleCourseMembership(batch_id=batch.id, course_id=old.id))
    db.session.commit()
    configure_week_limit()
    registration(case, 6)
    other = registration(case, 7)
    payload = {'course_code': 'TEST-C', 'selection_code': 'TEST-S', 'listening_info': '第6周星期一第3-4节'}
    # Time validation is unrelated to this counting rule and must not become date-sensitive.
    with mock.patch('app.blueprints.user.reservations.validate_listening_time', return_value=(True, None)):
        created = case.client.post('/user/api/create_reservation', json=payload)
        edited = case.client.put(f'/user/api/my_reservations/{other.id}', json={'listening_info': payload['listening_info']})
    assert created.status_code == 400
    assert edited.status_code == 400
    assert edited.get_json()['message'] == created.get_json()['message']
    assert db.session.get(CourseRegistration, other.id).listening_info == '第7周星期一第3-4节'


def test_reservation_weekly_limit_excludes_the_row_being_edited(listener_case):
    case = listener_case
    old, _ = select_current_courses(case)
    batch = ScheduleImportBatch.query.one()
    db.session.add(ScheduleCourseMembership(batch_id=batch.id, course_id=old.id))
    db.session.commit()
    configure_week_limit()
    row = registration(case, 6)
    with mock.patch('app.blueprints.user.reservations.validate_listening_time', return_value=(True, None)):
        response = case.client.put(f'/user/api/my_reservations/{row.id}', json={'listening_info': '第6周星期二第5-6节'})
    assert response.status_code == 200


def bind_capture(case, form):
    case.folder.mkdir(parents=True, exist_ok=True)
    photo_key = f'{uuid.uuid4().hex}.jpg'
    photo_path = case.folder / photo_key
    photo_path.write_bytes(b'private-preserved-photo-fixture')
    capture = LectureSiteCapture(user_id=case.user.id, photo_key=photo_key,
        received_at='2026-10-02T10:00:00+08:00', building='32', room_number='302',
        form_id=form.id, draft_json=json.dumps({'course_feedback': '较早草稿', 'unique_id': form.unique_id,
            'registration_id': form.registration_id}, ensure_ascii=False))
    db.session.add(capture)
    db.session.commit()
    return capture, photo_path


def enable_capture_with_current_schedule(case):
    seed_current_schedule('2026-2027-1')
    case.app.config['LECTURE_CAPTURE_ENABLED'] = True


def test_delete_pending_form_releases_photo_with_latest_review_for_resume(listener_case):
    case = listener_case
    row = registration(case)
    form = create_form(case, registration_id=str(row.id))
    capture, photo_path = bind_capture(case, form)
    form_id, capture_id, feedback = form.id, capture.id, form.course_feedback
    assert case.client.post(f'/user/delete_form/{form_id}').status_code == 200
    db.session.expire_all()
    assert db.session.get(LectureForm, form_id) is None
    capture = db.session.get(LectureSiteCapture, capture_id)
    assert capture.form_id is None
    assert photo_path.read_bytes() == b'private-preserved-photo-fixture'
    enable_capture_with_current_schedule(case)
    assert capture_id in [item['id'] for item in case.client.get('/user/api/site-capture/records').get_json()['data']]
    assert case.client.post(f'/user/api/site-capture/{capture_id}/resume').status_code == 200
    restored = case.client.get('/user/api/lecture_form_draft').get_json()['data']
    assert restored['course_feedback'] == feedback
    assert restored['site_capture_id'] == capture_id
    assert not restored.get('unique_id')
    assert not restored.get('registration_id')


def test_delete_form_transaction_failure_restores_form_capture_and_active_draft(listener_case):
    case = listener_case
    form = create_form(case)
    capture, photo_path = bind_capture(case, form)
    form_id, capture_id, old_capture_draft = form.id, capture.id, capture.draft_json
    draft = LectureFormDraft(user_id=case.user.id, draft_key='submit_form', payload_json='{"course_feedback":"另一次未提交的评价"}')
    db.session.add(draft)
    db.session.commit()
    response = None
    with mock.patch.object(db.session, 'commit', side_effect=RuntimeError('isolated commit failure')):
        try:
            response = case.client.post(f'/user/delete_form/{form_id}')
        except RuntimeError:
            pass
    assert response is not None and response.status_code == 500
    db.session.expire_all()
    assert db.session.get(LectureForm, form_id) is not None
    capture = db.session.get(LectureSiteCapture, capture_id)
    assert capture.form_id == form_id
    assert capture.draft_json == old_capture_draft
    assert photo_path.exists()
    assert case.client.get('/user/api/lecture_form_draft').get_json()['data']['course_feedback'] == '另一次未提交的评价'


def test_deleted_photo_draft_restores_latest_period_instead_of_old_derived_fields(listener_case):
    case = listener_case
    form = create_form(case, class_period='第5-6节', start_period='5', end_period='6')
    capture, _ = bind_capture(case, form)
    capture.draft_json = json.dumps({'start_period': '3', 'end_period': '4',
        'class_period': '第3-4节', 'lecture_date_display': '2026/10/01星期四'})
    db.session.commit()
    assert case.client.post(f'/user/delete_form/{form.id}').status_code == 200
    enable_capture_with_current_schedule(case)
    assert case.client.post(f'/user/api/site-capture/{capture.id}/resume').status_code == 200
    restored = case.client.get('/user/api/lecture_form_draft').get_json()['data']
    assert (restored['start_period'], restored['end_period']) == ('5', '6')
    assert restored['class_period'] == '第5-6节'
    assert restored['lecture_date'] == '2026-10-02'
    assert restored['lecture_date_display'] == '2026/10/02星期五'


def test_legacy_null_logical_id_stays_in_one_history_after_refill(listener_case):
    case = listener_case
    old = create_form(case)
    old.unique_id, old.status = None, '已驳回'
    db.session.commit()
    payload = valid_payload()
    payload.update(**revision_fields(old), unique_id=str(old.id), course_feedback='补充的课堂案例。' + payload['course_feedback'])
    assert case.client.post('/user/submit_form', data=payload).status_code == 302
    html = case.client.get('/user/listening_registration?tab=records').get_data(as_text=True)
    assert len(re.findall(r'data-form-row="(\d+)"', html)) == 1


@pytest.mark.parametrize('boundary', ['selection', 'payload', 'registration'])
def test_edit_validation_failure_preserves_posted_values_and_mode(listener_case, boundary):
    case = listener_case
    row = registration(case)
    form = create_form(case, registration_id=str(row.id))
    case.app.config['LECTURE_CAPTURE_ENABLED'] = True
    draft = LectureFormDraft(user_id=case.user.id, draft_key='submit_form', payload_json='{"course_feedback":"另一次听课草稿"}')
    db.session.add(draft)
    db.session.commit()
    payload = valid_payload()
    payload.update(**revision_fields(form), unique_id=str(form.unique_id), registration_id=str(row.id),
                   course_feedback='刚输入的修改内容，尚未保存。' * 5, student_signature2='修改后的见证同学',
                   contact_phone2='13900000002', suggestions='刚输入的建议')
    if boundary == 'selection':
        payload['assistant_payload'] = '{"stage":"confirmed"}'
        patched = mock.patch('app.blueprints.user.forms.revalidate_selection', side_effect=AssistantSelectionError('expired fixture'))
    elif boundary == 'payload':
        payload['assistant_payload'] = 'not-json'
        patched = mock.patch('app.blueprints.user.forms.revalidate_selection')
    else:
        payload['registration_id'] = '999999'
        patched = mock.patch('app.blueprints.user.forms.revalidate_selection')
    with patched:
        response = case.client.post('/user/submit_form', data=payload)
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert 'const lectureFormIsEditMode = true;' in html
    data = initial_data(response)
    for field in ('unique_id', 'registration_id', 'course_feedback', 'student_signature2', 'contact_phone2', 'suggestions'):
        assert str(data[field]) == payload[field]
    assert 'data-site-capture>' not in html
    assert db.session.get(LectureForm, form.id).course_feedback != payload['course_feedback']
    assert case.client.get('/user/api/lecture_form_draft').get_json()['data']['course_feedback'] == '另一次听课草稿'


@pytest.mark.parametrize('status,label', [('待审核', '修改'), ('已驳回', '重填')])
def test_latest_single_version_has_direct_edit_or_refill_entry(listener_case, status, label):
    case = listener_case
    form = create_form(case)
    form.status = status
    db.session.commit()
    html = case.client.get('/user/listening_registration?tab=records').get_data(as_text=True)
    row = re.search(rf'<tr data-form-row="{form.id}".*?</tr>', html, re.S).group(0)
    assert f'href="/user/form/edit/{form.id}"' in row
    assert re.search(rf'href="/user/form/edit/{form.id}"[^>]*>{label}</a>', row)


def test_normal_department_rejection_refill_retains_version_history(listener_case):
    case = listener_case
    row = registration(case)
    form = create_form(case, registration_id=str(row.id))
    logical_id = form.unique_id
    reviewer = make_user('LISTENER-REVIEWER', '管理员')
    permission = Permission(name='审表_部门')
    db.session.add(permission)
    db.session.flush()
    db.session.add(RolePermission(role='管理员', permission_id=permission.id))
    db.session.commit()
    login(case.client, reviewer)
    assert case.client.post(f'/admin/api/review/reject/{form.id}', json={
        **revision_fields(form), 'reason': '请补充课堂案例'}).status_code == 200
    login(case.client, case.user)
    rejected = LectureForm.query.filter_by(unique_id=logical_id).order_by(LectureForm.id.desc()).first()
    assert rejected.status == '已驳回'
    assert LectureForm.query.filter_by(unique_id=logical_id).count() == 2
    assert case.client.get(f'/user/form/edit/{rejected.id}').status_code == 200
    payload = valid_payload()
    payload.update(**revision_fields(rejected), unique_id=str(logical_id), course_feedback='补充具体课堂案例。' + payload['course_feedback'])
    assert case.client.post('/user/submit_form', data=payload).status_code == 302
    latest = LectureForm.query.filter_by(unique_id=logical_id).order_by(LectureForm.id.desc()).first()
    assert latest.status == '待审核'
    assert latest.registration_id == row.id
    assert LectureForm.query.filter_by(unique_id=logical_id).count() == 3


def test_capture_draft_manual_submit_still_works(listener_case):
    from PIL import Image
    case = listener_case
    enable_capture_with_current_schedule(case)
    image = io.BytesIO()
    Image.new('RGB', (160, 120), 'white').save(image, 'JPEG')
    image.seek(0)
    with mock.patch('app.services.lecture_site_capture.now_at_site', return_value=datetime(2026, 10, 2, 10, 0, tzinfo=timezone(timedelta(hours=8)))), \
         mock.patch('app.services.lecture_site_capture.recognize_door', return_value={'room_number': '302', 'status': 'recognized', 'alternatives': ['302']}):
        uploaded = case.client.post('/user/api/site-capture', data={'photo': (image, 'door.jpg')}, content_type='multipart/form-data')
    assert uploaded.status_code == 201
    capture_id = uploaded.get_json()['data']['id']
    assert case.client.patch(f'/user/api/site-capture/{capture_id}', json={'building': '32', 'room_number': '302'}).status_code == 200
    payload = valid_payload()
    saved = case.client.put('/user/api/lecture_form_draft', json={'data': payload, 'expected_site_capture_id': capture_id})
    assert saved.status_code == 200
    assert case.client.post(f'/user/api/site-capture/{capture_id}/resume').status_code == 200
    assert case.client.post('/user/submit_form', data=payload).status_code == 302
    capture = db.session.get(LectureSiteCapture, capture_id)
    assert db.session.get(LectureForm, capture.form_id) is not None
    assert not case.client.get('/user/api/lecture_form_draft').get_json()['exists']
