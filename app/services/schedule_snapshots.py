# -*- coding: utf-8 -*-
"""Canonical raw schedule snapshot persistence and retrieval.

This module owns the admin-maintained ``ScheduleImportBatch`` /
``ScheduleImportRow`` snapshot and the authoritative per-semester selection
pointer (``ScheduleSemesterSelection``).  It keeps the raw cell text that the
legacy AutoReviewEngine matcher reads from ``pd.read_excel`` /
``str(row[...])`` and is intentionally separate from the normalized/frozen
``review_automation`` dataset layer.
"""
import datetime
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

from app.models import (
    ScheduleImportBatch,
    ScheduleImportRow,
    ScheduleImportRowScalarMeta,
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
INVALID_AUTHORITY = 'INVALID_AUTHORITY'

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
    error: Optional[str] = None

    @property
    def is_ready(self) -> bool:
        return self.status == READY


@dataclass
class AuthorityResolution:
    """Internal distinction between absent, valid, and invalid authority."""
    selection_exists: bool
    batch: Optional[ScheduleImportBatch] = None
    valid: bool = False
    error: Optional[str] = None


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


def _scalar_kind(value) -> str:
    """Classify the legacy scalar type for matcher anchor candidate selection."""
    from numbers import Integral, Real

    if value is None or _is_nan(value):
        return 'nan'
    if value is pd.NaT or (hasattr(value, 'is_nat') and getattr(value, 'is_nat')()):
        return 'nat'
    if isinstance(value, bool):
        return 'bool'
    if isinstance(value, Integral):
        return 'int'
    if isinstance(value, Real):
        return 'float'
    if isinstance(value, pd.Timestamp):
        return 'datetime'
    if isinstance(value, datetime.datetime):
        return 'datetime'
    if isinstance(value, datetime.date):
        return 'date'
    if isinstance(value, datetime.time):
        return 'time'
    if isinstance(value, str):
        return 'text'
    return 'unknown'


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


def _resolve_authority(semester: str) -> AuthorityResolution:
    """Return the exact authority state: absent, valid, or invalid.

    A present-but-invalid authority (dangling pointer or wrong-semester
    pointer) must never fall back to ``ScheduleImportBatch.status``.
    """
    selection = db.session.get(ScheduleSemesterSelection, semester)
    if selection is None:
        return AuthorityResolution(selection_exists=False)

    batch = db.session.get(ScheduleImportBatch, selection.active_batch_id)
    if batch is None:
        return AuthorityResolution(
            selection_exists=True,
            valid=False,
            error='dangling_authority_pointer',
        )
    if batch.semester != semester:
        return AuthorityResolution(
            selection_exists=True,
            batch=batch,
            valid=False,
            error='authority_semester_mismatch',
        )

    return AuthorityResolution(
        selection_exists=True,
        batch=batch,
        valid=True,
    )


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

        row_objects = []
        for source_index, row in rows:
            source_row = int(source_index) + source_row_offset
            row_obj = ScheduleImportRow(
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
            )
            db.session.add(row_obj)
            row_objects.append((source_index, row, row_obj))

        db.session.flush()
        for _, row, row_obj in row_objects:
            db.session.add(ScheduleImportRowScalarMeta(
                row_id=row_obj.id,
                teacher_name_kind=_scalar_kind(row.get('姓名')),
                course_name_kind=_scalar_kind(row.get('课程名称')),
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

    The authoritative source is ``ScheduleSemesterSelection``.  A present but
    invalid authority (dangling/wrong-semester) fails closed; the legacy
    single-active fallback is allowed only when the selection row is absent.
    """
    authority = _resolve_authority(semester)
    if authority.selection_exists:
        return authority.batch if authority.valid else None
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
    *,
    include_rows: bool = True,
) -> CurrentScheduleSelection:
    """Resolve the current teaching semester to a structured snapshot result.

    This is the only canonical schedule-selection boundary that Round 6B should
    consume.  It never maps an unset current semester to a blank-semester
    batch, and it never silently picks among ambiguous active rows.
    Consumers with a separate index can set include_rows=False to resolve
    the same authority without materializing the raw import snapshot.
    """
    if semester is None:
        semester = get_current_teaching_semester()

    if semester is None or str(semester).strip() == '':
        return CurrentScheduleSelection(
            status=CURRENT_SEMESTER_UNSET,
            semester='',
        )

    semester = str(semester).strip()

    authority = _resolve_authority(semester)
    if authority.selection_exists:
        if not authority.valid:
            return CurrentScheduleSelection(
                status=INVALID_AUTHORITY,
                semester=semester,
                error=authority.error,
            )
        return CurrentScheduleSelection(
            status=READY,
            semester=semester,
            batch=authority.batch,
            rows=_rows_for_batch(authority.batch) if include_rows else [],
        )

    # Selection row absent: the legacy exactly-one active fallback is allowed.
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
        rows=_rows_for_batch(batch) if include_rows else [],
    )
