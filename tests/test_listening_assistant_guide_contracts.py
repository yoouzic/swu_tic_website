import json
from datetime import date

import pytest

from app.services.listening_assistant_contracts import Candidate
from app.services.listening_assistant_guide_contracts import (
    ALLOWED_GUIDED_STAGES,
    MAX_GUIDED_CANDIDATES,
    MAX_GUIDED_FACT_LENGTH,
    MAX_GUIDED_QUESTIONS,
    GuidedAssistantState,
    GuidedOption,
    GuidedQuestion,
    GuidedResult,
    normalize_candidate_ids,
    normalize_known_facts,
    normalize_question_count,
    normalize_question_kinds,
    normalize_stage,
)


def make_candidate(candidate_id='candidate-1'):
    return Candidate(
        candidate_id=candidate_id,
        lecture_date=date(2026, 9, 29),
        room='A101',
        period=(3, 4),
        course_title='高等数学',
        teacher_name='李老师',
        teacher_college='数学学院',
        source_kind='primary',
        source_label='权威课表',
    )


def test_initial_memory_question_has_frozen_abc_and_custom_contract():
    question = GuidedQuestion.initial_memory_question()

    assert question.to_public_dict() == {
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


def test_question_count_over_budget_is_invalid():
    with pytest.raises(ValueError):
        normalize_question_count(MAX_GUIDED_QUESTIONS + 1)
    with pytest.raises(ValueError):
        GuidedAssistantState({}, (), (), MAX_GUIDED_QUESTIONS + 1, 'question')


def test_option_public_dict_is_exactly_the_four_public_fields():
    option = GuidedOption('A', '候选课程', 'candidate-1', candidate_count=1)

    assert option.to_public_dict() == {
        'code': 'A',
        'label': '候选课程',
        'value': 'candidate-1',
        'candidate_count': 1,
    }


def test_state_round_trip_is_json_safe():
    state = GuidedAssistantState(
        {'date': '2026-09-29', 'teacher': '李老师'},
        ('candidate-1',),
        ('date',),
        1,
        'candidate',
    )

    restored = GuidedAssistantState.from_public_dict(json.loads(json.dumps(state.to_public_dict())))

    assert restored == state
    assert restored.is_valid is True


@pytest.mark.parametrize(
    'payload',
    [
        {'unknown': 'x'},
        {'date': 20260929},
        {'date': 'x' * (MAX_GUIDED_FACT_LENGTH + 1)},
    ],
)
def test_known_facts_reject_unknown_wrong_type_and_overlong_values(payload):
    with pytest.raises(ValueError):
        normalize_known_facts(payload)


def test_state_rejects_unknown_nested_keys_and_wrong_types():
    valid = {
        'known_facts': {},
        'candidate_ids': [],
        'asked_question_kinds': [],
        'question_count': 0,
        'stage': 'question',
    }
    with pytest.raises(ValueError):
        GuidedAssistantState.from_public_dict({**valid, 'extra': True})
    with pytest.raises(ValueError):
        GuidedAssistantState.from_public_dict({**valid, 'known_facts': {'date': {'value': 'x'}}})
    with pytest.raises(ValueError):
        GuidedAssistantState.from_public_dict({**valid, 'candidate_ids': 'candidate-1'})


def test_candidate_ids_reject_duplicates_and_more_than_budget():
    with pytest.raises(ValueError):
        normalize_candidate_ids(('candidate-1', 'candidate-1'))
    with pytest.raises(ValueError):
        normalize_candidate_ids(tuple(f'candidate-{i}' for i in range(MAX_GUIDED_CANDIDATES + 1)))


def test_question_kinds_and_stage_are_whitelisted():
    with pytest.raises(ValueError):
        normalize_question_kinds(('date', 'not-a-question'))
    with pytest.raises(ValueError):
        normalize_stage('unknown')
    assert ALLOWED_GUIDED_STAGES == {'question', 'candidate', 'confirm', 'manual', 'done'}


def test_result_public_dict_uses_privacy_safe_candidate_payload():
    candidate = make_candidate()
    result = GuidedResult(
        state=GuidedAssistantState({}, ('candidate-1',), (), 0, 'confirm'),
        question=None,
        candidates=(candidate,),
        needs_confirmation=True,
    )

    public = result.to_public_dict()
    serialized = json.dumps(public, ensure_ascii=False)

    assert public['candidates'] == [candidate.to_public_dict()]
    assert 'phone' not in serialized
    assert 'signature' not in serialized
    assert 'credential' not in serialized
    assert 'evaluation' not in serialized
    assert public['needs_confirmation'] is True
