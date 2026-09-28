"""Composable candidate search for the universal listening assistant.

The service deliberately sits on top of the batch-linked schedule loader.  It
does not read legacy course aggregates, infer a date from a weekday alone, or
silently switch to a retired source.  The result is a small, JSON-safe
boundary for the later HTTP and form layers.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date, datetime
from numbers import Real
from typing import Any, Callable, Iterable, Mapping

from app.services.listening_assistant_contracts import (
    AssistantQuery,
    Candidate,
    ScheduleEntry,
    normalize_class_for_display,
    normalize_room,
    overlap_periods,
    parse_period,
    stable_candidate_id,
)
from app.services.listening_assistant_schedule import (
    BACKUP_SOURCE_LABEL,
    PRIMARY_SOURCE_LABEL,
    load_schedule_entries,
)
from app.services.teaching_calendar import TeachingCalendarConfig, parse_lecture_date, teaching_week_number


_INVALID_WEEK_DATA = object()
_UNSET = object()
BACKUP_FALLBACK_REASONS = frozenset({'no_result', 'rejected_candidates'})


def _text(value: object) -> str:
    """Apply the same conservative text normalization as the contracts."""
    if value is None:
        return ''
    if isinstance(value, Real) and not isinstance(value, bool):
        try:
            if not math.isfinite(float(value)):
                return ''
        except (OverflowError, TypeError, ValueError):
            return ''
    normalized = unicodedata.normalize('NFKC', str(value)).replace('\u3000', ' ')
    return re.sub(r'\s+', ' ', normalized).strip()


def _optional_text(value: object) -> str | None:
    normalized = _text(value)
    return normalized or None


def _normalize_backup_reason(reason: object) -> str:
    if not isinstance(reason, str):
        raise ValueError(
            'backup reason must be one of: no_result, rejected_candidates'
        )
    normalized = _text(reason)
    if normalized not in BACKUP_FALLBACK_REASONS:
        raise ValueError(
            'backup reason must be one of: no_result, rejected_candidates'
        )
    return normalized


def _value(entry: object, *names: str, default: object = None) -> object:
    for name in names:
        try:
            value = getattr(entry, name)
        except (AttributeError, TypeError):
            continue
        if value is not None:
            return value
    return default


def _date_value(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return parse_lecture_date(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _has_date_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _weekday(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Real):
        try:
            number = float(value)
        except (OverflowError, TypeError, ValueError):
            return None
        if math.isfinite(number) and number.is_integer() and 1 <= int(number) <= 7:
            return int(number)
        return None
    text = _text(value).replace('星期', '').replace('周', '')
    if text in {'一', '二', '三', '四', '五', '六', '日', '天'}:
        return {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '日': 7, '天': 7}[text]
    if re.fullmatch(r'[1-7]', text):
        return int(text)
    return None


def _entry_weekday(entry: object) -> int | None:
    parsed = _weekday(_value(entry, 'weekday'))
    if parsed is not None:
        return parsed
    return _weekday(_value(entry, 'weekday_raw'))


def _entry_period(entry: object) -> tuple[int, int] | None:
    for name in ('period', 'period_raw', 'venue_period_raw', 'class_period_raw'):
        parsed = parse_period(_value(entry, name))
        if parsed is not None:
            return parsed

    start = _value(entry, 'period_start')
    end = _value(entry, 'period_end')
    parsed = parse_period((start, end)) if start is not None and end is not None else None
    if parsed is not None:
        return parsed

    start = _value(entry, 'venue_period_start')
    end = _value(entry, 'venue_period_end')
    return parse_period((start, end)) if start is not None and end is not None else None


def _entry_room(entry: object) -> str:
    value = _value(entry, 'room', 'location_normalized', 'location_raw')
    return normalize_room(value)


def _entry_teacher(entry: object) -> str:
    return _text(_value(entry, 'teacher_name', 'teacher_name_raw'))


def _entry_class(entry: object) -> str:
    value = _value(entry, 'student_grade_class', 'class_raw')
    return normalize_class_for_display(value)


def _identity_token(value: object) -> str:
    """Return a typed, deterministic marker for a hashed identity input."""
    if value is None:
        return 'none:'
    if isinstance(value, bool):
        return f'bool:{value!r}'
    if isinstance(value, int):
        return f'int:{value}'
    if isinstance(value, float):
        if math.isnan(value):
            return 'float:nan'
        if math.isinf(value):
            return 'float:+inf' if value > 0 else 'float:-inf'
        return f'float:{value!r}'
    value_type = f'{type(value).__module__}.{type(value).__qualname__}'
    return f'{value_type}:{_text(value)}'


def _is_canonical_db_entry(
    entry_id: str,
    source_batch_id: str | None,
    source_row: object,
) -> bool:
    if not entry_id or source_batch_id is None or source_row is None:
        return False
    if not _text(source_row):
        return False
    return entry_id == f'listening-assistant:{source_batch_id}:{source_row}'


def _identity_source_row(
    *,
    source_batch_id: str | None,
    source_row: object,
    entry_id: str,
    selection_code: str,
    course_code: str,
    course_title: str,
    teacher: str,
    teacher_college: str,
    room: str,
    period: tuple[int, int],
    student_grade_class: str,
) -> object:
    """Build the source-row input without changing existing DB-backed IDs."""
    if source_row is not None and _text(source_row):
        if _is_canonical_db_entry(entry_id, source_batch_id, source_row):
            return source_row
        if not selection_code:
            return source_row
        return (
            f'source_row:{_identity_token(source_row)}'
            f'|selection_code:{_identity_token(selection_code)}'
        )

    if entry_id:
        return (
            f'entry_id:{_identity_token(entry_id)}'
            f'|selection_code:{_identity_token(selection_code)}'
        )

    # ScheduleEntry normally requires an entry_id.  Keep a deterministic,
    # typed fallback for duck-typed injected rows rather than using a process
    # address or exposing any of these values in the public candidate.
    fallback = {
        'course_code': course_code,
        'course_title': course_title,
        'period': period,
        'room': room,
        'selection_code': selection_code,
        'source_batch_id': source_batch_id,
        'student_grade_class': student_grade_class,
        'teacher': teacher,
        'teacher_college': teacher_college,
    }
    return 'fallback:' + json.dumps(
        fallback,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )


def _parse_week_set(value: object) -> frozenset[int] | None:
    """Parse the schedule workbook's raw teaching-week expressions safely."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, Real):
        try:
            number = float(value)
        except (OverflowError, TypeError, ValueError):
            return None
        if math.isfinite(number) and number.is_integer() and 1 <= int(number) <= 52:
            return frozenset({int(number)})
        return None

    text = _text(value)
    if not text:
        return None
    parity_match = re.search(r'[（(]\s*([单双])\s*[）)]', text)
    parity = parity_match.group(1) if parity_match else None
    if parity_match:
        text = text[:parity_match.start()] + text[parity_match.end():]
    text = re.sub(r'(?:教学)?周(?:次)?', '', text, flags=re.IGNORECASE)
    text = text.replace('第', '').replace('－', '-').replace('～', '~')
    text = text.replace('至', '-').replace('到', '-')
    text = re.sub(r'\s*(?:-|~)\s*', '-', text)
    if not re.fullmatch(r'[0-9\s,，、;；\-~]+', text):
        return None

    weeks: set[int] = set()
    for part in re.split(r'[,，、;；\s]+', text.strip()):
        if not part:
            continue
        range_match = re.fullmatch(r'(\d+)(?:-|~)(\d+)', part)
        if range_match:
            start, end = map(int, range_match.groups())
            if start > end or start < 1 or end > 52:
                return None
            weeks.update(range(start, end + 1))
        elif part.isdigit():
            weeks.add(int(part))
        else:
            return None

    if not weeks or any(week < 1 or week > 52 for week in weeks):
        return None
    if parity == '单':
        weeks = {week for week in weeks if week % 2 == 1}
    elif parity == '双':
        weeks = {week for week in weeks if week % 2 == 0}
    return frozenset(weeks) if weeks else None


def _entry_week_range(entry: object) -> frozenset[int] | None | object:
    """Return known week numbers, None when no raw field exists, or invalid."""
    raw_values: list[object] = []
    for names in (
        ('venue_start_week_raw', 'venue_start_week'),
        ('start_week_raw', 'start_week'),
    ):
        raw = _value(entry, *names, default=_UNSET)
        if raw is _UNSET or (isinstance(raw, str) and not raw.strip()) or raw is None:
            continue
        raw_values.append(raw)

    if not raw_values:
        return None

    parsed_ranges: list[frozenset[int]] = []
    for raw in raw_values:
        parsed = _parse_week_set(raw)
        if parsed is None:
            return _INVALID_WEEK_DATA
        parsed_ranges.append(parsed)

    result = set(parsed_ranges[0])
    for parsed in parsed_ranges[1:]:
        result.intersection_update(parsed)
    return frozenset(result) if result else _INVALID_WEEK_DATA


def _calendar_week(target: date, calendar: object) -> int | None:
    if calendar is None:
        return None
    if isinstance(calendar, TeachingCalendarConfig):
        return teaching_week_number(target, calendar)
    if isinstance(calendar, Mapping):
        try:
            config = TeachingCalendarConfig(
                first_week_date=_date_value(calendar['first_week_date']),
                week_start_day=int(calendar.get('week_start_day', 0)),
                total_weeks=int(calendar.get('total_weeks', 20)),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        return teaching_week_number(target, config)
    method = getattr(calendar, 'teaching_week_number', None)
    if callable(method):
        try:
            value = method(target)
        except (TypeError, ValueError, OverflowError):
            return None
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else None
    if callable(calendar):
        try:
            value = calendar(target)
        except (TypeError, ValueError, OverflowError):
            return None
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else None
    return None


def _default_calendar() -> TeachingCalendarConfig | None:
    """Read settings lazily so pure loader-injected service tests stay safe."""
    try:
        from app.services.teaching_calendar_settings import load_teaching_calendar_config

        config, _error_code = load_teaching_calendar_config()
        return config
    except Exception:
        # Missing application context, an uninitialized database, and malformed
        # settings all mean that a date inferred from weekday needs confirmation.
        return None


def _load_calendar(provider: object, semester: str | None) -> object:
    if provider is None:
        return _default_calendar()
    if not callable(provider):
        return provider
    try:
        result = provider(semester=semester)
    except TypeError:
        try:
            result = provider(semester)
        except TypeError:
            result = provider()
    if isinstance(result, tuple) and len(result) == 2:
        return result[0]
    return result


@dataclass(frozen=True)
class _DateMatch:
    matched: bool
    needs_confirmation: bool = False
    conflict: str | None = None


def _match_date(entry: object, query_date: date, calendar: object) -> _DateMatch:
    raw_date = _value(entry, 'lecture_date')
    if _has_date_value(raw_date):
        exact_date = _date_value(raw_date)
        if exact_date is None or exact_date != query_date:
            return _DateMatch(False)
        entry_weekday = _entry_weekday(entry)
        if entry_weekday is not None and entry_weekday != query_date.isoweekday():
            return _DateMatch(False)
        return _DateMatch(True)

    entry_weekday = _entry_weekday(entry)
    if entry_weekday != query_date.isoweekday():
        return _DateMatch(False)

    week_range = _entry_week_range(entry)
    if week_range is _INVALID_WEEK_DATA:
        return _DateMatch(False)
    if calendar is None or week_range is None:
        return _DateMatch(True, needs_confirmation=True, conflict='date_needs_confirmation')

    query_week = _calendar_week(query_date, calendar)
    if query_week is None or query_week not in week_range:
        return _DateMatch(False)
    return _DateMatch(True)


def _normalize_rejected_ids(rejected_ids: Iterable[object] | object | None) -> frozenset[str]:
    if rejected_ids is None:
        return frozenset()
    if isinstance(rejected_ids, str):
        rejected_ids = (rejected_ids,)
    try:
        values = iter(rejected_ids)
    except TypeError:
        values = iter((rejected_ids,))
    return frozenset(
        normalized
        for item in values
        for normalized in (_text(item),)
        if normalized
    )


@dataclass(frozen=True)
class ListeningAssistantSearchResult:
    """Typed, JSON-safe result shared by future routes and form code."""

    candidates: tuple[Candidate, ...]
    always_show_none: bool = True
    source_kind: str = 'primary'
    source_label: str = PRIMARY_SOURCE_LABEL
    backup_rescue_available: bool = False
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'candidates', tuple(self.candidates))
        if self.always_show_none is not True:
            raise ValueError('always_show_none must be True')
        if self.fallback_reason is not None:
            object.__setattr__(
                self,
                'fallback_reason',
                _normalize_backup_reason(self.fallback_reason),
            )

    @property
    def backup_available(self) -> bool:
        """Compatibility alias for callers that use a shorter field name."""
        return self.backup_rescue_available

    @property
    def explicit_backup_rescue_available(self) -> bool:
        return self.backup_rescue_available

    def to_public_dict(self) -> dict[str, Any]:
        return {
            'candidates': [candidate.to_public_dict() for candidate in self.candidates],
            'always_show_none': True,
            'source_kind': self.source_kind,
            'source_label': self.source_label,
            'backup_rescue_available': self.backup_rescue_available,
            'backup_available': self.backup_rescue_available,
            'fallback_reason': self.fallback_reason,
        }

    def __getitem__(self, key: str) -> object:
        return self.to_public_dict()[key]


class ListeningAssistantService:
    """Search the authoritative schedule index with explicit source control."""

    def __init__(
        self,
        schedule_loader: Callable[..., Iterable[ScheduleEntry]] | None = None,
        *,
        loader: Callable[..., Iterable[ScheduleEntry]] | None = None,
        semester: str | None = None,
        calendar: object = None,
        calendar_provider: object = None,
        backup_source_batch_id: int | str | None = None,
    ) -> None:
        if schedule_loader is not None and loader is not None:
            raise ValueError('provide only one schedule loader')
        self._schedule_loader = schedule_loader or loader or load_schedule_entries
        self._semester = semester
        self._calendar = calendar if calendar is not None else calendar_provider
        self._backup_source_batch_id = backup_source_batch_id

    def search(
        self,
        query: AssistantQuery,
        *,
        semester: str | None = None,
        rejected_ids: Iterable[object] | object | None = None,
    ) -> ListeningAssistantSearchResult:
        self._validate_query(query)
        selected_semester = self._semester if semester is None else semester
        entries = self._schedule_loader(
            source_kind='primary',
            semester=selected_semester,
        )
        return self._search_entries(
            query,
            entries,
            source_kind='primary',
            source_label=PRIMARY_SOURCE_LABEL,
            semester=selected_semester,
            rejected_ids=rejected_ids,
        )

    @staticmethod
    def reject(candidates: Iterable[Candidate] | Candidate | None) -> tuple[str, ...]:
        """Return stable candidate IDs that can be passed to ``rejected_ids``."""
        if candidates is None:
            return ()
        if isinstance(candidates, (Candidate, Mapping)):
            candidates = (candidates,)
        try:
            iterator = iter(candidates)
        except TypeError as error:
            raise ValueError('candidates must be iterable') from error

        rejected: list[str] = []
        for candidate in iterator:
            if isinstance(candidate, Mapping):
                candidate_id = candidate.get('candidate_id')
            else:
                candidate_id = getattr(candidate, 'candidate_id', None)
            normalized = _text(candidate_id)
            if normalized and normalized not in rejected:
                rejected.append(normalized)
        return tuple(rejected)

    def search_by_teacher(
        self,
        query: AssistantQuery,
        teacher_name: str | None = None,
        *,
        semester: str | None = None,
        rejected_ids: Iterable[object] | object | None = None,
        source_kind: str = 'primary',
        source_batch_id: int | str | None = None,
        explicit_fallback: bool = False,
        reason: str | None = None,
    ) -> ListeningAssistantSearchResult:
        """Search by teacher, optionally through the explicit backup path."""
        self._validate_query(query, require_anchor=False)
        normalized_teacher = _text(
            teacher_name if teacher_name is not None else query.teacher_name
        )
        if not normalized_teacher:
            raise ValueError('teacher_name is required')
        query = replace(
            query,
            room=None,
            teacher_name=normalized_teacher,
        )
        normalized_source_kind = _text(source_kind).lower()
        if normalized_source_kind == 'backup':
            return self.search_backup(
                query,
                source_batch_id=source_batch_id,
                semester=semester,
                rejected_ids=rejected_ids,
                explicit_fallback=explicit_fallback,
                reason=reason,
            )
        if normalized_source_kind != 'primary':
            raise ValueError("source_kind must be 'primary' or 'backup'")
        return self.search(query, semester=semester, rejected_ids=rejected_ids)

    def search_backup(
        self,
        query: AssistantQuery,
        source_batch_id: int | str | None = None,
        *,
        semester: str | None = None,
        rejected_ids: Iterable[object] | object | None = None,
        explicit_fallback: bool = False,
        reason: str | None = None,
    ) -> ListeningAssistantSearchResult:
        """Search exactly one caller-selected retired batch.

        The fallback flag and retired source batch id must be supplied on
        every call.  Constructor metadata can describe availability but
        cannot authorize or select a retired batch.  No-result primary
        searches never call this method implicitly.
        """
        self._validate_query(query)
        if explicit_fallback is not True:
            raise ValueError('backup search requires an explicit fallback')
        normalized_reason = _normalize_backup_reason(reason)
        selected_batch_id = source_batch_id
        if selected_batch_id is None or not _text(selected_batch_id):
            raise ValueError('backup source requires an explicit retired batch id')
        selected_semester = self._semester if semester is None else semester
        entries = self._schedule_loader(
            source_kind='backup',
            semester=selected_semester,
            source_batch_id=selected_batch_id,
        )
        return self._search_entries(
            query,
            entries,
            source_kind='backup',
            source_label=BACKUP_SOURCE_LABEL,
            semester=selected_semester,
            rejected_ids=rejected_ids,
            fallback_reason=normalized_reason,
        )

    @staticmethod
    def _validate_query(
        query: AssistantQuery,
        *,
        require_anchor: bool = True,
    ) -> None:
        if not isinstance(query, AssistantQuery):
            raise TypeError('query must be an AssistantQuery')
        if require_anchor and not query.lookup_anchors:
            raise ValueError('room or teacher_name anchor is required')

    def _search_entries(
        self,
        query: AssistantQuery,
        entries: Iterable[ScheduleEntry] | None,
        *,
        source_kind: str,
        source_label: str,
        semester: str | None,
        rejected_ids: Iterable[object] | object | None,
        fallback_reason: str | None = None,
    ) -> ListeningAssistantSearchResult:
        rows = tuple(entries or ())
        rejected = _normalize_rejected_ids(rejected_ids)
        calendar = None
        if any(not _has_date_value(_value(row, 'lecture_date')) for row in rows):
            calendar = _load_calendar(self._calendar, semester)

        candidates: list[Candidate] = []
        for row in rows:
            date_match = _match_date(row, query.lecture_date, calendar)
            if not date_match.matched:
                continue

            room = _entry_room(row)
            teacher = _entry_teacher(row)
            if query.room and room != query.room:
                continue
            if query.teacher_name and teacher != _text(query.teacher_name):
                continue

            student_grade_class = _entry_class(row)
            if (
                query.student_grade_class
                and student_grade_class != normalize_class_for_display(query.student_grade_class)
            ):
                continue

            period = _entry_period(row)
            if period is None:
                continue

            conflicts: list[str] = []
            if date_match.conflict:
                conflicts.append(date_match.conflict)
            if query.period is not None and not overlap_periods(period, query.period):
                conflicts.append('period_mismatch')

            course_code = _text(_value(row, 'course_code', 'course_code_raw'))
            selection_code = _text(_value(row, 'selection_code', 'selection_code_raw'))
            course_title = _text(_value(row, 'course_title', 'course_title_raw', 'course_name'))
            teacher_college = _text(_value(row, 'teacher_college', 'teacher_college_raw'))
            source_batch_id = _optional_text(_value(row, 'source_batch_id', 'batch_id'))
            source_row = _value(row, 'source_row')
            entry_id = _text(_value(row, 'entry_id'))
            identity_source_row = _identity_source_row(
                source_batch_id=source_batch_id,
                source_row=source_row,
                entry_id=entry_id,
                selection_code=selection_code,
                course_code=course_code,
                course_title=course_title,
                teacher=teacher,
                teacher_college=teacher_college,
                room=room,
                period=period,
                student_grade_class=student_grade_class,
            )
            candidate_id = stable_candidate_id(
                source_kind=source_kind,
                source_batch_id=source_batch_id,
                source_row=identity_source_row,
                lecture_date=query.lecture_date,
                room=room,
                period=period,
                course_code=course_code,
                course_title=course_title,
                teacher_name=teacher,
                student_grade_class=student_grade_class,
            )

            try:
                candidate = Candidate(
                    candidate_id=candidate_id,
                    lecture_date=query.lecture_date,
                    room=room,
                    period=period,
                    course_code=course_code,
                    selection_code=selection_code,
                    course_title=course_title,
                    teacher_name=teacher,
                    teacher_college=teacher_college,
                    student_grade_class=student_grade_class,
                    source_kind=source_kind,
                    source_label=source_label,
                    source_batch_id=source_batch_id,
                    weekday=_entry_weekday(row) or query.lecture_date.isoweekday(),
                    conflicts=tuple(conflicts),
                    needs_confirmation=(
                        source_kind == 'backup'
                        or date_match.needs_confirmation
                        or bool(conflicts)
                    ),
                )
            except (TypeError, ValueError):
                # A malformed row must not break a later route's complete
                # result.  Candidate construction remains the final safety
                # boundary for required fields such as date and period.
                continue

            candidates.append(candidate)

        candidates.sort(
            key=lambda candidate: (
                candidate.period[0],
                candidate.period[1],
                candidate.course_title,
                candidate.teacher_name,
                candidate.needs_confirmation,
                candidate.candidate_id,
            )
        )

        deduplicated: list[Candidate] = []
        seen_visible: set[tuple[object, ...]] = set()
        for candidate in candidates:
            visible_key = (
                candidate.lecture_date,
                candidate.room,
                candidate.period,
                candidate.course_code,
                candidate.selection_code,
                candidate.course_title,
                candidate.teacher_name,
                candidate.teacher_college,
                candidate.student_grade_class,
                candidate.source_kind,
                candidate.source_label,
                candidate.source_batch_id,
            )
            if visible_key in seen_visible:
                continue
            seen_visible.add(visible_key)
            if candidate.candidate_id not in rejected:
                deduplicated.append(candidate)

        return ListeningAssistantSearchResult(
            candidates=tuple(deduplicated),
            always_show_none=True,
            source_kind=source_kind,
            source_label=source_label,
            backup_rescue_available=(
                source_kind == 'primary' and self._backup_source_batch_id is not None
            ),
            fallback_reason=fallback_reason,
        )


SearchResult = ListeningAssistantSearchResult


__all__ = [
    'ListeningAssistantSearchResult',
    'ListeningAssistantService',
    'BACKUP_FALLBACK_REASONS',
    'SearchResult',
]
