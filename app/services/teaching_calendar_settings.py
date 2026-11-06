# -*- coding: utf-8 -*-
"""Settings boundary adapter for the teaching-calendar core.

This module is allowed to read ``SystemSetting`` but must not depend on Flask
or blueprints.  It does not produce UI/HTTP messages; callers translate the
structured error codes into their own contracts.
"""
from datetime import datetime
from typing import Optional, Tuple

from app.models import SystemSetting
from app.services.teaching_calendar import (
    TeachingCalendarConfig,
)


MISSING_FIRST_WEEK = 'MISSING_FIRST_WEEK'
INVALID_FIRST_WEEK = 'INVALID_FIRST_WEEK'
SETTING_KEY_SCHEDULE_FIRST_MONDAY = 'schedule_first_week_monday'


def _load_total_weeks():
    try:
        total_weeks = int(SystemSetting.get('teaching_total_weeks', '20') or 20)
    except (TypeError, ValueError):
        total_weeks = 20
    return total_weeks if 1 <= total_weeks <= 52 else 20


def load_schedule_calendar_config() -> Tuple[Optional[TeachingCalendarConfig], Optional[str]]:
    """Use Monday-based school weeks independently of assessment periods.

    An unset value retains the historical shared calendar for existing
    installations. Invalid explicit dates fail closed rather than silently
    using an unrelated assessment-cycle date.
    """
    raw = SystemSetting.get(SETTING_KEY_SCHEDULE_FIRST_MONDAY)
    if raw is None or not str(raw).strip():
        return load_teaching_calendar_config()
    try:
        first_week_date = datetime.strptime(str(raw).strip(), '%Y-%m-%d').date()
        if first_week_date.weekday() != 0:
            return None, INVALID_FIRST_WEEK
    except (TypeError, ValueError):
        return None, INVALID_FIRST_WEEK
    return TeachingCalendarConfig(
        first_week_date=first_week_date,
        week_start_day=0,
        total_weeks=_load_total_weeks(),
    ), None


def load_teaching_calendar_config() -> Tuple[Optional[TeachingCalendarConfig], Optional[str]]:
    """Read SystemSetting and return ``(config, None)`` or ``(None, error_code)``.

    Missing/invalid first-week rows are errors; an invalid week-start or
    total-weeks value falls back to the established safe defaults so legacy
    bad rows do not make every consumer fail.
    """
    first_week_raw = SystemSetting.get('teaching_first_week_monday')
    if not first_week_raw or not str(first_week_raw).strip():
        return None, MISSING_FIRST_WEEK
    try:
        first_week_date = datetime.strptime(str(first_week_raw).strip(), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None, INVALID_FIRST_WEEK

    week_start_raw = SystemSetting.get('teaching_week_start_day', '0') or 0
    try:
        week_start_day = int(week_start_raw)
    except (TypeError, ValueError):
        week_start_day = 0
    if week_start_day < 0 or week_start_day > 6:
        week_start_day = 0

    return TeachingCalendarConfig(
        first_week_date=first_week_date,
        week_start_day=week_start_day,
        total_weeks=_load_total_weeks(),
    ), None
