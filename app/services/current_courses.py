"""Current operational Course rows bound to the authoritative schedule batch.

Historical Course rows and their registrations remain intact. New imports have
an exact membership map; old snapshots are matched conservatively and expose
their compatibility status for diagnosis.
"""
from collections import defaultdict
from sqlalchemy.orm import joinedload

from app.models import (
    Course, ListeningAssistantScheduleEntry, ScheduleCourseMapping,
    ScheduleCourseMembership, ScheduleImportBatch, ScheduleSemesterSelection, db,
)
from app.services.academic_term import get_current_teaching_semester
from app.services.schedule_snapshots import resolve_current_schedule_snapshot


def ensure_current_course_schema():
    """Add only the two new tables to an existing installation."""
    ScheduleCourseMapping.__table__.create(bind=db.engine, checkfirst=True)
    ScheduleCourseMembership.__table__.create(bind=db.engine, checkfirst=True)


def persist_course_mapping(batch, course_ids):
    """Mark a complete exact map in the surrounding import transaction."""
    db.session.add(ScheduleCourseMapping(batch_id=batch.id))
    for course_id in sorted(set(course_ids)):
        db.session.add(ScheduleCourseMembership(batch_id=batch.id, course_id=course_id))


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

The Course-only compatibility path is limited to installations with no formal
semester or snapshot authority at all. Once authority is configured, absence
or invalid authority yields an empty query.
"""
    ensure_current_course_schema()
    if _is_course_only_legacy():
        query = Course.query
        return query.filter(Course.semester == semester) if semester else query
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
