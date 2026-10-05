import json
from datetime import date, datetime, timezone, timedelta
from unittest import mock

from tests.test_lecture_site_capture import setup_capture, upload
from app.models import db, LectureSiteCapture
from app.services.listening_assistant import ListeningAssistantService
from app.services.listening_assistant_contracts import ScheduleEntry
from app.services.campus_buildings import load_buildings, representative_point


def entries():
    return [ScheduleEntry(entry_id=str(i),lecture_date=date(2026,4,7),room=room,
          period=period,course_title=title,teacher_name='张老师',teacher_college='测试学院',
          student_grade_class='2025级1班',source_batch_id='1',source_row=i,
          semester='2025-2026-2')
        for i,(room,period,title) in enumerate([
            ('8-609',(1,2),'8教课程'),('25-501',(1,2),'25教其他房间'),
            ('25-609',(7,8),'25教下午课程')],1)]


def prepare(case):
    instant=datetime(2026,4,7,9,0,tzinfo=timezone(timedelta(hours=8)))
    with mock.patch('app.services.lecture_site_capture.now_at_site',return_value=instant):
        public=upload(case).get_json()['data']
    record=db.session.get(LectureSiteCapture,public['id'])
    record.client_captured_at=instant.isoformat()
    f=next(f for f in load_buildings()['features'] if f['properties']['code']=='25')
    lon,lat=representative_point(f)
    record.location_json=json.dumps({'latitude':lat,'longitude':lon,'accuracy':35})
    record.ocr_alternatives_json=json.dumps({'hypotheses':[{'value':'0609','score':.998}]})
    db.session.commit()
    service=ListeningAssistantService(schedule_loader=lambda **kwargs:entries(),semester='2025-2026-2')
    return record,service


def test_suggestions_are_read_only_and_do_not_confirm_inferred_room(setup_capture):
    case=setup_capture;record,service=prepare(case)
    path=f'/user/api/site-capture/{record.id}/suggestions'
    with mock.patch('app.blueprints.user.site_capture.ListeningAssistantService',return_value=service):
        response=case.client.post(path,json={'facts':{},'asked':[]})
    assert response.status_code==200,response.get_json()
    result=response.get_json()['data']
    assert result['candidates'][0]['course_title']=='8教课程'
    assert record.building is None and record.confirmed_candidate_id is None
    draft=case.client.get('/user/api/lecture_form_draft').get_json()['data']
    assert not draft.get('course_title')


def test_human_fact_overrides_guess_and_old_ocr_json_loads(setup_capture):
    case=setup_capture;record,service=prepare(case)
    record.ocr_alternatives_json='["6090"]';db.session.commit()
    with mock.patch('app.blueprints.user.site_capture.ListeningAssistantService',return_value=service):
        response=case.client.post(f'/user/api/site-capture/{record.id}/suggestions',
                                 json={'facts':{'building':'8','room_number':'0609'},'asked':['building']})
    assert response.status_code==200
    assert [c['room'] for c in response.get_json()['data']['candidates']]==['8-609']
    assert case.client.get(f'/user/api/site-capture/{record.id}').get_json()['data']['alternatives']==['6090']


def test_suggestion_endpoint_rejects_other_user_and_invalid_facts(setup_capture):
    case=setup_capture;record,service=prepare(case);path=f'/user/api/site-capture/{record.id}/suggestions'
    assert case.client.post(path,json={'facts':{'building':True}}).status_code==400
    case._login_as(case.other_user)
    assert case.client.get(path).status_code==404


def test_missing_primary_source_retains_photo_and_manual_path(setup_capture):
    from app.services.listening_assistant_schedule import ScheduleSourceUnavailable
    case=setup_capture;record,service=prepare(case)
    service.search_partial=mock.Mock(side_effect=ScheduleSourceUnavailable(status='missing'))
    with mock.patch('app.blueprints.user.site_capture.ListeningAssistantService',return_value=service):
        response=case.client.get(f'/user/api/site-capture/{record.id}/suggestions')
    assert response.status_code==200
    assert response.get_json()['data']['source_unavailable'] is True
    assert response.get_json()['data']['manual_available'] is True
    assert case.client.get(f'/user/api/site-capture/{record.id}/photo').status_code==200


def test_ranked_card_must_use_existing_context_and_confirmation_revalidation(setup_capture):
    case=setup_capture;record,service=prepare(case)
    with mock.patch('app.blueprints.user.site_capture.ListeningAssistantService',return_value=service),\
         mock.patch('app.services.listening_assistant_evidence.ListeningAssistantService',return_value=service),\
         mock.patch('app.blueprints.user.site_capture.get_current_teaching_semester',return_value='2025-2026-2'):
        result=case.client.get(f'/user/api/site-capture/{record.id}/suggestions').get_json()['data']
        chosen=result['candidates'][0]
        assert case.client.post(f'/user/api/site-capture/{record.id}/confirm',json={'candidate_id':chosen['candidate_id']}).status_code==409

        case.client.patch(f'/user/api/site-capture/{record.id}',json={'building':'8','room_number':'0609'})
        response=case.client.post(f'/user/api/site-capture/{record.id}/confirm',json={'candidate_id':chosen['candidate_id']})
        assert response.status_code==200,response.get_json()
        assert response.get_json()['draft']['start_period']=='1'
        assert response.get_json()['draft']['end_period']=='2'
        case.client.patch(f'/user/api/site-capture/{record.id}',json={'building':'8','room_number':'0612'})
        assert record.confirmed_candidate_id is None
        assert case.client.post(f'/user/api/site-capture/{record.id}/confirm',json={'candidate_id':chosen['candidate_id']}).status_code==409


def test_region_retry_preserves_photo_and_does_not_confirm_room(setup_capture):
    case=setup_capture;record,service=prepare(case)
    photo=case.client.get(f'/user/api/site-capture/{record.id}/photo').data
    region={'x':.1,'y':.2,'width':.5,'height':.3}
    result={'status':'recognized','room_number':'0612','alternatives':['0612'],
            'hypotheses':[{'value':'0612','score':.99,'method':'manual_region'}]}
    with mock.patch('app.services.lecture_site_capture.recognize_door',return_value=result) as ocr:
        response=case.client.post(f'/user/api/site-capture/{record.id}/ocr-region',json={'region':region})
    assert response.status_code==200,response.get_json()
    assert ocr.call_args.kwargs['region']==region
    assert set(response.get_json()['data']['alternatives'])=={'0609','0612'}
    assert record.building is None and record.confirmed_candidate_id is None
    assert case.client.get(f'/user/api/site-capture/{record.id}/photo').data==photo


def test_region_retry_failure_and_foreign_record_leave_evidence_intact(setup_capture):
    case=setup_capture;record,service=prepare(case);original=record.ocr_alternatives_json
    path=f'/user/api/site-capture/{record.id}/ocr-region'
    with mock.patch('app.services.lecture_site_capture.recognize_door',side_effect=RuntimeError('offline')):
        assert case.client.post(path,json={'region':{}}).status_code==503
    assert record.ocr_alternatives_json==original
    record.confirmed_candidate_id='already-confirmed';db.session.commit()
    assert case.client.post(path,json={'region':{}}).status_code==409
    case._login_as(case.other_user)
    assert case.client.post(path,json={'region':{}}).status_code==404


def test_map_is_private_read_only_and_retains_unmapped_buildings(setup_capture):
    case=setup_capture;record,service=prepare(case);before=record.location_json
    with mock.patch('app.blueprints.user.site_capture._schedule_building_codes',return_value=['8','25','46'],create=True):
        response=case.client.get(f'/user/api/site-capture/{record.id}/map')
    assert response.status_code==200
    data=response.get_json()['data']
    assert data['nearby']['unmapped_buildings']==['46']
    assert '8' in [b['code'] for b in data['nearby']['buildings']]
    assert data['coordinate_system']=='WGS84'
    assert record.location_json==before and record.building is None
    case._login_as(case.other_user)
    assert case.client.get(f'/user/api/site-capture/{record.id}/map').status_code==404


def test_suggestions_carry_question_budget_and_reject_forged_counts(setup_capture):
    case=setup_capture;record,service=prepare(case)
    path=f'/user/api/site-capture/{record.id}/suggestions'
    with mock.patch('app.blueprints.user.site_capture.ListeningAssistantService',return_value=service):
        response=case.client.post(path,json={'facts':{},'asked':[],'question_count':2})
        assert response.get_json()['data']['guidance']['questions_remaining']==0
        for count in [-1,3,True,'1']:
            assert case.client.post(path,json={'question_count':count}).status_code==400
