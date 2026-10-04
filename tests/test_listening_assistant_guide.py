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
    lecture_date=LOOKUP_DATE,
    weekday=2,
    start_week_raw=None,
    venue_start_week_raw=None,
    venue_period_raw=None,
):
    return ScheduleEntry(
        entry_id=f'primary:batch-current:{row}',
        lecture_date=lecture_date,
        room=room,
        period=period,
        course_code=f'C{row:03d}',
        selection_code=f'S{row:03d}',
        course_title=course,
        teacher_name=teacher,
        teacher_college='计算机学院',
        student_grade_class=student_class,
        weekday=weekday,
        semester=SEMESTER,
        source_kind='primary',
        source_label='当前权威课表',
        source_batch_id='batch-current',
        source_row=row,
        start_week_raw=start_week_raw,
        venue_start_week_raw=venue_start_week_raw,
        venue_period_raw=venue_period_raw,
    )


def make_service(entries):
    loader = LoaderSpy(entries)
    return ListeningAssistantService(schedule_loader=loader), loader


def test_skip_factual_question_preserves_facts_and_asks_a_different_dimension():
    service,_=make_service([entry(i,teacher=f'教师{i}',room=f'8-{300+i}',period=(i,i+1)) for i in range(1,7)])
    guide=ListeningAssistantGuideService(service)
    start=guide.start(known_facts={'date':LOOKUP_DATE.isoformat()})
    result=guide.answer(start.state,question_kind=start.question.kind,option_code='SKIP',custom_value=None)
    assert result.state.known_facts==start.state.known_facts
    assert result.state.question_count==start.state.question_count+1
    assert result.question is None or result.question.kind!=start.question.kind


def test_skipping_all_questions_is_bounded_and_never_writes_unknown_as_a_fact():
    service,_=make_service([entry(i,teacher=f'教师{i}',room=f'8-{300+i}',period=(i,i+1)) for i in range(1,7)])
    guide=ListeningAssistantGuideService(service)
    result=guide.answer(guide.start().state,question_kind='memory',option_code='A',custom_value=None)
    steps=0
    while result.state.stage=='question':
        result=guide.answer(result.state,question_kind=result.question.kind,option_code='SKIP',custom_value=None)
        steps+=1
        assert steps<=4
    assert result.state.known_facts=={}
    assert result.state.stage in ('manual','candidate','confirm')


def test_skip_initial_memory_has_manual_exit():
    service,_=make_service([]);guide=ListeningAssistantGuideService(service)
    result=guide.answer(guide.start().state,question_kind='memory',option_code='SKIP',custom_value=None)
    assert result.state.stage=='manual' and result.state.known_facts=={}


def test_custom_answer_loads_schedule_once_without_repeating_the_old_query():
    service, loader = make_service([entry(1)])
    guide = ListeningAssistantGuideService(service)
    initial = guide.start()
    question = guide.answer(initial.state, question_kind='memory', option_code='A', custom_value=None)
    loader.calls.clear()

    result = guide.answer(question.state, question_kind='date', option_code=None, custom_value=LOOKUP_DATE.isoformat())

    assert result.state.known_facts['date'] == LOOKUP_DATE.isoformat()
    assert result.candidates
    assert len(loader.calls) == 1


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


@pytest.mark.parametrize(
    ('memory_code', 'fact_kind', 'fact_value'),
    (
        ('B', 'teacher', '张三'),
        ('C', 'room', '8-0309'),
    ),
)
def test_memory_teacher_or_room_without_dates_continues_by_asking_for_date(
    memory_code,
    fact_kind,
    fact_value,
):
    service, _ = make_service([
        entry(
            1,
            teacher='张三',
            room='8-0309',
            lecture_date=None,
            weekday=2,
            start_week_raw='1-16',
            venue_start_week_raw='1-16',
            venue_period_raw='第3-4节',
        ),
        entry(
            2,
            teacher='李四',
            room='8-0310',
            lecture_date=None,
            weekday=2,
            start_week_raw='1-16',
            venue_start_week_raw='1-16',
            venue_period_raw='第3-4节',
        ),
    ])
    guide = ListeningAssistantGuideService(service)
    memory = guide.start(semester=SEMESTER)

    fact_question = guide.answer(
        memory.state,
        question_kind='memory',
        option_code=memory_code,
        custom_value=None,
        semester=SEMESTER,
    )
    after_fact = guide.answer(
        fact_question.state,
        question_kind=fact_kind,
        option_code=None,
        custom_value=fact_value,
        semester=SEMESTER,
    )

    assert after_fact.state.stage == 'question'
    expected_fact = '8-309' if fact_kind == 'room' else fact_value
    assert after_fact.state.known_facts[fact_kind] == expected_fact
    assert after_fact.question is not None
    assert after_fact.question.kind == 'date'


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


def test_candidate_with_primary_conflict_reaches_confirmation_instead_of_forced_manual():
    service, _ = make_service([entry(1, period=(3, 4))])
    guide = ListeningAssistantGuideService(service)
    result = guide.start(known_facts={
        'date': LOOKUP_DATE.isoformat(),
        'teacher': '张三',
        'period': '第5-6节',
    })

    assert result.state.stage == 'candidate'
    selected = guide.answer(
        result.state,
        question_kind=result.question.kind,
        option_code='A',
        custom_value=None,
    )

    assert selected.state.stage == 'confirm'
    assert selected.needs_confirmation is True
    assert selected.candidates[0].conflicts


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


@pytest.mark.parametrize(
    ('state', 'question_kind', 'option_code'),
    (
        (GuidedAssistantState({}, (), (), 4, 'question'), 'memory', 'A'),
        (
            GuidedAssistantState(
                {'date': LOOKUP_DATE.isoformat()},
                (),
                ('teacher',),
                4,
                'question',
            ),
            'teacher',
            'A',
        ),
    ),
)
def test_answer_rejects_question_state_at_budget_before_recomputing(state, question_kind, option_code):
    service, loader = make_service([entry(1), entry(2, teacher='李四')])
    guide = ListeningAssistantGuideService(service)

    with pytest.raises(ValueError, match='question budget is exhausted'):
        guide.answer(
            state,
            question_kind=question_kind,
            option_code=option_code,
            custom_value=None,
            semester=SEMESTER,
        )

    assert loader.calls == []


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
        ({'date': '2026-09-29'}, 'teacher', '我不知道'),
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


def test_option_batches_include_courses_beyond_the_display_limit():
    service, _ = make_service([
        entry(i, teacher=f'教师{i:02}', course=f'课程{i:02}') for i in range(1, 31)
    ])
    guide = ListeningAssistantGuideService(service)
    initial = guide.start(known_facts={'date': LOOKUP_DATE.isoformat()})
    question = initial.question.to_public_dict()
    batches = question['option_batches']
    assert len(initial.candidates) == 20
    assert len(initial.state.candidate_ids) == 20
    assert len(batches) == 10
    assert [o['value'] for batch in batches for o in batch] == [f'教师{i:02}' for i in range(1, 31)]
    assert all([o['code'] for o in batch] == ['A', 'B', 'C'] for batch in batches)
    selected = guide.answer(initial.state, question_kind='teacher', option_code=None, custom_value=batches[-1][-1]['value'])
    assert selected.state.stage == 'confirm'
    assert selected.candidates[0].teacher_name == '教师30'
    assert selected.state.question_count == initial.state.question_count + 1


def test_long_class_group_does_not_break_the_question_or_remove_courses():
    service, _ = make_service([
        entry(i, student_class=('班级' + '甲' * 130 if i == 6 else f'班级{i}'), period=(1, 2) if i <= 3 else (3, 4))
        for i in range(1, 7)
    ])
    guide = ListeningAssistantGuideService(service)
    initial = guide.start(known_facts={'date': LOOKUP_DATE.isoformat()})
    assert initial.question.kind == 'period'
    assert len(initial.candidates) == 6
    reachable = set()
    for option in initial.question.options:
        result = guide.answer(initial.state, question_kind='period', option_code=option.code, custom_value=None)
        assert result.state.stage in {'candidate', 'manual'}
        assert result.state.question_count == 1
        reachable.update(candidate.course_title + candidate.candidate_id for candidate in result.candidates)
    assert len(reachable) == 6


def test_first_batch_prioritizes_more_covered_courses_and_keeps_other_answers():
    names = ['教师甲'] * 5 + ['教师乙'] * 3 + ['教师丙'] * 2 + ['教师丁']
    service, _ = make_service([entry(i, teacher=name) for i, name in enumerate(names, 1)])
    guide = ListeningAssistantGuideService(service)
    result = guide.start(known_facts={'date': LOOKUP_DATE.isoformat()})
    assert result.question.kind == 'teacher'
    assert [o.value for o in result.question.options] == ['教师甲', '教师乙', '教师丙']
    assert [o.candidate_count for o in result.question.options] == [5, 3, 2]
    assert {o.value for batch in result.question.option_batches for o in batch} == set(names)
    answered = guide.answer(result.state, question_kind='teacher', option_code='A', custom_value=None)
    assert answered.state.known_facts['teacher'] == '教师甲'
