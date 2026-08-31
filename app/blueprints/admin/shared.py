# -*- coding: utf-8 -*-
# Phase 1 mechanical split from app/blueprints/admin.py
# Module: shared

from app.models import db, SystemSetting, PersonnelMovementRecord
from app.services.assessment_scope import (
    active_user_query as _active_user_query,
    get_accessible_department_users as _get_accessible_department_users,
    resolve_assessment_users as _resolve_assessment_users,
)
from app.services.excel_utils import (
    allowed_file,
    excel_cell_to_text as _excel_cell_to_text,
    to_int_or_none as _to_int_or_none,
)
from app.services.organization_membership import clear_user_group
from app.services.review_form_queries import (
    build_review_form_filter_datetime as _build_review_form_filter_datetime,
    get_form_latest_timestamp as _get_form_latest_timestamp,
    latest_form_groups_for_users as _latest_form_groups_for_users,
    normalize_review_form_time_filter as _normalize_review_form_time_filter,
    parse_lecture_date_value as _parse_lecture_date_value,
)
from app.utils.user_status import UNASSIGNED_DEPARTMENT_NAME, UNASSIGNED_GROUP_NAME
import secrets
import string
import json


def generate_random_password(length=8):
    """生成随机密码"""
    characters = string.ascii_letters + string.digits
    return ''.join(secrets.choice(characters) for _ in range(length))


def get_reviewer_display_mode():
    mode = SystemSetting.get('teaching_reviewer_display_mode', 'name')
    if mode not in ['name', 'number']:
        mode = 'name'
    return mode


def _snapshot_user_for_movement(user):
    return {
        'id': user.id,
        'number': user.number,
        'name': user.name,
        'department': user.department,
        'group': user.group,
        'group_id': user.group_id,
        'gender': user.gender,
        'grade': user.grade,
        'college': user.college,
        'major': user.major,
        'dormitory': user.dormitory,
        'phone': user.phone,
        'qq': user.qq,
        'student_id': user.student_id,
        'role': user.role,
        'is_active': user.is_active,
    }


def _create_personnel_movement_record(operator_user_id, target_snapshot, action_type, summary, changes=None):
    department_name = (
        target_snapshot.get('department_after')
        or target_snapshot.get('department')
        or target_snapshot.get('department_before')
    )
    group_name = (
        target_snapshot.get('group_after')
        or target_snapshot.get('group')
        or target_snapshot.get('group_before')
    )
    record = PersonnelMovementRecord(
        target_user_id=target_snapshot.get('id'),
        target_user_number=target_snapshot.get('number'),
        target_user_name=target_snapshot.get('name') or '未知用户',
        department_name=department_name,
        group_name=group_name,
        action_type=action_type,
        summary=summary,
        details_json=json.dumps({'changes': changes or []}, ensure_ascii=False) if changes else None,
        operator_user_id=operator_user_id
    )
    db.session.add(record)
    return record


def _serialize_user_basic(user):
    return {
        'id': user.id,
        'number': user.number,
        'name': user.name,
        'gender': user.gender,
        'grade': user.grade,
        'college': user.college,
        'major': user.major,
        'dormitory': user.dormitory,
        'phone': user.phone,
        'qq': user.qq,
        'student_id': user.student_id,
        'role': user.role,
        'department': user.department,
        'group': user.group,
        'group_id': user.group_id,
        'is_active': user.is_active is not False,
    }


def _set_user_unassigned(user):
    user.department = UNASSIGNED_DEPARTMENT_NAME
    clear_user_group(user)




