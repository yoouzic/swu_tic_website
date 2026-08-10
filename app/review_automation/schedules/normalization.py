"""Pure, strict normalization functions used by schedule imports."""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime, timedelta
from numbers import Real
from typing import Iterable, Mapping


class NormalizationError(ValueError):
    """A safe, field-scoped input normalization failure."""

    def __init__(self, field: str, code: str, value=None, message: str | None = None):
        self.field = field
        self.code = code
        self.value = value
        self.message = message or f'cannot normalize {field}'
        super().__init__(self.message)


def _error(field: str, code: str, value=None, message: str | None = None):
    raise NormalizationError(field, code, value=value, message=message)


def _text(value, field: str) -> str:
    if value is None:
        _error(field, 'missing_value', value=value, message=f'{field} is required')
    text = unicodedata.normalize('NFKC', str(value)).replace('\u3000', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        _error(field, 'missing_value', value=value, message=f'{field} is required')
    return text


def _as_date_from_excel_serial(value) -> date:
    if isinstance(value, bool) or not isinstance(value, Real):
        _error('date', 'invalid_date', value=value)
    number = float(value)
    if not math.isfinite(number) or number < 1 or number > 100000:
        _error('date', 'invalid_date', value=value)
    try:
        return (datetime(1899, 12, 30) + timedelta(days=number)).date()
    except (OverflowError, ValueError):
        _error('date', 'invalid_date', value=value)


def normalize_date(value, semester_monday=None) -> date:
    """Normalize Excel/date text, including text with a weekday suffix."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, Real) and not isinstance(value, bool):
        return _as_date_from_excel_serial(value)

    text = _text(value, 'date')
    text = re.sub(
        r'[\(（\[【]?\s*(?:星期|周)[一二三四五六日天1-7]\s*[\)）\]】]?',
        '',
        text,
    ).strip()

    match = re.search(
        r'(?P<year>\d{4})\s*(?:年|[-/.])\s*'
        r'(?P<month>\d{1,2})\s*(?:月|[-/.])\s*'
        r'(?P<day>\d{1,2})\s*日?',
        text,
    )
    if match:
        year = int(match.group('year'))
        month = int(match.group('month'))
        day = int(match.group('day'))
    else:
        match = re.search(r'(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日', text)
        if not match:
            _error('date', 'invalid_date', value=value)
        if semester_monday is None:
            _error('date', 'missing_year', value=value)
        if isinstance(semester_monday, datetime):
            semester_monday = semester_monday.date()
        if not isinstance(semester_monday, date):
            _error('date', 'invalid_semester_monday', value=semester_monday)
        year = semester_monday.year
        month = int(match.group('month'))
        day = int(match.group('day'))

    try:
        return date(year, month, day)
    except ValueError:
        _error('date', 'invalid_date', value=value)


def _numeric_integer(value, field: str) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Real):
        number = float(value)
        if not math.isfinite(number) or not number.is_integer():
            _error(field, 'non_integer', value=value)
        return int(number)
    return None


def parse_weeks(value) -> frozenset[int]:
    """Parse ranges, lists, and odd/even teaching-week expressions."""
    number = _numeric_integer(value, 'weeks')
    if number is not None:
        if not 1 <= number <= 52:
            _error('weeks', 'invalid_week', value=value)
        return frozenset({number})

    text = _text(value, 'weeks')
    parity_match = re.search(r'[\(（]\s*([单双])\s*[\)）]', text)
    parity = parity_match.group(1) if parity_match else None
    text = re.sub(r'[\(（]\s*[单双]\s*[\)）]', '', text)
    text = re.sub(r'(?:教学)?周(?:次)?', '', text, flags=re.IGNORECASE)
    text = text.replace('第', '')
    text = text.replace('－', '-').replace('～', '~')
    text = text.replace('至', '-').replace('到', '-')
    if not re.fullmatch(r'[0-9\s,，、;；\-~]+', text):
        _error('weeks', 'invalid_week', value=value)

    result: set[int] = set()
    for part in re.split(r'[,，、;；\s]+', text.strip()):
        if not part:
            continue
        range_match = re.fullmatch(r'(\d+)\s*(?:-|~)\s*(\d+)', part)
        if range_match:
            start, end = map(int, range_match.groups())
            if start > end:
                _error('weeks', 'invalid_week_range', value=value)
            result.update(range(start, end + 1))
        elif part.isdigit():
            result.add(int(part))
        else:
            _error('weeks', 'invalid_week', value=value)

    if not result or any(not 1 <= item <= 52 for item in result):
        _error('weeks', 'invalid_week', value=value)
    if parity == '单':
        result = {item for item in result if item % 2 == 1}
    elif parity == '双':
        result = {item for item in result if item % 2 == 0}
    if not result:
        _error('weeks', 'invalid_week_parity', value=value)
    return frozenset(result)


_WEEKDAY_NAMES = {
    '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6,
    '日': 7, '天': 7,
}


def normalize_weekday(value) -> int:
    number = _numeric_integer(value, 'weekday')
    if number is not None:
        if not 1 <= number <= 7:
            _error('weekday', 'invalid_weekday', value=value)
        return number
    text = _text(value, 'weekday')
    text = re.sub(r'^(?:星期|周)', '', text)
    if text in _WEEKDAY_NAMES:
        return _WEEKDAY_NAMES[text]
    if text.isdigit() and 1 <= int(text) <= 7:
        return int(text)
    _error('weekday', 'invalid_weekday', value=value)


def _validate_periods(start: int, end: int, value) -> tuple[int, int]:
    if not 1 <= start <= 20 or not 1 <= end <= 20 or start > end:
        _error('periods', 'invalid_period', value=value)
    return start, end


def parse_period_range(value) -> tuple[int, int]:
    number = _numeric_integer(value, 'periods')
    if number is not None:
        if 1 <= number <= 20:
            return number, number
        if 100 <= number <= 9999:
            padded = f'{number:04d}'
            return _validate_periods(int(padded[:2]), int(padded[2:]), value)
        _error('periods', 'invalid_period', value=value)

    text = _text(value, 'periods').replace('第', '').replace('节', '')
    text = text.replace('－', '-').replace('～', '~').replace('至', '-').replace('到', '-')
    pair_match = re.fullmatch(r'(\d{1,2})\s*(?:-|~|、|,|，)\s*(\d{1,2})', text)
    if pair_match:
        return _validate_periods(*map(int, pair_match.groups()), value)
    if re.fullmatch(r'\d{4}', text):
        return _validate_periods(int(text[:2]), int(text[2:]), value)
    if text.isdigit():
        number = int(text)
        return _validate_periods(number, number, value)
    _error('periods', 'invalid_period', value=value)


def normalize_identifier(value) -> str:
    number = _numeric_integer(value, 'identifier')
    if number is not None:
        if number < 0:
            _error('identifier', 'invalid_identifier', value=value)
        return str(number)
    if isinstance(value, Real) and not isinstance(value, bool):
        _error('identifier', 'invalid_identifier', value=value)
    return _text(value, 'identifier')


def _strip_wrapping_brackets(text: str) -> str:
    pairs = {
        '(': ')', '[': ']', '{': '}', '〈': '〉', '《': '》',
        '【': '】', '［': '］', '｛': '｝',
    }
    changed = True
    while changed and len(text) >= 2:
        changed = False
        closing = pairs.get(text[0])
        if closing and text.endswith(closing):
            text = text[1:-1].strip()
            changed = True
    return text


def normalize_name(value) -> str:
    return _strip_wrapping_brackets(_text(value, 'name'))


def normalize_college(value, aliases: Mapping[str, str] | None = None) -> str:
    normalized = normalize_name(value)
    if not aliases:
        return normalized
    normalized_aliases = {
        normalize_name(key): normalize_name(alias)
        for key, alias in aliases.items()
    }
    return normalized_aliases.get(normalized, normalized)


def normalize_class_name(value) -> str:
    return normalize_name(value)


def _header_key(value) -> str:
    text = unicodedata.normalize('NFKC', str(value)).strip().lower()
    return re.sub(r'[\s_\-—–()\[\]{}:：/／]', '', text)


def resolve_columns(
    columns: Iterable[str],
    alias_map: Mapping[str, Iterable[str] | str],
    required: Iterable[str],
) -> dict[str, str]:
    """Resolve canonical fields to source headers; never guesses by position."""
    source_columns = [column for column in columns if column is not None and str(column).strip()]
    aliases_by_field: dict[str, set[str]] = {}
    for canonical, aliases in alias_map.items():
        values = (aliases,) if isinstance(aliases, str) else aliases
        aliases_by_field[canonical] = {_header_key(canonical), *(_header_key(item) for item in values)}

    resolved: dict[str, str] = {}
    for field in required:
        candidates = [
            column for column in source_columns
            if _header_key(column) in aliases_by_field.get(field, {_header_key(field)})
        ]
        if not candidates:
            _error('columns', 'missing_required_column', value=field)
        if len(candidates) > 1:
            _error('columns', 'ambiguous_column', value=field)
        resolved[field] = candidates[0]
    return resolved


def periods_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


__all__ = [
    'NormalizationError',
    'normalize_class_name',
    'normalize_college',
    'normalize_date',
    'normalize_identifier',
    'normalize_name',
    'normalize_weekday',
    'parse_period_range',
    'parse_weeks',
    'periods_overlap',
    'resolve_columns',
]
