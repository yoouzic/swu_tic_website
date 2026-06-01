REVIEW_TAG_REQUIRED = '需要人工审核'
REVIEW_TAG_NOT_REQUIRED = '无需人工审核'
LATE_TAG_LATE = '晚交'
LATE_TAG_NORMAL = '正常'

REVIEW_TAG_OPTIONS = (REVIEW_TAG_REQUIRED, REVIEW_TAG_NOT_REQUIRED)
LATE_TAG_OPTIONS = (LATE_TAG_LATE, LATE_TAG_NORMAL)


def parse_audit_tag(raw_value):
    raw = str(raw_value or '').strip()
    parts = [part.strip() for part in raw.split(';')] if raw else []
    review_tag = parts[0] or None if parts else None
    late_tag = parts[1] or None if len(parts) > 1 else None
    extra_tags = [part for part in parts[2:] if part]
    return {
        'raw': raw,
        'review_tag': review_tag,
        'late_tag': late_tag,
        'extra_tags': extra_tags,
    }


def build_audit_tag(review_tag, late_tag=None):
    review_value = str(review_tag or '').strip()
    late_value = str(late_tag or '').strip()
    if not review_value:
        return ''
    if late_value:
        return f'{review_value};{late_value}'
    return review_value


def validate_audit_tag(raw_value, allow_empty=False):
    parsed = parse_audit_tag(raw_value)
    errors = []
    if not parsed['review_tag']:
        if not allow_empty:
            errors.append('缺少人工审核标签')
        return errors
    if parsed['review_tag'] not in REVIEW_TAG_OPTIONS:
        errors.append('人工审核标签不合法')
    if parsed['late_tag'] and parsed['late_tag'] not in LATE_TAG_OPTIONS:
        errors.append('晚交标签不合法')
    if parsed['extra_tags']:
        errors.append('audit_tag 仅支持两个类别标签')
    return errors


def is_auto_review_allowed(raw_value):
    return parse_audit_tag(raw_value).get('review_tag') == REVIEW_TAG_NOT_REQUIRED


# --- 2026-04: 审核标签第二段升级为“教学周修正”，同时兼容历史的“晚交/正常” ---
import re

REVIEW_TAG_REQUIRED = '需要人工审核'
REVIEW_TAG_NOT_REQUIRED = '无需人工审核'
LATE_TAG_LATE = '晚交'
LATE_TAG_NORMAL = '正常'
LEGACY_SECOND_TAG_OPTIONS = (LATE_TAG_LATE, LATE_TAG_NORMAL)
LATE_TAG_OPTIONS = LEGACY_SECOND_TAG_OPTIONS
REVIEW_TAG_OPTIONS = (REVIEW_TAG_REQUIRED, REVIEW_TAG_NOT_REQUIRED)
WEEK_CORRECTION_TAG_PATTERN = re.compile(r'^第\s*(\d+)\s*周$')


def _parse_week_correction_week_no(second_tag):
    text = str(second_tag or '').strip()
    if not text:
        return None
    match = WEEK_CORRECTION_TAG_PATTERN.fullmatch(text)
    if not match:
        return None
    try:
        week_no = int(match.group(1))
    except (TypeError, ValueError):
        return None
    return week_no if week_no > 0 else None


def build_week_correction_tag(week_no):
    try:
        value = int(week_no)
    except (TypeError, ValueError):
        return ''
    if value <= 0:
        return ''
    return f'第{value}周'


def parse_audit_tag(raw_value):
    raw = str(raw_value or '').strip()
    parts = [part.strip() for part in raw.split(';')] if raw else []
    review_tag = parts[0] or None if parts else None
    second_tag = parts[1] or None if len(parts) > 1 else None
    extra_tags = [part for part in parts[2:] if part]
    week_correction_week_no = _parse_week_correction_week_no(second_tag)
    legacy_late_tag = second_tag if second_tag in LEGACY_SECOND_TAG_OPTIONS else None
    return {
        'raw': raw,
        'review_tag': review_tag,
        'second_tag': second_tag,
        'week_correction_tag': second_tag if week_correction_week_no is not None else None,
        'week_correction_week_no': week_correction_week_no,
        'legacy_late_tag': legacy_late_tag,
        # 兼容旧字段名
        'late_tag': second_tag,
        'extra_tags': extra_tags,
    }


def build_audit_tag(review_tag, week_correction_tag=None):
    review_value = str(review_tag or '').strip()
    correction_value = str(week_correction_tag or '').strip()
    if not review_value:
        return ''
    if correction_value:
        return f'{review_value};{correction_value}'
    return review_value


def validate_audit_tag(raw_value, allow_empty=False):
    parsed = parse_audit_tag(raw_value)
    errors = []
    if not parsed['review_tag']:
        if not allow_empty:
            errors.append('缺少人工审核标签')
        return errors
    if parsed['review_tag'] not in REVIEW_TAG_OPTIONS:
        errors.append('人工审核标签不合法')
    second_tag = parsed['second_tag']
    if second_tag and parsed['week_correction_week_no'] is None and parsed['legacy_late_tag'] is None:
        errors.append('教学周修正标签不合法，应为“第x周”')
    if parsed['extra_tags']:
        errors.append('audit_tag 仅支持两个类别标签')
    return errors


def is_auto_review_allowed(raw_value):
    return parse_audit_tag(raw_value).get('review_tag') == REVIEW_TAG_NOT_REQUIRED
