"""Safe listening-assistant selection evidence and submit-time revalidation.

This module is deliberately the only persistence boundary for assistant
selection state.  The client may provide a candidate id and query, but the
candidate snapshot is always rebuilt from a fresh server-side search result.
Only normalized schedule/course fields and explicit confirmation actions are
serialized; student signatures, contact numbers, evaluation text, credentials,
and arbitrary request data never enter the evidence row.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

from app.models import ListeningAssistantEvidence, db
from app.services.listening_assistant import (
    BACKUP_FALLBACK_REASONS,
    ListeningAssistantService,
)
from app.services.listening_assistant_contracts import (
    AssistantQuery,
    Candidate,
    ConfirmationResult,
    SAFE_OVERRIDE_KEYS,
    parse_period,
)


SELECTION_KEYS = frozenset({
    'stage',
    'query',
    'rejected_ids',
    'source_kind',
    'source_batch_id',
    'semester',
    'candidate_id',
    'overrides',
    'template_version',
    'fallback_reason',
    'reason',
    'explicit_fallback',
    'acknowledged_source',
    'assistant_filled_fields',
    'assistant_filled_groups',
})
QUERY_KEYS = frozenset({
    'lecture_date',
    'room',
    'teacher_name',
    'period',
    'student_grade_class',
    'semester',
})
CONFIRMED_STAGES = frozenset({'confirmed', 'done', 'complete'})
SNAPSHOT_FIELDS = (
    'lecture_date',
    'class_period',
    'lecture_location',
    'teacher_name',
    'teacher_college',
    'course_title',
    'student_grade_class',
)
ASSISTANT_FILLED_FIELD_KEYS = frozenset({
    'lecture_date',
    'lecture_location',
    'teacher_name',
    'teacher_college',
    'course_title',
    'student_grade_class',
})
ASSISTANT_FILLED_GROUP_KEYS = frozenset({'period'})
MAX_TEMPLATE_VERSION_LENGTH = 100
MAX_SEMESTER_LENGTH = 50


class AssistantSelectionError(ValueError):
    """Raised when a client selection cannot be safely revalidated."""

    def __init__(self, message: str, *, code: str = 'invalid_assistant_selection'):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class NormalizedAssistantSelection:
    """Fresh, typed, JSON-safe selection data for a form transaction."""

    user_id: int
    query: AssistantQuery
    query_payload: Mapping[str, Any]
    candidate: Candidate
    candidate_payload: Mapping[str, Any]
    source_kind: str
    source_batch_id: str
    semester: str
    template_version: str
    overrides: Mapping[str, Any]
    confirmation: ConfirmationResult
    field_snapshot: Mapping[str, Any]
    stage: str | None = None
    fallback_reason: str | None = None
    explicit_fallback: bool = False

    @property
    def snapshot(self) -> Mapping[str, Any]:
        """Short alias used by form integration callers."""
        return self.field_snapshot

    @property
    def candidate_json(self) -> Mapping[str, Any]:
        return self.candidate_payload


def _text(value: object, *, field_name: str, required: bool = False, maximum: int | None = None) -> str:
    if value is None:
        if required:
            raise AssistantSelectionError(f'{field_name} is required')
        return ''
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise AssistantSelectionError(f'{field_name} must be text')
    normalized = unicodedata.normalize('NFKC', str(value)).replace('\u3000', ' ')
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    if maximum is not None and len(normalized) > maximum:
        raise AssistantSelectionError(f'{field_name} is too long')
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise AssistantSelectionError(f'{field_name} contains an invalid control character')
    if required and not normalized:
        raise AssistantSelectionError(f'{field_name} is required')
    return normalized


def _optional_text(value: object, *, field_name: str, maximum: int | None = None) -> str | None:
    normalized = _text(value, field_name=field_name, maximum=maximum)
    return normalized or None


def _required_bool(payload: Mapping[str, Any], key: str, *, default: bool = False) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise AssistantSelectionError(f'{key} must be a boolean')
    return value


def _validate_assistant_filled_provenance(payload: Mapping[str, Any]) -> None:
    """Validate client-side fill provenance without treating it as authority."""
    for key, allowed in (
        ('assistant_filled_fields', ASSISTANT_FILLED_FIELD_KEYS),
        ('assistant_filled_groups', ASSISTANT_FILLED_GROUP_KEYS),
    ):
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            raise AssistantSelectionError(f'{key} must be a list')
        for item in value:
            if not isinstance(item, str) or item not in allowed:
                raise AssistantSelectionError(f'{key} contains an unsupported value')


def _json_safe_copy(value: Any) -> Any:
    """Copy a contract value through JSON so no mutable client object is kept."""
    return json.loads(json.dumps(value, ensure_ascii=False, separators=(',', ':')))


def _date_from_payload(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise AssistantSelectionError('query.lecture_date must be YYYY-MM-DD')
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise AssistantSelectionError('query.lecture_date must be YYYY-MM-DD') from error


def _normalize_query(raw_query: object) -> tuple[AssistantQuery, dict[str, Any], str | None]:
    if not isinstance(raw_query, Mapping):
        raise AssistantSelectionError('query must be an object')
    unknown = set(raw_query) - QUERY_KEYS
    if unknown:
        raise AssistantSelectionError('query contains unsupported fields')
    if 'lecture_date' not in raw_query:
        raise AssistantSelectionError('query.lecture_date is required')

    query_semester = None
    if 'semester' in raw_query:
        query_semester = _text(
            raw_query.get('semester'),
            field_name='query.semester',
            required=True,
            maximum=MAX_SEMESTER_LENGTH,
        )

    values: dict[str, Any] = {
        'lecture_date': _date_from_payload(raw_query.get('lecture_date')),
    }
    for key in ('room', 'teacher_name', 'student_grade_class'):
        if key in raw_query and raw_query[key] is not None:
            if not isinstance(raw_query[key], str):
                raise AssistantSelectionError(f'query.{key} must be text or None')
            values[key] = raw_query[key]
        else:
            values[key] = None

    if 'period' in raw_query:
        period = raw_query['period']
        if isinstance(period, Mapping) or isinstance(period, bool):
            raise AssistantSelectionError('query.period must be a valid period')
        if isinstance(period, (list, tuple)) and not all(
            isinstance(item, int) and not isinstance(item, bool) for item in period
        ):
            raise AssistantSelectionError('query.period must be a valid period')
        values['period'] = period
    else:
        values['period'] = None

    try:
        query = AssistantQuery(**values)
    except (TypeError, ValueError) as error:
        raise AssistantSelectionError(f'invalid query: {error}') from error

    normalized_query = {
        'lecture_date': query.lecture_date.isoformat(),
        'room': query.room,
        'teacher_name': query.teacher_name,
        'period': list(query.period) if query.period is not None else None,
        'student_grade_class': query.student_grade_class,
    }
    if query_semester is not None:
        normalized_query['semester'] = query_semester
    return query, normalized_query, query_semester


def _normalize_semester(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _optional_text(value, field_name=field_name, maximum=MAX_SEMESTER_LENGTH)


def _normalize_rejected_ids(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise AssistantSelectionError('rejected_ids must be a list')
    result: list[str] = []
    for index, item in enumerate(value):
        normalized = _text(item, field_name=f'rejected_ids[{index}]')
        if normalized and normalized not in result:
            result.append(normalized)
    return tuple(result)


def _candidate_from_value(value: object) -> Candidate:
    if isinstance(value, Candidate):
        return value
    if not isinstance(value, Mapping):
        raise AssistantSelectionError('search returned an invalid candidate')
    try:
        raw_date = value.get('lecture_date')
        return Candidate(
            candidate_id=value.get('candidate_id'),
            lecture_date=_date_from_payload(raw_date),
            room=value.get('room', ''),
            period=value.get('period'),
            course_title=value.get('course_title', ''),
            teacher_name=value.get('teacher_name', ''),
            teacher_college=value.get('teacher_college', ''),
            student_grade_class=value.get('student_grade_class', ''),
            source_kind=value.get('source_kind', ''),
            source_label=value.get('source_label', ''),
            source_batch_id=value.get('source_batch_id'),
            course_code=value.get('course_code', ''),
            selection_code=value.get('selection_code', ''),
            weekday=value.get('weekday'),
            conflicts=value.get('conflicts', ()),
            needs_confirmation=value.get('needs_confirmation', False),
        )
    except (TypeError, ValueError) as error:
        raise AssistantSelectionError('search returned an invalid candidate') from error


def _period_display(value: object) -> str:
    period = parse_period(value)
    if period is None:
        raise AssistantSelectionError('candidate period is invalid')
    start, end = period
    return f'第{start}节' if start == end else f'第{start}-{end}节'


def safe_field_snapshot(
    candidate: Candidate | Mapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Map one fresh candidate to only the seven safe LectureForm fields."""
    normalized_candidate = _candidate_from_value(candidate)
    confirmation = ConfirmationResult(
        confirmed=True,
        overrides=overrides,
    )
    snapshot: dict[str, Any] = {
        'lecture_date': normalized_candidate.lecture_date.isoformat(),
        'class_period': _period_display(normalized_candidate.period),
        'lecture_location': normalized_candidate.room,
        'teacher_name': normalized_candidate.teacher_name,
        'teacher_college': normalized_candidate.teacher_college,
        'course_title': normalized_candidate.course_title,
        'student_grade_class': normalized_candidate.student_grade_class,
    }
    aliases = {
        'room': 'lecture_location',
        'period': 'class_period',
    }
    for key, value in confirmation.overrides.items():
        target = aliases.get(key, key)
        if target not in SNAPSHOT_FIELDS:
            # ConfirmationResult currently prevents this branch.  Keeping the
            # allowlist here makes the helper safe if that contract evolves.
            continue
        if key in {'period', 'class_period'}:
            snapshot[target] = None if value is None else _period_display(value)
        elif key == 'lecture_date' and value is not None:
            # ConfirmationResult already validates this exact date shape.
            snapshot[target] = value
        else:
            snapshot[target] = value
    return snapshot


def _fresh_candidates(result: object) -> tuple[Candidate, ...]:
    if isinstance(result, Mapping):
        values = result.get('candidates', ())
    else:
        values = getattr(result, 'candidates', ())
    try:
        return tuple(_candidate_from_value(value) for value in (values or ()))
    except TypeError as error:
        raise AssistantSelectionError('search returned an invalid candidate list') from error


def _has_non_empty_override(overrides: Mapping[str, Any], *keys: str) -> bool:
    for key in keys:
        if key not in overrides:
            continue
        value = overrides[key]
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return True
    return False


def _require_candidate_conflict_overrides(
    candidate: Candidate,
    overrides: Mapping[str, Any],
) -> None:
    conflicts = set(candidate.conflicts)
    missing: list[str] = []
    if (
        'period_mismatch' in conflicts
        or 'venue_period_needs_confirmation' in conflicts
    ) and not _has_non_empty_override(overrides, 'period', 'class_period'):
        missing.append('period')
    if 'date_needs_confirmation' in conflicts and not _has_non_empty_override(
        overrides,
        'lecture_date',
    ):
        missing.append('lecture_date')
    if any('location' in conflict for conflict in conflicts) and not _has_non_empty_override(
        overrides,
        'room',
        'lecture_location',
    ):
        missing.append('room')
    if missing:
        raise AssistantSelectionError(
            'assistant candidate conflict requires an explicit override',
            code='conflict_requires_override',
        )


def revalidate_selection(
    user: object,
    selection_payload: Mapping[str, Any],
    service: ListeningAssistantService | None = None,
    semester: str | None = None,
) -> NormalizedAssistantSelection:
    """Re-run the authoritative search and normalize one confirmed choice.

    The caller owns the transaction.  This function performs no writes and
    never trusts a client-provided candidate snapshot.
    """
    if not getattr(user, 'id', None):
        raise AssistantSelectionError('selection user is required')
    if not isinstance(selection_payload, Mapping):
        raise AssistantSelectionError('assistant payload must be an object')
    unknown = set(selection_payload) - SELECTION_KEYS
    if unknown:
        raise AssistantSelectionError('assistant payload contains unsupported fields')
    _validate_assistant_filled_provenance(selection_payload)

    if 'stage' not in selection_payload:
        raise AssistantSelectionError('assistant selection stage is required')
    stage = _text(selection_payload.get('stage'), field_name='stage', required=True)
    if stage not in CONFIRMED_STAGES:
        raise AssistantSelectionError('assistant selection is not confirmed')

    source_kind = _text(
        selection_payload.get('source_kind'),
        field_name='source_kind',
        required=True,
    ).lower()
    if source_kind not in {'primary', 'backup'}:
        raise AssistantSelectionError('source_kind must be primary or backup')

    query, query_payload, query_semester = _normalize_query(selection_payload.get('query'))
    if not query.lookup_anchors:
        raise AssistantSelectionError('query requires a room or teacher_name anchor')
    requested_semester = _normalize_semester(semester, field_name='semester')
    payload_semester = _normalize_semester(
        selection_payload.get('semester'),
        field_name='semester',
    )
    if requested_semester and payload_semester and requested_semester != payload_semester:
        raise AssistantSelectionError('selection semester does not match the current request')
    if query_semester and payload_semester and query_semester != payload_semester:
        raise AssistantSelectionError('query semester does not match selection semester')
    selected_semester = payload_semester or query_semester or requested_semester
    if not selected_semester:
        raise AssistantSelectionError('selection requires an explicit semester')

    service = service or ListeningAssistantService(semester=selected_semester)

    source_batch_id = _optional_text(
        selection_payload.get('source_batch_id'),
        field_name='source_batch_id',
        maximum=100,
    )
    explicit_fallback = _required_bool(selection_payload, 'explicit_fallback')
    acknowledged_source = _required_bool(selection_payload, 'acknowledged_source')
    reason_value = selection_payload.get(
        'fallback_reason',
        selection_payload.get('reason'),
    )
    fallback_reason = None
    if reason_value is not None:
        fallback_reason = _text(reason_value, field_name='fallback_reason', required=True)
        if fallback_reason not in BACKUP_FALLBACK_REASONS:
            raise AssistantSelectionError('fallback_reason is not supported')

    if source_kind == 'backup':
        if not explicit_fallback:
            raise AssistantSelectionError('backup selection requires explicit fallback')
        if not acknowledged_source:
            raise AssistantSelectionError('backup selection requires source acknowledgement')
        if not source_batch_id:
            raise AssistantSelectionError('backup selection requires source_batch_id')
        if not selected_semester:
            raise AssistantSelectionError('backup selection requires semester')
        if fallback_reason is None:
            raise AssistantSelectionError('backup selection requires fallback_reason')
    elif explicit_fallback or acknowledged_source or fallback_reason is not None:
        raise AssistantSelectionError('backup confirmation fields are only valid for backup sources')

    candidate_id = _text(
        selection_payload.get('candidate_id'),
        field_name='candidate_id',
        required=True,
        maximum=300,
    )
    template_version = _text(
        selection_payload.get('template_version'),
        field_name='template_version',
        required=True,
        maximum=MAX_TEMPLATE_VERSION_LENGTH,
    )
    rejected_ids = _normalize_rejected_ids(selection_payload.get('rejected_ids'))

    if source_kind == 'backup':
        fresh_result = service.search_backup(
            query,
            source_batch_id=source_batch_id,
            semester=selected_semester,
            rejected_ids=rejected_ids,
            explicit_fallback=True,
            reason=fallback_reason,
        )
    else:
        fresh_result = service.search(
            query,
            semester=selected_semester,
            rejected_ids=rejected_ids,
        )

    candidates = _fresh_candidates(fresh_result)
    selected = next(
        (candidate for candidate in candidates if candidate.candidate_id == candidate_id),
        None,
    )
    if selected is None:
        raise AssistantSelectionError(
            'assistant candidate is stale or unknown',
            code='candidate_not_found',
        )
    if selected.source_kind != source_kind:
        raise AssistantSelectionError('candidate source kind does not match selection')
    selected_batch_id = _optional_text(
        selected.source_batch_id,
        field_name='candidate.source_batch_id',
        maximum=100,
    )
    if not selected_batch_id:
        raise AssistantSelectionError('candidate has no source batch')
    if source_batch_id and selected_batch_id != source_batch_id:
        raise AssistantSelectionError('candidate source batch does not match selection')
    if source_kind == 'backup' and selected_batch_id != source_batch_id:
        raise AssistantSelectionError('backup candidate source batch does not match selection')

    try:
        confirmation = ConfirmationResult(
            confirmed=True,
            candidate_id=selected.candidate_id,
            source_kind=source_kind,
            source_batch_id=selected_batch_id,
            overrides=selection_payload.get('overrides'),
            acknowledged_source=acknowledged_source,
        )
    except (TypeError, ValueError) as error:
        raise AssistantSelectionError(f'invalid assistant overrides: {error}') from error

    overrides = _json_safe_copy(confirmation.overrides)
    _require_candidate_conflict_overrides(selected, overrides)
    candidate_payload = _json_safe_copy(selected.to_public_dict())
    field_snapshot = safe_field_snapshot(selected, overrides=overrides)
    confirmation_payload = {
        'confirmed': confirmation.confirmed,
        'candidate_id': selected.candidate_id,
        'source_kind': source_kind,
        'source_batch_id': selected_batch_id,
        'acknowledged_source': confirmation.acknowledged_source,
        'stage': stage,
        'explicit_fallback': explicit_fallback,
        'fallback_reason': fallback_reason,
    }

    return NormalizedAssistantSelection(
        user_id=int(user.id),
        query=query,
        query_payload=_json_safe_copy(query_payload),
        candidate=selected,
        candidate_payload=candidate_payload,
        source_kind=source_kind,
        source_batch_id=selected_batch_id,
        semester=selected_semester,
        template_version=template_version,
        overrides=overrides,
        confirmation=confirmation,
        field_snapshot=field_snapshot,
        stage=stage,
        fallback_reason=fallback_reason,
        explicit_fallback=explicit_fallback,
    )


def _dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def create_evidence(
    user: object,
    lecture_form: object | None,
    selection_payload: NormalizedAssistantSelection | Mapping[str, Any],
    service: ListeningAssistantService | None = None,
    semester: str | None = None,
) -> ListeningAssistantEvidence:
    """Stage one evidence row in the caller's transaction; never commit."""
    user_id = getattr(user, 'id', None)
    if not user_id:
        raise AssistantSelectionError('evidence user is required')
    if isinstance(selection_payload, NormalizedAssistantSelection):
        normalized = selection_payload
    else:
        normalized = revalidate_selection(
            user,
            selection_payload,
            service=service,
            semester=semester,
        )
    if normalized.user_id != int(user_id):
        raise AssistantSelectionError('evidence user does not match selection')

    lecture_form_id = None
    if lecture_form is not None:
        lecture_form_id = getattr(lecture_form, 'id', None)
        if not lecture_form_id:
            raise AssistantSelectionError('lecture form must be flushed before evidence')
        user_number = _optional_text(
            getattr(user, 'number', None),
            field_name='user.number',
            maximum=50,
        )
        form_number = _optional_text(
            getattr(lecture_form, 'listener_number', None),
            field_name='lecture_form.listener_number',
            maximum=50,
        )
        if not user_number or not form_number or user_number != form_number:
            raise AssistantSelectionError('evidence user does not own lecture form')

    evidence = ListeningAssistantEvidence(
        user_id=int(user_id),
        lecture_form_id=lecture_form_id,
        source_kind=normalized.source_kind,
        source_batch_id=normalized.source_batch_id,
        semester=normalized.semester,
        template_version=normalized.template_version,
        query_json=_dump_json(normalized.query_payload),
        candidate_json=_dump_json(normalized.candidate_payload),
        overrides_json=_dump_json(normalized.overrides),
        confirmation_json=_dump_json({
            key: value
            for key, value in {
                'confirmed': normalized.confirmation.confirmed,
                'candidate_id': normalized.candidate.candidate_id,
                'source_kind': normalized.source_kind,
                'source_batch_id': normalized.source_batch_id,
                'acknowledged_source': normalized.confirmation.acknowledged_source,
                'stage': normalized.stage,
                'explicit_fallback': normalized.explicit_fallback,
                'fallback_reason': normalized.fallback_reason,
            }.items()
            if value is not None
        }),
        confirmed_at=datetime.now(),
    )
    db.session.add(evidence)
    return evidence


# Explicit aliases keep the helper discoverable to form/API slices without
# duplicating the safety logic.
build_safe_field_snapshot = safe_field_snapshot
field_snapshot_for_form = safe_field_snapshot


__all__ = [
    'AssistantSelectionError',
    'NormalizedAssistantSelection',
    'SELECTION_KEYS',
    'SNAPSHOT_FIELDS',
    'build_safe_field_snapshot',
    'create_evidence',
    'field_snapshot_for_form',
    'revalidate_selection',
    'safe_field_snapshot',
]
