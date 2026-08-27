# -*- coding: utf-8 -*-
"""Pure teaching-calendar core.

This module must stay free of Flask, SQLAlchemy, app.models and
SystemSetting.  It contains only deterministic calendar mathematics and the
legacy lecture-date parser that was duplicated across the codebase.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional


@dataclass(frozen=True)
class TeachingCalendarConfig:
    """Canonical teaching-calendar settings snapshot.

    ``first_week_date`` is the reference Monday of the first teaching week.
    The actual teaching term starts at the most recent configured
    ``week_start_day`` on or before that reference Monday.
    """
    first_week_date: date
    week_start_day: int
    total_weeks: int

    def __post_init__(self):
        if isinstance(self.first_week_date, datetime):
            object.__setattr__(self, 'first_week_date', self.first_week_date.date())
        if not isinstance(self.first_week_date, date):
            raise ValueError('first_week_date 必须是 date')
        if not isinstance(self.week_start_day, int) or isinstance(self.week_start_day, bool):
            raise ValueError('week_start_day 必须是整数')
        if self.week_start_day < 0 or self.week_start_day > 6:
            raise ValueError('week_start_day 必须在 0..6 之间')
        if not isinstance(self.total_weeks, int) or isinstance(self.total_weeks, bool):
            raise ValueError('total_weeks 必须是整数')
        if self.total_weeks < 1 or self.total_weeks > 52:
            raise ValueError('total_weeks 必须在 1..52 之间')


def teaching_term_start(config: TeachingCalendarConfig) -> date:
    """Return the first teaching-day boundary for the configured week start."""
    first = config.first_week_date
    days_to_subtract = (first.weekday() - config.week_start_day) % 7
    return first - timedelta(days=days_to_subtract)


def teaching_term_end(config: TeachingCalendarConfig) -> date:
    """Return the exclusive end date: term_start + total_weeks * 7 days."""
    return teaching_term_start(config) + timedelta(days=config.total_weeks * 7)


def teaching_week_number(target_date, config: TeachingCalendarConfig) -> Optional[int]:
    """Return 1..total_weeks for a date inside the term, otherwise None."""
    if target_date is None:
        return None
    if isinstance(target_date, datetime):
        target = target_date.date()
    else:
        target = target_date
    if not isinstance(target, date):
        return None

    start = teaching_term_start(config)
    end = teaching_term_end(config)
    if target < start or target >= end:
        return None
    return ((target - start).days // 7) + 1


def date_for_teaching_weekday(
    week_no: int,
    weekday_no: int,
    config: TeachingCalendarConfig,
) -> date:
    """Return the calendar date for teaching-week ``week_no`` and weekday 1..7."""
    if not isinstance(week_no, int) or isinstance(week_no, bool):
        raise ValueError('week_no 必须是整数')
    if week_no < 1 or week_no > config.total_weeks:
        raise ValueError('week_no 超出教学周范围')
    if not isinstance(weekday_no, int) or isinstance(weekday_no, bool):
        raise ValueError('weekday_no 必须是整数')
    if weekday_no < 1 or weekday_no > 7:
        raise ValueError('weekday_no 必须在 1..7 之间')

    target_python_weekday = weekday_no - 1
    offset = (target_python_weekday - config.week_start_day) % 7
    return teaching_term_start(config) + timedelta(weeks=week_no - 1, days=offset)


def parse_lecture_date(value) -> Optional[date]:
    """Parse the legacy lecture-date formats into a date, or None.

    Supports date/datetime, YYYY-MM-DD, YYYY/MM/DD, YYYY.MM.DD, Chinese
    year/month/day, and the existing regex fallback.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace('年', '-').replace('月', '-').replace('日', '')
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d'):
        try:
            return datetime.strptime(normalized, fmt).date()
        except (TypeError, ValueError):
            continue
    match = re.search(r'(\d{4})\D+(\d{1,2})\D+(\d{1,2})', text)
    if not match:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
    except (TypeError, ValueError):
        return None
