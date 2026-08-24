# -*- coding: utf-8 -*-
"""Single boundary for review schedule source resolution.

This module owns:
- canonical snapshot adapter to legacy-shaped DataFrame
- scalar metadata completeness gate
- explicit/legacy/canonical/fail-closed source decision
- legacy Excel loader compatibility

No Flask/request/session dependency is allowed here.
"""
import datetime
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from app.models import (
    ScheduleImportRow,
    ScheduleImportRowScalarMeta,
    SystemSetting,
)
from app.services.schedule_snapshots import (
    CURRENT_SEMESTER_UNSET,
    READY,
    CurrentScheduleSelection,
    resolve_current_schedule_snapshot,
)
from app.utils.env_config import env_path

logger = logging.getLogger(__name__)

# Final source kinds used by AutoReviewEngine.
EXPLICIT_LEGACY = 'explicit_legacy'
CANONICAL_SNAPSHOT = 'canonical_snapshot'
LEGACY_FALLBACK = 'legacy_fallback'
NONE = 'none'
SCALAR_METADATA_INCOMPLETE = 'scalar_metadata_incomplete'

# Service-level state returned before the engine decides whether a legacy file
# actually exists.
LEGACY_FALLBACK_ELIGIBLE = 'legacy_fallback_eligible'

DEFAULT_SCHEDULE_PATH = env_path(
    'AUTO_REVIEW_DEFAULT_SCHEDULE_PATH',
    os.path.join(
        'data', 'storage', 'templates', 'auto_review', '2025-2026-1课表.xlsx'
    ),
)
SETTING_KEY_SCHEDULE_PATH = 'auto_review_schedule_path'

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

# Canonical scalar-kind vocabulary.  All values fit String(10).
KNOWN_SCALAR_KINDS = {
    'text',
    'int',
    'float',
    'bool',
    'datetime',
    'date',
    'time',
    'nan',
    'nat',
}


def _exists(path: str) -> bool:
    try:
        return os.path.exists(path)
    except Exception:
        return False


def _read_excel(path: str, sheet_name: int = 0) -> Optional[pd.DataFrame]:
    """Read a legacy schedule workbook with the frozen loader fallback order."""
    if not _exists(path):
        return None
    try:
        if path.lower().endswith('.xls'):
            try:
                df = pd.read_excel(path, sheet_name=sheet_name, engine='xlrd')
            except Exception:
                try:
                    df = pd.read_excel(path, sheet_name=sheet_name, engine='openpyxl')
                except Exception:
                    df = pd.read_excel(path, sheet_name=sheet_name)
        else:
            df = pd.read_excel(path, sheet_name=sheet_name)
        return df
    except Exception as e:
        logger.error("读取Excel文件失败 %s: %s", path, e)
        return None


@dataclass(frozen=True)
class ReviewScheduleSourceResolution:
    """Final structured result for review schedule source resolution."""
    kind: str
    canonical_status: Optional[str] = None
    semester: str = ''
    dataframe: Optional[pd.DataFrame] = None
    schedule_path: Optional[str] = None


def snapshot_scalar_metadata_complete(rows: List[ScheduleImportRow]) -> bool:
    """Return True only if every row has exactly one complete scalar meta."""
    row_ids = [row.id for row in rows if row.id is not None]
    if not row_ids:
        return True

    metas = ScheduleImportRowScalarMeta.query.filter(
        ScheduleImportRowScalarMeta.row_id.in_(row_ids)
    ).all()
    by_row: Dict[int, ScheduleImportRowScalarMeta] = {
        meta.row_id: meta for meta in metas
    }

    for row in rows:
        meta = by_row.get(row.id)
        if meta is None:
            return False
        if meta.teacher_name_kind not in KNOWN_SCALAR_KINDS:
            return False
        if meta.course_name_kind not in KNOWN_SCALAR_KINDS:
            return False
    return len(by_row) == len(row_ids)


def _anchor_cell(row, meta, field: str):
    text = getattr(row, field)
    if meta is None:
        raise ValueError('missing scalar metadata for canonical snapshot use')
    kind = getattr(meta, f'{field}_kind')

    if kind == 'text':
        return text
    if kind == 'int':
        return int(text)
    if kind == 'float':
        return float(text)
    if kind == 'bool':
        return text == 'True'
    if kind == 'datetime':
        return pd.Timestamp(text)
    if kind == 'date':
        return datetime.date.fromisoformat(str(text)[:10])
    if kind == 'time':
        return datetime.time.fromisoformat(str(text))
    if kind == 'nan':
        return float('nan')
    if kind == 'nat':
        return pd.NaT
    raise ValueError(f'unknown scalar kind: {kind}')


def snapshot_rows_to_legacy_df(
    rows: List[ScheduleImportRow],
) -> pd.DataFrame:
    """Convert canonical snapshot rows to legacy matcher-facing DataFrame."""
    row_ids = [row.id for row in rows if row.id is not None]
    meta_by_row: Dict[int, ScheduleImportRowScalarMeta] = {}
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


def _try_legacy_fallback() -> ReviewScheduleSourceResolution:
    schedule_config = SystemSetting.get(SETTING_KEY_SCHEDULE_PATH)
    path = schedule_config or DEFAULT_SCHEDULE_PATH
    if not _exists(path) and _exists(DEFAULT_SCHEDULE_PATH):
        path = DEFAULT_SCHEDULE_PATH
    df = _read_excel(path)
    return ReviewScheduleSourceResolution(
        kind=LEGACY_FALLBACK if df is not None else NONE,
        canonical_status=CURRENT_SEMESTER_UNSET,
        semester='',
        dataframe=df,
        schedule_path=path,
    )


def resolve_review_schedule_source(
    explicit_schedule_path: Optional[str] = None,
) -> ReviewScheduleSourceResolution:
    """Resolve the final review schedule source decision.

    This is the single boundary shared by AutoReviewEngine and reference-data
    orchestration.  Explicit path wins; canonical READY requires complete
    scalar metadata; configured non-READY or incomplete states fail closed;
    legacy fallback is eligible only when current semester is unset.
    """
    if explicit_schedule_path:
        df = _read_excel(explicit_schedule_path)
        return ReviewScheduleSourceResolution(
            kind=EXPLICIT_LEGACY if df is not None else NONE,
            semester='',
            dataframe=df,
            schedule_path=explicit_schedule_path,
        )

    selection: CurrentScheduleSelection = resolve_current_schedule_snapshot()

    if selection.status == READY:
        if not snapshot_scalar_metadata_complete(selection.rows):
            return ReviewScheduleSourceResolution(
                kind=NONE,
                canonical_status=SCALAR_METADATA_INCOMPLETE,
                semester=selection.semester,
            )
        return ReviewScheduleSourceResolution(
            kind=CANONICAL_SNAPSHOT,
            canonical_status=READY,
            semester=selection.semester,
            dataframe=snapshot_rows_to_legacy_df(selection.rows),
        )

    if selection.status == CURRENT_SEMESTER_UNSET:
        return _try_legacy_fallback()

    return ReviewScheduleSourceResolution(
        kind=NONE,
        canonical_status=selection.status,
        semester=selection.semester,
    )
