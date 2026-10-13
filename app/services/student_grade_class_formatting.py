"""Conservative form presentation; raw timetable text and candidate IDs stay intact."""
import re
from collections import OrderedDict


def _symbols(value):
    text = str(value or '').strip().translate(str.maketrans({'(': '（', ')': '）'}))
    return re.sub(r'（\s*师\s*）', '（师范）', text)


def _parts(value, *, majors=False):
    # Separators inside a major's parentheses belong to its name.
    result, buffer, depth = [], [], 0
    text = _symbols(value)
    for index, char in enumerate(text):
        if char == '（':
            depth += 1
        elif char == '）':
            depth = max(0, depth - 1)
        separator = char in ';；\n' or (
            char in ',，、' and (majors or re.match(r'\s*20\d{2}', text[index + 1:])))
        if not depth and separator:
            if ''.join(buffer).strip():
                result.append(''.join(buffer).strip())
            buffer = []
        else:
            buffer.append(char)
    if ''.join(buffer).strip():
        result.append(''.join(buffer).strip())
    return result


def _numeric_class(value):
    text = re.sub(r'\s+', '', _symbols(value))
    match = re.fullmatch(
        r'(20\d{2})级?(.+?)(?<!\d)(\d{1,2}(?:班)?(?:[、,，/]\d{1,2}(?:班)?)*班)', text)
    if not match:
        return None
    year, major, numbers = match.groups()
    return year, major, [f'{int(number):02d}' for number in re.findall(r'\d+', numbers)]


def format_class_text(value, major_aliases=None):
    """Normalize only explicit facts; special names and uncertain aliases survive."""
    aliases = major_aliases or {}
    groups = OrderedDict()
    for index, part in enumerate(_parts(value)):
        parsed = _numeric_class(part)
        if parsed is None:
            preserved = re.sub(r'^(20\d{2})\s*(?:级\s*)?', r'\1级', _symbols(part))
            groups[('literal', index)] = preserved
            continue
        year, major, numbers = parsed
        major = _symbols(aliases.get(major, major))
        key = (year, major)
        group = groups.setdefault(key, [])
        for number in numbers:
            if number not in group:
                group.append(number)
    return '；'.join(value if key[0] == 'literal' else
                    f'{key[0]}级{key[1]}{"、".join(value)}班'
                    for key, value in groups.items())


def build_major_aliases(rows):
    """Accept only one-to-one workbook evidence; reject competing interpretations."""
    proposals = {}
    for classes, majors in rows:
        parsed = [_numeric_class(part) for part in _parts(classes)]
        if not parsed or any(part is None for part in parsed):
            continue
        names = {part[1] for part in parsed}
        full_names = {_symbols(part).replace(' ', '') for part in _parts(majors, majors=True)}
        if not full_names:
            continue
        for name in names:
            targets = proposals.setdefault(name, set())
            if len(names) == 1 and len(full_names) == 1:
                targets.update(full_names)
            elif name in full_names:
                targets.add(name)
            elif len(names) == 1:
                # One class name explicitly represents several majors.
                targets.add(None)
            # A mixed-major row alone cannot assign an alias; other one-to-one
            # rows remain valid evidence for that name.
    return {name: next(iter(targets)) for name, targets in proposals.items()
            if len(targets) == 1 and None not in targets}


def current_major_aliases(*, source_batch_id=None):
    # Pure contracts can still be imported/used outside the web runtime.
    try:
        from flask import g, has_app_context
    except ImportError:
        return {}
    if not has_app_context():
        return {}
    from app.models import Course, ScheduleCourseMembership, db
    from app.services.schedule_snapshots import resolve_current_schedule_snapshot
    from sqlalchemy import inspect
    selection = resolve_current_schedule_snapshot(include_rows=False)
    if not selection.is_ready or (source_batch_id is not None and
                                  str(source_batch_id) != str(selection.batch.id)):
        return {}
    key = (id(db.engine), selection.batch.id, selection.batch.source_sha256)
    cache = getattr(g, '_student_class_aliases', {})
    if key not in cache:
        if not inspect(db.engine).has_table(ScheduleCourseMembership.__tablename__):
            return {}
        rows = db.session.query(Course.class_composition, Course.major_composition).join(
            ScheduleCourseMembership, ScheduleCourseMembership.course_id == Course.id).filter(
                ScheduleCourseMembership.batch_id == selection.batch.id).distinct()
        cache[key] = build_major_aliases(rows)
        g._student_class_aliases = cache
    return cache[key]


def format_student_grade_class(value, *, source_batch_id=None):
    return format_class_text(value, current_major_aliases(source_batch_id=source_batch_id))


def class_text_matches(requested, scheduled, major_aliases=None):
    """Compare explicit class members, including a requested subset of a merged class."""
    def members(text):
        result = set()
        for part in _parts(format_class_text(text, major_aliases)):
            parsed = _numeric_class(part)
            if parsed:
                year, major, numbers = parsed
                result.update((year, major, number) for number in numbers)
            else:
                result.add(('literal', part))
        return result
    required = members(requested)
    return bool(required) and required.issubset(members(scheduled))
