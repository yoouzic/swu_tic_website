import re
from datetime import datetime, timedelta

from ..models import CourseRegistration, SystemSetting
from .time_validator import TimeValidator


SETTING_KEY_COURSE_WEEKLY_LIMIT_ENABLED = 'course_registration_weekly_limit_enabled'
SETTING_KEY_COURSE_WEEKLY_LIMIT_COUNT = 'course_registration_weekly_limit_count'
DEFAULT_COURSE_WEEKLY_LIMIT_COUNT = 1


def parse_listening_week_no(listening_info):
    """Extract the teaching week from a registration note."""
    if not listening_info:
        return None

    text = str(listening_info)
    for pattern in (r'第([一二三四五六七八九十\d]+)周', r'([一二三四五六七八九十\d]+)周'):
        match = re.search(pattern, text)
        if match:
            return TimeValidator.parse_chinese_number(match.group(1))
    return None


def _normalize_positive_int(value, default=DEFAULT_COURSE_WEEKLY_LIMIT_COUNT):
    try:
        parsed = int(value)
    except Exception:
        return default
    return parsed if parsed > 0 else default


def get_course_weekly_limit_settings():
    limit_count = _normalize_positive_int(
        SystemSetting.get(SETTING_KEY_COURSE_WEEKLY_LIMIT_COUNT, str(DEFAULT_COURSE_WEEKLY_LIMIT_COUNT))
    )
    return {
        'enabled': SystemSetting.get(SETTING_KEY_COURSE_WEEKLY_LIMIT_ENABLED, 'false') == 'true',
        'limit_count': limit_count,
    }


def normalize_course_weekly_limit_count(value):
    return _normalize_positive_int(value)


def get_current_teaching_week_no():
    first_week_raw = SystemSetting.get('teaching_first_week_monday')
    if not first_week_raw:
        return None

    try:
        first_week_date = datetime.strptime(first_week_raw, '%Y-%m-%d').date()
    except Exception:
        return None

    try:
        week_start_day = int(SystemSetting.get('teaching_week_start_day', '0') or 0)
    except Exception:
        week_start_day = 0
    if week_start_day < 0 or week_start_day > 6:
        week_start_day = 0

    teaching_start = first_week_date - timedelta(days=(first_week_date.weekday() - week_start_day) % 7)
    diff_days = (datetime.now().date() - teaching_start).days
    if diff_days < 0:
        return None
    week_no = (diff_days // 7) + 1

    try:
        total_weeks = int(SystemSetting.get('teaching_total_weeks', '20') or 20)
    except Exception:
        total_weeks = 20
    if total_weeks < 1 or total_weeks > 52:
        total_weeks = 20

    if week_no > total_weeks:
        return None
    return week_no


def get_course_registration_count_for_week(course_code, selection_code, week_no):
    if not course_code or not selection_code or not week_no:
        return 0

    registrations = CourseRegistration.query.filter_by(
        course_code=course_code,
        selection_code=selection_code
    ).all()
    return sum(
        1 for registration in registrations
        if parse_listening_week_no(registration.listening_info) == week_no
    )


def validate_course_weekly_registration_limit(course_code, selection_code, listening_info):
    settings = get_course_weekly_limit_settings()
    if not settings['enabled']:
        return True, None

    week_no = parse_listening_week_no(listening_info)
    if not week_no:
        return True, None

    existing_count = get_course_registration_count_for_week(course_code, selection_code, week_no)
    limit_count = settings['limit_count']
    if existing_count >= limit_count:
        return False, (
            f'该课程第{week_no}周已达到每周最大登记次数'
            f'（{limit_count}次），不能再登记'
        )
    return True, None
