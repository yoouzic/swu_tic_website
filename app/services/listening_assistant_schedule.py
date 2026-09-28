"""Persistence and source-controlled reads for the listening-assistant index."""

from __future__ import annotations

import math
import re
import unicodedata
from numbers import Real
from typing import Any, Iterable

import pandas as pd
from sqlalchemy import inspect
from sqlalchemy.schema import CreateColumn

from app.models import (
    ListeningAssistantScheduleEntry,
    ScheduleImportBatch,
    db,
)
from app.services.listening_assistant_contracts import (
    ScheduleEntry,
    normalize_class_for_display,
    normalize_room,
    parse_period,
)
from app.services.schedule_snapshots import (
    DEFAULT_SOURCE_ROW_OFFSET,
    READY,
    RETIRED,
    resolve_current_schedule_snapshot,
)


PRIMARY_SOURCE_LABEL = '当前权威课表'
BACKUP_SOURCE_LABEL = '备用课表线索 · 需核对'

_MISSING = object()

# The Chinese aliases are the current admin workbook names.  The short
# English aliases keep this service usable with normalized workbook fixtures
# without manufacturing values for columns that are absent.
_COLUMN_ALIASES = {
    'semester': ('学期', 'semester'),
    'academic_year': ('学年', 'academic_year'),
    'course_code': ('课程号', 'course_code'),
    'selection_code': ('选课课号', 'selection_code'),
    'teacher_name': ('姓名', 'teacher_name'),
    'teacher_college': ('教师所属学院', 'teacher_college'),
    'course_title': ('课程名称', 'course_title', 'course_name'),
    'student_grade_class': (
        '教学班组成',
        'student_grade_class',
        'class_composition',
    ),
    'venue_id': ('场地编号', 'venue_id'),
    'location_raw': ('场地名称', 'location_raw', '上课地点', 'location'),
    'start_week_raw': ('起始周', 'start_week_raw', 'start_week'),
    'weekday_raw': ('星期几', 'weekday_raw', 'weekday'),
    'period_raw': (
        '上课节次',
        'period_raw',
        'class_period',
        'class_period_raw',
    ),
    'venue_start_week_raw': (
        '场地上课起始周',
        'venue_start_week_raw',
        'venue_start_week',
    ),
    'venue_period_raw': (
        '场地上课节次',
        'venue_period_raw',
        'venue_class_period',
    ),
}


def _ensure_listening_assistant_schema_on_bind(bind) -> None:
    table = ListeningAssistantScheduleEntry.__table__
    connection_inspector = inspect(bind)
    if not connection_inspector.has_table(table.name):
        table.create(bind=bind, checkfirst=True)
        return

    existing_columns = {
        column['name']
        for column in connection_inspector.get_columns(table.name)
    }
    preparer = bind.dialect.identifier_preparer
    for column in table.columns:
        if column.name in existing_columns:
            continue
        if column.primary_key or not column.nullable:
            raise RuntimeError(
                f"Cannot add non-nullable assistant schema column "
                f"'{column.name}' without a migration."
            )
        column_ddl = str(
            CreateColumn(column).compile(dialect=bind.dialect)
        )
        bind.exec_driver_sql(
            f'ALTER TABLE {preparer.quote(table.name)} '
            f'ADD COLUMN {column_ddl}'
        )
        existing_columns.add(column.name)

    existing_indexes = {
        index['name']
        for index in inspect(bind).get_indexes(table.name)
    }
    for index in table.indexes:
        if index.name not in existing_indexes:
            index.create(bind=bind, checkfirst=True)


def ensure_listening_assistant_schema(bind=None) -> None:
    """Idempotently create/extend only the assistant index schema.

    This is deliberately additive: it requires the canonical snapshot parent,
    creates the assistant table when absent, and adds only nullable model
    columns and missing model indexes when an older assistant table exists.
    """
    engine = db.engine
    if bind is None and not inspect(engine).has_table('schedule_import_batches'):
        raise RuntimeError(
            "Cannot initialize the listening-assistant schedule index because "
            "the prerequisite canonical table 'schedule_import_batches' is "
            "missing. Initialize the canonical schedule snapshot schema first."
        )
    if bind is not None:
        if not inspect(bind).has_table('schedule_import_batches'):
            raise RuntimeError(
                "Cannot initialize the listening-assistant schedule index because "
                "the prerequisite canonical table 'schedule_import_batches' is "
                "missing. Initialize the canonical schedule snapshot schema first."
            )
        _ensure_listening_assistant_schema_on_bind(bind)
        return

    with engine.begin() as connection:
        _ensure_listening_assistant_schema_on_bind(connection)


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        result = pd.isna(value)
        return bool(result)
    except (TypeError, ValueError):
        return False


def _cell(row: Any, field: str) -> object:
    """Return the first non-empty value from known workbook aliases."""
    for column in _COLUMN_ALIASES[field]:
        try:
            value = row[column]
        except (KeyError, IndexError, TypeError):
            try:
                value = row.get(column, _MISSING)
            except AttributeError:
                value = _MISSING
        if value is not _MISSING and not _is_missing(value):
            return value
    return None


def _raw_text(value: object) -> str | None:
    if _is_missing(value):
        return None
    return str(value)


def _normalized_text(value: object) -> str | None:
    raw = _raw_text(value)
    if raw is None:
        return None
    normalized = unicodedata.normalize('NFKC', raw).replace('\u3000', ' ')
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized or None


def _semester_key(value: object) -> str:
    return _normalized_text(value) or ''


def _source_row(
    source_index: object,
    position: int,
    source_row_offset: int,
) -> int:
    try:
        normalized_index = int(source_index)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(
            f'source index {source_index!r} must be numeric to align with '
            'canonical schedule snapshots'
        ) from error
    return normalized_index + source_row_offset


def _weekday(value: object) -> int | None:
    if _is_missing(value) or isinstance(value, bool):
        return None
    if isinstance(value, Real):
        number = float(value)
        if math.isfinite(number) and number.is_integer() and 1 <= int(number) <= 7:
            return int(number)
        return None

    text = _normalized_text(value) or ''
    text = text.replace('星期', '').replace('周', '')
    if text in {'一', '二', '三', '四', '五', '六', '日', '天'}:
        return {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '日': 7, '天': 7}[text]
    if re.fullmatch(r'[1-7]', text):
        return int(text)
    return None


def _batch_map(batches: Iterable[ScheduleImportBatch]) -> dict[str, ScheduleImportBatch]:
    return {_semester_key(batch.semester): batch for batch in batches}


def latest_retired_batch_id(semester: str | None) -> int | None:
    """Return the newest retired batch id for one normalized semester.

    This is a read-only discovery helper.  It never selects or mutates the
    current snapshot, and callers must still pass the returned id explicitly
    to the backup loader for its retired/same-semester validation.
    """
    requested_semester = _semester_key(semester)
    if not requested_semester:
        return None

    batches = ScheduleImportBatch.query.filter_by(
        status=RETIRED,
    ).order_by(
        ScheduleImportBatch.created_at.desc(),
        ScheduleImportBatch.id.desc(),
    ).all()
    for batch in batches:
        if _semester_key(batch.semester) == requested_semester:
            return batch.id
    return None


def _period_bounds(value: object) -> tuple[int | None, int | None]:
    period = parse_period(value) if not _is_missing(value) else None
    if period is None:
        return None, None
    return period


def persist_listening_assistant_entries(
    dataframe,
    batches: Iterable[ScheduleImportBatch],
    source_row_offset: int = DEFAULT_SOURCE_ROW_OFFSET,
) -> list[ListeningAssistantScheduleEntry]:
    """Persist assistant rows for the batches created by one workbook import.

    The function intentionally does not commit.  The caller can therefore
    commit the legacy import, canonical snapshot, and assistant index as one
    transaction.  ``batch_id + source_row`` is the stable idempotency key.
    """
    if dataframe is None or len(dataframe) == 0:
        return []

    batch_list = list(batches or ())
    if not batch_list:
        return []
    ensure_listening_assistant_schema(bind=db.session.connection())
    if any(batch.id is None for batch in batch_list):
        db.session.flush()

    batches_by_semester = _batch_map(batch_list)
    persisted: list[ListeningAssistantScheduleEntry] = []
    seen_keys: set[tuple[int, int]] = set()

    for position, (source_index, row) in enumerate(dataframe.iterrows()):
        batch = batches_by_semester.get(_semester_key(_cell(row, 'semester')))
        if batch is None:
            continue

        source_row = _source_row(source_index, position, source_row_offset)
        key = (batch.id, source_row)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        existing = ListeningAssistantScheduleEntry.query.filter_by(
            batch_id=batch.id,
            source_row=source_row,
        ).order_by(ListeningAssistantScheduleEntry.id.asc()).first()
        if existing is not None:
            persisted.append(existing)
            continue

        semester_value = _cell(row, 'semester')
        academic_year_value = _cell(row, 'academic_year')
        course_code_value = _cell(row, 'course_code')
        selection_code_value = _cell(row, 'selection_code')
        teacher_name_value = _cell(row, 'teacher_name')
        teacher_college_value = _cell(row, 'teacher_college')
        course_title_value = _cell(row, 'course_title')
        class_value = _cell(row, 'student_grade_class')
        venue_id_value = _cell(row, 'venue_id')

        course_code = _normalized_text(course_code_value)
        selection_code = _normalized_text(selection_code_value)
        teacher_name = _normalized_text(teacher_name_value)
        teacher_college = _normalized_text(teacher_college_value)
        course_title = _normalized_text(course_title_value)
        class_raw = _raw_text(class_value)
        class_normalized = normalize_class_for_display(class_value) or None
        venue_id = _normalized_text(venue_id_value)
        location_value = _cell(row, 'location_raw')
        location_raw = _raw_text(location_value)
        location_normalized = normalize_room(location_value) or None
        start_week_raw = _raw_text(_cell(row, 'start_week_raw'))
        weekday_value = _cell(row, 'weekday_raw')
        weekday_raw = _raw_text(weekday_value)
        period_value = _cell(row, 'period_raw')
        period_raw = _raw_text(period_value)
        venue_start_week_raw = _raw_text(_cell(row, 'venue_start_week_raw'))
        venue_period_value = _cell(row, 'venue_period_raw')
        venue_period_raw = _raw_text(venue_period_value)
        period_start, period_end = _period_bounds(period_value)
        venue_period_start, venue_period_end = _period_bounds(venue_period_value)

        academic_year = (
            _normalized_text(academic_year_value)
            or _normalized_text(batch.academic_year)
        )

        entry = ListeningAssistantScheduleEntry(
            batch_id=batch.id,
            source_row=source_row,
            semester=_normalized_text(batch.semester) or '',
            academic_year=academic_year,
            semester_raw=_raw_text(semester_value),
            academic_year_raw=_raw_text(academic_year_value),
            course_code=course_code,
            course_code_raw=_raw_text(course_code_value),
            selection_code=selection_code,
            selection_code_raw=_raw_text(selection_code_value),
            teacher_name=teacher_name,
            teacher_name_raw=_raw_text(teacher_name_value),
            teacher_college=teacher_college,
            teacher_college_raw=_raw_text(teacher_college_value),
            course_title=course_title,
            course_title_raw=_raw_text(course_title_value),
            student_grade_class=class_normalized,
            student_grade_class_raw=class_raw,
            venue_id=venue_id,
            venue_id_raw=_raw_text(venue_id_value),
            location_normalized=location_normalized,
            location_raw=location_raw,
            start_week_raw=start_week_raw,
            weekday_raw=weekday_raw,
            period_raw=period_raw,
            venue_start_week_raw=venue_start_week_raw,
            venue_period_raw=venue_period_raw,
            weekday=_weekday(weekday_value),
            period_start=period_start,
            period_end=period_end,
            venue_period_start=venue_period_start,
            venue_period_end=venue_period_end,
        )
        db.session.add(entry)
        persisted.append(entry)

    db.session.flush()
    return persisted


def _requested_batch_id(
    source_batch_id: int | str | None,
    batch_id: int | str | None,
) -> int:
    def _coerce(value: int | str) -> int:
        if type(value) is int and value > 0:
            return value
        if type(value) is str and re.fullmatch(r'[1-9][0-9]*', value):
            return int(value)
        raise ValueError('backup source batch id must be a positive integer')

    normalized_source = (
        _coerce(source_batch_id)
        if source_batch_id is not None
        else None
    )
    normalized_batch = (
        _coerce(batch_id)
        if batch_id is not None
        else None
    )
    if normalized_source is not None and normalized_batch is not None:
        if normalized_source != normalized_batch:
            raise ValueError('source_batch_id and batch_id must match')
        return normalized_source
    if normalized_source is not None:
        return normalized_source
    if normalized_batch is not None:
        return normalized_batch
    raise ValueError('backup source requires an explicit retired batch id')


def _rows_for_batch(batch_id: int) -> list[ListeningAssistantScheduleEntry]:
    return ListeningAssistantScheduleEntry.query.filter_by(
        batch_id=batch_id,
    ).order_by(
        ListeningAssistantScheduleEntry.source_row.asc(),
        ListeningAssistantScheduleEntry.id.asc(),
    ).all()


def _as_contract(
    entry: ListeningAssistantScheduleEntry,
    *,
    source_kind: str,
    source_label: str,
) -> ScheduleEntry:
    main_period = parse_period(entry.period_raw) if entry.period_raw is not None else None
    venue_period = (
        parse_period(entry.venue_period_raw)
        if entry.venue_period_raw is not None
        else None
    )
    if venue_period is None and (
        entry.venue_period_start is not None
        and entry.venue_period_end is not None
    ):
        venue_period = (
            entry.venue_period_start,
            entry.venue_period_end,
        )
    if venue_period is not None:
        period = venue_period
        period_raw = entry.venue_period_raw
    else:
        period = main_period
        period_raw = entry.period_raw
        if period is None:
            if entry.period_start is not None and entry.period_end is not None:
                period = (entry.period_start, entry.period_end)

    return ScheduleEntry(
        entry_id=f'listening-assistant:{entry.batch_id}:{entry.source_row}',
        room=entry.location_normalized or entry.location_raw or '',
        period=period,
        course_code=entry.course_code or '',
        selection_code=entry.selection_code or '',
        course_title=entry.course_title or '',
        teacher_name=entry.teacher_name or '',
        teacher_college=entry.teacher_college or '',
        student_grade_class=entry.student_grade_class or '',
        weekday=entry.weekday,
        semester=entry.semester or '',
        academic_year=entry.academic_year or '',
        source_kind=source_kind,
        source_label=source_label,
        source_batch_id=str(entry.batch_id),
        source_row=entry.source_row,
        location_raw=entry.location_raw,
        period_raw=period_raw,
        class_raw=entry.student_grade_class_raw,
        start_week_raw=entry.start_week_raw,
        venue_start_week_raw=entry.venue_start_week_raw,
        venue_period_raw=entry.venue_period_raw,
    )


def load_schedule_entries(
    source_kind: str = 'primary',
    semester: str | None = None,
    source_batch_id: int | str | None = None,
    batch_id: int | str | None = None,
) -> list[ScheduleEntry]:
    """Load assistant contracts from one authoritative or explicit batch.

    Primary reads resolve the canonical current-snapshot pointer.  Backup
    reads are intentionally fail-closed: a caller must name one retired batch;
    no historical union and no legacy ``Course`` fallback is allowed.
    """
    normalized_kind = str(source_kind or '').strip().lower()
    if normalized_kind not in {'primary', 'backup'}:
        raise ValueError("source_kind must be 'primary' or 'backup'")

    if normalized_kind == 'primary':
        snapshot = resolve_current_schedule_snapshot(semester)
        if snapshot.status != READY or snapshot.batch is None:
            return []
        rows = _rows_for_batch(snapshot.batch.id)
        return [
            _as_contract(
                entry,
                source_kind='primary',
                source_label=PRIMARY_SOURCE_LABEL,
            )
            for entry in rows
        ]

    requested_semester = _semester_key(semester)
    if not requested_semester:
        raise ValueError('backup source requires a non-empty semester')

    requested_id = _requested_batch_id(source_batch_id, batch_id)
    batch = db.session.get(ScheduleImportBatch, requested_id)
    if batch is None:
        raise ValueError('backup source batch does not exist')
    if batch.status != RETIRED:
        raise ValueError('backup source batch must be retired')
    if requested_semester != _semester_key(batch.semester):
        raise ValueError('backup source batch semester does not match requested semester')

    rows = _rows_for_batch(batch.id)
    return [
        _as_contract(
            entry,
            source_kind='backup',
            source_label=BACKUP_SOURCE_LABEL,
        )
        for entry in rows
    ]


__all__ = [
    'BACKUP_SOURCE_LABEL',
    'PRIMARY_SOURCE_LABEL',
    'ensure_listening_assistant_schema',
    'latest_retired_batch_id',
    'load_schedule_entries',
    'persist_listening_assistant_entries',
]
