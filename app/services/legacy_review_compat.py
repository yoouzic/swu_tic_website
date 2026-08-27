# -*- coding: utf-8 -*-
"""Compatibility boundary between canonical system data and legacy review.

The legacy AutoReviewEngine historically read ``semester_first_monday`` and
computed weeks with a simple Monday-based delta.  This adapter now prefers the
canonical teaching-calendar semantics (``first_week_date``, ``week_start_day``,
``total_weeks``) when the canonical configuration is valid.  The legacy key is
only a fallback when the canonical first-week setting is completely missing;
an explicitly invalid canonical setting is not silently masked by a stale
legacy value.

This module deliberately contains no Flask/HTTP/blueprint dependency.
"""
from datetime import date, datetime
from typing import Optional

from app.models import SystemSetting
from app.services.teaching_calendar import teaching_week_number
from app.services.teaching_calendar_settings import (
    INVALID_FIRST_WEEK,
    MISSING_FIRST_WEEK,
    load_teaching_calendar_config,
)


LEGACY_SEMESTER_MONDAY_KEY = 'semester_first_monday'


def _parse_legacy_monday(value):
    if not value or not str(value).strip():
        return None
    try:
        return datetime.strptime(str(value).strip(), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def resolve_legacy_semester_monday():
    """Return a ``YYYY-MM-DD`` first-week-Monday string for legacy compatibility.

    Precedence:
        1. valid canonical ``teaching_first_week_monday``
        2. legacy ``semester_first_monday`` when canonical is completely missing
        3. ``None`` otherwise (including invalid canonical, which is not masked)
    """
    config, error = load_teaching_calendar_config()
    if error is None and config is not None:
        return config.first_week_date.isoformat()

    if error == MISSING_FIRST_WEEK:
        legacy_value = SystemSetting.get(LEGACY_SEMESTER_MONDAY_KEY)
        if legacy_value and str(legacy_value).strip():
            return str(legacy_value).strip()
    return None


def get_legacy_review_week_no(target_date) -> Optional[int]:
    """Return the legacy-review week number for ``target_date``.

    - valid canonical config -> canonical ``teaching_week_number``
    - canonical missing -> legacy Monday-delta fallback
    - canonical invalid -> ``None`` (no stale legacy masking)
    """
    config, error = load_teaching_calendar_config()
    if error is None and config is not None:
        return teaching_week_number(target_date, config)

    if error == MISSING_FIRST_WEEK:
        legacy_monday = _parse_legacy_monday(
            SystemSetting.get(LEGACY_SEMESTER_MONDAY_KEY)
        )
        if legacy_monday is not None:
            if isinstance(target_date, datetime):
                target = target_date.date()
            elif isinstance(target_date, date):
                target = target_date
            else:
                return None
            delta_days = (target - legacy_monday).days
            if delta_days < 0:
                return None
            return (delta_days // 7) + 1
        return None
    return None
