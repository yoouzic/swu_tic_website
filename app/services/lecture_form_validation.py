"""Business validation for human edits to an existing lecture form."""
import re

from app.services.review_mutation import REVIEW_EDITABLE_FIELD_LABELS
from app.services.teaching_calendar import parse_lecture_date


def submission_field_errors(submitted_fields):
    """Validate a new form; legacy unchanged-value exemptions do not apply."""
    class EmptyOriginal:
        def __getattr__(self, name):
            return object()
    fields = {field: submitted_fields.get(field) for field in REVIEW_EDITABLE_FIELD_LABELS}
    return review_field_errors(EmptyOriginal(), fields)


def review_field_errors(original, submitted_fields):
    """Reject newly invalid edits without retroactively blocking legacy data.

    Historic forms may predate a field's required rule. Unchanged legacy values
    can proceed, while clearing or corrupting a previously filled value cannot.
    Missing keys retain the route's existing payload policy.
    """
    errors = {}
    for field, label in REVIEW_EDITABLE_FIELD_LABELS.items():
        if field not in submitted_fields:
            continue
        value = submitted_fields[field]
        old_value = getattr(original, field, None)
        if value is not None and not isinstance(value, str):
            errors[field] = f'{label}应填写文字'
            continue
        if value == old_value or (value in (None, '') and old_value in (None, '')):
            continue
        if not isinstance(value, str) or not value.strip():
            errors[field] = f'请填写{label}'
            continue
        value = value.strip()
        if field in {'contact_phone1', 'contact_phone2'}:
            if re.fullmatch(r'1[3-9]\d{9}', value) is None:
                errors[field] = '请填写有效的11位手机号码'
        elif field == 'course_feedback' and len(value) < 50:
            errors[field] = '课程反馈至少需要50字'
        elif field == 'lecture_date' and parse_lecture_date(value) is None:
            errors[field] = '请填写有效的听课日期'
        elif field == 'class_period':
            period = re.fullmatch(r'(?:第)?(\d{1,2})(?:[-~至到](\d{1,2}))?(?:节)?',
                                  re.sub(r'\s+', '', value))
            if period is None:
                errors[field] = '请填写有效的起止节次'
            else:
                start, end = int(period[1]), int(period[2] or period[1])
                if not 1 <= start <= end <= 14:
                    errors[field] = '起止节次应在第1—14节且结束不早于开始'
    return errors
