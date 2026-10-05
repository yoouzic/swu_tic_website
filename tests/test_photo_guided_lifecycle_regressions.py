"""Route-level regressions for slow OCR and recoverable per-photo drafts."""
import json
import threading
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from app.models import LectureFormDraft, LectureSiteCapture, db
from app.services.listening_assistant import ListeningAssistantService
from app.services.listening_assistant_contracts import ScheduleEntry
from tests.test_lecture_site_capture import setup_capture, photo, upload


@pytest.mark.parametrize('later_state', ['manual', 'confirmed', 'submitted', 'archived', 'confirmed_ocr_failed'])
def test_slow_upload_ocr_preserves_later_human_or_terminal_state(setup_capture, later_state):
    case = setup_capture
    instant = datetime(2026, 6, 4, 10, 56, tzinfo=timezone(timedelta(hours=8)))
    entry = ScheduleEntry(entry_id='late-ocr-test', lecture_date=instant.date(),
        room='32-302', period=(3, 4), course_title='Database Systems', teacher_name='Teacher A',
        teacher_college='Test College', student_grade_class='2024 Test Class',
        source_kind='primary', source_label='隔离课表', source_batch_id='1', source_row=1,
        semester='2025-2026-2')
    service = ListeningAssistantService(schedule_loader=lambda **kwargs: [entry], semester='2025-2026-2')
    user_id = case.user.id
    entered, finish = threading.Event(), threading.Event()
    result = {}

    def slow_ocr(path):
        entered.set()
        assert finish.wait(10), 'test coordinator did not release OCR'
        if later_state == 'confirmed_ocr_failed':
            raise RuntimeError('synthetic OCR fault after confirmation')
        return {'room_number': '601', 'status': 'recognized', 'alternatives': ['601']}

    def worker():
        client = case.app.test_client()
        with client.session_transaction() as session:
            session.update(user_id=user_id, user_role='信息员')
        try:
            response = client.post('/user/api/site-capture', data={
                'photo': (photo(), 'slow.jpg')}, content_type='multipart/form-data')
            result.update(status=response.status_code, body=response.get_json())
        except Exception as error:
            result['error'] = repr(error)

    with mock.patch('app.services.lecture_site_capture.recognize_door', side_effect=slow_ocr), \
         mock.patch('app.services.lecture_site_capture.now_at_site', return_value=instant), \
         mock.patch('app.blueprints.user.site_capture.ListeningAssistantService', return_value=service), \
         mock.patch('app.blueprints.user.site_capture.get_current_teaching_semester', return_value='2025-2026-2'), \
         mock.patch('app.services.listening_assistant_evidence.ListeningAssistantService', return_value=service):
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            assert entered.wait(10), 'photo commit did not reach OCR'
            current = case.client.get('/user/api/lecture_form_draft').get_json()['data']
            capture_id = current['site_capture_id']
            if later_state == 'archived':
                with mock.patch('app.services.lecture_site_capture.recognize_door', return_value={
                        'room_number': '302', 'status': 'recognized', 'alternatives': ['302']}):
                    retake = case.client.post('/user/api/site-capture', data={
                        'photo': (photo(), 'retake.jpg'), 'replace_capture_id': str(capture_id)})
                assert retake.status_code == 201
            else:
                corrected = case.client.patch(f'/user/api/site-capture/{capture_id}',
                    json={'building': '32', 'room_number': '302'})
                assert corrected.status_code == 200, corrected.get_json()
                if later_state.startswith('confirmed'):
                    cid = corrected.get_json()['candidates'][0]['candidate_id']
                    confirmed = case.client.post(f'/user/api/site-capture/{capture_id}/confirm',
                        json={'candidate_id': cid})
                    assert confirmed.status_code == 200, confirmed.get_json()
                elif later_state == 'submitted':
                    submitted = case.client.post('/user/submit_form', data=case._valid_form_payload())
                    assert submitted.status_code == 302
            db.session.expire_all()
            before = db.session.get(LectureSiteCapture, capture_id)
            snapshot = {'room': before.room_number, 'building': before.building,
                'confirmed': before.confirmed_candidate_id, 'form_id': before.form_id,
                'archived': before.is_archived, 'ocr_status': before.ocr_status,
                'draft_json': before.draft_json}
        finally:
            finish.set()
            thread.join(10)
        assert not thread.is_alive()
        assert 'error' not in result, result
        assert result['status'] == 201, result
        db.session.expire_all()
        after = db.session.get(LectureSiteCapture, capture_id)
        assert after.room_number == snapshot['room']
        assert after.building == snapshot['building']
        assert after.confirmed_candidate_id == snapshot['confirmed']
        assert after.form_id == snapshot['form_id']
        assert after.is_archived == snapshot['archived']
        assert after.draft_json == snapshot['draft_json']
        if later_state != 'manual':
            assert after.ocr_status == snapshot['ocr_status']
        assert case.client.get(f'/user/api/site-capture/{capture_id}/photo').status_code == 200


def test_photo_resume_uses_same_legacy_assistant_fallback_as_main_draft(setup_capture):
    case = setup_capture
    first = upload(case).get_json()['data']
    legacy = {'site_capture_id': first['id'], 'course_feedback': '保留人工评价',
        'student_signature1': '模拟见证人', 'contact_phone1': '13800000001',
        'assistant': {'stage': 'confirmed', 'candidate_id': 'legacy:candidate', 'old_schema_key': 'old-client'}}
    record = db.session.get(LectureSiteCapture, first['id'])
    record.draft_json = json.dumps(legacy, ensure_ascii=False)
    record.confirmed_candidate_id = 'legacy:candidate'
    draft = LectureFormDraft.query.filter_by(user_id=case.user.id).one()
    draft.payload_json = record.draft_json
    db.session.commit()
    normal = case.client.get('/user/api/lecture_form_draft').get_json()['data']
    assert normal['course_feedback'] == legacy['course_feedback']
    assert 'assistant' not in normal
    with mock.patch('app.services.lecture_site_capture.recognize_door', return_value={
            'room_number': '302', 'status': 'recognized', 'alternatives': ['302']}):
        second = case.client.post('/user/api/site-capture', data={
            'photo': (photo(), 'second.jpg'), 'new_record': '1'})
    assert second.status_code == 201
    # Model a photo record that slept through the assistant schema change.
    db.session.get(LectureSiteCapture, first['id']).draft_json = json.dumps(legacy, ensure_ascii=False)
    db.session.commit()
    response = case.client.post(f"/user/api/site-capture/{first['id']}/resume")
    assert response.status_code == 200
    restored = case.client.get('/user/api/lecture_form_draft').get_json()['data']
    assert restored['site_capture_id'] == first['id']
    assert restored['course_feedback'] == legacy['course_feedback']
    assert restored['student_signature1'] == legacy['student_signature1']
    assert restored['contact_phone1'] == legacy['contact_phone1']
    assert 'assistant' not in restored
    assert response.get_json()['data']['course_confirmed'] is False
    assert case.client.get(first['photo_url']).status_code == 200


@pytest.mark.parametrize('raw', ['not json', '[]', 'null'])
def test_invalid_photo_draft_does_not_crash_resume(setup_capture, raw):
    case = setup_capture
    record = upload(case).get_json()['data']
    current = db.session.get(LectureSiteCapture, record['id'])
    current.draft_json = raw
    current.confirmed_candidate_id = 'legacy:candidate'
    db.session.commit()
    # DELETE only the active ordinary draft; the independent photo remains.
    case.client.delete('/user/api/lecture_form_draft')
    records=case.client.get('/user/api/site-capture/records')
    assert records.status_code==200
    assert records.get_json()['data'][0]['course_title']=='待确认课程'
    response = case.client.post(f"/user/api/site-capture/{record['id']}/resume")
    assert response.status_code == 200
    assert case.client.get('/user/api/lecture_form_draft').get_json()['data']['site_capture_id'] == record['id']
    assert response.get_json()['data']['course_confirmed'] is False


def test_real_calendar_schedule_index_confirm_and_complete_submit_still_work(setup_capture):
    import pandas as pd
    from app.models import LectureForm, ListeningAssistantEvidence, SystemSetting
    from app.services.schedule_snapshots import persist_import_snapshot
    from app.services.listening_assistant_schedule import persist_listening_assistant_entries
    from tests.test_schedule_snapshots import HEADERS, full_row
    case=setup_capture
    frame=pd.DataFrame([full_row(semester='2025-2026-2',weekday=4,
        course_name='实际索引回归课程',teacher_name='索引测试教师',teacher_college='索引测试学院',
        class_comp='2024级索引测试班',venue='32-302',start_week='1-16')],columns=HEADERS)
    batches=persist_import_snapshot(frame,source_filename='synthetic-photo-regression.xlsx',source_sha256='b'*64)
    assert persist_listening_assistant_entries(frame,batches)
    SystemSetting.set('teaching_current_semester','2025-2026-2')
    SystemSetting.set('teaching_first_week_monday','2026-03-02')
    SystemSetting.set('teaching_week_start_day','0')
    SystemSetting.set('teaching_total_weeks','20')
    db.session.commit()
    instant=datetime(2026,6,4,10,56,tzinfo=timezone(timedelta(hours=8)))
    with mock.patch('app.services.lecture_site_capture.now_at_site',return_value=instant):
        capture=upload(case).get_json()['data']
    assert capture['period_number']==4
    # No service/search/selection/calendar mocks: exercise the current authority,
    # imported index, date derivation, selection revalidation and evidence writer.
    found=case.client.patch(f"/user/api/site-capture/{capture['id']}",json={'building':'32','room_number':'302'})
    assert found.status_code==200,found.get_json()
    candidates=found.get_json()['candidates']
    assert len(candidates)==1
    assert candidates[0]['course_title']=='实际索引回归课程'
    confirmed=case.client.post(f"/user/api/site-capture/{capture['id']}/confirm",json={'candidate_id':candidates[0]['candidate_id']})
    assert confirmed.status_code==200,confirmed.get_json()
    draft=confirmed.get_json()['draft']
    assert (draft['start_period'],draft['end_period'])==('3','4')
    assert case.client.put('/user/api/lecture_form_draft',json={
        'data':{**draft,'course_feedback':'课后人工评价已保留。'*8},'expected_site_capture_id':capture['id']}).status_code==200
    assert case.client.post(f"/user/api/site-capture/{capture['id']}/resume").status_code==200
    restored=case.client.get('/user/api/lecture_form_draft').get_json()['data']
    assert restored['course_feedback']=='课后人工评价已保留。'*8
    payload=case._valid_form_payload()
    for field in ('lecture_date','lecture_location','teacher_name','teacher_college','course_title','student_grade_class','class_period','start_period','end_period'):
        payload[field]=restored[field]
    payload.update(site_capture_id=str(capture['id']),assistant_payload=json.dumps(restored['assistant']),
                   course_feedback=restored['course_feedback'])
    submitted=case.client.post('/user/submit_form',data=payload)
    assert submitted.status_code==302,submitted.get_data(as_text=True)[-1000:]
    record=db.session.get(LectureSiteCapture,capture['id'])
    form=db.session.get(LectureForm,record.form_id)
    assert form.course_title=='实际索引回归课程'
    assert form.class_period=='第3-4节'
    assert form.teacher_name=='索引测试教师'
    assert ListeningAssistantEvidence.query.filter_by(lecture_form_id=form.id).count()==1
    assert LectureFormDraft.query.filter_by(user_id=case.user.id).count()==0
    assert case.client.get(capture['photo_url']).status_code==200
