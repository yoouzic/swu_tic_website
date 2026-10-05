"""Only explicitly bound assessment rules apply to the configured semester."""
from sqlalchemy import false

from app.models import AssessmentOverride
from app.services.academic_term import get_current_teaching_semester


def current_override_semester():
    return get_current_teaching_semester().strip()


def override_semester_filter(semester=None):
    semester = current_override_semester() if semester is None else str(semester).strip()
    return AssessmentOverride.semester == semester if semester else false()


def override_applies_to_semester(record, semester=None):
    semester = current_override_semester() if semester is None else str(semester).strip()
    return bool(semester and record and record.semester == semester)


def unassigned_override_warning(count):
    return (f'有 {count} 条历史规则未归属学期，暂不参与考核或请假补交；'
            '请核实原始记录后由超级管理员明确设置归属学期。') if count else ''
