import json

from ..models import SystemSetting


SETTING_KEY_PROFILE_EDITABLE_FIELDS = 'profile_editable_fields'

PROFILE_EDITABLE_FIELD_OPTIONS = [
    {'key': 'name', 'label': '姓名'},
    {'key': 'gender', 'label': '性别'},
    {'key': 'grade', 'label': '年级'},
    {'key': 'college', 'label': '学院'},
    {'key': 'major', 'label': '专业'},
    {'key': 'dormitory', 'label': '宿舍'},
    {'key': 'phone', 'label': '手机号码'},
    {'key': 'qq', 'label': 'QQ号码'},
]

PROFILE_EDITABLE_FIELD_KEYS = [item['key'] for item in PROFILE_EDITABLE_FIELD_OPTIONS]
DEFAULT_PROFILE_EDITABLE_FIELDS = list(PROFILE_EDITABLE_FIELD_KEYS)


def normalize_profile_editable_fields(value):
    if value is None:
        return list(DEFAULT_PROFILE_EDITABLE_FIELDS)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return list(DEFAULT_PROFILE_EDITABLE_FIELDS)
    if not isinstance(value, list):
        return list(DEFAULT_PROFILE_EDITABLE_FIELDS)
    return [field for field in PROFILE_EDITABLE_FIELD_KEYS if field in value]


def get_profile_editable_fields():
    saved_value = SystemSetting.get(SETTING_KEY_PROFILE_EDITABLE_FIELDS)
    return normalize_profile_editable_fields(saved_value)
