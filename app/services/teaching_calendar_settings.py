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
INVALID_WEEK_START = 'INVALID_WEEK_START'
INVALID_TOTAL_WEEKS = 'INVALID_TOTAL_WEEKS'


class TeachingCalendarSettingsError(ValueError):
    """Raised when teaching settings cannot be loaded or normalized."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


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

    total_weeks_raw = SystemSetting.get('teaching_total_weeks', '20') or 20
    try:
        total_weeks = int(total_weeks_raw)
    except (TypeError, ValueError):
        total_weeks = 20
    if total_weeks < 1 or total_weeks > 52:
        total_weeks = 20

    return TeachingCalendarConfig(
        first_week_date=first_week_date,
        week_start_day=week_start_day,
        total_weeks=total_weeks,
    ), None
