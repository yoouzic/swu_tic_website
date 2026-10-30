"""Read canonical admin snapshots as frozen, normalized school evidence.

This adapter never writes automation datasets, opens workbooks, or changes the
current schedule selection. Only batch creation resolves current authority;
workers read the explicit batch identity captured in their configuration.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping

from app.models import (
    ListeningAssistantScheduleEntry, ScheduleImportBatch, ScheduleImportRow,
    ScheduleImportRowScalarMeta, db,
)
from app.services.listening_assistant_contracts import normalize_room, parse_period_segments
from app.services.schedule_snapshots import resolve_current_schedule_snapshot

from .normalization import NormalizationError, normalize_name, normalize_weekday, parse_weeks


CANONICAL_SOURCE = 'canonical_snapshot'
CANONICAL_VERSION = 'canonical-school-v1'


@dataclass(frozen=True)
class CanonicalSchoolEntry:
    id: str
    teacher_name: str
    teacher_college: str | None
    course_title: str
    teaching_class: str | None
    major: str | None
    weeks_json: str
    weekday: int
    start_period: int
    end_period: int
    location: str | None
    source_row: int


def snapshot_canonical_school(semester):
    """Freeze one safely selected, nonempty canonical source at batch creation."""
    if not isinstance(semester, str) or not semester.strip():
        return None
    semester = semester.strip()
    selected = resolve_current_schedule_snapshot(semester, include_rows=False)
    batch = selected.batch
    if not selected.is_ready or batch is None or batch.semester != semester:
        return None
    if not db.session.query(ScheduleImportRow.id).filter_by(batch_id=batch.id).first():
        return None
    return {
        'source': CANONICAL_SOURCE,
        'id': str(batch.id),
        'sha256': batch.source_sha256,
        'version': CANONICAL_VERSION,
    }


def _optional_text(value):
    if value is None or str(value).strip().lower() in {'', 'nan', 'none', 'nat'}:
        return None
    return normalize_name(value)


def _anchor(value, kind):
    # The raw snapshot stores strings even when the original Excel cell was a
    # number. Such cells must not become valid course/teacher text anchors.
    if kind is not None and kind != 'text':
        return None
    text = _optional_text(value)
    if kind is None and text and re.fullmatch(r'[+-]?\d+(?:\.\d+)?', text):
        return None
    return text


def _numeric_raw(value):
    # pandas can preserve an integral cell as e.g. "3.0" in the raw snapshot.
    # Coerce only that exact numeric spelling, never arbitrary malformed text.
    if isinstance(value, str) and re.fullmatch(r'\d+\.0+', value.strip()):
        return int(value.strip().split('.')[0])
    return value


def load_canonical_school_entries(ref, semester):
    """Read the captured batch even after retirement; never resolve live state."""
    if not isinstance(ref, Mapping) or ref.get('source') != CANONICAL_SOURCE:
        return []
    batch_id = ref.get('id')
    if type(batch_id) not in (str, int) or not re.fullmatch(r'[1-9]\d*', str(batch_id)):
        return []
    batch = db.session.get(ScheduleImportBatch, int(batch_id))
    if (
        batch is None or batch.semester != str(semester)
        or ref.get('sha256') != batch.source_sha256
        or ref.get('version') != CANONICAL_VERSION
    ):
        return []
    rows = db.session.query(
        ScheduleImportRow, ScheduleImportRowScalarMeta, ListeningAssistantScheduleEntry,
    ).outerjoin(
        ScheduleImportRowScalarMeta,
        ScheduleImportRowScalarMeta.row_id == ScheduleImportRow.id,
    ).outerjoin(
        ListeningAssistantScheduleEntry,
        (ListeningAssistantScheduleEntry.batch_id == ScheduleImportRow.batch_id)
        & (ListeningAssistantScheduleEntry.source_row == ScheduleImportRow.source_row),
    ).filter(
        ScheduleImportRow.batch_id == batch.id,
    ).order_by(ScheduleImportRow.source_row.asc(), ScheduleImportRow.id.asc()).all()

    entries = []
    for row, scalar_meta, indexed in rows:
        try:
            teacher = _anchor(row.teacher_name, scalar_meta.teacher_name_kind if scalar_meta else None)
            course = _anchor(row.course_name, scalar_meta.course_name_kind if scalar_meta else None)
            if not teacher or not course:
                continue
            # Venue columns describe this meeting, unlike offering-level period
            # aggregates. Keep every segment separate so gaps cannot conflict.
            venue_raw = _optional_text(indexed.venue_period_raw) if indexed else None
            venue_periods = parse_period_segments(venue_raw) if venue_raw else ()
            if venue_raw and not venue_periods:
                continue
            periods = venue_periods or parse_period_segments(_numeric_raw(row.class_period_raw))
            if not periods:
                continue
            weeks = parse_weeks(_numeric_raw(row.start_week_raw))
            venue_weeks = _optional_text(indexed.venue_start_week_raw) if indexed else None
            if venue_weeks:
                weeks = weeks & parse_weeks(_numeric_raw(venue_weeks))
            if not weeks:
                continue
            weekday = normalize_weekday(_numeric_raw(row.weekday_raw))
            college = _optional_text(row.teacher_college)
            teaching_class = _optional_text(row.class_composition_raw)
            raw_location = _optional_text(row.location_raw)
            location = normalize_room(raw_location) if raw_location else None
        except (NormalizationError, TypeError, ValueError):
            # Bad rows cannot manufacture evidence or block all valid rows.
            continue
        for start, end in periods:
            entries.append(CanonicalSchoolEntry(
                id=f'canonical:{batch.id}:{row.id}:{start}-{end}',
                teacher_name=teacher, teacher_college=college, course_title=course,
                teaching_class=teaching_class, major=None,
                weeks_json=json.dumps(sorted(weeks), separators=(',', ':')),
                weekday=weekday, start_period=start, end_period=end,
                location=location, source_row=row.source_row,
            ))
    return entries


__all__ = [
    'CANONICAL_SOURCE', 'CanonicalSchoolEntry', 'load_canonical_school_entries',
    'snapshot_canonical_school',
]
