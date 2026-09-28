import os
import json
from dataclasses import asdict, replace
from fractions import Fraction
from pathlib import Path
import subprocess
import sys
from datetime import date

import pytest

from app.services.listening_assistant_contracts import (
    AssistantQuery,
    Candidate,
    ConfirmationResult,
    ScheduleEntry,
    normalize_class_for_display,
    normalize_room,
    overlap_periods,
    parse_period,
    stable_candidate_id,
)


def test_contract_module_imports_without_site_packages_or_review_automation_runtime():
    repo_root = Path(__file__).resolve().parents[1]
    child_environment = os.environ.copy()
    child_environment['PYTHONPATH'] = os.pathsep.join(
        filter(None, [str(repo_root), child_environment.get('PYTHONPATH')])
    )
    result = subprocess.run(
        [
            sys.executable,
            '-S',
            '-c',
            (
                'from app.services.listening_assistant_contracts import parse_period; '
                "assert parse_period('第3-4节') == (3, 4)"
            ),
        ],
        cwd=repo_root,
        env=child_environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, (
        f'contract import failed without site-packages:\n'
        f'STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}'
    )


def test_room_normalization_handles_approved_building_variants_without_inventing_values():
    assert normalize_room('第1教学楼A101') == '1教A101'
    assert normalize_room('1教A101') == '1教A101'
    assert normalize_room('1 号教学楼 A101') == '1教A101'
    assert normalize_room('08-0309') == '8-309'
    assert normalize_room('荣昌 0309') == '荣昌 0309'


def test_class_normalization_preserves_meaningful_class_content():
    assert normalize_class_for_display('2024级计算机科学与技术1班') == (
        '2024级计算机科学与技术1班'
    )
    assert normalize_class_for_display(' ２０２４ 级 计算机科学与技术 1 班 ') == (
        '2024级计算机科学与技术1班'
    )
    assert normalize_class_for_display('软件工程（卓越）1班') == '软件工程(卓越)1班'


def test_period_parser_accepts_single_and_inclusive_range_values():
    assert parse_period('第3-4节') == (3, 4)
    assert parse_period('7') == (7, 7)
    assert parse_period('第3、4节') == (3, 4)
    assert parse_period('0102') == (1, 2)


def test_period_overlap_is_inclusive_and_rejects_unparsed_values():
    assert overlap_periods((3, 4), (4, 6)) is True
    assert overlap_periods('第3-4节', '5') is False
    assert overlap_periods((3, 4), (5, 6)) is False
    assert overlap_periods(None, (1, 2)) is False
    assert overlap_periods('not a period', (1, 2)) is False


def test_query_requires_date_and_at_least_one_lookup_anchor():
    room_query = AssistantQuery(lecture_date=date(2026, 9, 18), room='第1教学楼A101')
    teacher_query = AssistantQuery(lecture_date=date(2026, 9, 18), teacher_name=' 张老师 ')
    date_only_query = AssistantQuery(lecture_date=date(2026, 9, 18))
    both_query = AssistantQuery(
        lecture_date=date(2026, 9, 18),
        room='1教A101',
        teacher_name='张老师',
    )

    assert room_query.anchor == 'room'
    assert room_query.room == '1教A101'
    assert teacher_query.anchor == 'teacher'
    assert teacher_query.teacher_name == '张老师'
    assert date_only_query.anchor is None
    assert date_only_query.lookup_anchors == ()
    assert both_query.anchor == 'room'
    assert both_query.lookup_anchors == ('room', 'teacher')

    with pytest.raises(ValueError):
        AssistantQuery(lecture_date=None)


def test_malformed_or_empty_normalization_returns_safe_empty_or_none_values():
    assert normalize_room(None) == ''
    assert normalize_room('   ') == ''
    assert normalize_class_for_display(None) == ''
    assert parse_period(None) is None
    assert parse_period('') is None
    assert parse_period('第4-2节') is None
    assert parse_period('不是节次') is None


def test_schedule_entry_and_confirmation_contracts_keep_source_identity():
    entry = ScheduleEntry(
        entry_id='primary:batch-1:row-2',
        lecture_date=date(2026, 9, 18),
        room='第1教学楼A101',
        period='第3-4节',
        course_code='CS101',
        course_title='数据结构',
        teacher_name='张老师',
        student_grade_class='2024级计算机科学与技术1班',
        source_kind='primary',
        source_batch_id='batch-1',
        source_row=2,
        start_week_raw='1-16',
        venue_start_week_raw='1-16',
    )
    confirmation = ConfirmationResult(
        confirmed=True,
        candidate_id=entry.entry_id,
        source_kind=entry.source_kind,
        source_batch_id=entry.source_batch_id,
        overrides={'room': entry.room},
    )

    assert entry.room == '1教A101'
    assert entry.period == (3, 4)
    assert entry.start_week_raw == '1-16'
    assert entry.venue_start_week_raw == '1-16'
    assert confirmation.confirmed is True
    assert confirmation.candidate_id == 'primary:batch-1:row-2'
    assert confirmation.overrides == {'room': '1教A101'}


def test_candidate_serializes_source_and_conflicts_without_personal_data():
    candidate = Candidate(
        candidate_id='primary:batch-1:row-2',
        lecture_date=date(2026, 9, 18),
        room='8-309',
        period=(3, 4),
        course_code='CS101',
        course_title='数据结构',
        teacher_name='张老师',
        teacher_college='计算机学院',
        student_grade_class='2025计算机01班',
        source_kind='primary',
        source_label='当前权威课表',
        source_batch_id='batch-1',
        conflicts=('period_mismatch',),
        needs_confirmation=True,
    )

    payload = candidate.to_public_dict()

    assert payload['candidate_id'] == 'primary:batch-1:row-2'
    assert payload['lecture_date'] == '2026-09-18'
    assert payload['period'] == [3, 4]
    assert payload['source_kind'] == 'primary'
    assert payload['source_label'] == '当前权威课表'
    assert payload['conflicts'] == ['period_mismatch']
    assert payload['needs_confirmation'] is True
    assert 'phone' not in payload
    assert 'student_phone' not in payload
    assert 'student_signature1' not in payload
    assert 'student_signature2' not in payload
    assert set(payload) == {
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


def test_candidate_conflicts_none_is_empty_and_non_iterable_is_rejected_clearly():
    candidate = Candidate(
        candidate_id='primary:batch-1:row-2',
        lecture_date=date(2026, 9, 18),
        room='8-309',
        period=(3, 4),
        course_title='数据结构',
        teacher_name='张老师',
        conflicts=None,
    )

    assert candidate.conflicts == ()
    with pytest.raises(ValueError, match='conflicts'):
        Candidate(
            candidate_id='primary:batch-1:row-2',
            lecture_date=date(2026, 9, 18),
            room='8-309',
            period=(3, 4),
            course_title='数据结构',
            teacher_name='张老师',
            conflicts=123,
        )


@pytest.mark.parametrize(
    'conflicts',
    (
        [{'student_signature1': '签名'}],
        ({'contact_phone1': '13800000000'},),
        {'student_signature1': '签名'},
        [object()],
        [None],
    ),
)
def test_candidate_rejects_non_string_conflict_entries_before_public_serialization(conflicts):
    with pytest.raises(ValueError, match='conflicts'):
        Candidate(
            candidate_id='primary:batch-1:row-2',
            lecture_date=date(2026, 9, 18),
            room='8-309',
            period=(3, 4),
            course_title='数据结构',
            teacher_name='张老师',
            conflicts=conflicts,
        )


def test_confirmation_result_accepts_only_safe_json_override_payloads():
    source_overrides = {
        'lecture_date': '2026-09-18',
        'lecture_location': '1教A101',
        'class_period': '第3-4节',
        'student_grade_class': None,
    }
    confirmation = ConfirmationResult(
        confirmed=True,
        overrides=source_overrides,
    )

    assert confirmation.overrides == {
        'lecture_date': '2026-09-18',
        'lecture_location': '1教A101',
        'class_period': '第3-4节',
        'student_grade_class': None,
    }
    assert confirmation.overrides is not source_overrides

    period_string = ConfirmationResult(
        confirmed=True,
        overrides={'period': '第3-4节'},
    )
    period_pair = ConfirmationResult(
        confirmed=True,
        overrides={'period': [3, 4]},
    )
    assert period_string.overrides == {'period': '第3-4节'}
    assert period_pair.overrides == {'period': [3, 4]}

    invalid_overrides = (
        {'student_signature1': '签名'},
        {'contact_phone1': '13800000000'},
        {'unsupported_field': 'value'},
        {'lecture_location': {'phone': '13800000000'}},
        {'lecture_location': ['1教A101']},
        {'course_title': 123},
        {'class_period': {'value': '第3-4节'}},
        {'lecture_location': object()},
        {'class_period': (3, 4)},
        {'period': {'start': 3, 'end': 4}},
        {'period': [3, '4']},
        {'period': 'bad'},
        {'period': None},
        {'lecture_date': 'not-a-date'},
        {'lecture_date': '2026-02-30'},
        {'lecture_date': '2026-9-18'},
        {'lecture_date': date(2026, 9, 18)},
        {'class_period': float('nan')},
    )
    for overrides in invalid_overrides:
        with pytest.raises(ValueError, match='overrides'):
            ConfirmationResult(confirmed=True, overrides=overrides)


def test_confirmation_result_rejects_duplicate_override_aliases_even_when_equal():
    with pytest.raises(ValueError, match='aliases'):
        ConfirmationResult(
            confirmed=True,
            overrides={
                'room': '1教A101',
                'lecture_location': '1教A101',
            },
        )
    with pytest.raises(ValueError, match='aliases'):
        ConfirmationResult(
            confirmed=True,
            overrides={
                'period': [3, 4],
                'class_period': '第3-4节',
            },
        )


def test_confirmation_result_allows_clearing_lecture_date_with_none():
    confirmation = ConfirmationResult(
        confirmed=True,
        overrides={'lecture_date': None},
    )

    assert confirmation.overrides == {'lecture_date': None}


@pytest.mark.parametrize(
    ('field_name', 'value'),
    (
        ('confirmed', 'true'),
        ('confirmed', 1),
        ('confirmed', None),
        ('confirmed', object()),
        ('acknowledged_source', 'false'),
        ('acknowledged_source', 0),
        ('acknowledged_source', None),
        ('acknowledged_source', object()),
    ),
)
def test_confirmation_result_requires_real_boolean_flags(field_name, value):
    values = {'confirmed': True, 'acknowledged_source': False}
    values[field_name] = value

    with pytest.raises(ValueError, match=field_name):
        ConfirmationResult(**values)


def test_confirmation_result_preserves_valid_boolean_flags():
    result = ConfirmationResult(confirmed=False, acknowledged_source=True)

    assert result.confirmed is False
    assert result.acknowledged_source is True


def test_confirmation_result_overrides_are_deep_copy_isolated():
    source_overrides = {
        'period': [3, 4],
        'student_grade_class': '2024级计算机科学与技术1班',
    }
    result = ConfirmationResult(confirmed=True, overrides=source_overrides)

    source_overrides['period'][0] = 20
    exposed = result.overrides
    with pytest.raises(TypeError):
        exposed['period'][1] = 20
    with pytest.raises(TypeError):
        exposed['student_grade_class'] = '被修改'

    assert result.overrides == {
        'period': [3, 4],
        'student_grade_class': '2024级计算机科学与技术1班',
    }


def test_confirmation_result_keeps_standard_dataclass_interop_and_json_shape():
    result = ConfirmationResult(
        confirmed=True,
        acknowledged_source=True,
        overrides={'period': [3, 4]},
    )

    serialized = asdict(result)
    replaced = replace(result, confirmed=False)

    assert 'overrides' in serialized
    assert '_overrides' not in serialized
    assert serialized['overrides'] == {'period': [3, 4]}
    assert json.dumps(serialized, ensure_ascii=False)
    assert replaced.confirmed is False
    assert replaced.acknowledged_source is True
    assert replaced.overrides == {'period': [3, 4]}


@pytest.mark.parametrize(
    ('field_name', 'value'),
    (
        ('weekday', 0),
        ('weekday', 8),
        ('weekday', '1'),
        ('weekday', True),
        ('source_row', 0),
        ('source_row', -1),
        ('source_row', '2'),
        ('source_row', True),
    ),
)
def test_schedule_entry_rejects_malformed_weekday_and_source_row(field_name, value):
    with pytest.raises(ValueError, match=field_name):
        ScheduleEntry(entry_id='primary:batch-1:row-2', **{field_name: value})


def test_schedule_entry_accepts_valid_weekday_and_one_based_source_row():
    entry = ScheduleEntry(
        entry_id='primary:batch-1:row-2',
        weekday=7,
        source_row=1,
    )

    assert entry.weekday == 7
    assert entry.source_row == 1


def test_schedule_entry_keeps_original_and_normalized_provenance_values():
    original_room = ' 第1教学楼 A101 '
    original_period = ' 第3-4节 '
    original_class = ' ２０２４ 级 计算机科学与技术 1 班 '
    entry = ScheduleEntry(
        entry_id='primary:batch-1:row-2',
        room=original_room,
        period=original_period,
        student_grade_class=original_class,
    )
    explicit = ScheduleEntry(
        entry_id='primary:batch-1:row-3',
        room='第1教学楼A101',
        period='第3-4节',
        student_grade_class='2024级计算机科学与技术1班',
        location_raw=' explicit room ',
        period_raw=' explicit period ',
        class_raw=' explicit class ',
    )

    assert entry.room == '1教A101'
    assert entry.period == (3, 4)
    assert entry.student_grade_class == '2024级计算机科学与技术1班'
    assert entry.location_raw == original_room
    assert entry.period_raw == original_period
    assert entry.class_raw == original_class
    assert explicit.location_raw == ' explicit room '
    assert explicit.period_raw == ' explicit period '
    assert explicit.class_raw == ' explicit class '


@pytest.mark.parametrize('weekday', (0, 8, '1', True))
def test_candidate_rejects_malformed_weekday(weekday):
    with pytest.raises(ValueError, match='weekday'):
        Candidate(
            candidate_id='primary:batch-1:row-2',
            lecture_date=date(2026, 9, 18),
            room='8-309',
            period=(3, 4),
            course_title='数据结构',
            teacher_name='张老师',
            weekday=weekday,
        )


def test_candidate_weekday_is_serialized_after_validation():
    candidate = Candidate(
        candidate_id='primary:batch-1:row-2',
        lecture_date=date(2026, 9, 18),
        room='8-309',
        period=(3, 4),
        course_title='数据结构',
        teacher_name='张老师',
        weekday=1,
    )

    assert candidate.to_public_dict()['weekday'] == 1


@pytest.mark.parametrize('needs_confirmation', ('true', 1, None, object()))
def test_candidate_requires_a_real_boolean_confirmation_flag(needs_confirmation):
    with pytest.raises(ValueError, match='needs_confirmation'):
        Candidate(
            candidate_id='primary:batch-1:row-2',
            lecture_date=date(2026, 9, 18),
            room='8-309',
            period=(3, 4),
            course_title='数据结构',
            teacher_name='张老师',
            needs_confirmation=needs_confirmation,
        )


def test_stable_candidate_id_is_deterministic_and_source_scoped():
    common = {
        'source_kind': 'primary',
        'source_batch_id': 'batch-1',
        'source_row': 2,
        'lecture_date': date(2026, 9, 18),
        'room': '第1教学楼A101',
        'period': '第3-4节',
        'course_code': 'CS101',
        'course_title': '数据结构',
        'teacher_name': '张老师',
        'student_grade_class': '2024级计算机科学与技术1班',
    }

    first = stable_candidate_id(**common)
    second = stable_candidate_id(**common)
    changed_row = stable_candidate_id(**{**common, 'source_row': 3})

    assert first == second
    assert first.startswith('primary:batch-1:')
    assert first != changed_row


def test_stable_candidate_id_distinguishes_invalid_period_from_missing_period():
    common = {
        'source_kind': 'primary',
        'source_batch_id': 'batch-1',
        'source_row': 2,
        'lecture_date': date(2026, 9, 18),
        'room': '8-309',
        'course_code': 'CS101',
        'course_title': '数据结构',
        'teacher_name': '张老师',
        'student_grade_class': '2024级计算机科学与技术1班',
    }

    missing_period = stable_candidate_id(**common, period=None)
    invalid_period = stable_candidate_id(**common, period='bad')

    assert missing_period != invalid_period


def test_stable_candidate_id_marks_nonfinite_and_malformed_periods():
    common = {
        'source_kind': 'primary',
        'source_batch_id': 'batch-1',
        'source_row': 2,
        'lecture_date': date(2026, 9, 18),
        'room': '8-309',
        'course_code': 'CS101',
        'course_title': '数据结构',
        'teacher_name': '张老师',
        'student_grade_class': '2024级计算机科学与技术1班',
    }
    missing_period = stable_candidate_id(**common, period=None)
    nan_period = stable_candidate_id(**common, period=float('nan'))
    positive_infinity = stable_candidate_id(**common, period=float('inf'))
    negative_infinity = stable_candidate_id(**common, period=float('-inf'))
    malformed_list = stable_candidate_id(**common, period=[3, 'bad'])

    assert nan_period != missing_period
    assert positive_infinity != missing_period
    assert negative_infinity != missing_period
    assert malformed_list != missing_period
    assert len({nan_period, positive_infinity, negative_infinity, malformed_list}) == 4


def test_stable_candidate_id_marks_distinct_non_builtin_real_values():
    common = {
        'source_kind': 'primary',
        'source_batch_id': 'batch-1',
        'source_row': 2,
        'lecture_date': date(2026, 9, 18),
        'room': '8-309',
        'course_code': 'CS101',
        'course_title': '数据结构',
        'teacher_name': '张老师',
        'student_grade_class': '2024级计算机科学与技术1班',
    }
    zero = stable_candidate_id(**common, period=Fraction(0, 1))
    twenty_one = stable_candidate_id(**common, period=Fraction(21, 1))
    twenty_two = stable_candidate_id(**common, period=Fraction(22, 1))

    assert len({zero, twenty_one, twenty_two}) == 3
