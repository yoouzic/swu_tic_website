# -*- coding: utf-8 -*-
"""Adapter from canonical schedule snapshots to the legacy schedule shape.

This module deliberately does not normalize any cell text.  Snapshot rows
already contain the exact ``str(row['...'])`` values that the legacy
AutoReviewEngine matcher expects; the existing matcher performs its own
normalization later.
"""
from typing import List, Optional

import pandas as pd

from app.models import ScheduleImportRow
from app.services.schedule_snapshots import (
    READY,
    CurrentScheduleSelection,
    resolve_current_schedule_snapshot,
)

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


def snapshot_rows_to_legacy_df(
    rows: List[ScheduleImportRow],
) -> pd.DataFrame:
    """Convert canonical snapshot rows to legacy matcher-facing DataFrame.

    Row order is preserved from the input list.  Callers must pass rows in
    ``source_row ASC`` order; no sorting/normalization is performed here.
    """
    data = []
    for row in rows:
        data.append({
            '姓名': row.teacher_name,
            '教师所属学院': row.teacher_college,
            '课程名称': row.course_name,
            '星期几': row.weekday_raw,
            '上课节次': row.class_period_raw,
            '场地名称': row.location_raw,
            '教学班组成': row.class_composition_raw,
            '起始周': row.start_week_raw,
        })
    return pd.DataFrame(data, columns=LEGACY_SCHEDULE_COLUMNS)


def current_canonical_legacy_df() -> Optional[pd.DataFrame]:
    """Return a legacy-shaped DataFrame for the current READY snapshot.

    Returns ``None`` when the canonical selection is not READY.
    """
    selection: CurrentScheduleSelection = resolve_current_schedule_snapshot()
    if selection.status != READY:
        return None
    return snapshot_rows_to_legacy_df(selection.rows)
