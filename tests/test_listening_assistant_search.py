from datetime import date
from types import SimpleNamespace

import pytest

from app.services.listening_assistant_contracts import AssistantQuery, ScheduleEntry
from app.services.teaching_calendar import TeachingCalendarConfig
from app.services.listening_assistant import ListeningAssistantService


LOOKUP_DATE = date(2026, 9, 18)  # Friday, teaching week 2 in the configured fixture.
SEMESTER = '2026-2027-1'


class LoaderSpy:
    def __init__(self, primary=(), backup=()):
        self.primary = list(primary)
        self.backup = list(backup)
        self.calls = []

    def __call__(
        self,
        *,
        source_kind='primary',
        semester=None,
        source_batch_id=None,
        batch_id=None,
    ):
        self.calls.append({
            'source_kind': source_kind,
            'semester': semester,
            'source_batch_id': source_batch_id,
            'batch_id': batch_id,
        })
        return list(self.primary if source_kind == 'primary' else self.backup)


def entry(
    row,
    *,
    room='08-0309',
    teacher='张三',
    course='数据结构',
    course_code='C001',
    student_class='2024级计算机1班',
    period=(3, 4),
    lecture_date=LOOKUP_DATE,
    weekday=5,
    source_kind='primary',
    source_batch_id='batch-current',
):
    return ScheduleEntry(
        entry_id=f'{source_kind}:{source_batch_id}:{row}',
        lecture_date=lecture_date,
        room=room,
        period=period,
        course_code=course_code,
        course_title=course,
        teacher_name=teacher,
        teacher_college='计算机学院',
        student_grade_class=student_class,
        weekday=weekday,
        semester=SEMESTER,
        source_kind=source_kind,
        source_batch_id=source_batch_id,
        source_row=row,
    )


def raw_week_entry(
    row,
    *,
    start_week_raw='2-4',
    weekday=5,
    lecture_date=None,
    course='数据结构',
):
    return SimpleNamespace(
        entry_id=f'primary:batch-current:{row}',
        lecture_date=lecture_date,
        room='8-309',
        period=(3, 4),
        course_code='C001',
        selection_code='S001',
        course_title=course,
        teacher_name='张三',
        teacher_college='计算机学院',
        student_grade_class='2024级计算机1班',
        weekday=weekday,
        semester=SEMESTER,
        source_kind='primary',
        source_label='当前权威课表',
        source_batch_id='batch-current',
        source_row=row,
        start_week_raw=start_week_raw,
        venue_start_week_raw=start_week_raw,
    )


def service_with(*, primary=(), backup=(), calendar=None):
    loader = LoaderSpy(primary=primary, backup=backup)
    service = ListeningAssistantService(
        schedule_loader=loader,
        calendar=calendar,
    )
    return service, loader


def test_search_matches_room_or_teacher_exactly_and_requires_both_anchors():
    rows = [
        entry(1),
        entry(2, student_class='2024级计算机2班'),
        entry(3, teacher='李四', room='8-309'),
        entry(4, teacher='李四', room='9-101'),
    ]
    service, loader = service_with(primary=rows)

    by_room = service.search(
        AssistantQuery(LOOKUP_DATE, room='08-0309'),
        semester=SEMESTER,
    )
    assert {candidate.teacher_name for candidate in by_room.candidates} == {'张三', '李四'}
    assert all(candidate.room == '8-309' for candidate in by_room.candidates)

    by_teacher = service.search(
        AssistantQuery(LOOKUP_DATE, teacher_name=' 李四 '),
        semester=SEMESTER,
    )
    assert {candidate.room for candidate in by_teacher.candidates} == {'8-309', '9-101'}

    both = service.search(
        AssistantQuery(LOOKUP_DATE, room='08-0309', teacher_name='李四'),
        semester=SEMESTER,
    )
    assert [(candidate.room, candidate.teacher_name) for candidate in both.candidates] == [
        ('8-309', '李四'),
    ]
    assert loader.calls[0]['source_kind'] == 'primary'
    assert loader.calls[0]['semester'] == SEMESTER


def test_date_matching_rejects_other_exact_dates_and_marks_weekday_only_rows_for_confirmation():
    rows = [
        entry(1, lecture_date=LOOKUP_DATE),
        entry(2, lecture_date=date(2026, 9, 19), weekday=6),
        entry(3, lecture_date=None, weekday=5, student_class='2024级计算机2班'),
        entry(4, lecture_date=None, weekday=4),
    ]
    service, _ = service_with(primary=rows)

    result = service.search(AssistantQuery(LOOKUP_DATE, room='8-309'))

    assert len(result.candidates) == 2
    assert {candidate.student_grade_class for candidate in result.candidates} == {
        '2024级计算机1班',
        '2024级计算机2班',
    }
    unresolved = [candidate for candidate in result.candidates if candidate.needs_confirmation]
    assert len(unresolved) == 1
    assert 'date_needs_confirmation' in unresolved[0].conflicts


def test_calendar_week_range_filters_inferred_dates_without_returning_other_weekdays():
    calendar = TeachingCalendarConfig(
        first_week_date=date(2026, 9, 7),
        week_start_day=0,
        total_weeks=20,
    )
    rows = [
        raw_week_entry(1, start_week_raw='2-4', weekday=5, course='命中课程'),
        raw_week_entry(2, start_week_raw='1', weekday=5, course='错误周课程'),
        raw_week_entry(3, start_week_raw='2-4', weekday=4, course='错误星期课程'),
    ]
    service, _ = service_with(primary=rows, calendar=calendar)

    result = service.search(AssistantQuery(LOOKUP_DATE, room='8-309'))

    assert [candidate.course_title for candidate in result.candidates] == ['命中课程']
    assert result.candidates[0].needs_confirmation is False


def test_period_mismatch_is_kept_with_a_stable_warning_and_invalid_rows_are_skipped():
    mismatch = entry(1, period=(7, 8))
    invalid = SimpleNamespace(**{
        **mismatch.__dict__,
        'entry_id': 'primary:batch-current:2',
        'period': 'bad',
        'period_raw': 'bad',
        'source_row': 2,
    })
    service, _ = service_with(primary=[mismatch, invalid])

    result = service.search(
        AssistantQuery(LOOKUP_DATE, room='8-309', period=(3, 4)),
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].conflicts == ('period_mismatch',)
    assert result.candidates[0].needs_confirmation is True


def test_exact_visible_duplicates_are_deduped_but_class_variants_and_rejections_remain_distinct():
    rows = [
        entry(1),
        entry(2),
        entry(3, student_class='2024级计算机2班'),
    ]
    service, _ = service_with(primary=rows)
    query = AssistantQuery(LOOKUP_DATE, room='8-309')

    first = service.search(query)
    assert len(first.candidates) == 2
    assert {candidate.student_grade_class for candidate in first.candidates} == {
        '2024级计算机1班',
        '2024级计算机2班',
    }

    rejected_id = first.candidates[0].candidate_id
    after_rejection = service.search(query, rejected_ids=[rejected_id])
    assert len(after_rejection.candidates) == 1
    assert rejected_id not in {candidate.candidate_id for candidate in after_rejection.candidates}


def test_primary_search_never_queries_backup_or_legacy_rows_and_backup_is_explicit():
    primary = entry(1, teacher='当前教师')
    backup = entry(
        1,
        teacher='备用教师',
        source_kind='backup',
        source_batch_id='retired-7',
    )
    service, loader = service_with(primary=[], backup=[backup])
    query = AssistantQuery(LOOKUP_DATE, teacher_name='备用教师')

    primary_result = service.search(query, semester=SEMESTER)
    assert primary_result.candidates == ()
    assert primary_result.source_kind == 'primary'
    assert primary_result.always_show_none is True
    assert [call['source_kind'] for call in loader.calls] == ['primary']

    with pytest.raises(ValueError, match='retired batch'):
        service.search_backup(query, semester=SEMESTER, reason='no_result')
    assert [call['source_kind'] for call in loader.calls] == ['primary']

    backup_result = service.search_backup(
        query,
        source_batch_id='retired-7',
        semester=SEMESTER,
        explicit_fallback=True,
        reason='no_result',
    )
    assert len(backup_result.candidates) == 1
    candidate = backup_result.candidates[0]
    assert candidate.source_kind == 'backup'
    assert candidate.source_label == '备用课表线索 · 需核对'
    assert candidate.needs_confirmation is True
    assert loader.calls[-1] == {
        'source_kind': 'backup',
        'semester': SEMESTER,
        'source_batch_id': 'retired-7',
        'batch_id': None,
    }


@pytest.mark.parametrize(
    ('reason', 'normalized_reason'),
    (
        ('no_result', 'no_result'),
        ('rejected_candidates', 'rejected_candidates'),
        ('  no_result  ', 'no_result'),
    ),
)
def test_backup_accepts_only_approved_fallback_reasons_after_normalization(
    reason,
    normalized_reason,
):
    backup = entry(
        1,
        teacher='备用教师',
        source_kind='backup',
        source_batch_id='retired-7',
    )
    service, loader = service_with(backup=[backup])

    result = service.search_backup(
        AssistantQuery(LOOKUP_DATE, teacher_name='备用教师'),
        source_batch_id='retired-7',
        semester=SEMESTER,
        explicit_fallback=True,
        reason=reason,
    )

    assert result.fallback_reason == normalized_reason
    assert loader.calls[-1]['source_kind'] == 'backup'


@pytest.mark.parametrize(
    'reason',
    (None, '', 'unknown', 'NO_RESULT', 'no result', 123, object()),
)
def test_backup_rejects_missing_or_invalid_fallback_reasons_before_loading(reason):
    service, loader = service_with()

    with pytest.raises(ValueError, match='reason'):
        service.search_backup(
            AssistantQuery(LOOKUP_DATE, teacher_name='备用教师'),
            source_batch_id='retired-7',
            semester=SEMESTER,
            explicit_fallback=True,
            reason=reason,
        )

    assert loader.calls == []


def test_search_by_teacher_delegates_to_search_and_result_is_always_safe_to_decline():
    rows = [entry(1, teacher='赵老师')]
    service, _ = service_with(primary=rows)

    result = service.search_by_teacher(
        AssistantQuery(LOOKUP_DATE, teacher_name='赵老师'),
        semester=SEMESTER,
    )
    assert len(result.candidates) == 1
    assert result.always_show_none is True
    assert result.to_public_dict()['always_show_none'] is True

    empty = service.search(
        AssistantQuery(LOOKUP_DATE, teacher_name='不存在'),
        semester=SEMESTER,
    )
    assert empty.candidates == ()
    assert empty.always_show_none is True


def test_public_candidates_contain_no_personal_fields():
    service, _ = service_with(primary=[entry(1)])

    result = service.search(AssistantQuery(LOOKUP_DATE, room='8-309'))
    payload = result.to_public_dict()
    candidate_payload = payload['candidates'][0]

    assert 'phone' not in candidate_payload
    assert 'student_phone' not in candidate_payload
    assert 'student_signature1' not in candidate_payload
    assert 'student_signature2' not in candidate_payload
    assert set(candidate_payload) <= {
        'candidate_id',
        'lecture_date',
        'room',
        'period',
        'weekday',
        'course_code',
        'course_title',
        'teacher_name',
        'teacher_college',
        'student_grade_class',
        'source_kind',
        'source_label',
        'source_batch_id',
        'conflicts',
        'needs_confirmation',
    }
