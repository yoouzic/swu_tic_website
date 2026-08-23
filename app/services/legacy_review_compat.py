# -*- coding: utf-8 -*-
"""Compatibility boundary between canonical system data and legacy review.

The legacy AutoReviewEngine historically read ``semester_first_monday`` as its
only semester source.  That key formerly had an admin control plane which has
now been retired.  This adapter reads the canonical teaching-calendar setting
first and only falls back to the legacy key for old deployments.

This module deliberately contains no Flask/HTTP/blueprint dependency.
"""
from app.models import SystemSetting
from app.services.teaching_calendar_settings import load_teaching_calendar_config


LEGACY_SEMESTER_MONDAY_KEY = 'semester_first_monday'


def resolve_legacy_semester_monday():
    """Return a ``YYYY-MM-DD`` first-week-Monday string for legacy review.

    Precedence:
        1. canonical ``teaching_first_week_monday`` via teaching-calendar config
        2. legacy ``semester_first_monday`` (historical deployments only)
        3. ``None`` when neither source is usable
    """
    config, error = load_teaching_calendar_config()
    if error is None and config is not None:
        return config.first_week_date.isoformat()

    legacy_value = SystemSetting.get(LEGACY_SEMESTER_MONDAY_KEY)
    if legacy_value and str(legacy_value).strip():
        return str(legacy_value).strip()
    return None
