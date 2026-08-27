# -*- coding: utf-8 -*-
"""Explicit current teaching semester control-plane service.

The current teaching semester is an administrator-configured business value.
It is deliberately not inferred from calendar dates or from any automation
dataset upload parameter.
"""
from app.models import SystemSetting

SETTING_KEY_CURRENT_TEACHING_SEMESTER = 'teaching_current_semester'
MAX_SEMESTER_IDENTIFIER_LENGTH = 50


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
