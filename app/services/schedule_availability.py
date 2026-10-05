"""Read-only current snapshot, candidate, and registration capabilities."""
from flask import current_app
from app.models import (Course, ListeningAssistantScheduleEntry, ScheduleCourseMapping,
                        ScheduleCourseMembership, db)
from app.services.schedule_snapshots import READY, resolve_current_schedule_snapshot


def site_capture_assistance_enabled():
    """Photo/location-assisted entry is useful only with current course candidates."""
    return bool(current_app.config.get('LECTURE_CAPTURE_ENABLED', False)
                and current_schedule_availability()['candidates_ready'])


def current_schedule_availability():
    """History cannot become current authority; a snapshot alone is not an index."""
    selection = resolve_current_schedule_snapshot(include_rows=False)
    ready = selection.status == READY
    candidates_ready = False
    registration_ready = False
    if ready:
        candidates_ready = ListeningAssistantScheduleEntry.query.filter_by(
            batch_id=selection.batch.id).first() is not None
        complete_mapping = db.session.get(ScheduleCourseMapping, selection.batch.id)
        if complete_mapping is not None:
            registration_ready = db.session.query(ScheduleCourseMembership.course_id).join(
                Course, ScheduleCourseMembership.course_id == Course.id,
            ).filter(ScheduleCourseMembership.batch_id == selection.batch.id,
                     Course.semester == selection.semester).first() is not None
    message = '' if ready else '本学期课表未导入'
    return {
        'ready': ready, 'semester': selection.semester, 'status': selection.status,
        'batch_id': selection.batch.id if ready else None,
        'message': message, 'candidates_ready': candidates_ready,
        'registration_ready': registration_ready,
        'candidate_message': '' if candidates_ready else (
            message or '本学期课表尚未完成课程索引，请联系管理员。'),
        'registration_message': '' if registration_ready else (
            message or '本学期课表尚未完成登记课程导入，请联系管理员。'),
    }
