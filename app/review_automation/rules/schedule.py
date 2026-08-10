"""Deterministic schedule and college checks with reviewable evidence."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from app.review_automation.contracts import (
    EvidenceStrength,
    FindingDraft,
    FindingSeverity,
    FindingSource,
    ScheduleCoverage,
)
from app.review_automation.schedules.normalization import (
    NormalizationError,
    normalize_college,
    normalize_date,
    normalize_name,
    normalize_weekday,
    parse_period_range,
    periods_overlap,
)

from .registry import RuleContext, RuleRevisionView, field_value


def _enum(value, enum_type, default):
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return default


def schedule_coverage(schedule: Any) -> ScheduleCoverage:
    if schedule is None:
        return ScheduleCoverage.NONE
    return _enum(
        field_value(schedule, 'coverage', default=ScheduleCoverage.NONE.value),
        ScheduleCoverage,
        ScheduleCoverage.NONE,
    )


def _slots(schedule: Any) -> tuple[Any, ...]:
    if schedule is None:
        return ()
    values = field_value(schedule, 'slots', default=())
    if values is None:
        return ()
    if isinstance(values, Mapping):
        return tuple(values.values())
    return tuple(values)


def _slot_value(slot: Any, name: str, default: Any = None) -> Any:
    value = field_value(slot, name, default=default)
    if name == 'weeks' and isinstance(value, str):
        try:
            return frozenset(int(item) for item in json.loads(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            return frozenset()
    return value


def _form_week(form: Any, semester_monday: date | datetime | None = None) -> int | None:
    explicit = field_value(form, 'teaching_week', 'lecture_week', 'week')
    if explicit is not None and str(explicit).strip():
        try:
            value = int(explicit)
            return value if value > 0 else None
        except (TypeError, ValueError):
            return None
    lecture_date = field_value(form, 'lecture_date', 'date')
    if lecture_date is None or semester_monday is None:
        return None
    try:
        current = normalize_date(lecture_date, semester_monday=semester_monday)
        monday = semester_monday.date() if isinstance(semester_monday, datetime) else semester_monday
        return ((current - monday).days // 7) + 1
    except (NormalizationError, TypeError, ValueError):
        return None


def _form_weekday(form: Any) -> int | None:
    value = field_value(form, 'weekday', 'lecture_weekday')
    if value is not None and str(value).strip():
        try:
            return normalize_weekday(value)
        except NormalizationError:
            return None
    lecture_date = field_value(form, 'lecture_date', 'date')
    if lecture_date is None:
        return None
    try:
        return normalize_date(lecture_date).weekday() + 1
    except NormalizationError:
        return None


def _form_periods(form: Any) -> tuple[int, int] | None:
    value = field_value(form, 'class_period', 'periods')
    if value is None or not str(value).strip():
        return None
    try:
        return parse_period_range(value)
    except NormalizationError:
        return None


def _listener_college(form: Any, explicit: str | None) -> str | None:
    value = explicit
    if value is None:
        value = field_value(form, 'listener_college', 'listener_department', 'department')
    if value is None:
        listener = field_value(form, 'listener', 'user')
        value = field_value(listener, 'college', 'department') if listener is not None else None
    if value is None:
        name = field_value(form, 'listener_name', default='') or ''
        if '+' in str(name):
            value = str(name).rsplit('+', 1)[1]
        elif '（' in str(name) and str(name).endswith('）'):
            value = str(name).rsplit('（', 1)[1][:-1]
    return value


def _safe_normalized_college(value: Any, aliases: Mapping[str, str] | None = None) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        return normalize_college(value, aliases=aliases)
    except NormalizationError:
        return None


def _finding(
    rule_key: str,
    severity: FindingSeverity,
    title: str,
    message: str,
    *,
    objective: bool,
    evidence_strength: EvidenceStrength,
    evidence: dict[str, Any],
) -> FindingDraft:
    return FindingDraft(
        rule_key=rule_key,
        source=FindingSource.SCHEDULE,
        severity=severity,
        title=title,
        message=message,
        objective=objective,
        evidence_strength=evidence_strength,
        evidence=evidence,
    )


def _candidate_value(candidate: Any, name: str, default: Any = None) -> Any:
    return field_value(candidate, name, default=default)


def evaluate_schedule_rules(
    form: Any,
    schedule: Any = None,
    *,
    school_matches: Iterable[Any] = (),
    listener_college: str | None = None,
    college_aliases: Mapping[str, str] | None = None,
    semester_monday: date | datetime | None = None,
    only_rule: str | None = None,
    rule_key_override: str | None = None,
) -> tuple[FindingDraft, ...]:
    """Evaluate schedule evidence without changing the form or review state."""
    findings: list[FindingDraft] = []
    week = _form_week(form, semester_monday=semester_monday)
    weekday = _form_weekday(form)
    periods = _form_periods(form)
    coverage = schedule_coverage(schedule)

    if only_rule in (None, 'same_college_teacher'):
        listener_value = _safe_normalized_college(_listener_college(form, listener_college), college_aliases)
        teacher_value = _safe_normalized_college(
            field_value(form, 'teacher_college', 'college'), college_aliases,
        )
        if listener_value and teacher_value and listener_value == teacher_value:
            findings.append(_finding(
                rule_key_override or 'same_college_teacher',
                FindingSeverity.HIGH,
                '信息员与授课教师属于同一学院',
                '规范化学院名称相同，属于客观高风险证据，需人工复核。',
                objective=True,
                evidence_strength=EvidenceStrength.EXACT,
                evidence={
                    'listener_college': listener_value,
                    'teacher_college': teacher_value,
                    'normalized_match': True,
                },
            ))

    if only_rule in (None, 'personal_schedule_conflict', 'class_schedule_conflict'):
        for slot in _slots(schedule):
            slot_weeks = _slot_value(slot, 'weeks', default=frozenset()) or frozenset()
            slot_weekday = _slot_value(slot, 'weekday')
            slot_start = _slot_value(slot, 'start_period')
            slot_end = _slot_value(slot, 'end_period')
            try:
                overlaps = (
                    week is not None
                    and weekday is not None
                    and periods is not None
                    and week in slot_weeks
                    and weekday == int(slot_weekday)
                    and periods_overlap(periods[0], periods[1], int(slot_start), int(slot_end))
                )
            except (TypeError, ValueError):
                overlaps = False
            if not overlaps:
                continue
            strength = _enum(
                _slot_value(slot, 'evidence_strength'),
                EvidenceStrength,
                EvidenceStrength.APPROXIMATE,
            )
            if strength == EvidenceStrength.EXACT and only_rule in (None, 'personal_schedule_conflict'):
                findings.append(_finding(
                    rule_key_override or 'personal_schedule_conflict',
                    FindingSeverity.HIGH,
                    '精确个人课表存在时间冲突',
                    '个人课表与听课记录在同一教学周、星期和节次重叠，属于客观高风险证据。',
                    objective=True,
                    evidence_strength=EvidenceStrength.EXACT,
                    evidence={
                        'coverage': coverage.value,
                        'week': week,
                        'weekday': weekday,
                        'periods': {'start': periods[0], 'end': periods[1]},
                        'schedule_periods': {'start': int(slot_start), 'end': int(slot_end)},
                        'course_title': _slot_value(slot, 'course_title'),
                    },
                ))
            elif strength == EvidenceStrength.APPROXIMATE and only_rule in (None, 'class_schedule_conflict'):
                findings.append(_finding(
                    rule_key_override or 'class_schedule_conflict',
                    FindingSeverity.REVIEW,
                    '行政班推导课表存在近似冲突',
                    '行政班映射推导出的课表与听课记录时间重叠，证据为 approximate，仅建议人工复核。',
                    objective=True,
                    evidence_strength=EvidenceStrength.APPROXIMATE,
                    evidence={
                        'coverage': coverage.value,
                        'week': week,
                        'weekday': weekday,
                        'periods': {'start': periods[0], 'end': periods[1]},
                        'schedule_periods': {'start': int(slot_start), 'end': int(slot_end)},
                        'admin_classes': list(field_value(schedule, 'admin_classes', default=()) or ()),
                        'course_title': _slot_value(slot, 'course_title'),
                    },
                ))

    if only_rule in (None, 'school_schedule_mismatch'):
        candidates = tuple(school_matches or ())
        mismatched = [item for item in candidates if _candidate_value(item, 'mismatched_fields', ())]
        if mismatched:
            confidences = [float(_candidate_value(item, 'confidence', 0.0)) for item in mismatched]
            ambiguous = len(mismatched) > 1 and len(set(confidences)) == 1
            findings.append(_finding(
                rule_key_override or 'school_schedule_mismatch',
                FindingSeverity.REVIEW,
                '全校课表候选存在字段不一致',
                '已找到课程或教师锚点候选，但存在学院、时间、地点等字段不一致，建议人工复核。',
                objective=not ambiguous,
                evidence_strength=EvidenceStrength.APPROXIMATE if ambiguous else EvidenceStrength.EXACT,
                evidence={
                    'candidate_count': len(mismatched),
                    'match_confidence': confidences,
                    'matched_fields': [list(_candidate_value(item, 'matched_fields', ())) for item in mismatched],
                    'mismatched_fields': [list(_candidate_value(item, 'mismatched_fields', ())) for item in mismatched],
                    'ambiguous': ambiguous,
                },
            ))

    return tuple(findings)


def evaluate_rule_revision(context: RuleContext, revision: RuleRevisionView):
    return evaluate_schedule_rules(
        context.form,
        context.schedule,
        school_matches=context.school_matches,
        listener_college=context.listener_college,
        college_aliases=context.college_aliases,
        semester_monday=context.semester_monday,
        only_rule=revision.handler,
        rule_key_override=revision.rule_key,
    )


run_schedule_rules = evaluate_schedule_rules


__all__ = [
    'evaluate_rule_revision',
    'evaluate_schedule_rules',
    'run_schedule_rules',
    'schedule_coverage',
]
