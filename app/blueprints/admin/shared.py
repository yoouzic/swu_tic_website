# -*- coding: utf-8 -*-
# Phase 1 mechanical split from app/blueprints/admin.py
# Module: shared

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, send_file, current_app
from app.models import User, Department, Group, LectureForm, Permission, RolePermission, Teacher, Venue, Course, ListeningBan, CourseRegistration, db, SystemSetting, ScoreRecord, ScoreItem, StatisticsSnapshot, PersonnelMovementRecord, AssessmentOverride
from sqlalchemy import func
from datetime import datetime, timedelta
from app.utils.auto_review import AutoReviewEngine, SETTING_KEY_SEMESTER_MONDAY, SETTING_KEY_SCHEDULE_PATH, SETTING_KEY_CONTACTS_PATH, SETTING_KEY_FEEDBACK_PATH
from app.blueprints.auth import login_required, role_required
from app.utils.review_permissions import (
    get_user_review_permission, 
    can_review_status, 
    get_user_structure_for_review, 
    get_reviewable_users,
    get_reviewable_status_list,
    get_next_status_after_review,
    get_review_permission_presentation,
)
from app.utils.permission_feedback import (
    build_forbidden_message,
    build_forbidden_payload,
    forbidden_json,
    flash_forbidden,
)
from app.utils.manage_permissions import (
    get_user_manage_permission,
    check_manage_permission
)
from app.utils.password_audit import record_password_audit
from app.utils.audit_tags import (
    REVIEW_TAG_OPTIONS,
    LATE_TAG_OPTIONS,
    LATE_TAG_LATE,
    REVIEW_TAG_REQUIRED,
    build_audit_tag,
    parse_audit_tag,
    validate_audit_tag,
    is_auto_review_allowed,
)
from app.utils.leave_management import (
    ASSESSMENT_EXEMPT_OVERRIDE_TYPES,
    LEAVE_OVERRIDE_TYPE,
    build_leave_status_payload,
    get_current_teaching_week as get_leave_current_teaching_week,
    get_form_effective_week_no as get_leave_form_effective_week_no,
    get_leave_makeup_forms,
    get_teaching_settings as get_leave_teaching_settings,
    parse_lecture_date_value,
    set_leave_makeup_forms,
)
from app.utils.user_status import (
    UNASSIGNED_DEPARTMENT_NAME,
    UNASSIGNED_GROUP_NAME,
    active_user_filter,
    is_user_active,
)
from app.utils.profile_settings import (
    PROFILE_EDITABLE_FIELD_OPTIONS,
    SETTING_KEY_PROFILE_EDITABLE_FIELDS,
    get_profile_editable_fields,
    normalize_profile_editable_fields,
)
from app.utils.course_registration_limits import (
    SETTING_KEY_COURSE_WEEKLY_LIMIT_COUNT,
    SETTING_KEY_COURSE_WEEKLY_LIMIT_ENABLED,
    get_course_weekly_limit_settings,
    normalize_course_weekly_limit_count,
)
from app.utils.review_drafts import (
    delete_review_form_draft,
    load_review_form_draft,
    normalize_review_draft_payload,
    parse_review_form_draft,
    save_review_form_draft,
)
from app.utils.env_config import env_path
import pandas as pd
import openpyxl
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from io import BytesIO
import os
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
import string
from datetime import datetime
import json
import numpy as np
import re
from collections import defaultdict

ALLOWED_EXTENSIONS = {'xlsx', 'xls'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


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


def _active_user_query():
    return User.query.filter(active_user_filter())


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
    user.group_id = None
    user.group = UNASSIGNED_GROUP_NAME


def _build_review_form_filter_datetime(group_data, time_filter_type):
    latest_form = (group_data or {}).get('latest_form')
    if not latest_form:
        return None
    if time_filter_type == 'lecture':
        lecture_date = _parse_lecture_date_value(latest_form.lecture_date)
        if not lecture_date:
            return None
        return datetime.combine(lecture_date, datetime.min.time())
    if time_filter_type == 'updated':
        return _get_form_latest_timestamp(latest_form)

    created_candidates = [
        form.created_at for form in ((group_data or {}).get('forms') or [])
        if getattr(form, 'created_at', None)
    ]
    if created_candidates:
        return min(created_candidates)
    return latest_form.created_at


def _normalize_review_form_time_filter(time_filter_type):
    if time_filter_type in ['lecture', 'updated', 'created']:
        return time_filter_type
    return 'created'


def _get_form_latest_timestamp(form):
    if not form:
        return None
    return form.updated_at or form.created_at


def _excel_cell_to_text(value):
    if value is None or pd.isna(value):
        return ''
    text = str(value).strip()
    if text.lower() == 'nan':
        return ''
    return text


def _to_int_or_none(value):
    text = _excel_cell_to_text(value)
    if not text:
        return None
    if text.endswith('.0'):
        text = text[:-2]
    try:
        return int(text)
    except Exception:
        return None


def _resolve_assessment_users(current_user_id, user_ids):
    current_user = User.query.get(current_user_id)
    if not current_user:
        return []
    manage_permission = get_user_manage_permission(current_user_id)
    if manage_permission == '管理部门':
        scoped_users = _active_user_query().filter(
            User.role.in_(['信息员', '管理员']),
            User.department == current_user.department,
            User.id != current_user_id
        ).all()
        reviewable_ids = set(u.id for u in scoped_users)
    else:
        reviewable_ids = set(get_reviewable_users(current_user_id))
    if user_ids:
        selected_ids = set()
        for uid in user_ids:
            try:
                selected_ids.add(int(uid))
            except Exception:
                continue
        target_ids = reviewable_ids.intersection(selected_ids)
    else:
        target_ids = reviewable_ids
    if not target_ids:
        return []
    users = _active_user_query().filter(User.id.in_(list(target_ids))).all()
    return users


def _get_accessible_department_users(current_user_id):
    users = _resolve_assessment_users(current_user_id, [])
    department_map = {}
    for user in users:
        department_name = (user.department or '').strip()
        if not department_name:
            continue
        department_map.setdefault(department_name, []).append(user)
    return dict(sorted(department_map.items(), key=lambda item: item[0]))


def _latest_form_groups_for_users(listener_numbers):
    if not listener_numbers:
        return []
    forms = LectureForm.query.filter(
        LectureForm.listener_number.in_(listener_numbers)
    ).order_by(LectureForm.unique_id.asc(), LectureForm.created_at.asc(), LectureForm.id.asc()).all()
    group_map = {}
    for form in forms:
        uid = form.unique_id or form.id
        if uid not in group_map:
            group_map[uid] = []
        group_map[uid].append(form)
    groups = []
    for uid, form_list in group_map.items():
        sorted_forms = sorted(form_list, key=lambda f: ((f.created_at or datetime.min), f.id), reverse=True)
        latest_form = sorted_forms[0]
        groups.append({
            'unique_id': uid,
            'latest_form': latest_form,
            'forms': sorted_forms
        })
    return groups


def _parse_lecture_date_value(raw_value):
    if not raw_value:
        return None
    if isinstance(raw_value, datetime):
        return raw_value.date()
    text = str(raw_value).strip()
    if not text:
        return None
    normalized = text.replace('年', '-').replace('月', '-').replace('日', '')
    for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d']:
        try:
            return datetime.strptime(normalized, fmt).date()
        except Exception:
            continue
    match = re.search(r'(\d{4})\D+(\d{1,2})\D+(\d{1,2})', text)
    if not match:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
    except Exception:
        return None


