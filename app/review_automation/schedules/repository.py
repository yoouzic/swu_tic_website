"""Authoritative database-backed schedule lookups and request-local unions."""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.models import db
from app.review_automation.contracts import EvidenceStrength, ScheduleCoverage
from app.review_automation.models import (
    AutomationAuditLog,
    ListenerClassMapping,
    PersonalScheduleSlot,
    ScheduleDataset,
    SchoolScheduleEntry,
)

from .importer import CLASS_MAPPING_KIND, PERSONAL_KIND, SCHOOL_KIND, normalize_dataset_kind
from .normalization import (
    NormalizationError,
    normalize_college,
    normalize_identifier,
    normalize_name,
    normalize_weekday,
    parse_period_range,
    parse_weeks,
    periods_overlap,
)


@dataclass(frozen=True)
class ScheduleSlot:
    course_title: str
    weeks: frozenset[int]
    weekday: int
    start_period: int
    end_period: int
    evidence_strength: EvidenceStrength


@dataclass(frozen=True)
class ListenerSchedule:
    coverage: ScheduleCoverage
    slots: tuple[ScheduleSlot, ...]
    admin_classes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SchoolScheduleMatch:
    entry: SchoolScheduleEntry
    confidence: float
    matched_fields: tuple[str, ...]
    mismatched_fields: tuple[str, ...]


def get_active_dataset(kind, semester):
    kind = normalize_dataset_kind(kind)
    return ScheduleDataset.query.filter_by(
        kind=kind, semester=str(semester), status='active',
    ).order_by(ScheduleDataset.created_at.desc()).first()


def activate_dataset(dataset_id, actor_id):
    """Switch one kind/semester to a staged dataset in one DB transaction."""
    dataset = db.session.get(ScheduleDataset, dataset_id)
    if dataset is None:
        raise KeyError(f'unknown dataset: {dataset_id}')
    if dataset.status not in {'staged', 'active'}:
        raise ValueError('only staged or active datasets can be activated')
    if dataset.status == 'active':
        return dataset

    try:
        previous = ScheduleDataset.query.filter_by(
            kind=dataset.kind,
            semester=dataset.semester,
            status='active',
        ).all()
        for old_dataset in previous:
            old_dataset.status = 'retired'
        dataset.status = 'active'
        db.session.add(AutomationAuditLog(
            actor_user_id=actor_id,
            action='activate_dataset',
            target_type='ScheduleDataset',
            target_id=dataset.id,
            details_json=json.dumps({
                'kind': dataset.kind,
                'semester': dataset.semester,
                'retired_dataset_ids': [item.id for item in previous],
            }, ensure_ascii=False, sort_keys=True),
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return dataset


def _identity(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return normalize_identifier(value)


def _weeks(slot_json):
    try:
        values = json.loads(slot_json or '[]')
        return frozenset(int(item) for item in values)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise NormalizationError('weeks', 'invalid_stored_weeks', value=None) from exc


def _slot_key(slot: ScheduleSlot):
    return (
        slot.course_title,
        tuple(sorted(slot.weeks)),
        slot.weekday,
        slot.start_period,
        slot.end_period,
    )


def _class_tokens(value):
    text = normalize_name(value) if value else ''
    return {
        normalize_name(token)
        for token in __import__('re').split(r'[;,；，、/|]+', text)
        if token.strip()
    }


def _entry_matches_class(entry, admin_class: str) -> bool:
    target = normalize_name(admin_class)
    return target in _class_tokens(entry.teaching_class) or target in _class_tokens(entry.major)


def _class_slot(entry: SchoolScheduleEntry) -> ScheduleSlot:
    return ScheduleSlot(
        course_title=entry.course_title,
        weeks=_weeks(entry.weeks_json),
        weekday=entry.weekday,
        start_period=entry.start_period,
        end_period=entry.end_period,
        evidence_strength=EvidenceStrength.APPROXIMATE,
    )


def _personal_slot(entry: PersonalScheduleSlot) -> ScheduleSlot:
    return ScheduleSlot(
        course_title=entry.course_title,
        weeks=_weeks(entry.weeks_json),
        weekday=entry.weekday,
        start_period=entry.start_period,
        end_period=entry.end_period,
        evidence_strength=EvidenceStrength.EXACT,
    )


def get_listener_schedule(listener_number, student_id, semester) -> ListenerSchedule:
    listener_number = _identity(listener_number)
    student_id = _identity(student_id)
    mapping_dataset = get_active_dataset(CLASS_MAPPING_KIND, semester)
    school_dataset = get_active_dataset(SCHOOL_KIND, semester)
    personal_dataset = get_active_dataset(PERSONAL_KIND, semester)

    mappings = []
    if mapping_dataset:
        mappings = [
            item for item in ListenerClassMapping.query.filter_by(dataset_id=mapping_dataset.id).all()
            if (listener_number and item.listener_number == listener_number)
            or (student_id and item.student_id == student_id)
        ]

    slot_map: dict[tuple, ScheduleSlot] = {}
    admin_classes = {mapping.admin_class for mapping in mappings}
    if school_dataset:
        entries = SchoolScheduleEntry.query.filter_by(dataset_id=school_dataset.id).all()
        for mapping in mappings:
            for entry in entries:
                if _entry_matches_class(entry, mapping.admin_class):
                    slot = _class_slot(entry)
                    slot_map[_slot_key(slot)] = slot

    personal_count = 0
    if personal_dataset:
        personal_rows = PersonalScheduleSlot.query.filter_by(dataset_id=personal_dataset.id).all()
        for entry in personal_rows:
            if (listener_number and entry.listener_number == listener_number) or (
                student_id and entry.student_id == student_id
            ):
                personal_count += 1
                slot = _personal_slot(entry)
                slot_map[_slot_key(slot)] = slot

    if personal_count:
        coverage = ScheduleCoverage.COMPLETE
    elif mappings and school_dataset:
        coverage = ScheduleCoverage.BASIC
    else:
        coverage = ScheduleCoverage.NONE
    return ListenerSchedule(
        coverage=coverage,
        slots=tuple(sorted(
            slot_map.values(),
            key=lambda item: (item.weekday, item.start_period, item.end_period, item.course_title),
        )),
        admin_classes=tuple(sorted(admin_classes)),
    )


def _field(form, *names):
    for name in names:
        if isinstance(form, dict) and name in form:
            return form[name]
        if hasattr(form, name):
            return getattr(form, name)
    return None


def find_school_candidates(form, semester):
    dataset = get_active_dataset(SCHOOL_KIND, semester)
    if not dataset:
        return []

    course = _field(form, 'course_title', 'course_name')
    teacher = _field(form, 'teacher_name')
    college = _field(form, 'teacher_college', 'college')
    location = _field(form, 'lecture_location', 'location')
    period_value = _field(form, 'class_period', 'periods')
    weekday_value = _field(form, 'weekday', 'lecture_weekday')
    if weekday_value is None:
        lecture_date = _field(form, 'lecture_date')
        if lecture_date:
            try:
                from .normalization import normalize_date
                weekday_value = normalize_date(lecture_date).weekday() + 1
            except NormalizationError:
                weekday_value = None

    normalized_course = normalize_name(course) if course else None
    normalized_teacher = normalize_name(teacher) if teacher else None
    normalized_college = normalize_college(college) if college else None
    normalized_location = normalize_name(location) if location else None
    period_range = parse_period_range(period_value) if period_value else None
    weekday = normalize_weekday(weekday_value) if weekday_value is not None else None

    matches = []
    for entry in SchoolScheduleEntry.query.filter_by(dataset_id=dataset.id).all():
        course_anchor = bool(normalized_course and entry.course_title == normalized_course)
        teacher_anchor = bool(normalized_teacher and entry.teacher_name == normalized_teacher)
        if not (course_anchor or teacher_anchor):
            continue
        matched = []
        mismatched = []
        comparisons = (
            ('course_title', normalized_course, entry.course_title),
            ('teacher_name', normalized_teacher, entry.teacher_name),
            ('teacher_college', normalized_college, entry.teacher_college),
            ('location', normalized_location, entry.location),
        )
        for name, expected, actual in comparisons:
            if expected is None:
                continue
            (matched if expected == actual else mismatched).append(name)
        if weekday is not None:
            (matched if weekday == entry.weekday else mismatched).append('weekday')
        if period_range is not None:
            (matched if periods_overlap(*period_range, entry.start_period, entry.end_period) else mismatched).append('periods')
        considered = len(matched) + len(mismatched)
        confidence = len(matched) / considered if considered else 0.0
        matches.append(SchoolScheduleMatch(
            entry=entry,
            confidence=confidence,
            matched_fields=tuple(matched),
            mismatched_fields=tuple(mismatched),
        ))
    return sorted(matches, key=lambda item: (-item.confidence, item.entry.id))


__all__ = [
    'ListenerSchedule',
    'ScheduleSlot',
    'SchoolScheduleMatch',
    'activate_dataset',
    'find_school_candidates',
    'get_active_dataset',
    'get_listener_schedule',
]
