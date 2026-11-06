"""A browser must write against the draft revision it actually observed."""
import json

import pytest

from app.app import create_app
from app.models import LectureForm, LectureFormDraft, LectureSiteCapture, SystemSetting, User, db


@pytest.fixture
def revision_case():
    app = create_app({'TESTING': True, 'WTF_CSRF_ENABLED': False,
                      'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:'})
    with app.app_context():
        db.create_all()
        user = User(number='REVISION-1', student_id='revision-user', name='Test',
                    role='信息员', department='测试部门', group='测试组', gender='',
                    grade='', college='', major='', dormitory='', phone='', qq='',
                    password_hash='unused', is_active=True)
        db.session.add(user)
        db.session.flush()
        draft = LectureFormDraft(user_id=user.id, draft_key='submit_form', entry_mode='manual',
                                 payload_json=json.dumps({'student_grade_class': '2024级农林经济管理01班'}))
        db.session.add(draft)
        db.session.commit()
        clients = [app.test_client(), app.test_client()]
        for client in clients:
            with client.session_transaction() as session:
                session['user_id'] = user.id
        yield app, clients, user
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def observed_revision(client):
    return client.get('/user/api/lecture_form_draft').get_json().get('draft_revision', 'pre-fix-no-token')


@pytest.mark.parametrize('operation', ['put', 'delete'])
def test_old_browser_cannot_overwrite_or_delete_newer_draft(revision_case, operation):
    _app, (fresh, stale), _user = revision_case
    token_a, token_b = observed_revision(fresh), observed_revision(stale)
    assert token_a == token_b
    saved = fresh.put('/user/api/lecture_form_draft', json={
        'data': {'student_grade_class': '2024农林经济01班'},
        'expected_draft_revision': token_a,
    })
    assert saved.status_code == 200
    if operation == 'put':
        response = stale.put('/user/api/lecture_form_draft', json={
            'data': {'student_grade_class': '2024级农林经济管理01班'},
            'expected_draft_revision': token_b,
        })
    else:
        response = stale.delete('/user/api/lecture_form_draft',
                                headers={'X-Lecture-Draft-Revision': token_b})
    assert response.status_code == 409
    assert response.get_json()['code'] == 'draft_conflict'
    assert fresh.get('/user/api/lecture_form_draft').get_json()['data']['student_grade_class'] == '2024级农林经济01班'


@pytest.mark.parametrize('operation', ['put', 'delete'])
def test_legacy_missing_revision_is_rejected_without_mutation(revision_case, operation):
    _app, (client, _other), _user = revision_case
    before = client.get('/user/api/lecture_form_draft').get_json()
    response = (client.put('/user/api/lecture_form_draft', json={'data': {'course_title': 'old'}})
                if operation == 'put' else client.delete('/user/api/lecture_form_draft'))
    assert response.status_code == 428
    assert response.get_json()['code'] == 'draft_revision_required'
    assert client.get('/user/api/lecture_form_draft').get_json()['data'] == before['data']


def test_revision_tracks_raw_payload_policy_and_microseconds(revision_case):
    from datetime import timedelta
    from app.services.lecture_form_draft_concurrency import get_draft_revision
    _app, (client, _other), user = revision_case
    row = LectureFormDraft.query.filter_by(user_id=user.id).one()
    first = get_draft_revision(user.id, 'submit_form', row)
    assert len(first) == 64
    row.updated_at += timedelta(microseconds=1)
    second = get_draft_revision(user.id, 'submit_form', row)
    assert first != second
    row.entry_mode = 'photo'
    third = get_draft_revision(user.id, 'submit_form', row)
    assert third != second
    row.payload_json += ' '
    assert get_draft_revision(user.id, 'submit_form', row) != third


def test_none_revision_create_and_metadata_not_persisted(revision_case):
    _app, (client, other), user = revision_case
    db.session.query(LectureFormDraft).delete()
    db.session.commit()
    assert observed_revision(client) == 'none'
    assert observed_revision(other) == 'none'
    created = client.put('/user/api/lecture_form_draft', json={
        'expected_draft_revision': 'none',
        'data': {'course_title': 'new', 'expected_draft_revision': 'forged', 'draft_revision': 'forged'},
    })
    assert created.status_code == 200
    assert len(created.json['draft_revision']) == 64
    assert other.put('/user/api/lecture_form_draft', json={
        'expected_draft_revision': 'none', 'data': {'course_title': 'old'},
    }).status_code == 409
    assert client.get('/user/api/lecture_form_draft').json['data'] == {'course_title': 'new'}
    deleted = client.delete('/user/api/lecture_form_draft', headers={
        'X-Lecture-Draft-Revision': created.json['draft_revision']})
    assert deleted.status_code == 200 and deleted.json['draft_revision'] == 'none'


def test_header_and_body_must_agree(revision_case):
    _app, (client, _other), _user = revision_case
    before = observed_revision(client)
    response = client.put('/user/api/lecture_form_draft',
        headers={'X-Lecture-Draft-Revision': before},
        json={'expected_draft_revision': 'different', 'data': {'course_title': 'bad'}})
    assert response.status_code == 409
    assert observed_revision(client) == before


@pytest.mark.parametrize('initially_absent', [False, True])
def test_success_response_never_grants_a_later_writers_revision(revision_case, monkeypatch, initially_absent):
    from datetime import datetime, timedelta
    from app.services.lecture_form_draft_concurrency import get_draft_revision
    _app, (client, _other), user = revision_case
    if initially_absent:
        db.session.query(LectureFormDraft).delete(); db.session.commit()
    token = observed_revision(client)
    original_commit = db.session.commit
    recorded = {}
    def commit_then_other_writer():
        row = LectureFormDraft.query.filter_by(user_id=user.id).one()
        recorded['own_revision'] = get_draft_revision(user.id, 'submit_form', row)
        original_commit()
        with db.engine.begin() as connection:
            connection.execute(db.update(LectureFormDraft).where(LectureFormDraft.user_id == user.id).values(
                payload_json='{"course_title":"other writer"}',
                updated_at=datetime.now() + timedelta(seconds=1)))
    monkeypatch.setattr(db.session, 'commit', commit_then_other_writer)
    response = client.put('/user/api/lecture_form_draft', headers={'X-Lecture-Draft-Revision': token},
                          json={'data': {'course_title': 'own writer'}})
    assert response.status_code == 200
    assert response.json['draft_revision'] == recorded['own_revision']
    assert observed_revision(client) != response.json['draft_revision']
    assert client.put('/user/api/lecture_form_draft',
                      headers={'X-Lecture-Draft-Revision': response.json['draft_revision']},
                      json={'data': {'course_title': 'late own writer'}}).status_code == 409


def test_delete_cas_rechecks_row_after_validation(revision_case, monkeypatch):
    from app.blueprints.user import forms
    _app, (client, _other), user = revision_case
    old = observed_revision(client)
    original = forms.delete_lecture_form_draft_atomic
    def intervening_write(user_id, key, draft):
        db.session.execute(db.update(LectureFormDraft).where(LectureFormDraft.id == draft.id)
                           .values(payload_json='{"course_title":"newer"}').execution_options(synchronize_session=False))
        return original(user_id, key, draft)
    monkeypatch.setattr(forms, 'delete_lecture_form_draft_atomic', intervening_write)
    response = client.delete('/user/api/lecture_form_draft', headers={'X-Lecture-Draft-Revision': old})
    assert response.status_code == 409
    assert LectureFormDraft.query.filter_by(user_id=user.id).count() == 1


def test_save_cas_rechecks_entry_policy_after_validation(revision_case, monkeypatch):
    from app.blueprints.user import forms
    _app, (client, _other), _user = revision_case
    old = observed_revision(client)
    original = forms.save_lecture_form_draft_atomic
    def intervening_policy_change(user_id, key, draft, payload, **kwargs):
        db.session.execute(db.update(LectureFormDraft).where(LectureFormDraft.id == draft.id)
                           .values(entry_mode='photo', updated_at=draft.updated_at)
                           .execution_options(synchronize_session=False))
        return original(user_id, key, draft, payload, **kwargs)
    monkeypatch.setattr(forms, 'save_lecture_form_draft_atomic', intervening_policy_change)
    response = client.put('/user/api/lecture_form_draft', headers={'X-Lecture-Draft-Revision': old},
                          json={'data': {'course_title': 'stale policy'}})
    assert response.status_code == 409
    assert response.json['code'] == 'draft_conflict'


def test_submit_stale_or_missing_revision_does_not_consume_draft(revision_case):
    _app, (fresh, stale), _user = revision_case
    old = observed_revision(stale)
    saved = fresh.put('/user/api/lecture_form_draft', headers={'X-Lecture-Draft-Revision': old},
                      json={'data': {'student_grade_class': '2024农林经济01班'}})
    assert saved.status_code == 200
    response = stale.post('/user/submit_form', data={'expected_draft_revision': old})
    assert response.status_code == 409
    assert old.encode() in response.data  # Error recovery must retain the submitted token.
    assert saved.json['draft_revision'].encode() not in response.data
    assert stale.post('/user/submit_form', data={}).status_code == 428
    assert LectureForm.query.count() == 0
    assert fresh.get('/user/api/lecture_form_draft').json['data']['student_grade_class'] == '2024级农林经济01班'


def test_submit_rechecks_client_revision_after_actor_lock(revision_case, monkeypatch):
    from app.blueprints.user import forms
    _app, (client, other), _user = revision_case
    old = observed_revision(client)
    original = forms.serialize_new_submission
    def newer_request_then_lock(user_id):
        response = other.put('/user/api/lecture_form_draft',
            headers={'X-Lecture-Draft-Revision': old},
            json={'data': {'student_grade_class': '2024农林经济01班'}})
        assert response.status_code == 200
        return original(user_id)
    monkeypatch.setattr(forms, 'serialize_new_submission', newer_request_then_lock)
    data = valid_form(); data['expected_draft_revision'] = old
    response = client.post('/user/submit_form', data=data)
    assert response.status_code == 409
    assert LectureForm.query.count() == 0
    assert client.get('/user/api/lecture_form_draft').json['data']['student_grade_class'] == '2024级农林经济01班'


def test_atomic_delete_lock_error_becomes_conflict(revision_case, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.services.lecture_form_draft_concurrency import delete_lecture_form_draft_atomic, LectureFormDraftConflict
    _app, (_client, _other), user = revision_case
    row = LectureFormDraft.query.filter_by(user_id=user.id).one()
    def locked(*args, **kwargs):
        raise OperationalError('DELETE', {}, Exception('database is locked'))
    monkeypatch.setattr(db.session, 'execute', locked)
    with pytest.raises(LectureFormDraftConflict):
        delete_lecture_form_draft_atomic(user.id, 'submit_form', row)


@pytest.mark.parametrize('route,method', [('context', 'patch'), ('confirm', 'post'), ('resume', 'post')])
def test_photo_mutations_reject_stale_revision(revision_case, route, method):
    from tests.schedule_fixture import seed_current_schedule
    app, (client, other), user = revision_case
    app.config['LECTURE_CAPTURE_ENABLED'] = True
    seed_current_schedule('2026-2027-1')
    capture = LectureSiteCapture(user_id=user.id, photo_key='fixture.jpg',
        received_at='2026-11-02T08:00:00+08:00', building='8', room_number='309', draft_json='{}')
    db.session.add(capture); db.session.flush()
    row = LectureFormDraft.query.filter_by(user_id=user.id).one()
    row.payload_json = json.dumps({'site_capture_id': capture.id, 'student_grade_class': 'old'})
    row.entry_mode = 'photo'; db.session.commit()
    old = observed_revision(client)
    assert other.put('/user/api/lecture_form_draft', headers={'X-Lecture-Draft-Revision': old},
                     json={'data': {'student_grade_class': 'new'}, 'expected_site_capture_id': capture.id}).status_code == 200
    suffix = '' if route == 'context' else '/' + route
    endpoint = f'/user/api/site-capture/{capture.id}{suffix}'
    response = getattr(client, method)(endpoint, json={'building': '8', 'room_number': '309'},
                                      headers={'X-Lecture-Draft-Revision': old})
    assert response.status_code == 409 and response.json['code'] == 'draft_conflict'
    missing = getattr(client, method)(endpoint, json={'building': '8', 'room_number': '309'})
    assert missing.status_code == 428
    assert client.get('/user/api/lecture_form_draft').json['data']['student_grade_class'] == 'new'


def test_photo_upload_requires_revision_before_writing_file(revision_case, tmp_path):
    from io import BytesIO
    from PIL import Image
    app, (client, _other), _user = revision_case
    app.config.update(LECTURE_CAPTURE_ENABLED=True, LECTURE_CAPTURE_FOLDER=str(tmp_path))
    photo = BytesIO(); Image.new('RGB', (20, 20), 'white').save(photo, 'JPEG'); photo.seek(0)
    response = client.post('/user/api/site-capture', data={'photo': (photo, 'photo.jpg')})
    assert response.status_code == 428
    assert LectureSiteCapture.query.count() == 0
    assert list(tmp_path.iterdir()) == []


def test_photo_successes_return_committed_revision(revision_case, tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image
    from tests.schedule_fixture import seed_current_schedule
    from app.services import lecture_site_capture
    app, (client, _other), user = revision_case
    app.config.update(LECTURE_CAPTURE_ENABLED=True, LECTURE_CAPTURE_FOLDER=str(tmp_path))
    seed_current_schedule('2026-2027-1')
    SystemSetting.set('schedule_first_week_monday', '2026-08-31')
    monkeypatch.setattr(lecture_site_capture, 'recognize_door', lambda path: {
        'room_number': '309', 'status': 'recognized', 'alternatives': ['309']})
    previous = observed_revision(client)
    photo = BytesIO(); Image.new('RGB', (20, 20), 'white').save(photo, 'JPEG'); photo.seek(0)
    response = client.post('/user/api/site-capture', data={
        'photo': (photo, 'photo.jpg'), 'expected_draft_revision': previous})
    assert response.status_code == 201
    current = response.json['draft_revision']
    assert current != previous and current == observed_revision(client)
    capture_id = response.json['data']['id']
    db.session.get(LectureSiteCapture, capture_id).received_at = '2026-11-02T08:00:00+08:00'
    db.session.commit()
    response = client.patch(f'/user/api/site-capture/{capture_id}',
        headers={'X-Lecture-Draft-Revision': current}, json={'building': '8', 'room_number': '309'})
    assert response.status_code == 200 and response.json['candidates']
    current = response.json['draft_revision']
    assert current == observed_revision(client)
    response = client.post(f'/user/api/site-capture/{capture_id}/confirm',
        headers={'X-Lecture-Draft-Revision': current},
        json={'candidate_id': response.json['candidates'][0]['candidate_id']})
    assert response.status_code == 200, response.get_json()
    current = response.json['draft_revision']
    assert current == observed_revision(client)
    other = LectureSiteCapture(user_id=user.id, photo_key='other.jpg',
        received_at='2026-11-02T08:00:00+08:00', draft_json='{"student_grade_class":"2024农林经济01班"}')
    db.session.add(other); db.session.commit()
    response = client.post(f'/user/api/site-capture/{other.id}/resume',
                          headers={'X-Lecture-Draft-Revision': current})
    assert response.status_code == 200
    assert response.json['draft_revision'] == observed_revision(client)
    assert response.json['draft_revision'] != current
    assert client.get('/user/api/lecture_form_draft').json['data']['student_grade_class'] == '2024级农林经济01班'


def valid_form():
    return dict(lecture_date='2026-11-05', lecture_date_display='2026/11/05星期四',
        class_period='第7-9节', lecture_location='32-409', teacher_name='测试教师',
        teacher_college='测试学院', course_title='区域经济学II',
        student_grade_class='2024农林经济01班', course_changes='无', abnormal_situation='无',
        teaching_method='讲授法', classroom_discipline='好', classroom_atmosphere='好',
        courseware_quality='无', overall_effect='好', quality_case='不推荐',
        course_feedback='课堂教学内容清晰教师讲解认真学生积极回答问题能够联系实际案例理解理论知识课程结构合理重点明确课后复习更有方向',
        suggestions='无', student_signature1='测试甲', contact_phone1='13800000001',
        student_signature2='测试乙', contact_phone2='13800000002')


def test_submit_consumes_only_observed_draft_and_edit_ignores_unrelated_draft(revision_case):
    _app, (client, _other), user = revision_case
    data = valid_form(); data['expected_draft_revision'] = observed_revision(client)
    response = client.post('/user/submit_form', data=data)
    assert response.status_code == 302
    assert LectureFormDraft.query.filter_by(user_id=user.id).count() == 0
    form = LectureForm.query.one()
    response = client.put('/user/api/lecture_form_draft', json={
        'expected_draft_revision': 'none', 'data': {'course_title': 'unrelated new draft'}})
    assert response.status_code == 200
    unrelated_revision = response.json['draft_revision']
    edit = valid_form()
    edit.update(unique_id=str(form.id), expected_form_id=str(form.id),
                expected_form_updated_at=form.updated_at.isoformat(), course_title='区域经济学II修订')
    assert client.post('/user/submit_form', data=edit).status_code == 302
    assert observed_revision(client) == unrelated_revision


def test_direct_submission_without_existing_draft_remains_compatible(revision_case):
    _app, (client, _other), _user = revision_case
    db.session.query(LectureFormDraft).delete(); db.session.commit()
    assert client.post('/user/submit_form', data=valid_form()).status_code == 302
    assert LectureForm.query.count() == 1


@pytest.mark.parametrize('newer_draft', [False, True])
def test_exact_receipt_retry_with_old_revision_is_read_only(revision_case, newer_draft):
    _app, (client, _other), _user = revision_case
    data = valid_form(); data['expected_draft_revision'] = observed_revision(client)
    first = client.post('/user/submit_form', data=data)
    assert first.status_code == 302
    newer_revision = 'none'
    if newer_draft:
        saved = client.put('/user/api/lecture_form_draft',
            headers={'X-Lecture-Draft-Revision': 'none'}, json={'data': {'course_title': 'newer draft'}})
        assert saved.status_code == 200
        newer_revision = saved.json['draft_revision']
    repeated = client.post('/user/submit_form', data=data)
    assert repeated.status_code == 302 and repeated.location == first.location
    assert LectureForm.query.count() == 1
    assert observed_revision(client) == newer_revision
    # A changed field is a new request; the receipt cannot authorize it.
    changed = dict(data, course_title='a different course')
    assert client.post('/user/submit_form', data=changed).status_code == 409
    assert LectureForm.query.count() == 1
    assert observed_revision(client) == newer_revision


def test_old_revision_does_not_reuse_or_clean_up_expired_receipt(revision_case):
    from datetime import datetime, timedelta
    from app.services.submission_receipts import submission_receipts
    _app, (client, _other), _user = revision_case
    data = valid_form(); data['expected_draft_revision'] = observed_revision(client)
    assert client.post('/user/submit_form', data=data).status_code == 302
    db.session.execute(db.update(submission_receipts).values(
        accepted_at=datetime.now() - timedelta(seconds=121)))
    db.session.commit()
    assert client.post('/user/submit_form', data=data).status_code == 409
    assert db.session.execute(db.select(db.func.count()).select_from(submission_receipts)).scalar() == 1
    assert LectureForm.query.count() == 1


def test_submit_consumption_cas_preserves_changed_entry_policy(revision_case):
    from app.services.submission_draft_consumption import snapshot_submission_draft, delete_consumed_submission_draft
    _app, (_client, _other), user = revision_case
    draft = LectureFormDraft.query.filter_by(user_id=user.id).one()
    revision = snapshot_submission_draft(draft)
    db.session.execute(db.update(LectureFormDraft).where(LectureFormDraft.id == draft.id)
                       .values(entry_mode='photo', updated_at=draft.updated_at)
                       .execution_options(synchronize_session=False))
    assert delete_consumed_submission_draft(revision) is False
    assert LectureFormDraft.query.count() == 1


def test_receipt_retry_after_waiting_for_actor_lock_preserves_new_draft(revision_case, monkeypatch):
    from app.blueprints.user import forms
    _app, (client, other), _user = revision_case
    data = valid_form(); data['expected_draft_revision'] = observed_revision(client)
    original = forms.serialize_new_submission
    nested = {'active': False}
    def other_request_finishes_first(user_id):
        if not nested['active']:
            nested['active'] = True
            accepted = other.post('/user/submit_form', data=data)
            assert accepted.status_code == 302
            nested['location'] = accepted.location
            saved = other.put('/user/api/lecture_form_draft',
                headers={'X-Lecture-Draft-Revision': 'none'}, json={'data': {'course_title': 'next draft'}})
            assert saved.status_code == 200
            nested['revision'] = saved.json['draft_revision']
        return original(user_id)
    monkeypatch.setattr(forms, 'serialize_new_submission', other_request_finishes_first)
    retry = client.post('/user/submit_form', data=data)
    assert retry.status_code == 302 and retry.location == nested['location']
    assert LectureForm.query.count() == 1
    assert observed_revision(client) == nested['revision']
