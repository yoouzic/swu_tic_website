import io
from datetime import datetime, timezone, timedelta
from unittest import mock
import pytest
from tests.test_lecture_form_draft import LectureFormDraftTest as _DraftTests
from app.models import db, LectureFormDraft, LectureSiteCapture
from app.services.lecture_site_capture import infer_period, room_numbers


@pytest.fixture
def setup_capture(tmp_path):
    case = _DraftTests('runTest');case.setUp()
    from app.app import app
    case.app = app
    case.app.config.update(LECTURE_CAPTURE_ENABLED=True, LECTURE_CAPTURE_FOLDER=str(tmp_path))
    from tests.schedule_fixture import seed_current_schedule
    seed_current_schedule('2025-2026-2')
    case._login_as(case.user)
    yield case
    case.app.config['LECTURE_CAPTURE_ENABLED']=False
    case.tearDown()


def photo():
    from PIL import Image
    out=io.BytesIO();Image.new('RGB',(240,160),'white').save(out,'JPEG');out.seek(0)
    return out


def upload(case):
    with mock.patch('app.services.lecture_site_capture.recognize_door',return_value={'room_number':'601','status':'recognized','alternatives':['601']}):
        return case.client.post('/user/api/site-capture',data={'photo':(photo(),'capture.jpg'),'location':'{"latitude":29.82,"longitude":106.43,"accuracy":35}'},content_type='multipart/form-data')


def test_clock_break_and_fourteenth_period():
    assert infer_period('08:55')==2
    assert infer_period('09:40') is None
    assert infer_period('21:54')==14
    assert infer_period('21:55') is None


def test_ocr_ambiguity_and_low_confidence_are_not_silently_chosen():
    assert room_numbers([['601',.97]])['room_number']=='601'
    assert room_numbers([['601',.97],['602',.96]])['room_number'] is None
    assert room_numbers([['601',.45]])['room_number'] is None
    assert room_numbers([['13812345678',.99]])['room_number'] is None


def test_upload_saved_to_draft_and_private_to_owner(setup_capture):
    case=setup_capture;response=upload(case)
    assert response.status_code==201
    data=response.get_json()['data']
    assert data['room_number']=='601'
    assert case.client.get(data['photo_url']).status_code==200
    assert case.client.get('/user/api/lecture_form_draft').get_json()['data']['site_capture_id']==data['id']
    case._login_as(case.other_user)
    assert case.client.get(data['photo_url']).status_code==404
    assert case.client.patch(f"/user/api/site-capture/{data['id']}",json={'building':'10','room_number':'601'}).status_code==404


def test_autosave_preserves_photo_and_cannot_forge_binding(setup_capture):
    case=setup_capture;data=upload(case).get_json()['data']
    case.client.put('/user/api/lecture_form_draft',json={'data':{'course_feedback':'课后评价','site_capture_id':999}})
    draft=case.client.get('/user/api/lecture_form_draft').get_json()['data']
    assert draft['site_capture_id']==data['id']
    assert draft['course_feedback']=='课后评价'


def test_invalid_photo_is_rejected_without_creating_record(setup_capture):
    case=setup_capture
    response=case.client.post('/user/api/site-capture',data={'photo':(io.BytesIO(b'not an image'),'a.jpg')})
    assert response.status_code==400
    assert LectureSiteCapture.query.count()==0


def test_ocr_failure_still_saves_photo_and_existing_review(setup_capture):
    case=setup_capture
    case.client.put('/user/api/lecture_form_draft',json={'data':{'course_feedback':'已经写的评价'}})
    with mock.patch('app.services.lecture_site_capture.recognize_door',side_effect=RuntimeError('offline engine unavailable')):
        result=case.client.post('/user/api/site-capture',data={'photo':(photo(),'a.jpg')})
    assert result.status_code==201
    assert result.get_json()['data']['ocr_status']=='unavailable'
    assert case.client.get('/user/api/lecture_form_draft').get_json()['data']['course_feedback']=='已经写的评价'


def test_new_capture_does_not_silently_replace_existing_draft_photo(setup_capture):
    case=setup_capture;first=upload(case).get_json()['data']
    assert upload(case).status_code==409
    assert LectureSiteCapture.query.count()==1
    assert case.client.get('/user/api/lecture_form_draft').get_json()['data']['site_capture_id']==first['id']


def test_missing_photo_and_forged_version_do_not_bypass_trial_requirement(setup_capture):
    case=setup_capture
    assert case.client.post('/user/submit_form',data=case._valid_form_payload()).status_code==400
    payload=case._valid_form_payload();payload['unique_id']='99999'
    assert case.client.post('/user/submit_form',data=payload).status_code==404


def test_submission_keeps_photo_after_draft_is_deleted(setup_capture):
    case=setup_capture
    instant=datetime(2026,6,4,10,5,tzinfo=timezone(timedelta(hours=8)))
    with mock.patch('app.services.lecture_site_capture.now_at_site',return_value=instant):
        capture=upload(case).get_json()['data']
    case.client.patch(f"/user/api/site-capture/{capture['id']}",json={'building':'32','room_number':'302'})
    response=case.client.post('/user/submit_form',data=case._valid_form_payload())
    assert response.status_code==302
    record=db.session.get(LectureSiteCapture,capture['id'])
    assert record.form_id is not None
    assert LectureFormDraft.query.filter_by(user_id=case.user.id).first() is None
    assert case.client.get(capture['photo_url']).status_code==200
    repeated=case._valid_form_payload();repeated['site_capture_id']=str(capture['id'])
    assert case.client.post('/user/submit_form',data=repeated).status_code==302
    from app.models import LectureForm
    assert LectureForm.query.filter_by(listener_number=case.user.number).count()==1


def test_confirm_uses_actual_candidate_and_context_change_invalidates_it(setup_capture):
    from app.services.listening_assistant import ListeningAssistantService
    from app.services.listening_assistant_contracts import ScheduleEntry
    from datetime import date
    case=setup_capture
    with mock.patch('app.services.lecture_site_capture.now_at_site',return_value=datetime(2026,6,4,10,56,tzinfo=timezone(timedelta(hours=8)))):
        capture=upload(case).get_json()['data']
    assert capture['period_number']==4
    entry=ScheduleEntry(entry_id='test-entry',lecture_date=date(2026,6,4),room='10-601',period=(3,4),course_title='测试课程',teacher_name='张老师',teacher_college='测试学院',student_grade_class='2025级1班',source_kind='primary',source_label='当前权威课表',source_batch_id='1',source_row=1,semester='2025-2026-2')
    service=ListeningAssistantService(schedule_loader=lambda **kwargs:[entry],semester='2025-2026-2')
    with mock.patch('app.blueprints.user.site_capture.ListeningAssistantService',return_value=service),mock.patch('app.blueprints.user.site_capture.get_current_teaching_semester',return_value='2025-2026-2'),mock.patch('app.services.listening_assistant_evidence.ListeningAssistantService',return_value=service):
        context=case.client.patch(f"/user/api/site-capture/{capture['id']}",json={'building':'10','room_number':'601'}).get_json()
        cid=context['candidates'][0]['candidate_id']
        confirmed=case.client.post(f"/user/api/site-capture/{capture['id']}/confirm",json={'candidate_id':cid})
        assert confirmed.status_code==200,confirmed.get_json()
        draft=case.client.get('/user/api/lecture_form_draft').get_json()['data']
        assert draft['course_title']=='测试课程'
        assert draft['start_period']=='3' and draft['end_period']=='4'
        assert draft['assistant']['candidate_id']==cid
        case.client.patch(f"/user/api/site-capture/{capture['id']}",json={'building':'10','room_number':'602'})
        assert db.session.get(LectureSiteCapture,capture['id']).confirmed_candidate_id is None
        assert not case.client.get('/user/api/lecture_form_draft').get_json()['data'].get('assistant')


def test_multiple_site_records_resume_their_own_review(setup_capture):
    case=setup_capture;first=upload(case).get_json()['data']
    case.client.put('/user/api/lecture_form_draft',json={'data':{'course_feedback':'第一门的评价','site_capture_id':first['id']}})
    with mock.patch('app.services.lecture_site_capture.recognize_door',return_value={'room_number':'302','status':'recognized','alternatives':['302']}):
        result=case.client.post('/user/api/site-capture',data={'photo':(photo(),'b.jpg'),'new_record':'1'})
    assert result.status_code==201
    second=result.get_json()['data']
    assert second['id']!=first['id']
    assert not case.client.get('/user/api/lecture_form_draft').get_json()['data'].get('course_feedback')
    case.client.put('/user/api/lecture_form_draft',json={'data':{'course_feedback':'第二门的评价'}})
    case.client.post(f"/user/api/site-capture/{first['id']}/resume")
    assert case.client.get('/user/api/lecture_form_draft').get_json()['data']['course_feedback']=='第一门的评价'
    case.client.post(f"/user/api/site-capture/{second['id']}/resume")
    assert case.client.get('/user/api/lecture_form_draft').get_json()['data']['course_feedback']=='第二门的评价'
    assert len(case.client.get('/user/api/site-capture/records').get_json()['data'])==2
    response=case.client.put('/user/api/lecture_form_draft',json={'data':{'course_feedback':'迟到的第一门保存请求'},'expected_site_capture_id':first['id']})
    assert response.status_code==409
    assert case.client.get('/user/api/lecture_form_draft').get_json()['data']['course_feedback']=='第二门的评价'


def test_explicit_retake_archives_old_photo_and_preserves_review(setup_capture):
    case=setup_capture;first=upload(case).get_json()['data']
    case.client.put('/user/api/lecture_form_draft',json={'data':{'course_feedback':'不丢失评价'}})
    with mock.patch('app.services.lecture_site_capture.recognize_door',return_value={'room_number':'601','status':'recognized','alternatives':['601']}):
        result=case.client.post('/user/api/site-capture',data={'photo':(photo(),'b.jpg'),'replace_capture_id':str(first['id'])})
    assert result.status_code==201
    assert db.session.get(LectureSiteCapture,first['id']).is_archived
    assert len(case.client.get('/user/api/site-capture/records').get_json()['data'])==1
    assert case.client.get('/user/api/lecture_form_draft').get_json()['data']['course_feedback']=='不丢失评价'


def test_late_location_only_improves_owned_recent_record(setup_capture):
    import json
    case=setup_capture;capture=upload(case).get_json()['data']
    path=f"/user/api/site-capture/{capture['id']}/location"
    assert case.client.patch(path,json={'location':{'latitude':29.82001,'longitude':106.43,'accuracy':12}}).status_code==200
    assert json.loads(db.session.get(LectureSiteCapture,capture['id']).location_json)['accuracy']==12
    assert case.client.patch(path,json={'location':{'latitude':29.83,'longitude':106.44,'accuracy':90}}).status_code==200
    assert json.loads(db.session.get(LectureSiteCapture,capture['id']).location_json)['accuracy']==12
    case._login_as(case.other_user)
    assert case.client.patch(path,json={'location':{'latitude':29.82,'longitude':106.43,'accuracy':8}}).status_code==404


def test_location_update_rejects_old_record_and_invalid_accuracy(setup_capture):
    case=setup_capture;capture=upload(case).get_json()['data'];record=db.session.get(LectureSiteCapture,capture['id'])
    path=f"/user/api/site-capture/{record.id}/location"
    assert case.client.patch(path,json={'location':{'latitude':29.82,'longitude':106.43,'accuracy':0}}).status_code==400
    record.received_at=(datetime.now(timezone(timedelta(hours=8)))-timedelta(minutes=2)).isoformat();db.session.commit()
    assert case.client.patch(path,json={'location':{'latitude':29.82,'longitude':106.43,'accuracy':8}}).status_code==409
