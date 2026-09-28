"""Contracts and conservative normalization for the listening assistant.

This module is deliberately independent of Flask, the database, and HTTP
routes.  Later assistant services can use these value objects while keeping
candidate payloads limited to course and schedule evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from numbers import Real
from typing import Any, Mapping, Sequence

from app.review_automation.schedules.normalization import (
    NormalizationError,
    parse_period_range,
)


Period = tuple[int, int]
PeriodInput = Period | str | int | float | list[int] | None


def _text(value: object) -> str:
    """Return conservative Unicode/whitespace normalization for display text."""
    if value is None:
        return ''
    if isinstance(value, Real) and not isinstance(value, bool):
        try:
            if not math.isfinite(float(value)):
                return ''
        except (OverflowError, ValueError):
            return ''
    normalized = unicodedata.normalize('NFKC', str(value)).replace('\u3000', ' ')
    return re.sub(r'\s+', ' ', normalized).strip()


def _optional_text(value: object) -> str | None:
    normalized = _text(value)
    return normalized or None


def _normalized_date(value: object, *, field_name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise ValueError(f'{field_name} must be a date')


def _normalized_optional_date(value: object, *, field_name: str) -> date | None:
    if value is None:
        return None
    return _normalized_date(value, field_name=field_name)


def _normalize_period_pair(value: Sequence[object]) -> Period | None:
    if len(value) != 2:
        return None
    numbers: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, Real):
            return None
        number = float(item)
        if not math.isfinite(number) or not number.is_integer():
            return None
        numbers.append(int(number))
    start, end = numbers
    if not 1 <= start <= end <= 20:
        return None
    return start, end


def normalize_room(value: object) -> str:
    """Normalize room punctuation and known building-name variants.

    Only explicit, structurally recognizable variants are collapsed.  Textual
    building names such as ``荣昌`` are retained rather than guessed.
    """
    text = _text(value)
    if not text:
        return ''

    text = re.sub(r'[‐‑‒–—−－]', '-', text)
    text = re.sub(r'\s*([\-/])\s*', r'\1', text)

    building_match = re.fullmatch(
        r'第?\s*0*(\d+)\s*(?:号?\s*教学楼|教)\s*(.+)',
        text,
        flags=re.IGNORECASE,
    )
    if building_match:
        building_number = str(int(building_match.group(1)))
        suffix = re.sub(r'\s+', '', building_match.group(2))
        suffix_match = re.fullmatch(r'([A-Za-z])?(0*\d+)([A-Za-z0-9_-]*)', suffix)
        if suffix_match:
            letter = (suffix_match.group(1) or '').upper()
            suffix = f'{letter}{int(suffix_match.group(2))}{suffix_match.group(3)}'
        return f'{building_number}教{suffix}'

    numeric_code_match = re.fullmatch(r'0*(\d+)\s*-\s*0*(\d+)', text)
    if numeric_code_match:
        return f'{int(numeric_code_match.group(1))}-{int(numeric_code_match.group(2))}'

    return text


def parse_period(value: object) -> Period | None:
    """Parse one period or an inclusive period range without raising.

    ``None`` is the safe rejection value for empty, malformed, reversed, or
    out-of-range input.  The existing schedule parser supplies the repository
    semantics for strings such as ``第3-4节`` and ``0102``.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (tuple, list)):
        return _normalize_period_pair(value)
    try:
        parsed = parse_period_range(value)
    except (NormalizationError, TypeError, ValueError, OverflowError):
        return None
    return _normalize_period_pair(parsed)


def normalize_class_for_display(value: object) -> str:
    """Normalize class text for matching while retaining its visible content."""
    text = _text(value)
    if not text:
        return ''
    text = re.sub(r'\s*([(),/])\s*', r'\1', text)
    text = re.sub(r'\s*(级|班)\s*', r'\1', text)
    return re.sub(r'\s+', '', text)


def overlap_periods(left: object, right: object) -> bool:
    """Return whether two valid inclusive period ranges overlap."""
    left_period = parse_period(left)
    right_period = parse_period(right)
    if left_period is None or right_period is None:
        return False
    return left_period[0] <= right_period[1] and right_period[0] <= left_period[1]


def _identity_text(value: object) -> str:
    return _text(value)


def _identity_date(value: object) -> str | None:
    normalized = _normalized_optional_date(value, field_name='lecture_date')
    return normalized.isoformat() if normalized else None


def stable_candidate_id(
    *,
    source_kind: str,
    source_batch_id: str | None,
    source_row: int | str | None,
    lecture_date: date | datetime | None,
    room: object,
    period: PeriodInput,
    course_code: object = '',
    course_title: object = '',
    teacher_name: object = '',
    student_grade_class: object = '',
) -> str:
    """Build a deterministic identity from non-sensitive schedule fields."""
    normalized_period = parse_period(period)
    identity = {
        'source_kind': _identity_text(source_kind),
        'source_batch_id': _identity_text(source_batch_id),
        'source_row': _identity_text(source_row),
        'lecture_date': _identity_date(lecture_date),
        'room': normalize_room(room),
        'period': normalized_period,
        'course_code': _identity_text(course_code),
        'course_title': _identity_text(course_title),
        'teacher_name': _identity_text(teacher_name),
        'student_grade_class': normalize_class_for_display(student_grade_class),
    }
    serialized = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    digest = hashlib.sha256(serialized).hexdigest()[:24]
    source = re.sub(r'[^0-9A-Za-z._~-]+', '-', identity['source_kind']).strip('-') or 'unknown'
    batch = re.sub(r'[^0-9A-Za-z._~-]+', '-', identity['source_batch_id']).strip('-') or 'unbatched'
    return f'{source}:{batch}:{digest}'


@dataclass(frozen=True)
class AssistantQuery:
    """Validated lookup input; room and teacher may both be supplied."""

    lecture_date: date
    room: str | None = None
    teacher_name: str | None = None
    period: PeriodInput = None
    student_grade_class: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            'lecture_date',
            _normalized_date(self.lecture_date, field_name='lecture_date'),
        )
        normalized_room = normalize_room(self.room)
        normalized_teacher = _optional_text(self.teacher_name)
        if not normalized_room and not normalized_teacher:
            raise ValueError('room or teacher_name is required')
        object.__setattr__(self, 'room', normalized_room or None)
        object.__setattr__(self, 'teacher_name', normalized_teacher)

        if self.period is not None:
            normalized_period = parse_period(self.period)
            if normalized_period is None:
                raise ValueError('period is invalid')
            object.__setattr__(self, 'period', normalized_period)
        object.__setattr__(
            self,
            'student_grade_class',
            _optional_text(normalize_class_for_display(self.student_grade_class)),
        )

    @property
    def anchor(self) -> str:
        """Return the primary anchor, preferring room when both are present."""
        return 'room' if self.room else 'teacher'

    @property
    def lookup_anchors(self) -> tuple[str, ...]:
        anchors: list[str] = []
        if self.room:
            anchors.append('room')
        if self.teacher_name:
            anchors.append('teacher')
        return tuple(anchors)


@dataclass(frozen=True)
class ScheduleEntry:
    """Normalized schedule evidence with source and raw-value provenance."""

    entry_id: str
    lecture_date: date | None = None
    room: str = ''
    period: PeriodInput = None
    course_code: str = ''
    selection_code: str = ''
    course_title: str = ''
    teacher_name: str = ''
    teacher_college: str = ''
    student_grade_class: str = ''
    weekday: int | None = None
    semester: str = ''
    academic_year: str = ''
    source_kind: str = 'primary'
    source_label: str = ''
    source_batch_id: str | None = None
    source_row: int | None = None
    location_raw: str | None = None
    period_raw: str | None = None
    class_raw: str | None = None

    def __post_init__(self) -> None:
        entry_id = _text(self.entry_id)
        if not entry_id:
            raise ValueError('entry_id is required')
        object.__setattr__(self, 'entry_id', entry_id)
        object.__setattr__(
            self,
            'lecture_date',
            _normalized_optional_date(self.lecture_date, field_name='lecture_date'),
        )
        object.__setattr__(self, 'room', normalize_room(self.room))
        if self.period is not None:
            normalized_period = parse_period(self.period)
            if normalized_period is None:
                raise ValueError('period is invalid')
            object.__setattr__(self, 'period', normalized_period)
        for field_name in (
            'course_code',
            'selection_code',
            'course_title',
            'teacher_name',
            'teacher_college',
            'semester',
            'academic_year',
            'source_kind',
            'source_label',
        ):
            object.__setattr__(self, field_name, _text(getattr(self, field_name)))
        object.__setattr__(
            self,
            'student_grade_class',
            normalize_class_for_display(self.student_grade_class),
        )
        object.__setattr__(self, 'source_batch_id', _optional_text(self.source_batch_id))
        for field_name in ('location_raw', 'period_raw', 'class_raw'):
            object.__setattr__(self, field_name, _optional_text(getattr(self, field_name)))


@dataclass(frozen=True)
class Candidate:
    """A user-selectable course candidate with an intentionally safe payload."""

    candidate_id: str
    lecture_date: date
    room: str
    period: PeriodInput
    course_title: str
    teacher_name: str
    teacher_college: str = ''
    student_grade_class: str = ''
    source_kind: str = 'primary'
    source_label: str = ''
    source_batch_id: str | None = None
    course_code: str = ''
    selection_code: str = ''
    weekday: int | None = None
    conflicts: tuple[str, ...] = ()
    needs_confirmation: bool = False

    def __post_init__(self) -> None:
        candidate_id = _text(self.candidate_id)
        if not candidate_id:
            raise ValueError('candidate_id is required')
        object.__setattr__(self, 'candidate_id', candidate_id)
        object.__setattr__(
            self,
            'lecture_date',
            _normalized_date(self.lecture_date, field_name='lecture_date'),
        )
        object.__setattr__(self, 'room', normalize_room(self.room))
        normalized_period = parse_period(self.period)
        if normalized_period is None:
            raise ValueError('period is invalid')
        object.__setattr__(self, 'period', normalized_period)
        for field_name in (
            'course_title',
            'teacher_name',
            'teacher_college',
            'source_kind',
            'source_label',
            'course_code',
            'selection_code',
        ):
            object.__setattr__(self, field_name, _text(getattr(self, field_name)))
        object.__setattr__(
            self,
            'student_grade_class',
            normalize_class_for_display(self.student_grade_class),
        )
        object.__setattr__(self, 'source_batch_id', _optional_text(self.source_batch_id))
        raw_conflicts = (self.conflicts,) if isinstance(self.conflicts, str) else self.conflicts
        conflicts: list[str] = []
        for conflict in raw_conflicts:
            normalized = _text(conflict)
            if normalized and normalized not in conflicts:
                conflicts.append(normalized)
        object.__setattr__(self, 'conflicts', tuple(conflicts))

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize only course/schedule evidence safe for a public candidate card."""
        return {
            'candidate_id': self.candidate_id,
            'lecture_date': self.lecture_date.isoformat(),
            'room': self.room,
            'period': list(self.period),
            'weekday': self.weekday,
            'course_code': self.course_code,
            'course_title': self.course_title,
            'teacher_name': self.teacher_name,
            'teacher_college': self.teacher_college,
            'student_grade_class': self.student_grade_class,
            'source_kind': self.source_kind,
            'source_label': self.source_label,
            'source_batch_id': self.source_batch_id,
            'conflicts': list(self.conflicts),
            'needs_confirmation': self.needs_confirmation,
        }


@dataclass(frozen=True)
class ConfirmationResult:
    """Result of a later server-side candidate confirmation/revalidation."""

    confirmed: bool
    candidate_id: str | None = None
    source_kind: str | None = None
    source_batch_id: str | None = None
    overrides: Mapping[str, Any] = field(default_factory=dict)
    acknowledged_source: bool = False
    message: str = ''
    error_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'candidate_id', _optional_text(self.candidate_id))
        object.__setattr__(self, 'source_kind', _optional_text(self.source_kind))
        object.__setattr__(self, 'source_batch_id', _optional_text(self.source_batch_id))
        object.__setattr__(self, 'overrides', dict(self.overrides))
        object.__setattr__(self, 'message', _text(self.message))
        object.__setattr__(self, 'error_code', _optional_text(self.error_code))


__all__ = [
    'AssistantQuery',
    'Candidate',
    'ConfirmationResult',
    'Period',
    'ScheduleEntry',
    'normalize_class_for_display',
    'normalize_room',
    'overlap_periods',
    'parse_period',
    'stable_candidate_id',
]
