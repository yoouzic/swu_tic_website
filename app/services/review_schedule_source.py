# -*- coding: utf-8 -*-
"""Adapter and fail-closed source resolution for canonical schedule snapshots.

This module deliberately does not normalize any cell text.  Snapshot rows
already contain the exact ``str(row['...'])`` values that the legacy
AutoReviewEngine matcher expects; the existing matcher performs its own
normalization later.
"""
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from app.models import ScheduleImportRow, ScheduleImportRowScalarMeta
from app.services.schedule_snapshots import (
    CURRENT_SEMESTER_UNSET,
    READY,
    CurrentScheduleSelection,
    resolve_current_schedule_snapshot,
)

# Final source kinds used by AutoReviewEngine.
EXPLICIT_LEGACY = 'explicit_legacy'
CANONICAL_SNAPSHOT = 'canonical_snapshot'
LEGACY_FALLBACK = 'legacy_fallback'
NONE = 'none'

# Service-level state returned before the engine decides whether a legacy file
# actually exists.
LEGACY_FALLBACK_ELIGIBLE = 'legacy_fallback_eligible'

# Exact legacy column order used by the matcher.
LEGACY_SCHEDULE_COLUMNS = [
    '姓名',
    '教师所属学院',
    '课程名称',
    '星期几',
    '上课节次',
    '场地名称',
    '教学班组成',
    '起始周',
]

ROW_FIELD_TO_LEGACY_COLUMN = {
    'teacher_name': '姓名',
    'teacher_college': '教师所属学院',
    'course_name': '课程名称',
    'weekday_raw': '星期几',
    'class_period_raw': '上课节次',
    'location_raw': '场地名称',
    'class_composition_raw': '教学班组成',
    'start_week_raw': '起始周',
}


@dataclass(frozen=True)
class ReviewScheduleSourceResolution:
    """Structured result for AutoReviewEngine schedule source resolution.

    ``dataframe`` is populated only for canonical READY.  For the legacy
    fallback-eligible state, the engine must decide whether an actual legacy
    file exists before setting the final source kind.
    """
    kind: str
    canonical_status: Optional[str] = None
    semester: str = ''
    dataframe: Optional[pd.DataFrame] = None


def _anchor_cell(row, meta, field: str):
    text = getattr(row, field)
    if meta is None:
        return text
    kind = getattr(meta, f'{field}_kind')
    if kind == 'missing':
        return float('nan')
    if kind == 'int':
        return int(text)
    if kind == 'float':
        return float(text)
    return text


def snapshot_rows_to_legacy_df(
    rows: List[ScheduleImportRow],
) -> pd.DataFrame:
    """Convert canonical snapshot rows to legacy matcher-facing DataFrame.

    Row order is preserved from the input list.  Callers must pass rows in
    ``source_row ASC`` order; no sorting/normalization is performed here.
    """
    row_ids = [row.id for row in rows if row.id is not None]
    meta_by_row = {}
    if row_ids:
        meta_by_row = {
            meta.row_id: meta
            for meta in ScheduleImportRowScalarMeta.query.filter(
                ScheduleImportRowScalarMeta.row_id.in_(row_ids)
            ).all()
        }

    data = []
    for row in rows:
        meta = meta_by_row.get(row.id)
        data.append({
            '姓名': _anchor_cell(row, meta, 'teacher_name'),
            '教师所属学院': row.teacher_college,
            '课程名称': _anchor_cell(row, meta, 'course_name'),
            '星期几': row.weekday_raw,
            '上课节次': row.class_period_raw,
            '场地名称': row.location_raw,
            '教学班组成': row.class_composition_raw,
            '起始周': row.start_week_raw,
        })
    return pd.DataFrame(data, columns=LEGACY_SCHEDULE_COLUMNS)


def resolve_review_schedule_source() -> ReviewScheduleSourceResolution:
    """Resolve the canonical schedule source into a structured state.

    The only non-READY canonical state allowed to consider legacy Excel is
    ``CURRENT_SEMESTER_UNSET``.  Configured-but-unavailable, ambiguous, and
    invalid-authority states intentionally return ``NONE`` so the compatibility
    resolver cannot silently use a stale historical Excel file.
    """
    selection: CurrentScheduleSelection = resolve_current_schedule_snapshot()

    if selection.status == READY:
        return ReviewScheduleSourceResolution(
            kind=CANONICAL_SNAPSHOT,
            canonical_status=READY,
            semester=selection.semester,
            dataframe=snapshot_rows_to_legacy_df(selection.rows),
        )

    if selection.status == CURRENT_SEMESTER_UNSET:
        return ReviewScheduleSourceResolution(
            kind=LEGACY_FALLBACK_ELIGIBLE,
            canonical_status=CURRENT_SEMESTER_UNSET,
            semester='',
        )

    return ReviewScheduleSourceResolution(
        kind=NONE,
        canonical_status=selection.status,
        semester=selection.semester,
    )


def current_canonical_legacy_df() -> Optional[pd.DataFrame]:
    """Return a legacy-shaped DataFrame for the current READY snapshot.

    Returns ``None`` when the canonical selection is not READY.
    """
    resolution = resolve_review_schedule_source()
    return resolution.dataframe
