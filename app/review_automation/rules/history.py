"""History rules over latest logical form versions and synthetic evidence only."""

from __future__ import annotations

import difflib
import re
from dataclasses import replace
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from app.review_automation.contracts import (
    EvidenceStrength,
    FindingDraft,
    FindingSeverity,
    FindingSource,
)
from app.review_automation.schedules.normalization import (
    NormalizationError,
    normalize_date,
    normalize_identifier,
    normalize_name,
    normalize_weekday,
    parse_period_range,
)
from app.review_automation.rules.text import normalize_feedback

from .registry import RuleContext, RuleRevisionView, field_value


def logical_form_key(record: Any) -> tuple[str, str]:
    """Return the COALESCE(unique_id, id) logical-form identity."""
    unique_id = field_value(record, 'unique_id')
    if unique_id is not None and str(unique_id).strip():
        return 'unique_id', str(unique_id)
    return 'id', str(field_value(record, 'id', default=''))


def _order_value(record: Any):
    value = field_value(record, 'updated_at', 'created_at')
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time()).timestamp()
    if value is None:
        return 0.0
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return 0.0


def latest_logical_forms(records: Iterable[Any]) -> tuple[Any, ...]:
    """Deduplicate exports and old versions before any cross-record count."""
    latest: dict[tuple[str, str], Any] = {}
    for record in records or ():
        key = logical_form_key(record)
        current = latest.get(key)
        if current is None or (_order_value(record), str(field_value(record, 'id', default=''))) >= (
            _order_value(current), str(field_value(current, 'id', default='')),
        ):
            latest[key] = record
    return tuple(sorted(
        latest.values(),
        key=lambda item: (str(field_value(item, 'listener_number', 'student_id', default='')), logical_form_key(item)),
    ))


def _listener_key(record: Any) -> str:
    for name in ('listener_number', 'student_id', 'listener_id'):
        value = field_value(record, name)
        if value is not None and str(value).strip():
            try:
                return normalize_identifier(value)
            except NormalizationError:
                return str(value).strip()
    return str(field_value(record, 'listener_name', default='')).strip()


def _week(record: Any, semester_monday: date | datetime | None = None) -> int | None:
    value = field_value(record, 'teaching_week', 'lecture_week', 'week')
    if value is not None and str(value).strip():
        try:
            parsed = int(value)
            return parsed if parsed > 0 else None
        except (TypeError, ValueError):
            return None
    lecture_date = field_value(record, 'lecture_date', 'date')
    if lecture_date is None or semester_monday is None:
        return None
    try:
        current = normalize_date(lecture_date, semester_monday=semester_monday)
        monday = semester_monday.date() if isinstance(semester_monday, datetime) else semester_monday
        return ((current - monday).days // 7) + 1
    except (NormalizationError, TypeError, ValueError):
        return None


def _weekday(record: Any) -> int | None:
    value = field_value(record, 'weekday', 'lecture_weekday')
    if value is not None and str(value).strip():
        try:
            return normalize_weekday(value)
        except NormalizationError:
            return None
    lecture_date = field_value(record, 'lecture_date', 'date')
    if lecture_date is None:
        return None
    try:
        return normalize_date(lecture_date).weekday() + 1
    except NormalizationError:
        return None


def _periods(record: Any) -> tuple[int, int] | None:
    value = field_value(record, 'class_period', 'periods')
    if value is None or not str(value).strip():
        return None
    try:
        return parse_period_range(value)
    except NormalizationError:
        return None


def _teacher(record: Any) -> str:
    value = field_value(record, 'teacher_name', default='') or ''
    try:
        return normalize_name(value)
    except NormalizationError:
        return str(value).strip()


def _witnesses(record: Any) -> tuple[dict[str, str], ...]:
    source = field_value(record, 'witnesses')
    values = []
    if source:
        for item in source:
            name = field_value(item, 'name', 'student_signature', default='')
            phone = field_value(item, 'phone', 'contact_phone', default='')
            values.append((name, phone))
    else:
        for index in (1, 2):
            values.append((
                field_value(record, f'student_signature{index}', f'witness_name{index}', default=''),
                field_value(record, f'contact_phone{index}', f'witness_phone{index}', default=''),
            ))
    result = []
    for name, phone in values:
        if name is None and phone is None:
            continue
        try:
            normalized_name = normalize_name(name) if name and str(name).strip() else ''
        except NormalizationError:
            normalized_name = str(name or '').strip()
        try:
            normalized_phone = normalize_identifier(phone) if phone and str(phone).strip() else ''
        except NormalizationError:
            normalized_phone = str(phone or '').strip()
        if normalized_name or normalized_phone:
            result.append({'name': normalized_name, 'phone': normalized_phone})
    return tuple(result)


def _same_listener(current: Any, record: Any) -> bool:
    return _listener_key(current) == _listener_key(record)


def _same_slot(record: Any):
    week = _week(record)
    weekday = _weekday(record)
    periods = _periods(record)
    listener = _listener_key(record)
    if week is None or weekday is None or periods is None or not listener:
        return None
    return listener, week, weekday, periods[0], periods[1]


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
        source=FindingSource.HISTORY,
        severity=severity,
        title=title,
        message=message,
        objective=objective,
        evidence_strength=evidence_strength,
        evidence=evidence,
    )


def evaluate_history_rules(
    form: Any,
    records: Iterable[Any],
    *,
    parameters: Mapping[str, Any] | None = None,
    only_rule: str | None = None,
    rule_key_override: str | None = None,
    semester_monday: date | datetime | None = None,
) -> tuple[FindingDraft, ...]:
    """Evaluate history evidence using only latest logical form versions."""
    parameters = dict(parameters or {})
    rows = list(latest_logical_forms(records or ()))
    current_key = logical_form_key(form)
    if not any(logical_form_key(item) == current_key for item in rows):
        rows.append(form)
    current_listener = _listener_key(form)
    findings: list[FindingDraft] = []

    def enabled(name):
        return only_rule in (None, name)

    def key(name):
        return rule_key_override or name

    if enabled('witness_reused_across_weeks') or enabled('witness_phone_name_conflict'):
        current_witnesses = _witnesses(form)
        for witness in current_witnesses:
            phone = witness['phone']
            name = witness['name']
            if not phone:
                continue
            matching = [
                item for item in rows
                if _same_listener(form, item)
                and any(other['phone'] == phone for other in _witnesses(item))
            ]
            week_map = {
                _week(item, semester_monday=semester_monday): logical_form_key(item)
                for item in matching
                if _week(item, semester_monday=semester_monday) is not None
            }
            distinct_weeks = sorted(week_map)
            minimum = int(parameters.get('minimum_distinct_weeks', 2))
            candidate_threshold = int(parameters.get('high_risk_candidate_weeks', 3))
            if enabled('witness_reused_across_weeks') and len(distinct_weeks) >= minimum:
                same_name = bool(name) and all(
                    any(other['phone'] == phone and other['name'] == name for other in _witnesses(item))
                    for item in matching
                )
                mode = 'name_and_phone' if same_name else 'phone_primary'
                candidate = len(distinct_weeks) >= candidate_threshold
                findings.append(_finding(
                    key('witness_reused_across_weeks'),
                    FindingSeverity.HIGH if candidate else FindingSeverity.REVIEW,
                    '同一信息员跨教学周重复使用见证人',
                    '同一信息员在不同教学周使用相同手机号见证人，建议人工复核。' + (
                        ' 已达到高风险候选次数，仍需结合其他客观证据。' if candidate else ''
                    ),
                    objective=not candidate,
                    evidence_strength=EvidenceStrength.EXACT,
                    evidence={
                        'match_mode': mode,
                        'identity_confirmed': mode == 'name_and_phone',
                        'distinct_weeks': distinct_weeks,
                        'distinct_form_count': len({logical_form_key(item) for item in matching}),
                        'high_risk_candidate': candidate,
                    },
                ))

        # Scan all witnesses belonging to this listener. The current witness
        # is not a required anchor for this weak anomaly: a name reused with
        # different phones is itself the observation, while other listeners'
        # records remain outside the comparison scope.
        name_phones: dict[str, set[str]] = {}
        name_forms: dict[str, set[tuple[str, str]]] = {}
        for item in rows:
            if not _same_listener(form, item):
                continue
            for witness in _witnesses(item):
                if witness['name'] and witness['phone']:
                    name_phones.setdefault(witness['name'], set()).add(witness['phone'])
                    name_forms.setdefault(witness['name'], set()).add(logical_form_key(item))
        weak_names = [name for name, phones in name_phones.items() if len(phones) > 1]
        if enabled('witness_reused_across_weeks') and weak_names:
            findings.append(_finding(
                key('witness_reused_across_weeks'),
                FindingSeverity.REVIEW,
                '见证人姓名相同但手机号不同',
                '相同姓名对应不同手机号，仅作为弱证据，建议人工复核。',
                objective=False,
                evidence_strength=EvidenceStrength.WEAK,
                evidence={
                    'match_mode': 'same_name_different_phone',
                    'distinct_form_count': len({
                        form_id for name in weak_names for form_id in name_forms[name]
                    }),
                },
            ))

    if enabled('witness_phone_name_conflict'):
        phone_names: dict[str, set[str]] = {}
        phone_forms: dict[str, set[tuple[str, str]]] = {}
        for item in rows:
            for witness in _witnesses(item):
                if witness['phone'] and witness['name']:
                    phone_names.setdefault(witness['phone'], set()).add(witness['name'])
                    phone_forms.setdefault(witness['phone'], set()).add(logical_form_key(item))
        for phone, names in phone_names.items():
            if len(names) > 1 and any(item['phone'] == phone for item in _witnesses(form)):
                findings.append(_finding(
                    key('witness_phone_name_conflict'),
                    FindingSeverity.REVIEW,
                    '手机号对应多个见证人姓名',
                    '不同见证人姓名共用同一手机号，属于独立身份异常。',
                    objective=True,
                    evidence_strength=EvidenceStrength.EXACT,
                    evidence={
                        'anomaly': 'different_name_same_phone',
                        'name_count': len(names),
                        'form_count': len(phone_forms.get(phone, set())),
                    },
                ))
                break

    if enabled('consecutive_teacher_weeks'):
        maximum_gap = int(parameters.get('maximum_week_gap', 1))
        teacher = _teacher(form)
        weeks = sorted({
            _week(item, semester_monday=semester_monday)
            for item in rows
            if _same_listener(form, item) and _teacher(item) == teacher
            and _week(item, semester_monday=semester_monday) is not None
        })
        pairs = [
            (left, right) for left, right in zip(weeks, weeks[1:])
            if 0 < right - left <= maximum_gap
        ]
        if pairs:
            findings.append(_finding(
                key('consecutive_teacher_weeks'),
                FindingSeverity.REVIEW,
                '同一信息员连续教学周听同一教师',
                '同一信息员连续教学周出现同一教师，建议人工复核；非连续周不触发。',
                objective=True,
                evidence_strength=EvidenceStrength.APPROXIMATE,
                evidence={'teacher_name': teacher, 'consecutive_week_pairs': [list(pair) for pair in pairs]},
            ))

    if enabled('same_listener_same_slot'):
        current_slot = _same_slot(form)
        if current_slot is not None:
            same_slot = [
                item for item in rows
                if _same_slot(item) == current_slot
            ]
            logical_ids = {logical_form_key(item) for item in same_slot}
            if len(logical_ids) > 1:
                findings.append(_finding(
                    key('same_listener_same_slot'),
                    FindingSeverity.HIGH,
                    '同一信息员同一时段存在不同逻辑表单',
                    '去除重复导出和旧版本后，同一信息员在同一教学周、星期和节次仍有不同逻辑表单。',
                    objective=True,
                    evidence_strength=EvidenceStrength.EXACT,
                    evidence={
                        'week': current_slot[1],
                        'weekday': current_slot[2],
                        'periods': {'start': current_slot[3], 'end': current_slot[4]},
                        'logical_form_count': len(logical_ids),
                    },
                ))

    if enabled('feedback_similarity'):
        current_feedback = normalize_feedback(field_value(form, 'course_feedback', 'feedback', default=''))
        prefix = '该老师'
        if current_feedback.startswith(prefix):
            current_body = current_feedback[len(prefix):]
            threshold = float(parameters.get('similarity_threshold', 0.9))
            for item in rows:
                if logical_form_key(item) == current_key or not _same_listener(form, item):
                    continue
                other_feedback = normalize_feedback(field_value(item, 'course_feedback', 'feedback', default=''))
                if not other_feedback.startswith(prefix):
                    continue
                similarity = difflib.SequenceMatcher(None, current_body, other_feedback[len(prefix):]).ratio()
                if similarity >= threshold:
                    findings.append(_finding(
                        key('feedback_similarity'),
                        FindingSeverity.REVIEW,
                        '反馈文本与其他逻辑表单高度相似',
                        '排除同一逻辑表单和旧版本后，规范化反馈仍高度相似，建议人工复核。',
                        objective=False,
                        evidence_strength=EvidenceStrength.APPROXIMATE,
                        evidence={
                            'similarity': round(similarity, 4),
                            'required_prefix': prefix,
                            'compared_logical_form': logical_form_key(item),
                        },
                    ))

    return tuple(findings)


def evaluate_rule_revision(context: RuleContext, revision: RuleRevisionView):
    return evaluate_history_rules(
        context.form,
        context.history,
        parameters=revision.parameters,
        only_rule=revision.handler,
        rule_key_override=revision.rule_key,
        semester_monday=context.semester_monday,
    )


run_history_rules = evaluate_history_rules


__all__ = [
    'evaluate_history_rules',
    'evaluate_rule_revision',
    'latest_logical_forms',
    'logical_form_key',
    'run_history_rules',
]
