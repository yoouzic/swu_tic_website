"""A timetable row can contain separate meetings with a gap between them."""
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from app.app import app
from app.models import db, ListeningAssistantScheduleEntry, ScheduleImportRow, SystemSetting, User
from app.services.listening_assistant import ListeningAssistantService
from app.services.listening_assistant_contracts import AssistantQuery, ScheduleEntry
from app.services.listening_assistant_schedule import persist_listening_assistant_entries
from app.services.schedule_snapshots import persist_import_snapshot
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

LOOKUP_DATE = date(2026, 10, 13)
SEMESTER = '2026-2027-1'


def timetable_row(main, venue=None, *, normalized=None, room='32-302'):
    return ScheduleEntry(
        entry_id='primary:multi:42', lecture_date=LOOKUP_DATE,
        room=room, period=normalized, period_raw=main, venue_period_raw=venue,
        course_code='C001', selection_code='S001', course_title='测试课程',
        teacher_name='测试教师', teacher_college='测试学院', weekday=2,
        student_grade_class='2025级测试班', semester=SEMESTER,
        source_batch_id='multi', source_row=42,
    )


def search_row(row, *, period=None, rejected_ids=()):
    service = ListeningAssistantService(schedule_loader=lambda **kwargs: [row])
    return service.search(AssistantQuery(LOOKUP_DATE, room='32-302', period=period),
                          rejected_ids=rejected_ids)


@pytest.mark.parametrize(('raw', 'expected'), [
    ('1-5节,7-11节', [(1, 5), (7, 11)]),
    ('第1-5节,第7-11节', [(1, 5), (7, 11)]),
    ('5节,10-11节', [(5, 5), (10, 11)]),
    ('１－３节，７－９节', [(1, 3), (7, 9)]),
])
def test_disjoint_periods_become_separate_candidates(raw, expected):
    result = search_row(timetable_row(raw, raw))
    assert [c.period for c in result.candidates] == expected
    assert all(c.conflicts == () and not c.needs_confirmation for c in result.candidates)
    assert result.skipped_invalid_rows == 0


def test_gap_is_not_an_invented_teaching_period():
    result = search_row(timetable_row('1-5节,7-11节'), period=(6, 6))
    assert [c.period for c in result.candidates] == [(1, 5), (7, 11)]
    assert all(c.conflicts == ('period_mismatch',) for c in result.candidates)


def test_venue_multi_periods_take_precedence_over_main_and_cached_period():
    row = timetable_row('第3-4节', '第1-5节,第7-11节', normalized=(3, 4))
    assert [c.period for c in search_row(row).candidates] == [(1, 5), (7, 11)]


def test_raw_multi_periods_take_precedence_over_a_cached_envelope():
    row = timetable_row('1-5节,7-11节', normalized=(1, 11))
    assert [c.period for c in search_row(row).candidates] == [(1, 5), (7, 11)]


@pytest.mark.parametrize('raw', ['1-3节,垃圾','1-3节,,7-9节'])
def test_bad_multi_periods_do_not_reappear_as_a_cached_envelope(raw):
    result = search_row(timetable_row(raw, normalized=(1,11)))
    assert result.candidates == ()


def test_duplicate_segments_with_unknown_room_still_require_location_confirmation():
    row=timetable_row('1-3节,1-3节',room='')
    service=ListeningAssistantService(schedule_loader=lambda **kwargs:[row])
    result=service.search(AssistantQuery(LOOKUP_DATE,teacher_name='测试教师'))
    assert len(result.candidates) == 1
    assert result.candidates[0].conflicts == ('location_needs_confirmation',)


def test_malformed_venue_still_requires_confirmation_for_each_main_segment():
    result = search_row(timetable_row('1-3节,7-9节', 'not-a-period'))
    assert [c.period for c in result.candidates] == [(1, 3), (7, 9)]
    assert all(c.conflicts == ('venue_period_needs_confirmation',) for c in result.candidates)


@pytest.mark.parametrize('venue_raw', ['1-5节,垃圾', '1-5节,,7-11节'])
def test_invalid_venue_segments_do_not_use_a_cached_gap_envelope(venue_raw):
    original = timetable_row('1-5节,7-11节', venue_raw, normalized=(1, 11))
    row = SimpleNamespace(**vars(original), venue_period_start=1, venue_period_end=11)
    result = search_row(row, period=(6, 6))
    assert [c.period for c in result.candidates] == [(1, 5), (7, 11)]
    assert all(c.conflicts == ('venue_period_needs_confirmation', 'period_mismatch')
               for c in result.candidates)


@pytest.mark.parametrize('venue_raw', ['1-5节,垃圾', '1-5节,,7-11节'])
def test_invalid_venue_cache_is_rejected_when_no_primary_period_is_available(venue_raw):
    original = timetable_row(None, venue_raw)
    row = SimpleNamespace(**vars(original), venue_period_start=1, venue_period_end=11)
    result = search_row(row, period=(6, 6))
    assert result.candidates == ()
    assert result.skipped_invalid_rows == 1


def test_segment_ids_are_stable_under_reordering_and_duplicate_segments():
    first = search_row(timetable_row('1-3节,7-9节'))
    second = search_row(timetable_row('7-9节,1-3节,7-9节'))
    assert len(first.candidates) == 2
    assert [c.candidate_id for c in first.candidates] == [c.candidate_id for c in second.candidates]
    assert len({c.candidate_id for c in first.candidates}) == 2
    rejected = search_row(timetable_row('1-3节,7-9节'), rejected_ids=[first.candidates[0].candidate_id])
    assert [c.period for c in rejected.candidates] == [(7, 9)]


@pytest.mark.parametrize('raw', ['1-3节,0节', '3-1节,7-9节', '1-3节,21节',
                                      '1-3节,,7-9节', '1-3节,垃圾', '6,3'])
def test_bad_segments_are_not_silently_dropped(raw):
    result = search_row(timetable_row(raw))
    assert result.candidates == ()
    assert result.skipped_invalid_rows == 1


@pytest.mark.parametrize('raw', ['3,4', '第3、4节', '0304', '第3-4节'])
def test_existing_single_range_syntax_keeps_its_candidate_id(raw):
    expected = search_row(timetable_row('第3-4节')).candidates
    actual = search_row(timetable_row(raw)).candidates
    assert [c.period for c in actual] == [(3, 4)]
    assert [c.candidate_id for c in actual] == [c.candidate_id for c in expected]


@pytest.fixture
def multi_period_client(tmp_path, request):
    configure_sqlite_database(app, db, tmp_path / 'multi-period.sqlite')
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.create_all()
        user=User(number='MULTI',student_id='multi',name='测试信息员',role='信息员',
                  department='测试部门',group='测试组',gender='-',grade='2025',
                  college='测试学院',major='-',dormitory='-',phone='-',qq='-',
                  password_hash='unused',is_active=True)
        db.session.add(user)
        frame=pd.DataFrame([{'姓名':'测试教师','教师所属学院':'测试学院',
            '课程名称':'测试课程','课程号':'C001','选课课号':'S001',
            '星期几':2,'上课节次':'1-5节,7-11节',
            '场地上课节次':'第1-5节,第7-11节',
            '场地名称':getattr(request,'param','32-302'),
            '上课地点':'32-302;8-410',
            '教学班组成':'2025级测试班','起始周':'1-20周','场地上课起始周':'1-20周',
            '学期':SEMESTER,'学年':'2026-2027'}])
        batches=persist_import_snapshot(frame,source_filename='multi.xlsx',source_sha256='f'*64)
        persist_listening_assistant_entries(frame,batches)
        SystemSetting.set('teaching_current_semester',SEMESTER)
        SystemSetting.set('teaching_first_week_monday','2026-08-31')
        SystemSetting.set('teaching_total_weeks','20')
        db.session.commit()
        client=app.test_client()
        with client.session_transaction() as session:
            session.update(user_id=user.id,user_role=user.role,user_name=user.name)
        yield client
        cleanup_sqlite_database(db,drop_all=True)


def test_existing_index_with_null_bounds_can_search_and_confirm_second_segment(multi_period_client):
    # Old indexes retained both raw fields even though they could not parse bounds.
    stored=ListeningAssistantScheduleEntry.query.one()
    assert stored.period_start is None and stored.venue_period_start is None
    r=multi_period_client.get('/user/api/listening-assistant/candidates',query_string={
        'date':LOOKUP_DATE.isoformat(),'room':'32-302','period':'7-11'})
    assert r.status_code == 200
    candidates=r.get_json()['data']['candidates']
    second=next((c for c in candidates if c['period']==[7,11]),None)
    assert second is not None
    r=multi_period_client.post('/user/api/listening-assistant/confirm',json={
        'query':{'date':LOOKUP_DATE.isoformat(),'room':'32-302','period':[7,11]},
        'candidate_id':second['candidate_id'],'source_kind':'primary',
        'semester':SEMESTER,'stage':'confirmed','overrides':{},'template_version':'multi-v1'})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()['data']['field_snapshot']['class_period'] == '第7-11节'


@pytest.mark.parametrize('venue_raw', ['第1-5节,垃圾', '第1-5节,,第7-11节'])
def test_invalid_venue_cache_does_not_hide_valid_main_periods_in_loader(multi_period_client, venue_raw):
    stored = ListeningAssistantScheduleEntry.query.one()
    stored.venue_period_raw = venue_raw
    stored.venue_period_start = 1
    stored.venue_period_end = 11
    db.session.commit()
    r = multi_period_client.get('/user/api/listening-assistant/candidates', query_string={
        'date': LOOKUP_DATE.isoformat(), 'room': '32-302', 'period': '6'})
    assert r.status_code == 200
    candidates = r.get_json()['data']['candidates']
    assert [c['period'] for c in candidates] == [[1, 5], [7, 11]]
    assert all(c['conflicts'] == ['venue_period_needs_confirmation', 'period_mismatch']
               for c in candidates)


def test_confirmation_in_a_gap_requires_explicit_period_override(multi_period_client):
    r=multi_period_client.get('/user/api/listening-assistant/candidates',query_string={
        'date':LOOKUP_DATE.isoformat(),'room':'32-302','period':'6'})
    candidates=r.get_json()['data']['candidates']
    assert len(candidates) == 2
    assert all(c['conflicts']==['period_mismatch'] for c in candidates)
    r=multi_period_client.post('/user/api/listening-assistant/confirm',json={
        'query':{'date':LOOKUP_DATE.isoformat(),'room':'32-302','period':[6,6]},
        'candidate_id':candidates[0]['candidate_id'],'source_kind':'primary',
        'semester':SEMESTER,'stage':'confirmed','overrides':{},'template_version':'multi-v1'})
    assert r.status_code == 400
    assert r.get_json()['success'] is False


@pytest.mark.parametrize('multi_period_client',[None],indirect=True)
def test_missing_row_room_does_not_borrow_a_different_meeting_room(multi_period_client):
    # The old index fell back to the offering-level location aggregate.
    assert ListeningAssistantScheduleEntry.query.one().location_raw == '32-302;8-410'
    r=multi_period_client.get('/user/api/listening-assistant/candidates',query_string={
        'date':LOOKUP_DATE.isoformat(),'room':'32-302'})
    assert r.get_json()['data']['candidates'] == []
    r=multi_period_client.get('/user/api/listening-assistant/candidates',query_string={
        'date':LOOKUP_DATE.isoformat(),'teacher':'测试教师'})
    candidates=r.get_json()['data']['candidates']
    assert [c['period'] for c in candidates] == [[1,5],[7,11]]
    assert all(c['room']=='' and c['conflicts']==['location_needs_confirmation'] for c in candidates)
    payload={'query':{'date':LOOKUP_DATE.isoformat(),'teacher':'测试教师','period':[7,11]},
             'candidate_id':candidates[1]['candidate_id'],'source_kind':'primary',
             'semester':SEMESTER,'stage':'confirmed','overrides':{},'template_version':'multi-v1'}
    r=multi_period_client.post('/user/api/listening-assistant/confirm',json=payload)
    assert r.status_code == 400
    payload['overrides']={'lecture_location':'实际核对的32-302'}
    r=multi_period_client.post('/user/api/listening-assistant/confirm',json=payload)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()['data']['field_snapshot']['lecture_location'] == '实际核对的32-302'


def test_missing_snapshot_row_does_not_restore_the_index_fallback_room(multi_period_client):
    ScheduleImportRow.query.delete()
    db.session.commit()
    r=multi_period_client.get('/user/api/listening-assistant/candidates',query_string={
        'date':LOOKUP_DATE.isoformat(),'teacher':'测试教师'})
    candidates=r.get_json()['data']['candidates']
    assert len(candidates) == 2
    assert all(c['room']=='' and c['conflicts']==['location_needs_confirmation'] for c in candidates)
