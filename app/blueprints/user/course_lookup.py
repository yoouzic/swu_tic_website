"""Read-only current-course lookup for active authenticated users."""
from datetime import date

from flask import render_template

from app.security import login_required
from app.services.academic_term import get_current_teaching_semester

from . import user_bp


@user_bp.route('/course_lookup', methods=['GET'])
@login_required
def course_lookup():
    return render_template(
        'user/course_lookup.html',
        current_semester=get_current_teaching_semester().strip(),
        lookup_date=date.today().isoformat(),
    )
