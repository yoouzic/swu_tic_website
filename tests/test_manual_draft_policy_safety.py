"""A persisted manual grant cannot survive switching to a photo record."""
import json
import threading
from unittest import mock

import pytest

# This helper isolates all environment paths before any application import.
from tests import test_lecture_form_draft_csrf as _csrf_helper
from app.blueprints.user import forms
from app.models import LectureForm, LectureFormDraft, LectureSiteCapture, db
from app.services.lecture_form_draft_entry_schema import manual_draft_entry_allowed


@pytest.fixture
def manual_case():
    case = _csrf_helper.LectureFormDraftCsrfTest('runTest')
    case.setUp()
    case.app.config['LECTURE_CAPTURE_ENABLED'] = True
    with case.app.app_context():
        from tests.schedule_fixture import seed_current_schedule
        seed_current_schedule()
        capture = LectureSiteCapture(user_id=case.user_id, photo_key='policy-photo.jpg',
            received_at='2026-10-05T10:00:00+08:00', draft_json=json.dumps({
                'course_feedback': '照片记录独立人工评价', 'entry_mode': 'manual',
                'manual_entry_allowed': True, 'csrf_token': 'legacy-session-token'}))
        db.session.add(capture)
        db.session.flush()
        case.capture_id = capture.id
        db.session.add(LectureFormDraft(user_id=case.user_id, draft_key='submit_form',
            entry_mode='manual', payload_json=json.dumps(case.manual_payload(), ensure_ascii=False)))
        db.session.commit()
    yield case
    case.tearDown()


def test_resuming_photo_revokes_server_manual_grant_even_when_photo_payload_claims_manual(manual_case):
    case = manual_case
    token = case.page_token(case.client)
    response = case.client.post(f'/user/api/site-capture/{case.capture_id}/resume',
                                headers={'X-CSRFToken': token})
    assert response.status_code == 200
    with case.app.app_context():
        draft = LectureFormDraft.query.one()
        assert draft.entry_mode == 'photo'
        assert not manual_draft_entry_allowed(draft)
        payload = json.loads(draft.payload_json)
        assert payload['course_feedback'] == '照片记录独立人工评价'
        assert payload['site_capture_id'] == case.capture_id
        assert not {'entry_mode', 'manual_entry_allowed', 'csrf_token'} & payload.keys()


def test_legacy_draft_with_retained_photo_id_does_not_receive_manual_grant(manual_case):
    case = manual_case
    with case.app.app_context():
        draft = LectureFormDraft.query.one()
        draft.entry_mode = 'legacy'
        draft.payload_json = json.dumps(dict(case.manual_payload(), site_capture_id=case.capture_id))
        db.session.commit()
        assert not manual_draft_entry_allowed(draft)
    token = case.page_token(case.client)
    response = case.client.post('/user/submit_form', data=dict(case.manual_payload(),
        csrf_token=token, entry_mode='manual', manual_entry_allowed='1'))
    assert response.status_code == 400
    with case.app.app_context():
        assert LectureForm.query.count() == 0
        assert LectureFormDraft.query.one().entry_mode == 'legacy'


def test_new_manual_submission_consumes_exact_legacy_draft_with_discarded_version_fields(manual_case):
    case = manual_case
    with case.app.app_context():
        draft = LectureFormDraft.query.one()
        draft.payload_json = json.dumps(dict(case.manual_payload(), unique_id='17',
            expected_form_id='18', expected_form_updated_at='old-version'), ensure_ascii=False)
        db.session.commit()
    token = case.page_token(case.client)
    loaded = case.client.get('/user/api/lecture_form_draft').get_json()['data']
    assert not {'unique_id', 'expected_form_id', 'expected_form_updated_at'} & loaded.keys()
    submitted = case.client.post('/user/submit_form', data=dict(loaded, csrf_token=token))
    assert submitted.status_code == 302
    with case.app.app_context():
        assert LectureForm.query.count() == 1
        assert LectureFormDraft.query.count() == 0, 'consumed legacy manual grant must not remain reusable'


def test_submission_paused_after_manual_grant_read_cannot_bypass_newer_photo_resume(manual_case):
    case = manual_case
    entered, release = threading.Event(), threading.Event()
    result = {}
    original_extract = forms._extract_assistant_submission_payload

    def paused_extract():
        if threading.current_thread().name == 'manual-grant-submit':
            entered.set()
            assert release.wait(10)
        return original_extract()

    def submit_manual():
        client = case.new_session()
        token = case.page_token(client)
        try:
            response = client.post('/user/submit_form', data=dict(case.manual_payload(), csrf_token=token))
            result.update(status=response.status_code, text=response.get_data(as_text=True))
        except Exception as error:
            result['error'] = repr(error)

    token = case.page_token(case.client)
    with mock.patch.object(forms, '_extract_assistant_submission_payload', side_effect=paused_extract):
        worker = threading.Thread(target=submit_manual, name='manual-grant-submit')
        worker.start()
        try:
            assert entered.wait(10)
            resumed = case.client.post(f'/user/api/site-capture/{case.capture_id}/resume',
                                       headers={'X-CSRFToken': token})
            assert resumed.status_code == 200
        finally:
            release.set()
            worker.join(10)
    assert not worker.is_alive()
    assert 'error' not in result, result
    assert result['status'] == 409
    assert '听课草稿已更新' in result['text']
    with case.app.app_context():
        assert LectureForm.query.count() == 0
        draft = LectureFormDraft.query.one()
        assert draft.entry_mode == 'photo'
        assert json.loads(draft.payload_json)['site_capture_id'] == case.capture_id
