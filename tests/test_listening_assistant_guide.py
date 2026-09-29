from dataclasses import replace
from datetime import date

import pytest

from app.services.listening_assistant import ListeningAssistantService
from app.services.listening_assistant_contracts import ScheduleEntry
from app.services.listening_assistant_guide import ListeningAssistantGuideService
from app.services.listening_assistant_guide_contracts import GuidedAssistantState


LOOKUP_DATE = date(2026, 9, 29)
SEMESTER = '2026-2027-1'


class LoaderSpy:
    def __init__(self, entries):
        self.entries = list(entries)
        self.calls = []

    def __call__(self, *, source_kind='primary', semester=None, source_batch_id=None, batch_id=None):
        self.calls.append((source_kind, semester, source_batch_id))
        return list(self.entries) if source_kind == 'primary' else []


def entry(
    row,
    *,
    teacher='张三',
    room='8-0309',
    period=(3, 4),
    course='数据结构',
    student_class='2024级计算机1班',
):
    return ScheduleEntry(
        entry_id=f'primary:batch-current:{row}',
        lecture_date=LOOKUP_DATE,
        room=room,
        period=period,
        course_code=f'C{row:03d}',
        selection_code=f'S{row:03d}',
        course_title=course,
        teacher_name=teacher,
        teacher_college='计算机学院',
        student_grade_class=student_class,
        weekday=2,
        semester=SEMESTER,
        source_kind='primary',
        source_label='当前权威课表',
        source_batch_id='batch-current',
        source_row=row,
    )


def make_service(entries):
    loader = LoaderSpy(entries)
    return ListeningAssistantService(schedule_loader=loader), loader


def test_search_partial_accepts_empty_facts_and_recomputes_primary_candidates():
    service, loader = make_service([entry(1), entry(2, teacher='李四', room='8-0310')])

    result = service.search_partial({}, semester=SEMESTER)

    assert len(result.candidates) == 2
    assert result.source_kind == 'primary'
    assert result.always_show_none is True
    assert loader.calls == [('primary', SEMESTER, None)]


def test_search_partial_caps_unanchored_candidates_at_twenty_in_stable_order():
    service, _ = make_service([
        entry(i, course=f'课程{i:02d}', teacher=f'教师{i:02d}', room=f'8-{i:04d}')
        for i in range(21, 0, -1)
    ])

    result = service.search_partial({}, semester=SEMESTER)

    assert len(result.candidates) == 20
    assert [candidate.course_title for candidate in result.candidates] == [
        f'课程{i:02d}' for i in range(1, 21)
    ]


def test_search_partial_normalizes_aliases_filters_fields_and_keeps_period_conflict():
    service, _ = make_service([
        entry(1, teacher='张三', room='08-0309', period=(3, 4)),
        entry(2, teacher='张三', room='08-0309', period=(5, 6)),
    ])

    result = service.search_partial({
        'lecture_date': '2026-09-29',
        'teacher_name': ' 张三 ',
        'location': '8-0309',
        'period': '第1-2节',
    }, semester=SEMESTER)

    assert len(result.candidates) == 2
    assert result.candidates[0].conflicts == ('period_mismatch',)
    assert result.candidates[1].conflicts == ('period_mismatch',)


def test_search_partial_rejects_unknown_or_unbounded_facts_and_excludes_rejected_ids():
    service, _ = make_service([entry(1)])
    candidate_id = service.search_partial({'date': LOOKUP_DATE.isoformat()}, semester=SEMESTER).candidates[0].candidate_id

    assert service.search_partial({}, semester=SEMESTER, rejected_ids=candidate_id).candidates == ()
    with pytest.raises(ValueError):
        service.search_partial({'phone': '13800000000'}, semester=SEMESTER)
    with pytest.raises(ValueError):
        service.search_partial({'teacher': 'x' * 121}, semester=SEMESTER)


def test_start_returns_memory_question_with_exact_abc_contract():
    service, _ = make_service([])
    result = ListeningAssistantGuideService(service).start()

    assert result.state.question_count == 0
    assert result.state.asked_question_kinds == ()
    assert result.question.to_public_dict() == {
        'kind': 'memory',
        'prompt': '你还记得哪类信息？',
        'options': [
            {'code': 'A', 'label': '听课日期', 'value': 'date', 'candidate_count': None},
            {'code': 'B', 'label': '授课教师', 'value': 'teacher', 'candidate_count': None},
            {'code': 'C', 'label': '教室', 'value': 'room', 'candidate_count': None},
        ],
        'allow_custom': True,
        'custom_label': 'D. 我自己填写',
    }


def test_memory_a_selects_date_then_d_custom_date_is_one_fact():
    service, _ = make_service([entry(1), entry(2, teacher='李四')])
    guide = ListeningAssistantGuideService(service)
    memory = guide.start()

    date_question = guide.answer(memory.state, question_kind='memory', option_code='A', custom_value=None)
    assert date_question.question.kind == 'date'
    assert date_question.state.known_facts == {}

    after_date = guide.answer(
        date_question.state,
        question_kind='date',
        option_code=None,
        custom_value='2026-09-29',
    )
    assert after_date.state.known_facts == {'date': '2026-09-29'}
    assert after_date.state.question_count == 1


def test_guide_dynamically_chooses_teacher_room_and_period_and_stable_top_three():
    entries = [
        entry(4, teacher='赵老师', room='8-0310', period=(3, 4), course='A课'),
        entry(1, teacher='张老师', room='8-0309', period=(3, 4), course='C课'),
        entry(3, teacher='李老师', room='8-0309', period=(3, 4), course='B课'),
        entry(2, teacher='王老师', room='8-0311', period=(3, 4), course='D课'),
    ]
    service, _ = make_service(entries)
    guide = ListeningAssistantGuideService(service)

    result = guide.start(known_facts={'date': LOOKUP_DATE.isoformat()})
    assert result.question.kind == 'teacher'
    assert [candidate.course_title for candidate in result.candidates] == ['A课', 'B课', 'C课', 'D课']
    assert [option.value for option in result.question.options] == ['张老师', '李老师', '王老师']

    teacher = guide.answer(result.state, question_kind='teacher', option_code='A', custom_value=None)
    assert teacher.state.known_facts['teacher'] == '张老师'
    assert teacher.state.stage == 'confirm'

    room_service, _ = make_service([
        entry(i, teacher='同一教师', room=f'8-03{i:02d}', period=(3, 4))
        for i in range(1, 5)
    ])
    room_result = ListeningAssistantGuideService(room_service).start(
        known_facts={'date': LOOKUP_DATE.isoformat(), 'teacher': '同一教师'}
    )
    assert room_result.question.kind == 'room'

    period_service, _ = make_service([
        entry(i, teacher='同一教师', room='8-0309', period=(i, i))
        for i in range(1, 5)
    ])
    period_result = ListeningAssistantGuideService(period_service).start(
        known_facts={
            'date': LOOKUP_DATE.isoformat(),
            'teacher': '同一教师',
            'room': '8-0309',
        }
    )
    assert period_result.question.kind == 'period'


def test_guide_returns_candidate_options_for_two_or_three_and_confirm_for_clean_single():
    service, _ = make_service([entry(1, course='A课'), entry(2, course='B课')])
    guide = ListeningAssistantGuideService(service)
    result = guide.start(known_facts={'date': LOOKUP_DATE.isoformat(), 'teacher': '张三'})

    assert result.state.stage == 'candidate'
    assert [option.code for option in result.question.options] == ['A', 'B']
    assert [option.value for option in result.question.options] == [candidate.candidate_id for candidate in result.candidates]

    single_service, _ = make_service([entry(1)])
    single = ListeningAssistantGuideService(single_service).start(
        known_facts={'date': LOOKUP_DATE.isoformat(), 'teacher': '张三'}
    )
    assert single.state.stage == 'confirm'
    assert single.question is None
    assert single.needs_confirmation is True


def test_guide_never_exceeds_four_questions_and_can_fall_back_to_manual():
    entries = [entry(i, teacher=f'教师{i}', room=f'8-{i:04d}', period=(i, i)) for i in range(1, 7)]
    service, _ = make_service(entries)
    guide = ListeningAssistantGuideService(service)
    result = guide.start(known_facts={'date': LOOKUP_DATE.isoformat()})

    for _ in range(4):
        if result.state.stage in {'manual', 'confirm'}:
            break
        assert result.question is not None
        result = guide.answer(result.state, question_kind=result.question.kind, option_code='A', custom_value=None)

    assert result.state.question_count <= 4
    assert result.state.stage in {'candidate', 'confirm', 'manual'}
    assert result.state.question_count != 5


def test_answer_rejects_invalid_question_option_custom_value_and_state():
    service, _ = make_service([entry(1), entry(2, teacher='李四')])
    guide = ListeningAssistantGuideService(service)
    state = guide.start().state

    with pytest.raises(ValueError):
        guide.answer(state, question_kind='not-a-kind', option_code='A', custom_value=None)
    with pytest.raises(ValueError):
        guide.answer(state, question_kind='memory', option_code='Z', custom_value=None)
    with pytest.raises(ValueError):
        guide.answer(state, question_kind='memory', option_code='A', custom_value='also-set')
    with pytest.raises(ValueError):
        guide.answer(
            GuidedAssistantState({}, (), (), 0, 'confirm'),
            question_kind='memory',
            option_code='A',
            custom_value=None,
        )


@pytest.mark.parametrize('stage', ('confirm', 'candidate', 'manual', 'done'))
def test_answer_rejects_memory_on_every_non_question_stage(stage):
    service, _ = make_service([entry(1), entry(2)])
    guide = ListeningAssistantGuideService(service)

    with pytest.raises(ValueError):
        guide.answer(
            GuidedAssistantState({}, (), ('date',), 0, stage),
            question_kind='memory',
            option_code='A',
            custom_value=None,
        )


def test_answer_rejects_wrong_kind_before_search_on_question_stage():
    service, loader = make_service([entry(1), entry(2, teacher='李四'), entry(3, teacher='王五'), entry(4, teacher='赵六')])
    guide = ListeningAssistantGuideService(service)
    state = guide.start(known_facts={'date': LOOKUP_DATE.isoformat()}).state
    loader.calls.clear()

    with pytest.raises(ValueError):
        guide.answer(state, question_kind='room', option_code='A', custom_value=None)

    assert loader.calls == []


@pytest.mark.parametrize('option_code', ('D', 'NONE'))
def test_option_code_and_custom_value_are_always_mutually_exclusive(option_code):
    service, _ = make_service([entry(i, teacher=f'教师{i}') for i in range(1, 5)])
    guide = ListeningAssistantGuideService(service)
    state = guide.start(known_facts={'date': LOOKUP_DATE.isoformat()}).state

    with pytest.raises(ValueError):
        guide.answer(
            state,
            question_kind=state.asked_question_kinds[-1],
            option_code=option_code,
            custom_value='自定义值',
        )


def test_memory_d_enters_manual_without_writing_an_arbitrary_fact():
    service, _ = make_service([entry(1)])
    guide = ListeningAssistantGuideService(service)
    state = guide.start().state

    result = guide.answer(state, question_kind='memory', option_code='D', custom_value=None)

    assert result.state.stage == 'manual'
    assert result.state.known_facts == {}
    assert result.question is None


def test_memory_d_cannot_write_multiple_facts_in_one_answer():
    service, _ = make_service([entry(1)])
    guide = ListeningAssistantGuideService(service)
    state = guide.start().state

    with pytest.raises(ValueError):
        guide.answer(
            state,
            question_kind='memory',
            option_code='D',
            custom_value={'date': '2026-09-29', 'teacher': '张三'},
        )


@pytest.mark.parametrize(
    ('known_facts', 'question_kind', 'uncertain'),
    (
        ({'date': '2026-09-29'}, 'teacher', '不确定'),
        ({'date': '2026-09-29', 'teacher': '张三'}, 'room', '未知'),
        ({'date': '2026-09-29', 'teacher': '张三', 'room': '8-0309'}, 'period', '不清楚'),
    ),
)
def test_uncertain_custom_values_are_noop_manual_paths(known_facts, question_kind, uncertain):
    if question_kind == 'teacher':
        entries = [entry(i, teacher=f'教师{i}', room='8-0309', period=(3, 4)) for i in range(1, 5)]
    elif question_kind == 'room':
        entries = [entry(i, teacher='张三', room=f'8-03{i:02d}', period=(3, 4)) for i in range(1, 5)]
    else:
        entries = [entry(i, teacher='张三', room='8-0309', period=(i, i)) for i in range(1, 5)]
    service, _ = make_service(entries)
    guide = ListeningAssistantGuideService(service)
    result = guide.start(known_facts=known_facts)
    original_facts = dict(result.state.known_facts)

    assert result.question.kind == question_kind
    answered = guide.answer(
        result.state,
        question_kind=question_kind,
        option_code=None,
        custom_value=uncertain,
    )

    assert answered.state.stage == 'manual'
    assert dict(answered.state.known_facts) == original_facts


@pytest.mark.parametrize('option_code', ('D', 'NONE'))
def test_candidate_stage_d_and_none_are_safe_manual_paths(option_code):
    service, _ = make_service([entry(1, course='A课'), entry(2, course='B课')])
    guide = ListeningAssistantGuideService(service)
    result = guide.start(known_facts={'date': LOOKUP_DATE.isoformat(), 'teacher': '张三'})

    answered = guide.answer(
        result.state,
        question_kind=result.question.kind,
        option_code=option_code,
        custom_value=None,
    )

    assert answered.state.stage == 'manual'


def test_select_kind_minimizes_largest_group_before_fixed_order_tie_breaker():
    entries = [
        entry(1, teacher='教师甲', room='8-0309'),
        entry(2, teacher='教师甲', room='8-0310'),
        entry(3, teacher='教师甲', room='8-0311'),
        entry(4, teacher='教师乙', room='8-0311'),
    ]
    service, _ = make_service(entries)

    result = ListeningAssistantGuideService(service).start(
        known_facts={'date': LOOKUP_DATE.isoformat()}
    )

    assert result.question.kind == 'room'


def test_answer_recomputes_candidates_and_does_not_trust_forged_candidate_ids():
    service, _ = make_service([
        entry(1, teacher='张三'),
        entry(2, teacher='李四'),
        entry(3, teacher='王五'),
        entry(4, teacher='赵六'),
    ])
    guide = ListeningAssistantGuideService(service)
    initial = guide.start(known_facts={'date': LOOKUP_DATE.isoformat()})
    forged = replace(initial.state, candidate_ids=('not-from-loader',))

    result = guide.answer(forged, question_kind='teacher', option_code='A', custom_value=None)

    assert all(candidate.candidate_id != 'not-from-loader' for candidate in result.candidates)
    assert result.state.known_facts['teacher'] == '张三'


def test_public_result_does_not_leak_private_fields():
    service, _ = make_service([entry(1)])
    public = ListeningAssistantGuideService(service).start(
        known_facts={'date': LOOKUP_DATE.isoformat(), 'teacher': '张三'}
    ).to_public_dict()
    serialized = str(public).lower()

    assert all(secret not in serialized for secret in ('phone', 'signature', 'credential', 'evaluation'))
