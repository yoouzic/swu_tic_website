from datetime import datetime, timedelta
import json
import re

from ..models import AssessmentOverride, LectureForm, SystemSetting
from .audit_tags import LATE_TAG_LATE, parse_audit_tag


LEAVE_OVERRIDE_TYPE = 'leave'
ASSESSMENT_EXEMPT_OVERRIDE_TYPES = ('exempt', LEAVE_OVERRIDE_TYPE)
LEAVE_PROMPT_COOLDOWN_HOURS = 12
LEAVE_MAKEUP_FORMS_KEY = 'makeup_forms'


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

    try:
        total_weeks = int(SystemSetting.get('teaching_total_weeks', '20') or 20)
    except Exception:
        total_weeks = 20
    if total_weeks < 1 or total_weeks > 52:
        total_weeks = 20

    return {
        'first_week_date': first_week_date,
        'week_start_day': week_start_day,
        'required_submission': required_submission,
        'total_weeks': total_weeks,
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
    week_no = (diff // 7) + 1
    total_weeks = settings.get('total_weeks')
    if total_weeks is not None and week_no > total_weeks:
        return None
    return week_no


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


def parse_leave_override_value(raw_value):
    if not raw_value:
        return {}
    try:
        data = json.loads(raw_value)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def get_leave_makeup_forms(record):
    data = parse_leave_override_value(getattr(record, 'override_value', None))
    forms = data.get(LEAVE_MAKEUP_FORMS_KEY)
    return forms if isinstance(forms, list) else []


def is_leave_makeup_completed(record):
    return len(get_leave_makeup_forms(record)) > 0


def _normalize_form_unique_id(form):
    value = getattr(form, 'unique_id', None) or getattr(form, 'id', None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _build_leave_makeup_form_entry(form, source='manual', operator_user_id=None):
    return {
        'form_id': getattr(form, 'id', None),
        'unique_id': _normalize_form_unique_id(form),
        'course_title': getattr(form, 'course_title', '') or '',
        'teacher_name': getattr(form, 'teacher_name', '') or '',
        'lecture_date': getattr(form, 'lecture_date', '') or '',
        'status': getattr(form, 'status', '') or '',
        'source': source,
        'recorded_by': operator_user_id,
        'recorded_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def set_leave_makeup_forms(record, forms, source='manual', operator_user_id=None):
    if not record:
        return []
    data = parse_leave_override_value(record.override_value)
    entries = [
        _build_leave_makeup_form_entry(form, source=source, operator_user_id=operator_user_id)
        for form in (forms or [])
        if form is not None
    ]
    data[LEAVE_MAKEUP_FORMS_KEY] = entries
    record.override_value = json.dumps(data, ensure_ascii=False)
    record.updated_at = datetime.now()
    return entries


def add_leave_makeup_form(record, form, source='auto', operator_user_id=None):
    if not record or not form:
        return []
    data = parse_leave_override_value(record.override_value)
    entries = get_leave_makeup_forms(record)
    unique_id = _normalize_form_unique_id(form)
    form_id = getattr(form, 'id', None)
    filtered = [
        entry for entry in entries
        if entry.get('unique_id') != unique_id and entry.get('form_id') != form_id
    ]
    filtered.append(_build_leave_makeup_form_entry(form, source=source, operator_user_id=operator_user_id))
    data[LEAVE_MAKEUP_FORMS_KEY] = filtered
    record.override_value = json.dumps(data, ensure_ascii=False)
    record.updated_at = datetime.now()
    return filtered


def record_leave_makeup_form(user, leave_status, form, source='auto', operator_user_id=None):
    if not user or not leave_status or not form:
        return None
    override_id = leave_status.get('override_id')
    if not override_id:
        return None
    record = AssessmentOverride.query.filter_by(
        id=override_id,
        user_id=user.id,
        override_type=LEAVE_OVERRIDE_TYPE,
    ).first()
    if not record:
        return None
    add_leave_makeup_form(record, form, source=source, operator_user_id=operator_user_id)
    return record


def build_leave_status_payload(record, current_week, user=None, settings=None):
    if not record or not current_week or current_week < record.start_week:
        return None
    makeup_forms = get_leave_makeup_forms(record)
    makeup_completed = len(makeup_forms) > 0
    if record.start_week <= current_week <= record.end_week:
        status = 'active'
        status_label = '请假中'
        leave_week = current_week
    else:
        status = 'completed' if makeup_completed else 'pending'
        status_label = '已补交' if makeup_completed else '待补交'
        leave_week = record.end_week
    required_submission = int((settings or {}).get('required_submission') or 0)
    submitted_count = len(makeup_forms)
    pending_count = 0 if makeup_completed else max(required_submission, 0)

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
        'makeup_completed': makeup_completed,
        'makeup_forms': makeup_forms,
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
