"""Real-request read/write races for the photo-bound ordinary draft."""
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest
from sqlalchemy import event

from app.blueprints.user import forms
from app.blueprints.user import site_capture
from app.models import LectureFormDraft, LectureSiteCapture, db
from app.services.listening_assistant import ListeningAssistantService
from app.services.listening_assistant_contracts import ScheduleEntry
from tests.test_lecture_site_capture import setup_capture, photo, upload


@pytest.mark.parametrize('winner', ['resume_other_record', 'newer_same_record_save', 'delete'])
def test_already_reading_older_put_returns_conflict_without_overwriting_winner(setup_capture, winner):
    case = setup_capture
    first = upload(case).get_json()['data']
    assert case.client.put('/user/api/lecture_form_draft', json={
        'data': {'course_feedback': 'A 原评价'}, 'expected_site_capture_id': first['id']}).status_code == 200
    if winner == 'resume_other_record':
        with mock.patch('app.services.lecture_site_capture.recognize_door', return_value={
                'room_number': '302', 'status': 'recognized', 'alternatives': ['302']}):
            second = case.client.post('/user/api/site-capture', data={
                'photo': (photo(), 'second.jpg'), 'new_record': '1'}).get_json()['data']
        assert case.client.put('/user/api/lecture_form_draft', json={
            'data': {'course_feedback': 'B 原评价'}, 'expected_site_capture_id': second['id']}).status_code == 200
        assert case.client.post(f"/user/api/site-capture/{first['id']}/resume").status_code == 200
    user_id = case.user.id
    entered, release = threading.Event(), threading.Event()
    result = {}
    original_load = forms._load_lecture_form_draft

    def paused_load(uid):
        draft = original_load(uid)
        if threading.current_thread().name == 'older-photo-draft-put':
            entered.set()
            assert release.wait(10)
        return draft

    def old_put():
        client = case.app.test_client()
        with client.session_transaction() as session:
            session.update(user_id=user_id, user_role='信息员')
        try:
            response = client.put('/user/api/lecture_form_draft', json={
                'data': {'course_feedback': 'A 迟到旧评价'}, 'expected_site_capture_id': first['id']})
            result.update(status=response.status_code, body=response.get_json())
        except Exception as error:
            result['error'] = repr(error)

    with mock.patch.object(forms, '_load_lecture_form_draft', side_effect=paused_load):
        worker = threading.Thread(target=old_put, name='older-photo-draft-put')
        worker.start()
        try:
            assert entered.wait(10)
            if winner == 'resume_other_record':
                won = case.client.post(f"/user/api/site-capture/{second['id']}/resume")
                expected_capture, expected_feedback = second['id'], 'B 原评价'
            elif winner == 'newer_same_record_save':
                won = case.client.put('/user/api/lecture_form_draft', json={
                    'data': {'course_feedback': 'A 较新评价'}, 'expected_site_capture_id': first['id']})
                expected_capture, expected_feedback = first['id'], 'A 较新评价'
            else:
                won = case.client.delete('/user/api/lecture_form_draft')
            assert won.status_code == 200
            db.session.expire_all()
            capture_draft_after_winner = db.session.get(LectureSiteCapture, first['id']).draft_json
        finally:
            release.set()
            worker.join(10)
    assert not worker.is_alive()
    assert 'error' not in result, result
    assert result['status'] == 409, result
    assert result['body']['code'] == 'draft_conflict'
    db.session.expire_all()
    remaining = case.client.get('/user/api/lecture_form_draft').get_json()
    if winner == 'delete':
        assert remaining['exists'] is False
    else:
        assert remaining['data']['site_capture_id'] == expected_capture
        assert remaining['data']['course_feedback'] == expected_feedback
    assert db.session.get(LectureSiteCapture, first['id']).draft_json == capture_draft_after_winner


def test_successive_same_payload_saves_have_monotonic_versions(setup_capture):
    case = setup_capture
    record = upload(case).get_json()['data']
    frozen = datetime(2026, 10, 3, 1, 30)
    current = LectureFormDraft.query.filter_by(user_id=case.user.id).one()
    current.updated_at = frozen + timedelta(minutes=1)
    db.session.commit()
    before = current.updated_at
    # Frozen/regressed wall clocks cannot produce the same revision for two saves.
    clock_module = (forms.save_lecture_form_draft_atomic.__module__
                    if hasattr(forms, 'save_lecture_form_draft_atomic') else forms.__name__)
    with mock.patch(clock_module+'.datetime') as clock:
        clock.now.return_value = frozen
        for _ in range(2):
            response = case.client.put('/user/api/lecture_form_draft', json={
                'data': {'course_feedback': '相同评价'}, 'expected_site_capture_id': record['id']})
            assert response.status_code == 200
            db.session.expire_all()
            after = LectureFormDraft.query.filter_by(user_id=case.user.id).one().updated_at
            assert after > before
            before = after


def test_photo_update_failure_rolls_back_claimed_main_draft(setup_capture):
    case = setup_capture
    record = upload(case).get_json()['data']
    assert case.client.put('/user/api/lecture_form_draft', json={
        'data': {'course_feedback': '原评价'}, 'expected_site_capture_id': record['id']}).status_code == 200
    current_capture = db.session.get(LectureSiteCapture, record['id'])
    current_capture.confirmed_candidate_id = 'primary:previous-confirmation'
    db.session.commit()
    draft = LectureFormDraft.query.filter_by(user_id=case.user.id).one()
    original = (draft.payload_json, draft.updated_at, current_capture.draft_json,
                current_capture.confirmed_candidate_id)

    def reject_photo_update(mapper, connection, target):
        if target.id == record['id']:
            raise RuntimeError('synthetic associated-photo write failure')

    event.listen(LectureSiteCapture, 'before_update', reject_photo_update)
    try:
        with pytest.raises(RuntimeError, match='associated-photo write failure'):
            case.client.put('/user/api/lecture_form_draft', json={
                'data': {'course_feedback': '失败请求新评价'}, 'expected_site_capture_id': record['id']})
    finally:
        event.remove(LectureSiteCapture, 'before_update', reject_photo_update)
    db.session.expire_all()
    saved = LectureFormDraft.query.filter_by(user_id=case.user.id).one()
    capture = db.session.get(LectureSiteCapture, record['id'])
    assert (saved.payload_json, saved.updated_at, capture.draft_json,
            capture.confirmed_candidate_id) == original


@pytest.mark.parametrize('writer', ['confirm', 'context', 'resume', 'new_upload'])
def test_site_writer_read_before_newer_put_cannot_overwrite_evaluation(setup_capture, writer):
    case = setup_capture
    first = upload(case).get_json()['data']
    from datetime import date
    entry = ScheduleEntry(entry_id='writer-race',lecture_date=date.fromisoformat(first['record_date']),
        room='32-302',period=(3,4),course_title='Database Systems',teacher_name='Teacher A',
        teacher_college='Test College',student_grade_class='2024 Test Class',source_kind='primary',
        source_label='隔离课表',source_batch_id='1',source_row=1,semester='2025-2026-2')
    service=ListeningAssistantService(schedule_loader=lambda **kwargs:[entry],semester='2025-2026-2')
    active=first['id']
    with mock.patch.object(site_capture,'ListeningAssistantService',return_value=service), \
         mock.patch.object(site_capture,'get_current_teaching_semester',return_value='2025-2026-2'), \
         mock.patch('app.services.listening_assistant_evidence.ListeningAssistantService',return_value=service):
        context=case.client.patch(f"/user/api/site-capture/{active}",json={'building':'32','room_number':'302'})
        assert context.status_code==200
        cid=context.get_json()['candidates'][0]['candidate_id']
        if writer=='resume':
            with mock.patch('app.services.lecture_site_capture.recognize_door',return_value={
                    'room_number':'302','status':'recognized','alternatives':['302']}):
                second=case.client.post('/user/api/site-capture',data={'photo':(photo(),'second.jpg'),'new_record':'1'})
            assert second.status_code==201
            active=second.get_json()['data']['id']
        assert case.client.put('/user/api/lecture_form_draft',json={
            'data':{'course_feedback':'A 较旧人工评价'},'expected_site_capture_id':active}).status_code==200
        uid=case.user.id
        entered,release=threading.Event(),threading.Event()
        original=site_capture._draft
        result={}

        def paused_draft(user):
            value=original(user)
            if threading.current_thread().name=='older-site-writer':
                entered.set()
                assert release.wait(10)
            return value

        def old_writer():
            client=case.app.test_client()
            with client.session_transaction() as session:session.update(user_id=uid,user_role='信息员')
            try:
                if writer=='confirm':
                    ret=client.post(f"/user/api/site-capture/{first['id']}/confirm",json={'candidate_id':cid})
                elif writer=='context':
                    ret=client.patch(f"/user/api/site-capture/{first['id']}",json={'building':'32','room_number':'303'})
                elif writer=='resume':
                    ret=client.post(f"/user/api/site-capture/{first['id']}/resume")
                else:
                    with mock.patch('app.services.lecture_site_capture.recognize_door',return_value={
                            'room_number':'302','status':'recognized','alternatives':['302']}):
                        ret=client.post('/user/api/site-capture',data={'photo':(photo(),'new.jpg'),'new_record':'1'})
                result.update(status=ret.status_code,body=ret.get_json())
            except Exception as error:result['error']=repr(error)

        with mock.patch.object(site_capture,'_draft',side_effect=paused_draft):
            thread=threading.Thread(target=old_writer,name='older-site-writer')
            thread.start()
            try:
                assert entered.wait(10)
                newer=case.client.put('/user/api/lecture_form_draft',json={
                    'data':{'course_feedback':'B 较新人工评价'},'expected_site_capture_id':active})
                assert newer.status_code==200,newer.get_json()
                db.session.expire_all()
                snapshots={r.id:(r.room_number,r.building,r.confirmed_candidate_id,r.is_archived,r.draft_json)
                           for r in LectureSiteCapture.query.all()}
                files={p.name for p in Path(case.app.config['LECTURE_CAPTURE_FOLDER']).glob('*.jpg')}
            finally:
                release.set();thread.join(10)
        assert not thread.is_alive()
        assert 'error' not in result,result
        assert result['status']==409,result
        assert result['body']['code']=='draft_conflict'
        db.session.expire_all()
        after=case.client.get('/user/api/lecture_form_draft').get_json()['data']
        assert after['site_capture_id']==active
        assert after['course_feedback']=='B 较新人工评价'
        assert {r.id:(r.room_number,r.building,r.confirmed_candidate_id,r.is_archived,r.draft_json)
                for r in LectureSiteCapture.query.all()}==snapshots
        assert {p.name for p in Path(case.app.config['LECTURE_CAPTURE_FOLDER']).glob('*.jpg')}==files


def test_photo_upload_losing_draft_creation_race_is_conflict_and_removes_uncommitted_photo(setup_capture):
    case=setup_capture
    uid=case.user.id
    entered,release=threading.Event(),threading.Event()
    original=site_capture._draft
    result={}

    def paused_draft(user):
        value=original(user)
        if threading.current_thread().name=='older-first-photo':
            entered.set();assert release.wait(10)
        return value

    def older_upload():
        client=case.app.test_client()
        with client.session_transaction() as session:session.update(user_id=uid,user_role='信息员')
        try:
            with mock.patch('app.services.lecture_site_capture.recognize_door',return_value={
                    'room_number':'302','status':'recognized','alternatives':['302']}):
                ret=client.post('/user/api/site-capture',data={'photo':(photo(),'first.jpg')})
            result.update(status=ret.status_code,body=ret.get_json())
        except Exception as error:result['error']=repr(error)

    with mock.patch.object(site_capture,'_draft',side_effect=paused_draft):
        thread=threading.Thread(target=older_upload,name='older-first-photo');thread.start()
        try:
            assert entered.wait(10)
            winner=case.client.put('/user/api/lecture_form_draft',json={'data':{'course_feedback':'较新创建评价'}})
            assert winner.status_code==200
        finally:
            release.set();thread.join(10)
    assert not thread.is_alive()
    assert 'error' not in result,result
    assert result['status']==409,result
    assert result['body']['code']=='draft_conflict'
    db.session.expire_all()
    assert case.client.get('/user/api/lecture_form_draft').get_json()['data']['course_feedback']=='较新创建评价'
    assert LectureSiteCapture.query.count()==0
    assert not list(Path(case.app.config['LECTURE_CAPTURE_FOLDER']).glob('*.jpg'))
