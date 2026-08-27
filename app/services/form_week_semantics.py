# -*- coding: utf-8 -*-
"""Business-level semantic: which teaching week does a lecture form belong to?

This module must not depend on Flask, DB, SQLAlchemy, blueprints, or ORM
models.  It combines calendar mathematics with audit-tag semantics only.
"""
from typing import Optional

from app.utils.audit_tags import LATE_TAG_LATE, parse_audit_tag
from app.services.teaching_calendar import (
    TeachingCalendarConfig,
    parse_lecture_date,
    teaching_week_number,
)


def effective_form_week(
    lecture_date,
    audit_tag,
    calendar_config: TeachingCalendarConfig,
) -> Optional[int]:
    """Return the business week number for a lecture form, or None."""
    if calendar_config is None:
        return None

    parsed_tag = parse_audit_tag(audit_tag)
    if parsed_tag.get('week_correction_week_no') is not None:
        return parsed_tag['week_correction_week_no']

    parsed_date = parse_lecture_date(lecture_date)
    week_no = teaching_week_number(parsed_date, calendar_config)
    if week_no is None:
        return None

    if parsed_tag.get('legacy_late_tag') == LATE_TAG_LATE:
        week_no -= 1
    if week_no < 1:
        return None
    return week_no
