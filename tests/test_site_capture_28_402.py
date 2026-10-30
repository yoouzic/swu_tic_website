"""Route-level replay of the 28-402 photo clarification and confirmation flow."""
import json
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from app.models import LectureSiteCapture, db
from app.services.campus_buildings import load_buildings, representative_point
from app.services.listening_assistant import ListeningAssistantService
from app.services.listening_assistant_contracts import ScheduleEntry
from tests.test_lecture_site_capture import photo, setup_capture, upload


def scene(case, *, located=False):
    instant = datetime(2026, 10, 14, 15, 48, 55, tzinfo=timezone(timedelta(hours=8)))
    with mock.patch('app.services.lecture_site_capture.now_at_site', return_value=instant):
        public = upload(case).get_json()['data']
    record = db.session.get(LectureSiteCapture, public['id'])
    record.client_captured_at = instant.isoformat()
    record.room_number = None
    record.location_json = '{}'
    if located:
        feature = next(f for f in load_buildings()['features'] if f['properties']['code'] == '28')
        lon, lat = representative_point(feature)
        record.location_json = json.dumps({'latitude': lat, 'longitude': lon, 'accuracy': 35})
    record.ocr_alternatives_json = json.dumps({'hypotheses': [
        {'value': '402', 'score': .7926047046979269, 'method': 'region_single_line'}]})
    db.session.commit()
    rows = []
    for index, building in enumerate(('5', '8', '10', '27', '28', '30', '31', '32'), 1):
        rows.append(ScheduleEntry(
            entry_id=f'scene-{index}', source_row=index, source_batch_id='1',
            lecture_date=date(2026, 10, 14), room=f'{building}-402', period=(7, 9),
            course_title='数据结构' if building == '28' else f'{building}教课程',
            teacher_name='王智' if building == '28' else f'教师{index}',
            teacher_college='测试学院', student_grade_class='2025级测试班',
            semester='2025-2026-2'))
    return record, ListeningAssistantService(schedule_loader=lambda **kwargs: rows, semester='2025-2026-2')


def test_missing_location_asks_all_buildings_then_confirms_28_402(setup_capture):
    case = setup_capture
    record, service = scene(case)
    base = f'/user/api/site-capture/{record.id}'
    with mock.patch('app.blueprints.user.site_capture.ListeningAssistantService', return_value=service), \
         mock.patch('app.services.listening_assistant_evidence.ListeningAssistantService', return_value=service), \
         mock.patch('app.blueprints.user.site_capture.get_current_teaching_semester', return_value='2025-2026-2'):
        result = case.client.post(base + '/suggestions', json={'facts': {}, 'asked': [], 'question_count': 0}).get_json()['data']
        assert result['guidance']['mode'] == 'clarify'
        question = result['guidance']['question']
        assert question['kind'] == 'building'
        assert len(question['options']) == 8
        assert '28' in {option['value'] for option in question['options']}
        assert result['question'] == question
        assert record.building is None

        result = case.client.post(base + '/suggestions', json={
            'facts': {'building': '28'}, 'asked': ['building'], 'question_count': 1}).get_json()['data']
        assert result['guidance']['mode'] == 'recommend'
        assert [candidate['room'] for candidate in result['candidates']] == ['28-402']
        chosen = result['candidates'][0]
        assert chosen['course_title'] == '数据结构'
        assert case.client.patch(base, json={'building': '28', 'room_number': '402'}).status_code == 200
        confirmed = case.client.post(base + '/confirm', json={'candidate_id': chosen['candidate_id']})
        assert confirmed.status_code == 200, confirmed.get_json()
        assert confirmed.get_json()['data']['course_confirmed'] is True
        draft = case.client.get('/user/api/lecture_form_draft').get_json()['data']
        assert draft['lecture_location'] == '28-402'
        assert draft['course_title'] == '数据结构'
        assert draft['teacher_name'] == '王智'
        assert (draft['start_period'], draft['end_period']) == ('7', '9')
        assert json.loads(record.location_json) == {}


def test_reliable_28_location_prioritizes_correct_course_without_confirming_it(setup_capture):
    case = setup_capture
    record, service = scene(case, located=True)
    with mock.patch('app.blueprints.user.site_capture.ListeningAssistantService', return_value=service):
        response = case.client.post(f'/user/api/site-capture/{record.id}/suggestions',
                                   json={'facts': {}, 'asked': [], 'question_count': 0})
    assert response.status_code == 200
    result = response.get_json()['data']
    assert result['candidates'][0]['room'] == '28-402'
    assert result['guidance']['mode'] == 'clarify'
    question = result['guidance']['question']
    assert question['kind'] == 'building'
    # 27 teaching building is physically adjacent to 28; retain confirmation
    # instead of turning the GPS ranking into automatic course acceptance.
    assert {option['value'] for option in question['options']} == {'28', '27'}
    assert len(result['candidates']) == 8
    assert record.confirmed_candidate_id is None


def test_delayed_location_keeps_only_timestamped_photo_window_readings(setup_capture):
    case = setup_capture
    record, _ = scene(case)
    instant = datetime.fromisoformat(record.received_at)
    photo_ms = instant.timestamp() * 1000
    point = {'latitude': 29.82, 'longitude': 106.43, 'accuracy': 25, 'timestamp': photo_ms + 2000}
    incoming = {**point, 'samples': [point, {**point, 'timestamp': photo_ms + 31_000}]}
    with mock.patch('app.services.lecture_site_capture.now_at_site', return_value=instant + timedelta(seconds=31)):
        response = case.client.patch(f'/user/api/site-capture/{record.id}/location', json={'location': incoming})
    assert response.status_code == 200, response.get_json()
    saved = json.loads(record.location_json)
    assert saved['accuracy'] == 25
    assert [p['timestamp'] for p in saved['samples']] == [photo_ms + 2000]


def test_late_location_rejects_a_new_room_reading_and_timestamp_free_payload(setup_capture):
    case = setup_capture
    record, _ = scene(case)
    instant = datetime.fromisoformat(record.received_at)
    point = {'latitude': 29.82, 'longitude': 106.43, 'accuracy': 25}
    with mock.patch('app.services.lecture_site_capture.now_at_site', return_value=instant + timedelta(seconds=31)):
        path = f'/user/api/site-capture/{record.id}/location'
        assert case.client.patch(path, json={'location': point}).status_code == 400
        assert case.client.patch(path, json={'location': {**point, 'timestamp': (instant.timestamp() + 31) * 1000}}).status_code == 400
    assert record.location_json == '{}'


def test_capture_metadata_identifies_the_photo_attempt_for_recovery(setup_capture):
    case = setup_capture
    record, _ = scene(case)
    public = case.client.get(f'/user/api/site-capture/{record.id}').get_json()['data']
    assert public['client_captured_at'] == record.client_captured_at


def test_initial_upload_keeps_photo_window_samples_after_a_slow_post(setup_capture):
    case = setup_capture
    captured = datetime(2026, 10, 14, 15, 48, 55, tzinfo=timezone(timedelta(hours=8)))
    point = {'latitude': 29.82, 'longitude': 106.43, 'accuracy': 25,
             'timestamp': captured.timestamp() * 1000 - 4000}
    with mock.patch('app.services.lecture_site_capture.now_at_site', return_value=captured + timedelta(seconds=31)), \
         mock.patch('app.services.lecture_site_capture.recognize_door', return_value={
             'room_number': '402', 'status': 'recognized', 'alternatives': ['402']}):
        response = case.client.post('/user/api/site-capture', data={
            'photo': (photo(), 'capture.jpg'), 'captured_at': captured.isoformat(),
            'location': json.dumps({**point, 'samples': [point]})}, content_type='multipart/form-data')
    assert response.status_code == 201, response.get_json()
    record = db.session.get(LectureSiteCapture, response.get_json()['data']['id'])
    saved = json.loads(record.location_json)
    assert saved['accuracy'] == 25
    assert [p['timestamp'] for p in saved['samples']] == [point['timestamp']]
