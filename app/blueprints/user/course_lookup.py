"""Read-only current-course lookup for active authenticated users."""
from datetime import date

from flask import render_template

from app.security import login_required
from app.services.academic_term import get_current_teaching_semester
from app.services.schedule_availability import current_schedule_availability

from . import user_bp


@user_bp.route('/course_lookup', methods=['GET'])
@login_required
def course_lookup():
    availability = current_schedule_availability()
    return render_template(
        'user/course_lookup.html',
        current_semester=get_current_teaching_semester().strip(),
        lookup_date=date.today().isoformat(),
        schedule_availability=availability,
    ), 200 if availability['candidates_ready'] else 503
