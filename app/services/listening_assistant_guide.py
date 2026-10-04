"""Adaptive, server-recomputed question orchestration for the listening assistant."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping
import unicodedata

from app.services.listening_assistant import ListeningAssistantService
from app.services.listening_assistant_contracts import (
    Candidate,
    normalize_class_for_display,
    normalize_room,
    parse_period,
)
from app.services.listening_assistant_guide_contracts import (
    MAX_GUIDED_QUESTIONS,
    MAX_GUIDED_CANDIDATES,
    MAX_GUIDED_FACT_LENGTH,
    GuidedAssistantState,
    GuidedOption,
    GuidedQuestion,
    GuidedResult,
    normalize_known_facts,
)
from app.services.teaching_calendar import parse_lecture_date


_FACT_KINDS = ('date', 'teacher', 'room', 'period', 'student_grade_class')
_NO_OP_CODES = frozenset({'NONE', 'NOT_THIS', 'UNSURE', '都不是', '不确定'})
_UNCERTAIN_VALUES = frozenset({
    '不确定',
    '不清楚',
    '不知道',
    '我不知道',
    '跳过',
    '跳过此题',
    '未知',
    '都不是',
    '不记得',
})
_PROMPTS = {
    'date': '你记得哪一天听课？',
    'teacher': '你记得授课教师吗？',
    'room': '你记得上课教室吗？',
    'period': '你记得是第几节吗？',
    'student_grade_class': '你记得听课班级吗？',
}
_LABELS = {
    'date': '听课日期',
    'teacher': '授课教师',
    'room': '教室',
    'period': '节次',
    'student_grade_class': '听课班级',
}


def _period_label(period: tuple[int, int]) -> str:
    return f'第{period[0]}-{period[1]}节'


def _candidate_value(candidate: Candidate, kind: str) -> str:
    if kind == 'date':
        return candidate.lecture_date.isoformat()
    if kind == 'teacher':
        return candidate.teacher_name
    if kind == 'room':
        return candidate.room
    if kind == 'period':
        return _period_label(candidate.period)
    if kind == 'student_grade_class':
        return candidate.student_grade_class
    raise ValueError('question kind is not allowed')


def _canonical_fact(kind: str, value: object) -> str:
    if kind == 'date':
        if isinstance(value, datetime):
            parsed = value.date()
        elif isinstance(value, date):
            parsed = value
        else:
            parsed = parse_lecture_date(value)
        if parsed is None:
            raise ValueError('date is invalid')
        return parsed.isoformat()
    if kind == 'teacher':
        if not isinstance(value, str) or not value.strip():
            raise ValueError('teacher must be a non-empty string')
        return ' '.join(value.strip().split())
    if kind == 'room':
        if not isinstance(value, str) or not value.strip():
            raise ValueError('room must be a non-empty string')
        normalized = normalize_room(value)
        if not normalized:
            raise ValueError('room must be a non-empty string')
        return normalized
    if kind == 'period':
        parsed = parse_period(value)
        if parsed is None:
            raise ValueError('period is invalid')
        return _period_label(parsed)
    if kind == 'student_grade_class':
        if not isinstance(value, str) or not value.strip():
            raise ValueError('student_grade_class must be a non-empty string')
        normalized = normalize_class_for_display(value)
        if not normalized:
            raise ValueError('student_grade_class must be a non-empty string')
        return normalized
    raise ValueError('question kind is not allowed')


def _is_uncertain_value(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = unicodedata.normalize('NFKC', value).strip().casefold()
    return normalized in _UNCERTAIN_VALUES


def _group_candidates(candidates: tuple[Candidate, ...], kind: str) -> dict[str, list[Candidate]]:
    groups: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        value = _candidate_value(candidate, kind)
        if not value:
            continue
        groups.setdefault(value, []).append(candidate)
    return groups


def _bounded_result(
    state: GuidedAssistantState,
    question: GuidedQuestion | None,
    candidates: tuple[Candidate, ...],
    needs_confirmation: bool,
) -> GuidedResult:
    # Limit transport/state only. Question grouping uses the complete search.
    return GuidedResult(state, question, candidates[:MAX_GUIDED_CANDIDATES], needs_confirmation)


class ListeningAssistantGuideService:
    """Choose one safe question at a time from a fresh primary search."""

    def __init__(self, assistant_service: ListeningAssistantService) -> None:
        if not isinstance(assistant_service, ListeningAssistantService):
            raise TypeError('assistant_service must be a ListeningAssistantService')
        self._assistant_service = assistant_service

    def start(
        self,
        *,
        known_facts: Mapping[str, object] | None = None,
        semester: str | None = None,
    ) -> GuidedResult:
        facts = self._normalize_facts(known_facts)
        if not facts:
            return _bounded_result(
                state=GuidedAssistantState({}, (), (), 0, 'question'),
                question=GuidedQuestion.initial_memory_question(),
                candidates=(),
                needs_confirmation=False,
            )

        search = self._search(facts, semester=semester)
        state = GuidedAssistantState(facts, (), (), 0, 'question')
        return self._render(state, search.candidates)

    def answer(
        self,
        state: GuidedAssistantState,
        *,
        question_kind: str,
        option_code: str | None,
        custom_value: object | None,
        semester: str | None = None,
    ) -> GuidedResult:
        self._validate_answer(state, question_kind, option_code, custom_value)

        # A typed fact is independent of the old option list. Search only
        # after incorporating it; option/candidate answers still need a fresh
        # old-query search to validate their selection against the timetable.
        typed_fact = (
            state.stage == 'question'
            and question_kind != 'memory'
            and option_code in {None, 'D'}
            and custom_value is not None
            and not _is_uncertain_value(custom_value)
        )
        candidates = () if typed_fact else self._search(
            dict(state.known_facts), semester=semester,
        ).candidates
        if question_kind == 'memory':
            if option_code in {'A', 'B', 'C'}:
                target = {'A': 'date', 'B': 'teacher', 'C': 'room'}[option_code]
                asked = self._append_kind(state.asked_question_kinds, 'memory')
                asked = self._append_kind(asked, target)
                next_state = GuidedAssistantState(
                    dict(state.known_facts),
                    tuple(candidate.candidate_id for candidate in candidates[:MAX_GUIDED_CANDIDATES]),
                    asked,
                    state.question_count,
                    'question',
                )
                return self._render(next_state, candidates, forced_kind=target)
            if option_code in {'D','SKIP'}:
                return self._manual(state, candidates)
            raise ValueError('memory answer must select A, B, or C')

        if state.stage == 'candidate':
            return self._answer_candidate(
                state,
                question_kind,
                option_code,
                custom_value,
                candidates,
            )

        if state.stage != 'question' or question_kind not in _FACT_KINDS:
            raise ValueError('question_kind does not match the current state')
        if not state.asked_question_kinds or state.asked_question_kinds[-1] != question_kind:
            raise ValueError('question_kind does not match the current question')
        if state.question_count >= MAX_GUIDED_QUESTIONS:
            raise ValueError('question budget is exhausted')

        facts = dict(state.known_facts)
        if option_code == 'SKIP':
            next_state=GuidedAssistantState(facts,
                tuple(c.candidate_id for c in candidates[:MAX_GUIDED_CANDIDATES]),
                state.asked_question_kinds,state.question_count+1,'question')
            if not candidates and next_state.question_count<MAX_GUIDED_QUESTIONS:
                available=[kind for kind in _FACT_KINDS if kind not in facts and kind not in next_state.asked_question_kinds]
                if available:return self._render(next_state,candidates,forced_kind=available[0])
            return self._render(next_state,candidates)
        if option_code in _NO_OP_CODES:
            return self._manual(state, candidates)
        if option_code is None and _is_uncertain_value(custom_value):
            return self._manual(state, candidates)
        if option_code is not None and option_code != 'D':
            question = self._field_question(question_kind, candidates)
            selected = next((option for option in question.options if option.code == option_code), None)
            if selected is None:
                raise ValueError('option_code is not valid for the current question')
            facts[question_kind] = _canonical_fact(question_kind, selected.value)
        else:
            if custom_value is None:
                raise ValueError('custom_value is required for a custom answer')
            facts[question_kind] = _canonical_fact(question_kind, custom_value)

        refreshed = self._search(facts, semester=semester)
        next_state = GuidedAssistantState(
            facts,
            tuple(candidate.candidate_id for candidate in refreshed.candidates[:MAX_GUIDED_CANDIDATES]),
            state.asked_question_kinds,
            state.question_count + 1,
            'question',
        )
        return self._render(next_state, refreshed.candidates)

    def _normalize_facts(self, known_facts: Mapping[str, object] | None) -> dict[str, str]:
        normalized = normalize_known_facts(known_facts if known_facts is not None else {})
        return {kind: _canonical_fact(kind, value) for kind, value in normalized.items()}

    def _search(self, facts: Mapping[str, object], *, semester: str | None):
        return self._assistant_service.search_partial(facts, semester=semester, candidate_limit=None)

    @staticmethod
    def _append_kind(kinds: tuple[str, ...], kind: str) -> tuple[str, ...]:
        return kinds if kind in kinds else (*kinds, kind)

    @staticmethod
    def _validate_answer(
        state: GuidedAssistantState,
        question_kind: str,
        option_code: str | None,
        custom_value: object | None,
    ) -> None:
        if not isinstance(state, GuidedAssistantState) or not state.is_valid:
            raise ValueError('state is invalid')
        if state.stage == 'question' and state.question_count >= MAX_GUIDED_QUESTIONS:
            raise ValueError('question budget is exhausted')
        if state.stage not in {'question', 'candidate'}:
            raise ValueError('state is not answerable')
        if not isinstance(question_kind, str) or question_kind not in {*_FACT_KINDS, 'memory'}:
            raise ValueError('question_kind is not allowed')
        expected_kind = ListeningAssistantGuideService._expected_question_kind(state)
        if question_kind != expected_kind:
            raise ValueError('question_kind does not match the current question')
        if option_code is not None and (
            not isinstance(option_code, str) or not option_code.strip()
        ):
            raise ValueError('option_code must be a non-empty string or None')
        if option_code is not None and custom_value is not None:
            raise ValueError('option_code and custom_value cannot both be supplied')
        if question_kind == 'memory' and option_code not in {'A', 'B', 'C', 'D','SKIP'}:
            raise ValueError('memory answer must select A, B, C, or D')

    @staticmethod
    def _expected_question_kind(state: GuidedAssistantState) -> str:
        if state.stage == 'question' and not state.asked_question_kinds:
            return 'memory'
        if not state.asked_question_kinds:
            raise ValueError('state has no current question')
        current_kind = state.asked_question_kinds[-1]
        if current_kind not in _FACT_KINDS:
            raise ValueError('state has no current question')
        return current_kind

    def _select_kind(
        self,
        candidates: tuple[Candidate, ...],
        known_facts: Mapping[str, object],
        asked: tuple[str, ...],
    ) -> str | None:
        available = [
            kind for kind in _FACT_KINDS
            if kind not in known_facts and kind not in asked
        ]
        scored = [
            (
                max(len(group) for group in _group_candidates(candidates, kind).values()),
                sum(len(group) ** 2 for group in _group_candidates(candidates, kind).values()),
                index,
                kind,
            )
            for index, kind in enumerate(_FACT_KINDS)
            if kind in available and len(_group_candidates(candidates, kind)) > 1
            # A source can aggregate many classes in one value. Asking it
            # cannot produce a valid answer within the fact contract. Keep
            # every course and choose another distinguishing fact instead.
            and all(len(value) <= MAX_GUIDED_FACT_LENGTH for value in _group_candidates(candidates, kind))
        ]
        return min(scored)[3] if scored else None

    def _field_question(self, kind: str, candidates: tuple[Candidate, ...]) -> GuidedQuestion:
        groups = _group_candidates(candidates, kind)
        ranked = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
        batches = tuple(
            tuple(GuidedOption(chr(ord('A') + index), value, value, len(group))
                  for index, (value, group) in enumerate(ranked[offset:offset + 3]))
            for offset in range(0, len(ranked), 3)
        )
        return GuidedQuestion(
            kind=kind,
            prompt=_PROMPTS[kind],
            options=batches[0] if batches else (),
            option_batches=batches if len(batches) > 1 else (),
        )

    def _candidate_kind(
        self,
        state: GuidedAssistantState,
        candidates: tuple[Candidate, ...],
    ) -> str:
        for kind in reversed(state.asked_question_kinds):
            if kind in _FACT_KINDS:
                return kind
        return self._select_kind(candidates, state.known_facts, ()) or 'date'

    def _candidate_question(
        self,
        kind: str,
        candidates: tuple[Candidate, ...],
    ) -> GuidedQuestion:
        options = tuple(
            GuidedOption(
                code=chr(ord('A') + index),
                label=(
                    f'{candidate.course_title} · {candidate.teacher_name} · '
                    f'{candidate.room} · {_period_label(candidate.period)}'
                )[:120],
                value=candidate.candidate_id,
                candidate_count=1,
            )
            for index, candidate in enumerate(candidates[:3])
        )
        return GuidedQuestion(
            kind=kind,
            prompt='请选择最符合的课程候选：',
            options=options,
        )

    def _render(
        self,
        state: GuidedAssistantState,
        candidates: tuple[Candidate, ...],
        *,
        forced_kind: str | None = None,
    ) -> GuidedResult:
        candidate_ids = tuple(candidate.candidate_id for candidate in candidates[:MAX_GUIDED_CANDIDATES])
        if forced_kind is not None:
            question = self._field_question(forced_kind, candidates)
            next_state = GuidedAssistantState(
                dict(state.known_facts),
                candidate_ids,
                self._append_kind(state.asked_question_kinds, forced_kind),
                state.question_count,
                'question',
            )
            return _bounded_result(next_state, question, candidates, False)

        if not candidates:
            if (
                state.question_count < MAX_GUIDED_QUESTIONS
                and state.known_facts
                and 'date' not in state.known_facts
                and 'date' not in state.asked_question_kinds
            ):
                next_state = GuidedAssistantState(
                    dict(state.known_facts),
                    (),
                    self._append_kind(state.asked_question_kinds, 'date'),
                    state.question_count,
                    'question',
                )
                return _bounded_result(
                    next_state,
                    self._field_question('date', ()),
                    (),
                    False,
                )
            next_state = GuidedAssistantState(
                dict(state.known_facts), (), state.asked_question_kinds,
                state.question_count, 'manual',
            )
            return _bounded_result(next_state, None, (), False)

        if len(candidates) == 1 and not candidates[0].conflicts:
            next_state = GuidedAssistantState(
                dict(state.known_facts), candidate_ids, state.asked_question_kinds,
                state.question_count, 'confirm',
            )
            return _bounded_result(next_state, None, candidates, True)

        if 1 <= len(candidates) <= 3:
            kind = self._candidate_kind(state, candidates)
            next_state = GuidedAssistantState(
                dict(state.known_facts), candidate_ids,
                self._append_kind(state.asked_question_kinds, kind),
                state.question_count, 'candidate',
            )
            return _bounded_result(next_state, self._candidate_question(kind, candidates), candidates, True)

        if state.question_count >= MAX_GUIDED_QUESTIONS:
            next_state = GuidedAssistantState(
                dict(state.known_facts), candidate_ids, state.asked_question_kinds,
                state.question_count, 'manual',
            )
            return _bounded_result(next_state, None, candidates, False)

        kind = self._select_kind(candidates, state.known_facts, state.asked_question_kinds)
        if kind is None:
            next_state = GuidedAssistantState(
                dict(state.known_facts), candidate_ids, state.asked_question_kinds,
                state.question_count, 'manual',
            )
            return _bounded_result(next_state, None, candidates, False)
        next_state = GuidedAssistantState(
            dict(state.known_facts), candidate_ids,
            self._append_kind(state.asked_question_kinds, kind),
            state.question_count, 'question',
        )
        return _bounded_result(next_state, self._field_question(kind, candidates), candidates, False)

    def _answer_candidate(
        self,
        state: GuidedAssistantState,
        question_kind: str,
        option_code: str | None,
        custom_value: object | None,
        candidates: tuple[Candidate, ...],
    ) -> GuidedResult:
        if custom_value is not None:
            raise ValueError('candidate selection does not accept custom_value')
        if question_kind != self._candidate_kind(state, candidates):
            raise ValueError('question_kind does not match the candidate question')
        if option_code in _NO_OP_CODES or option_code=='SKIP':
            return self._manual(state, candidates)
        if option_code == 'D':
            return self._manual(state, candidates)
        if option_code is None:
            raise ValueError('candidate selection requires A, B, or C')
        question = self._candidate_question(question_kind, candidates)
        selected_option = next((option for option in question.options if option.code == option_code), None)
        if selected_option is None:
            raise ValueError('option_code is not valid for the candidate question')
        selected = next(
            candidate for candidate in candidates
            if candidate.candidate_id == selected_option.value
        )
        # Conflicts are evidence that require explicit confirmation/overrides,
        # not a reason to discard a user-selected candidate into an opaque
        # manual state.  The existing /confirm route remains the authority
        # and will reject missing conflict overrides.
        stage = 'confirm'
        next_state = GuidedAssistantState(
            dict(state.known_facts), (selected.candidate_id,),
            state.asked_question_kinds, state.question_count, stage,
        )
        return _bounded_result(next_state, None, candidates, stage == 'confirm')

    @staticmethod
    def _manual(state: GuidedAssistantState, candidates: tuple[Candidate, ...]) -> GuidedResult:
        next_state = GuidedAssistantState(
            dict(state.known_facts),
            tuple(candidate.candidate_id for candidate in candidates[:MAX_GUIDED_CANDIDATES]),
            state.asked_question_kinds,
            state.question_count,
            'manual',
        )
        return _bounded_result(next_state, None, candidates, False)


__all__ = ['ListeningAssistantGuideService']
