# -*- coding: utf-8 -*-
"""Canonical raw schedule snapshot persistence and retrieval.

This module owns the admin-maintained ``ScheduleImportBatch`` /
``ScheduleImportRow`` snapshot and the authoritative per-semester selection
pointer (``ScheduleSemesterSelection``).  It keeps the raw cell text that the
legacy AutoReviewEngine matcher reads from ``pd.read_excel`` /
``str(row[...])`` and is intentionally separate from the normalized/frozen
``review_automation`` dataset layer.
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.models import (
    ScheduleImportBatch,
    ScheduleImportRow,
    ScheduleSemesterSelection,
    db,
)
from app.services.academic_term import get_current_teaching_semester

ACTIVE = 'active'
RETIRED = 'retired'

# Canonical selection statuses.
CURRENT_SEMESTER_UNSET = 'CURRENT_SEMESTER_UNSET'
CURRENT_SEMESTER_CONFIGURED_BUT_NO_ACTIVE_SNAPSHOT = (
    'CURRENT_SEMESTER_CONFIGURED_BUT_NO_ACTIVE_SNAPSHOT'
)
READY = 'READY'
AMBIGUOUS = 'AMBIGUOUS'

# Legacy matcher-facing column names in the admin schedule workbook.
SNAPSHOT_COLUMNS = {
    'teacher_name': '姓名',
    'teacher_college': '教师所属学院',
    'course_name': '课程名称',
    'weekday_raw': '星期几',
    'class_period_raw': '上课节次',
    'location_raw': '场地名称',
    'class_composition_raw': '教学班组成',
    'start_week_raw': '起始周',
}

# pandas read_excel with a normal header row puts the first data row on Excel
# row 2.  This matches the row numbers already used by the admin import errors.
DEFAULT_SOURCE_ROW_OFFSET = 2


@dataclass
class CurrentScheduleSelection:
    """Structured result of canonical current-schedule selection.

    ``rows`` is empty for every non-READY state; ``batch`` is ``None`` for
    every non-READY state.  This prevents callers from conflating
    ``CURRENT_SEMESTER_UNSET`` with a real blank-semester snapshot.
    """
    status: str
    semester: str = ''
    batch: Optional[ScheduleImportBatch] = None
    rows: List[ScheduleImportRow] = field(default_factory=list)

    @property
    def is_ready(self) -> bool:
        return self.status == READY


def _is_nan(value) -> bool:
    try:
        return math.isnan(value)
    except (TypeError, ValueError):
        return False


def legacy_cell_text(value) -> str:
    """Return the exact text that a legacy matcher would see.

    The legacy matcher does ``str(row['...'])`` after ``pd.read_excel``.
    Therefore this function intentionally does not strip, NFKC-normalize,
    convert numeric types, or map ``NaN`` to an empty string.
    """
    if value is None or _is_nan(value):
        return 'nan'
    return str(value)


def _semester_key(value) -> str:
    text = legacy_cell_text(value)
    if text.strip() == '' or text.lower() == 'nan':
        return ''
    return text.strip()


def _academic_year_key(value) -> str:
    text = legacy_cell_text(value)
    if text.strip() == '' or text.lower() == 'nan':
        return ''
    return text.strip()


def _row_cell(row, column: str) -> str:
    return legacy_cell_text(row.get(column))


def _set_semester_selection(semester: str, batch_id: int) -> None:
    """Atomically (within the surrounding transaction) point a semester at a batch.

    ``ScheduleSemesterSelection`` is the authority.  The previously selected
    batch status is demoted for historical metadata only.
    """
    selection = db.session.get(ScheduleSemesterSelection, semester)
    if selection is None:
        db.session.add(ScheduleSemesterSelection(
            semester=semester,
            active_batch_id=batch_id,
        ))
        return

    previous_batch = db.session.get(ScheduleImportBatch, selection.active_batch_id)
    if previous_batch is not None:
        previous_batch.status = RETIRED
    selection.active_batch_id = batch_id


def _active_batch_via_selection(semester: str) -> Optional[ScheduleImportBatch]:
    """Return the authoritative batch for a semester, if a selection exists."""
    selection = db.session.get(ScheduleSemesterSelection, semester)
    if selection is None:
        return None
    return db.session.get(ScheduleImportBatch, selection.active_batch_id)


def _legacy_single_active_fallback(semester: str) -> Optional[ScheduleImportBatch]:
    """One-time compatibility for pre-authority snapshot rows.

    If no selection row exists and exactly one batch carries ``status='active'``
    for this semester, that single batch is accepted.  If there are zero or
    multiple active rows, this returns ``None`` (fail closed) rather than
    silently choosing one by id.
    """
    actives = ScheduleImportBatch.query.filter_by(
        semester=semester,
        status=ACTIVE,
    ).all()
    if len(actives) == 1:
        return actives[0]
    return None


def _legacy_active_count(semester: str) -> int:
    return ScheduleImportBatch.query.filter_by(
        semester=semester,
        status=ACTIVE,
    ).count()


def persist_import_snapshot(
    df,
    source_filename: str,
    source_sha256: str,
    source_row_offset: int = DEFAULT_SOURCE_ROW_OFFSET,
) -> List[ScheduleImportBatch]:
    """Persist row-level snapshots for one admin schedule workbook.

    Rows are grouped by the workbook's ``学期`` column.  Each semester group
    becomes its own batch, and that semester's authoritative selection pointer
    is advanced to the new batch in the same transaction.  This function does
    not commit; the caller decides the transaction boundary.
    """
    if df is None or len(df) == 0:
        return []

    groups: Dict[str, List[Tuple[int, object]]] = {}
    for source_index, row in df.iterrows():
        semester = _semester_key(row.get('学期'))
        groups.setdefault(semester, []).append((source_index, row))

    batches: List[ScheduleImportBatch] = []
    for semester, rows in groups.items():
        academic_year = ''
        for _, row in rows:
            academic_year = _academic_year_key(row.get('学年'))
            if academic_year:
                break

        batch = ScheduleImportBatch(
            semester=semester,
            academic_year=academic_year or None,
            source_filename=source_filename or 'schedule.xlsx',
            source_sha256=source_sha256 or '',
            status=ACTIVE,
            row_count=len(rows),
        )
        db.session.add(batch)
        db.session.flush()
        batches.append(batch)

        for source_index, row in rows:
            source_row = int(source_index) + source_row_offset
            db.session.add(ScheduleImportRow(
                batch_id=batch.id,
                source_row=source_row,
                teacher_name=_row_cell(row, SNAPSHOT_COLUMNS['teacher_name']),
                teacher_college=_row_cell(row, SNAPSHOT_COLUMNS['teacher_college']),
                course_name=_row_cell(row, SNAPSHOT_COLUMNS['course_name']),
                weekday_raw=_row_cell(row, SNAPSHOT_COLUMNS['weekday_raw']),
                class_period_raw=_row_cell(row, SNAPSHOT_COLUMNS['class_period_raw']),
                location_raw=_row_cell(row, SNAPSHOT_COLUMNS['location_raw']),
                class_composition_raw=_row_cell(
                    row, SNAPSHOT_COLUMNS['class_composition_raw']
                ),
                start_week_raw=_row_cell(row, SNAPSHOT_COLUMNS['start_week_raw']),
            ))

        _set_semester_selection(semester, batch.id)

        # Best-effort status metadata consistency: the selection pointer is
        # authoritative, but keep the legacy status flag aligned so raw status
        # queries do not produce multiple active rows after a normal replace.
        for other_batch in ScheduleImportBatch.query.filter_by(
            semester=semester,
            status=ACTIVE,
        ).all():
            if other_batch.id != batch.id:
                other_batch.status = RETIRED

    return batches


def get_active_schedule_batch(semester: str) -> Optional[ScheduleImportBatch]:
    """Return the authoritative active snapshot batch for a semester.

    The authoritative source is ``ScheduleSemesterSelection``.  The legacy
    single-active fallback exists only for data created before the authority
    table was introduced and fails closed when ambiguous.
    """
    batch = _active_batch_via_selection(semester)
    if batch is not None:
        return batch
    return _legacy_single_active_fallback(semester)


def get_active_schedule_rows(semester: str) -> List[ScheduleImportRow]:
    """Return active snapshot rows in immutable source-row order."""
    batch = get_active_schedule_batch(semester)
    if batch is None:
        return []
    return _rows_for_batch(batch)


def _rows_for_batch(batch: ScheduleImportBatch) -> List[ScheduleImportRow]:
    return ScheduleImportRow.query.filter_by(
        batch_id=batch.id,
    ).order_by(
        ScheduleImportRow.source_row.asc(),
        ScheduleImportRow.id.asc(),
    ).all()


def resolve_current_schedule_snapshot(
    semester: Optional[str] = None,
) -> CurrentScheduleSelection:
    """Resolve the current teaching semester to a structured snapshot result.

    This is the only canonical schedule-selection boundary that Round 6B should
    consume.  It never maps an unset current semester to a blank-semester
    batch, and it never silently picks among ambiguous active rows.
    """
    if semester is None:
        semester = get_current_teaching_semester()

    if semester is None or str(semester).strip() == '':
        return CurrentScheduleSelection(
            status=CURRENT_SEMESTER_UNSET,
            semester='',
        )

    semester = str(semester).strip()

    batch = _active_batch_via_selection(semester)
    if batch is None:
        # No authority pointer: only accept an exactly-one legacy active row.
        active_count = _legacy_active_count(semester)
        if active_count == 0:
            return CurrentScheduleSelection(
                status=CURRENT_SEMESTER_CONFIGURED_BUT_NO_ACTIVE_SNAPSHOT,
                semester=semester,
            )
        if active_count > 1:
            return CurrentScheduleSelection(
                status=AMBIGUOUS,
                semester=semester,
            )
        batch = _legacy_single_active_fallback(semester)

    if batch is None:
        return CurrentScheduleSelection(
            status=CURRENT_SEMESTER_CONFIGURED_BUT_NO_ACTIVE_SNAPSHOT,
            semester=semester,
        )

    return CurrentScheduleSelection(
        status=READY,
        semester=semester,
        batch=batch,
        rows=_rows_for_batch(batch),
    )
