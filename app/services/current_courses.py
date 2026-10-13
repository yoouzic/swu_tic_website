"""Current operational Course rows bound to the authoritative schedule batch.

Historical Course rows and their registrations remain intact. New imports have
an exact membership map; old snapshots are matched conservatively and expose
their compatibility status for diagnosis.

``persist_courses_for_batch`` is the single importer for current courses.  The
admin upload route and the Windows debug launcher both call it, so the two entry
points cannot disagree about what the current course set contains.
"""
from collections import defaultdict
from numbers import Real

import pandas as pd
from sqlalchemy.orm import joinedload

from app.models import (
    Course, ListeningAssistantScheduleEntry, ScheduleCourseMapping,
    ScheduleCourseMembership, ScheduleImportBatch, ScheduleSemesterSelection,
    Teacher, Venue, db,
)
from app.services.academic_term import (
    clean_term_text,
    compose_semester_identifier,
    get_current_teaching_semester,
)
from app.services.schedule_snapshots import resolve_current_schedule_snapshot


def ensure_current_course_schema(bind=None):
    """Add only the two new tables to an existing installation.

    ``bind`` defaults to the session's own connection.  Creating the tables on a
    separate engine connection would need a second connection while the caller's
    import transaction still holds a write lock, which SQLite answers with
    ``database is locked`` on large workbooks.  Joining the pending transaction
    keeps the schema step inside the same unit of work.
    """
    if bind is None:
        bind = db.session.connection()
    ScheduleCourseMapping.__table__.create(bind=bind, checkfirst=True)
    ScheduleCourseMembership.__table__.create(bind=bind, checkfirst=True)


def persist_course_mapping(batch, course_ids):
    """Mark a complete exact map in the surrounding import transaction."""
    db.session.add(ScheduleCourseMapping(batch_id=batch.id))
    for course_id in sorted(set(course_ids)):
        db.session.add(ScheduleCourseMembership(batch_id=batch.id, course_id=course_id))


# Canonical workbook column for each Course field written by every importer.
_COURSE_KEY_FIELDS = (
    'course_code', 'selection_code', 'start_week', 'weekday', 'class_period',
    'course_name', 'venue_start_week', 'venue_class_period', 'class_size',
    'class_composition', 'credits', 'total_hours', 'offering_college',
    'major_composition', 'enrollment_count', 'weekly_hours', 'class_time',
    'class_location', 'course_nature', 'teacher_id', 'venue_id', 'semester',
    'academic_year',
)
COURSE_KEY_FIELDS = _COURSE_KEY_FIELDS

_COURSE_ROW_COLUMNS = {
    'course_code': '课程号',
    'selection_code': '选课课号',
    'start_week': '起始周',
    'weekday': '星期几',
    'class_period': '上课节次',
    'course_name': '课程名称',
    'venue_start_week': '场地上课起始周',
    'venue_class_period': '场地上课节次',
    'class_size': '教学班人数',
    'class_composition': '教学班组成',
    'credits': '学分',
    'total_hours': '总学时',
    'offering_college': '开课学院',
    'major_composition': '专业组成',
    'enrollment_count': '选课人数',
    'weekly_hours': '周学时',
    'class_time': '上课时间',
    'class_location': '上课地点',
    'course_nature': '课程性质',
    'teacher_id': '教工号',
    'venue_id': '场地编号',
    # 学期与学年参与自然键：两行只有在同一学期内才视为同一门课。
    'semester': '学期',
    'academic_year': '学年',
}
COURSE_ROW_COLUMNS = _COURSE_ROW_COLUMNS


def _as_key_text(value) -> str:
    """Return the canonical text for one natural-key field.

    This reproduces the admin importer's historical canonicalization exactly:
    both entry points must consider the same two rows duplicates.
    """
    if value is None or pd.isna(value):
        return ''
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, Real) and not isinstance(value, bool):
        number = float(value)
        return str(int(number)) if number.is_integer() else str(number)
    text = str(value).strip()
    return '' if text.lower() == 'nan' else text


def course_model_key(course):
    return tuple(_as_key_text(getattr(course, field, None)) for field in _COURSE_KEY_FIELDS)


def course_row_key(row):
    return tuple(_as_key_text(row.get(_COURSE_ROW_COLUMNS[field])) for field in _COURSE_KEY_FIELDS)


def course_legacy_term_key(course_key, academic_year, *, clear_year=False):
    """Return the same key as an installation that stored the raw term would have.

    ``persist_import_snapshot`` composes ``2026-2027`` + ``1`` into
    ``2026-2027-1`` before the Course rows are built.  An installation that
    imported the same workbook *before* that composition stored the bare term
    instead.  Recognising the raw-term variant is what lets a re-import reuse
    those existing rows - and their listening bans - instead of creating
    duplicates.  ``clear_year`` additionally blanks the academic year, for rows
    written by an importer running against a workbook that had no 学年 column.
    Returns ``None`` when there is no distinct variant.
    """
    if not academic_year:
        return None
    index = _COURSE_KEY_FIELDS.index('semester')
    current = course_key[index]
    prefix = f'{academic_year}-'
    if not current.startswith(prefix):
        return None
    term = current[len(prefix):]
    if not term or term == current:
        return None
    variant = list(course_key)
    variant[index] = term
    if clear_year:
        variant[_COURSE_KEY_FIELDS.index('academic_year')] = ''
    return tuple(variant)


def _course_cell(row, field):
    return row.get(_COURSE_ROW_COLUMNS[field])


def _present(value) -> bool:
    return value is not None and not pd.isna(value)


def _text_or_none(value):
    return str(value).strip() if _present(value) else None


def _int_or_none(value):
    return int(value) if _present(value) else None


def _float_or_none(value):
    return float(value) if _present(value) else None


def persist_courses_for_batch(batch, frame):
    """Create teachers, venues, Course rows and the exact membership map.

    The single implementation shared by the admin upload route and the Windows
    debug launcher.  Each row keeps its *own* semester: a caller that hands over
    a whole multi-semester workbook must still get one Course per row carrying
    that row's semester, never the surrounding batch's, or a multi-semester
    workbook would be stamped with the wrong term.  It does not commit; the
    caller owns the transaction and may therefore reject the whole workbook on
    any row error.
    """
    ensure_current_course_schema(db.session.connection())

    stats = {
        'total_rows': 0 if frame is None else len(frame),
        'teachers_added': 0,
        'teachers_updated': 0,
        'venues_added': 0,
        'venues_updated': 0,
        'courses_added': 0,
        'courses_skipped_duplicate': 0,
        'errors': [],
    }
    if frame is None or len(frame) == 0:
        return stats

    batch_semester = clean_term_text(batch.semester)
    batch_academic_year = clean_term_text(batch.academic_year)

    teacher_columns = ['教工号', '姓名', '性别', '职称名称', '教师所属学院', '教师联系电话']
    if all(column in frame.columns for column in teacher_columns):
        teacher_data = frame[teacher_columns].drop_duplicates(subset=['教工号'])
        for _, row in teacher_data.iterrows():
            try:
                teacher_id = clean_term_text(row['教工号'])
                if not teacher_id:
                    continue
                teacher = db.session.get(Teacher, teacher_id)
                if teacher:
                    teacher.add_value('title', row['职称名称'])
                    teacher.add_value('college', row['教师所属学院'])
                    teacher.add_value('phone', row['教师联系电话'])
                    stats['teachers_updated'] += 1
                else:
                    db.session.add(Teacher(
                        teacher_id=teacher_id,
                        name=str(row['姓名']).strip() if pd.notna(row['姓名']) else '',
                        gender=_text_or_none(row['性别']),
                        title=_text_or_none(row['职称名称']),
                        college=_text_or_none(row['教师所属学院']),
                        phone=_text_or_none(row['教师联系电话']),
                    ))
                    stats['teachers_added'] += 1
            except Exception as error:  # noqa: BLE001 - collected for rollback
                stats['errors'].append(f"处理教师数据时出错（教工号：{row['教工号']}）：{error}")

    venue_columns = ['场地编号', '场地名称', '场地类别名称', '校区', '楼层号', '教学楼', '座位数']
    if all(column in frame.columns for column in venue_columns):
        venue_data = (frame[venue_columns].dropna(subset=['场地编号'])
                      .drop_duplicates(subset=['场地编号']))
        for _, row in venue_data.iterrows():
            try:
                venue_id = clean_term_text(row['场地编号'])
                if not venue_id:
                    continue
                if db.session.get(Venue, venue_id):
                    stats['venues_updated'] += 1
                else:
                    db.session.add(Venue(
                        venue_id=venue_id,
                        name=_text_or_none(row['场地名称']),
                        category=_text_or_none(row['场地类别名称']),
                        campus=_text_or_none(row['校区']),
                        floor=_float_or_none(row['楼层号']),
                        building=_text_or_none(row['教学楼']),
                        capacity=_float_or_none(row['座位数']),
                    ))
                    stats['venues_added'] += 1
            except Exception as error:  # noqa: BLE001 - collected for rollback
                stats['errors'].append(f"处理场地数据时出错（场地编号：{row['场地编号']}）：{error}")

    existing_keys = {course_model_key(course): course for course in Course.query.all()}
    imported_ids = []
    for _, row in frame.iterrows():
        course_code = clean_term_text(_course_cell(row, 'course_code'))
        selection_code = clean_term_text(_course_cell(row, 'selection_code'))
        try:
            if not course_code or not selection_code:
                stats['errors'].append(f"课程号或选课编号为空（行：{row.name + 2}）")
                continue

            # 每行使用自己的学期；只有整帧未携带时才回退到批次学期。
            row_semester = clean_term_text(_course_cell(row, 'semester')) or batch_semester
            row_academic_year = (
                clean_term_text(_course_cell(row, 'academic_year')) or batch_academic_year
            )

            course = Course(
                course_code=course_code,
                selection_code=selection_code,
                start_week=_text_or_none(_course_cell(row, 'start_week')),
                weekday=_int_or_none(_course_cell(row, 'weekday')),
                class_period=_text_or_none(_course_cell(row, 'class_period')),
                course_name=_text_or_none(_course_cell(row, 'course_name')) or '',
                venue_start_week=_text_or_none(_course_cell(row, 'venue_start_week')),
                venue_class_period=_text_or_none(_course_cell(row, 'venue_class_period')),
                class_size=_int_or_none(_course_cell(row, 'class_size')),
                class_composition=_text_or_none(_course_cell(row, 'class_composition')),
                credits=_float_or_none(_course_cell(row, 'credits')),
                total_hours=_float_or_none(_course_cell(row, 'total_hours')),
                offering_college=_text_or_none(_course_cell(row, 'offering_college')),
                enrollment_count=_int_or_none(_course_cell(row, 'enrollment_count')),
                weekly_hours=_text_or_none(_course_cell(row, 'weekly_hours')),
                class_time=_text_or_none(_course_cell(row, 'class_time')),
                class_location=_text_or_none(_course_cell(row, 'class_location')),
                course_nature=_text_or_none(_course_cell(row, 'course_nature')),
                major_composition=_text_or_none(_course_cell(row, 'major_composition')),
                teacher_id=_text_or_none(_course_cell(row, 'teacher_id')),
                venue_id=_text_or_none(_course_cell(row, 'venue_id')),
                semester=row_semester or None,
                academic_year=row_academic_year or None,
            )
            course_key = course_row_key(row)
            existing = existing_keys.get(course_key)
            legacy_reused = False
            if existing is None:
                # Pre-composition installations stored the workbook's bare term.
                # The variant needs the academic year, but such a row may predate
                # the workbook's 学年 column and hold NULL, so fall back to the
                # batch's year: without it the row would not be recognised, a
                # duplicate Course would be created, and any ListeningBan bound to
                # the original row would stop suppressing the course.
                legacy_year = row_academic_year or clean_term_text(batch.academic_year)
                legacy_key = course_legacy_term_key(course_key, legacy_year)
                if legacy_key is not None:
                    existing = existing_keys.get(legacy_key)
                    legacy_reused = existing is not None
                if existing is None and legacy_year:
                    # Older importers ran against workbooks without a 学年 column,
                    # so those rows hold no year at all.
                    legacy_key = course_legacy_term_key(course_key, legacy_year, clear_year=True)
                    if legacy_key is not None:
                        existing = existing_keys.get(legacy_key)
                        legacy_reused = existing is not None
            if existing is not None:
                if legacy_reused:
                    # Adopt the reused row into the composed identity so the
                    # registration gate can see it under the configured semester
                    # instead of an orphaned bare term.
                    if row_semester and existing.semester != row_semester:
                        existing.semester = row_semester
                    if row_academic_year and existing.academic_year != row_academic_year:
                        existing.academic_year = row_academic_year
                    existing_keys.pop(legacy_key, None)
                    existing_keys[course_key] = existing
                stats['courses_skipped_duplicate'] += 1
                imported_ids.append(existing.id)
                continue
            db.session.add(course)
            db.session.flush()
            existing_keys[course_key] = course
            imported_ids.append(course.id)
            stats['courses_added'] += 1
        except Exception as error:  # noqa: BLE001 - collected for rollback
            stats['errors'].append(
                f"处理课程数据时出错（课程号：{course_code}-{selection_code}）：{error}"
            )

    # A "complete" marker asserts that this batch's course set is exactly known.
    # Never record it for an empty set: that would disable the legacy
    # compatibility matcher for a batch that produced no mappable rows.
    if imported_ids:
        persist_course_mapping(batch, imported_ids)
    return stats


def _text(value):
    if value is None:
        return ''
    text = str(value).strip()
    if text.lower() == 'nan':
        return ''
    return text


def _number_text(value):
    try:
        number = float(value)
        return str(int(number)) if number.is_integer() else str(number)
    except (ValueError, TypeError):
        return _text(value)


def _legacy_snapshot_course_ids(selection):
    """Accept only unambiguous matches using immutable raw schedule cells."""
    courses = Course.query.options(
        joinedload(Course.teacher), joinedload(Course.venue),
    ).filter(Course.semester == selection.semester).all()
    entries = ListeningAssistantScheduleEntry.query.filter_by(batch_id=selection.batch.id).all()
    index = {entry.source_row: entry for entry in entries}
    by_raw_key = defaultdict(list)
    by_full_key = defaultdict(list)
    for course in courses:
        raw_key = (
            _text(course.course_name), _text(course.start_week),
            _text(course.class_period), _text(course.class_composition),
            _text(course.teacher.name if course.teacher else None),
            _text(course.venue.name if course.venue else course.class_location),
            _number_text(course.weekday),
        )
        by_raw_key[raw_key].append(course.id)
        entry_key = (
            _text(course.course_code), _text(course.selection_code),
            _text(course.venue_id), _text(course.venue_start_week),
            _text(course.venue_class_period),
        )
        by_full_key[(raw_key, entry_key)].append(course.id)
    ids = set()
    ambiguous_rows = 0
    for raw in selection.rows:
        entry = index.get(raw.source_row)
        raw_key = (
            _text(raw.course_name), _text(raw.start_week_raw),
            _text(raw.class_period_raw), _text(raw.class_composition_raw),
            _text(raw.teacher_name), _text(raw.location_raw),
            _number_text(raw.weekday_raw),
        )
        if entry is None:
            matches = by_raw_key.get(raw_key, [])
        else:
            entry_key = (
                _text(entry.course_code_raw), _text(entry.selection_code_raw),
                _text(entry.venue_id_raw), _text(entry.venue_start_week_raw),
                _text(entry.venue_period_raw),
            )
            matches = by_full_key.get((raw_key, entry_key), [])
        if len(matches) == 1:
            ids.add(matches[0])
        elif len(matches) > 1:
            ambiguous_rows += 1
    return sorted(ids), ambiguous_rows


def _is_course_only_legacy():
    return (
        not get_current_teaching_semester()
        and ScheduleSemesterSelection.query.first() is None
        and ScheduleImportBatch.query.first() is None
    )


def current_course_status(semester=None):
    ensure_current_course_schema()
    selection = resolve_current_schedule_snapshot(semester, include_rows=False)
    result = {'status': selection.status, 'semester': selection.semester,
              'batch_id': selection.batch.id if selection.batch else None,
              'mapping_status': None, 'course_ids': [], 'ambiguous_rows': 0}
    if not selection.is_ready:
        return result
    if db.session.get(ScheduleCourseMapping, selection.batch.id) is not None:
        result['mapping_status'] = 'COMPLETE'
        result['course_ids'] = [row[0] for row in db.session.query(ScheduleCourseMembership.course_id).filter_by(
            batch_id=selection.batch.id).all()]
    else:
        result['mapping_status'] = 'LEGACY_SNAPSHOT_MATCH'
        selection = resolve_current_schedule_snapshot(selection.semester)
        result['course_ids'], result['ambiguous_rows'] = _legacy_snapshot_course_ids(selection)
    return result


def current_course_query(semester=None, *, all_semesters=False):
    """Return a Course query suitable for existing search/filter composition.

New operational queries always require configured snapshot authority.
Course-only historical installations never become a current schedule.
"""
    ensure_current_course_schema()
    if all_semesters and _is_course_only_legacy():
        # Explicit administration/history queries keep their legacy data;
        # default queries used by new registration and audit tags fail closed.
        return Course.query.filter(Course.semester == semester) if semester else Course.query
    semesters = [semester] if semester else [None]
    if all_semesters and semester is None:
        semesters = [row[0] for row in db.session.query(ScheduleImportBatch.semester).distinct().all()]
    ids = set()
    batch_ids = set()
    for selected_semester in semesters:
        selection = resolve_current_schedule_snapshot(selected_semester, include_rows=False)
        if not selection.is_ready:
            continue
        if db.session.get(ScheduleCourseMapping, selection.batch.id) is not None:
            batch_ids.add(selection.batch.id)
        else:
            selection = resolve_current_schedule_snapshot(selection.semester)
            legacy_ids, _ = _legacy_snapshot_course_ids(selection)
            ids.update(legacy_ids)
    member_query = db.session.query(ScheduleCourseMembership.course_id).filter(
        ScheduleCourseMembership.batch_id.in_(batch_ids))
    return Course.query.filter(db.or_(Course.id.in_(member_query), Course.id.in_(ids)))


def is_current_course(course_id, semester=None):
    return current_course_query(semester).filter(Course.id == course_id).first() is not None
