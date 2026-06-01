from datetime import datetime, timedelta
import re

from ..models import AssessmentOverride, LectureForm, SystemSetting
from .audit_tags import LATE_TAG_LATE, parse_audit_tag


LEAVE_OVERRIDE_TYPE = 'leave'
ASSESSMENT_EXEMPT_OVERRIDE_TYPES = ('exempt', LEAVE_OVERRIDE_TYPE)
LEAVE_PROMPT_COOLDOWN_HOURS = 12


def get_teaching_settings():
    first_week_raw = SystemSetting.get('teaching_first_week_monday')
    if not first_week_raw:
        return None, '请先在制度设置中配置第一周起始日期'
    try:
        first_week_date = datetime.strptime(first_week_raw, '%Y-%m-%d').date()
    except Exception:
        return None, '制度设置中的第一周起始日期格式错误'

    try:
        week_start_day = int(SystemSetting.get('teaching_week_start_day', '0') or 0)
    except Exception:
        week_start_day = 0
    if week_start_day < 0 or week_start_day > 6:
        week_start_day = 0

    try:
        required_submission = int(SystemSetting.get('teaching_required_submission', '1') or 1)
    except Exception:
        required_submission = 1
    if required_submission < 0:
        required_submission = 0

    return {
        'first_week_date': first_week_date,
        'week_start_day': week_start_day,
        'required_submission': required_submission,
    }, None


def get_teaching_week_no(date_obj, settings):
    if not date_obj or not settings or not settings.get('first_week_date'):
        return None
    if isinstance(date_obj, datetime):
        target_date = date_obj.date()
    else:
        target_date = date_obj
    first_week_date = settings['first_week_date']
    week_start_day = settings['week_start_day']
    teaching_start = first_week_date - timedelta(days=(first_week_date.weekday() - week_start_day) % 7)
    diff = (target_date - teaching_start).days
    if diff < 0:
        return None
    return (diff // 7) + 1


def get_current_teaching_week(settings=None):
    if settings is None:
        settings, err = get_teaching_settings()
        if err:
            return None, err
    return get_teaching_week_no(datetime.now().date(), settings), None


def parse_lecture_date_value(raw_value):
    if not raw_value:
        return None
    if isinstance(raw_value, datetime):
        return raw_value.date()
    text = str(raw_value).strip()
    if not text:
        return None
    normalized = text.replace('年', '-').replace('月', '-').replace('日', '')
    for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d']:
        try:
            return datetime.strptime(normalized, fmt).date()
        except Exception:
            continue
    match = re.search(r'(\d{4})\D+(\d{1,2})\D+(\d{1,2})', text)
    if not match:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
    except Exception:
        return None


def get_form_effective_week_no(form, settings):
    if not form:
        return None
    parsed_tag = parse_audit_tag(form.audit_tag)
    if parsed_tag.get('week_correction_week_no') is not None:
        return parsed_tag['week_correction_week_no']

    lecture_date = parse_lecture_date_value(form.lecture_date)
    week_no = get_teaching_week_no(lecture_date, settings)
    if week_no is None:
        return None
    if parsed_tag.get('legacy_late_tag') == LATE_TAG_LATE:
        week_no -= 1
    if week_no < 1:
        return None
    return week_no


def _latest_form_groups_for_user_number(user_number):
    if not user_number:
        return []
    forms = LectureForm.query.filter_by(listener_number=user_number).order_by(
        LectureForm.unique_id.asc(),
        LectureForm.created_at.asc(),
        LectureForm.id.asc(),
    ).all()
    group_map = {}
    for form in forms:
        group_map.setdefault(form.unique_id or form.id, []).append(form)
    groups = []
    for unique_id, form_list in group_map.items():
        sorted_forms = sorted(
            form_list,
            key=lambda f: ((f.created_at or datetime.min), f.id),
            reverse=True,
        )
        groups.append({
            'unique_id': unique_id,
            'latest_form': sorted_forms[0],
            'forms': sorted_forms,
        })
    return groups


def count_user_effective_forms_for_week(user_number, week_no, settings):
    if not user_number or not week_no:
        return 0
    total = 0
    for group_data in _latest_form_groups_for_user_number(user_number):
        latest_form = group_data.get('latest_form')
        if get_form_effective_week_no(latest_form, settings) == week_no:
            total += 1
    return total


def _resolve_leave_status(record, current_week):
    if not record or not current_week:
        return None
    if record.start_week <= current_week <= record.end_week:
        return 'active', '请假中', current_week
    if current_week == record.end_week + 1:
        return 'ending', '请假结束期', record.end_week
    return None


def build_leave_status_payload(record, current_week, user=None, settings=None):
    resolved = _resolve_leave_status(record, current_week)
    if not resolved:
        return None
    status, status_label, leave_week = resolved
    required_submission = int((settings or {}).get('required_submission') or 0)
    submitted_count = 0
    if user is not None and settings:
        submitted_count = count_user_effective_forms_for_week(user.number, leave_week, settings)
    pending_count = max(required_submission - submitted_count, 0)

    payload = {
        'override_id': record.id,
        'user_id': record.user_id,
        'status': status,
        'status_label': status_label,
        'start_week': record.start_week,
        'end_week': record.end_week,
        'leave_week': leave_week,
        'required_submission': required_submission,
        'submitted_count': submitted_count,
        'pending_makeup_count': pending_count,
        'reason': record.reason or '',
        'created_at': record.created_at.strftime('%Y-%m-%d %H:%M:%S') if record.created_at else '',
    }
    if user is not None:
        payload.update({
            'name': user.name,
            'number': user.number,
            'department': user.department,
            'group': user.group,
            'role': user.role,
        })
    return payload


def get_user_relevant_leave_statuses(user, settings=None, current_week=None):
    if not user:
        return []
    if settings is None:
        settings, err = get_teaching_settings()
        if err:
            return []
    if current_week is None:
        current_week, err = get_current_teaching_week(settings)
        if err:
            return []

    records = AssessmentOverride.query.filter_by(
        user_id=user.id,
        override_type=LEAVE_OVERRIDE_TYPE,
    ).order_by(AssessmentOverride.start_week.asc(), AssessmentOverride.end_week.asc()).all()

    statuses = []
    for record in records:
        payload = build_leave_status_payload(record, current_week, user=user, settings=settings)
        if payload:
            statuses.append(payload)
    return statuses


def get_pending_leave_makeup(user):
    statuses = get_user_relevant_leave_statuses(user)
    for status in statuses:
        if status.get('pending_makeup_count', 0) > 0:
            return status
    return None


def build_leave_system_note(week_no):
    return f'【系统信息】：该表单为第{week_no}周请假补交表'


def append_leave_system_note(text, week_no):
    note = build_leave_system_note(week_no)
    base = (text or '').strip() or '无'
    if note in base:
        return base
    return f'{base}\n\n{note}'
