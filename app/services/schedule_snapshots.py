# -*- coding: utf-8 -*-
"""Canonical raw schedule snapshot persistence and retrieval.

This module owns the admin-maintained ``ScheduleImportBatch`` /
``ScheduleImportRow`` snapshot.  It deliberately keeps the raw cell text that
the legacy AutoReviewEngine matcher reads from ``pd.read_excel`` /
``str(row[...])``; it must not be confused with the normalized/frozen
``review_automation`` dataset layer.
"""
import math
from typing import Dict, List, Optional, Tuple

from app.models import (
    ScheduleImportBatch,
    ScheduleImportRow,
    db,
)

ACTIVE = 'active'
RETIRED = 'retired'

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


def persist_import_snapshot(
    df,
    source_filename: str,
    source_sha256: str,
    source_row_offset: int = DEFAULT_SOURCE_ROW_OFFSET,
) -> List[ScheduleImportBatch]:
    """Persist row-level snapshots for one admin schedule workbook.

    Rows are grouped by the workbook's ``学期`` column.  Each semester group
    becomes its own batch and that semester's previous active batch is retired
    in the same transaction.  This function does not commit; the caller decides
    the transaction boundary.
    """
    if df is None or len(df) == 0:
        return []

    groups: Dict[str, List[Tuple[int, object]]] = {}
    for source_index, row in df.iterrows():
        semester = _semester_key(row.get('学期'))
        groups.setdefault(semester, []).append((source_index, row))

    batches: List[ScheduleImportBatch] = []
    for semester, rows in groups.items():
        previous_actives = ScheduleImportBatch.query.filter_by(
            semester=semester,
            status=ACTIVE,
        ).all()
        for previous_active in previous_actives:
            previous_active.status = RETIRED

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

    return batches


def get_active_schedule_batch(semester: str) -> Optional[ScheduleImportBatch]:
    """Return the active snapshot batch for a semester, if any."""
    return ScheduleImportBatch.query.filter_by(
        semester=semester,
        status=ACTIVE,
    ).order_by(ScheduleImportBatch.id.desc()).first()


def get_active_schedule_rows(semester: str) -> List[ScheduleImportRow]:
    """Return active snapshot rows in immutable source-row order."""
    batch = get_active_schedule_batch(semester)
    if batch is None:
        return []
    return ScheduleImportRow.query.filter_by(
        batch_id=batch.id,
    ).order_by(
        ScheduleImportRow.source_row.asc(),
        ScheduleImportRow.id.asc(),
    ).all()
