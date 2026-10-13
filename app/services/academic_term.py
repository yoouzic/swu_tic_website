# -*- coding: utf-8 -*-
"""Explicit current teaching semester control-plane service.

The current teaching semester is an administrator-configured business value.
It is deliberately not inferred from calendar dates or from any automation
dataset upload parameter.

``compose_semester_identifier`` is the single composition rule that turns a
workbook's separate ``学年`` and ``学期`` cells into the one semester identifier
the application stores.  Both the admin upload route and the Windows debug
launcher must call it; nothing else may roll its own version.
"""
import math
import re
from numbers import Real

from app.models import SystemSetting

SETTING_KEY_CURRENT_TEACHING_SEMESTER = 'teaching_current_semester'
MAX_SEMESTER_IDENTIFIER_LENGTH = 50

_BARE_TERMS = frozenset({'1', '2'})
_ACADEMIC_YEAR_RE = re.compile(r'\d{4}-\d{4}')
_START_YEAR_RE = re.compile(r'\d{4}')


def _is_blank_number(value) -> bool:
    """True for ``NaN``/``NaT``-like scalars, including numpy floats."""
    if isinstance(value, bool) or not isinstance(value, Real):
        return False
    try:
        return math.isnan(value)
    except (TypeError, ValueError):
        return False


def clean_term_text(value) -> str:
    """Return the workbook cell as trimmed text, mapping blanks to ``''``."""
    if value is None or _is_blank_number(value):
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return '' if text.lower() == 'nan' else text


def compose_semester_identifier(term, academic_year) -> str:
    """Compose one semester identifier from a workbook's term and year cells.

    The institution's whole-school workbook keeps the term and the academic year
    in two separate columns (``学期`` = ``1``/``2``, ``学年`` = ``2026-2027``),
    while the application identifies a semester with the single identifier
    ``2026-2027-1``.  Reading only the raw term cell would bind every imported
    Course row to ``'1'`` and no configured semester could ever select them.

    Only the documented term/year combination is composed.  Anything else - a
    term that is already composed (``2025-2026-1``), a legacy bare term without
    a usable year, or arbitrary text - is passed through unchanged so existing
    installations keep their stored identifiers.  A blank cell stays blank:
    turning it into the literal ``'nan'`` would make the snapshot's ``''``-keyed
    batch and the assistant index disagree and silently drop rows.
    """
    term_text = clean_term_text(term)
    if not term_text:
        return ''

    year_text = clean_term_text(academic_year)
    if _START_YEAR_RE.fullmatch(year_text):
        # A lone start year (``2026``) still identifies the academic year.
        year_text = f'{year_text}-{int(year_text) + 1}'


    if term_text in _BARE_TERMS and _ACADEMIC_YEAR_RE.fullmatch(year_text):
        return f'{year_text}-{term_text}'
    return term_text


def get_current_teaching_semester() -> str:
    """Return the configured current teaching semester, or ``''`` when unset."""
    value = SystemSetting.get(SETTING_KEY_CURRENT_TEACHING_SEMESTER)
    if value is None:
        return ''
    return str(value)


def normalize_semester_identifier(value) -> str:
    """Validate and normalize a user-supplied semester identifier.

    The function only applies whitespace trimming and conservative safety
    checks (type, length, control characters).  It intentionally does not
    hard-code a semester regex because the project has not proven that every
    legitimate semester code follows one fixed pattern.
    """
    if not isinstance(value, str):
        raise ValueError('当前教学学期必须是字符串')

    text = value.strip()
    if len(text) > MAX_SEMESTER_IDENTIFIER_LENGTH:
        raise ValueError('当前教学学期长度不能超过50个字符')

    for char in text:
        if ord(char) < 32 or ord(char) == 127:
            raise ValueError('当前教学学期包含非法控制字符')

    return text
