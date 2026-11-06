"""Calendar and identity boundaries for the post-deployment assistant audit."""
from dataclasses import replace
from datetime import date
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from app.models import SystemSetting
from app.services.listening_assistant import ListeningAssistantService
from app.services.listening_assistant_contracts import AssistantQuery, ScheduleEntry
from app.services.teaching_calendar import TeachingCalendarConfig, teaching_week_number
from tests.test_schedule_calendar_separation import calendar_db


SCHOOL = TeachingCalendarConfig(date(2026, 8, 31), 0, 20)


def row(*, weekday=1, weeks='1-20周', **overrides):
    values = dict(entry_id='primary:boundary:1', weekday=weekday,
                  start_week_raw=weeks, venue_start_week_raw=weeks,
                  room='38-204', period='第1-2节', course_code='C1',
                  selection_code='S1', course_title='边界测试课程', teacher_name='测试教师',
                  student_grade_class='2026级测试1班', semester='2026-2027-1',
                  source_kind='primary', source_batch_id='boundary', source_row=1)
    values.update(overrides)
    return ScheduleEntry(**values)


def search(rows, day, **query):
    service = ListeningAssistantService(schedule_loader=lambda **kwargs: rows, calendar=SCHOOL)
    return service.search(AssistantQuery(day, room='38-204', **query)).candidates


@pytest.mark.parametrize(('day', 'week'), [
    (date(2026, 8, 30), None),
    (date(2026, 8, 31), 1),
    (date(2026, 11, 1), 9),
    (date(2026, 11, 2), 10),
    (date(2026, 11, 6), 10),
    (date(2026, 11, 7), 10),
    (date(2026, 11, 8), 10),
    (date(2026, 11, 9), 11),
    (date(2027, 1, 17), 20),
    (date(2027, 1, 18), None),
    (date(2027, 8, 30), None),
])
def test_school_week_rollover_and_term_boundaries(day, week):
    assert teaching_week_number(day, SCHOOL) == week
    candidates = search([row(weekday=day.isoweekday())], day)
    assert len(candidates) == (0 if week is None else 1)


@pytest.mark.parametrize(('weeks', 'day', 'expected'), [
    ('10-12周', date(2026, 11, 2), True),
    ('10-12周', date(2026, 11, 16), True),
    ('10-12周', date(2026, 10, 26), False),
    ('10-12周', date(2026, 11, 23), False),
    ('9-11周(单)', date(2026, 11, 2), False),
    ('9-11周(单)', date(2026, 11, 9), True),
    ('10-12周(双)', date(2026, 11, 2), True),
    ('10-12周(双)', date(2026, 11, 9), False),
    ('1-3周(单),4-6周(双),9-11周(单),10-12周(双)', date(2026, 11, 2), True),
    ('第10周', date(2026, 11, 2), True),
    ('第10周', date(2026, 11, 9), False),
])
def test_inclusive_ranges_and_segment_specific_parity(weeks, day, expected):
    assert bool(search([row(weeks=weeks)], day)) is expected


def test_correct_week_never_overrides_wrong_weekday():
    assert search([row(weekday=4, weeks='10周')], date(2026, 11, 6)) == ()


def test_room_teacher_and_class_remain_distinct_for_same_course_title():
    first = row(weekday=5, weeks='10周')
    other_class = replace(first, entry_id='primary:boundary:2', source_row=2,
                          selection_code='S2', student_grade_class='2026级测试2班')
    other_teacher = replace(first, entry_id='primary:boundary:3', source_row=3,
                            teacher_name='另一教师')
    other_room = replace(first, entry_id='primary:boundary:4', source_row=4, room='38-205')
    found = search([first, other_class, other_teacher, other_room], date(2026, 11, 6), teacher_name='测试教师')
    assert len(found) == 2
    assert len({c.candidate_id for c in found}) == 2
    found = search([first, other_class], date(2026, 11, 6),
                   teacher_name='测试教师', student_grade_class='2026级测试2班')
    assert len(found) == 1 and found[0].selection_code == 'S2'


def test_rongchang_room_prefix_is_not_removed_or_matched_to_beibei_room():
    rongchang = row(weekday=5, weeks='10周', room='荣昌9204')
    service = ListeningAssistantService(schedule_loader=lambda **kwargs: [rongchang], calendar=SCHOOL)
    assert service.search(AssistantQuery(date(2026, 11, 6), room='9-204')).candidates == ()
    found = service.search(AssistantQuery(date(2026, 11, 6), room='荣昌9204')).candidates
    assert len(found) == 1 and found[0].room == '荣昌9204'


@pytest.mark.parametrize('raw', ['2026-11-31', '2026-02-29', '11-06', '2026/11/06'])
def test_date_contract_rejects_invalid_or_yearless_lookup_dates(raw):
    with pytest.raises(ValueError):
        AssistantQuery(raw, room='38-204')


def test_same_service_rereads_calendar_after_admin_setting_change(calendar_db):
    SystemSetting.set('schedule_first_week_monday', '2026-08-31')
    service = ListeningAssistantService(schedule_loader=lambda **kwargs: [row(weekday=5, weeks='10周')])
    query = AssistantQuery(date(2026, 11, 6), room='38-204')
    assert len(service.search(query).candidates) == 1
    SystemSetting.set('schedule_first_week_monday', '2026-09-07')
    assert service.search(query).candidates == ()
    SystemSetting.set('schedule_first_week_monday', '2026-08-31')
    assert len(service.search(query).candidates) == 1


def test_unconfigured_calendar_does_not_claim_date_is_verified(calendar_db):
    SystemSetting.set('teaching_first_week_monday', '')
    service = ListeningAssistantService(schedule_loader=lambda **kwargs: [row(weekday=5, weeks='10周')])
    found = service.search(AssistantQuery(date(2026, 11, 6), room='38-204')).candidates
    assert len(found) == 1
    assert found[0].needs_confirmation
    assert 'date_needs_confirmation' in found[0].conflicts


@pytest.mark.parametrize(('selected_day', 'expected'), [
    ('2026-11-08', '2026/11/08星期日'),
    ('2026-11-09', '2026/11/09星期一'),
])
@pytest.mark.parametrize('hour', [0, 7])
def test_selected_date_display_preserves_sunday_monday_in_shanghai(selected_day, expected, hour):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for selected-date execution')
    source = (Path(__file__).resolve().parents[1] / 'app/templates/user/lecture_form.html').read_text(encoding='utf-8')
    function = re.search(r'function updateDateWithWeekday\(\) \{.*?\n\}', source, re.DOTALL)
    assert function is not None
    script = """
const NativeDate = Date;
const now = new NativeDate(2026, 10, 9, HOUR, 30);
global.Date = class extends NativeDate {
    constructor(...args) { super(...(args.length ? args : [now.getTime()])); }
};
const fields = {
    lecture_date: {value: SELECTED},
    lecture_date_display: {value: ''},
    'date-display': {hidden: false, textContent: ''},
};
const document = {getElementById: id => fields[id]};
FUNCTION
updateDateWithWeekday();
process.stdout.write(JSON.stringify(fields.lecture_date_display.value));
""".replace('HOUR', str(hour)).replace('SELECTED', json.dumps(selected_day)).replace('FUNCTION', function.group(0))
    result = subprocess.run([node, '-e', script], env={**os.environ, 'TZ': 'Asia/Shanghai'},
                            capture_output=True, text=True, encoding='utf-8', timeout=15, check=True)
    assert json.loads(result.stdout) == expected
