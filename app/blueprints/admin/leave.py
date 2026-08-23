# -*- coding: utf-8 -*-
# Phase 1 mechanical split from app/blueprints/admin.py
# Module: leave

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

from . import admin_bp  # noqa: F401
from .shared import _active_user_query, _latest_form_groups_for_users  # noqa: F401

def _resolve_department_leave_users(current_user_id, user_ids=None):
    current_user = User.query.get(current_user_id)
    if not current_user or get_user_manage_permission(current_user_id) != '超级管理员':
        return []

    query = _active_user_query().filter(User.role == '信息员')
    if user_ids:
        selected_ids = set()
        for uid in user_ids:
            try:
                selected_ids.add(int(uid))
            except (TypeError, ValueError):
                continue
        if not selected_ids:
            return []
        query = query.filter(User.id.in_(list(selected_ids)))

    return query.order_by(User.department.asc(), User.group.asc(), User.number.asc(), User.name.asc(), User.id.asc()).all()


@admin_bp.route('/api/leave-management/status', methods=['GET'])
@login_required
def get_leave_management_status():
    if get_user_manage_permission(session['user_id']) != '超级管理员':
        return forbidden_json('成员请假设置')

    settings, settings_err = get_leave_teaching_settings()
    current_week = None
    if not settings_err:
        current_week, week_err = get_leave_current_teaching_week(settings)
        if week_err:
            settings_err = week_err

    users = _resolve_department_leave_users(session['user_id'])
    user_map = {user.id: user for user in users}
    user_ids = list(user_map.keys())
    leave_records = AssessmentOverride.query.filter(
        AssessmentOverride.user_id.in_(user_ids),
        AssessmentOverride.override_type == LEAVE_OVERRIDE_TYPE,
    ).order_by(AssessmentOverride.start_week.asc(), AssessmentOverride.end_week.asc()).all() if user_ids else []

    current_leave_user_ids = set()
    active_items = []
    pending_items = []
    completed_items = []
    if current_week and settings:
        for record in leave_records:
            user = user_map.get(record.user_id)
            payload = build_leave_status_payload(record, current_week, user=user, settings=settings)
            if not payload:
                continue
            if payload['status'] == 'active':
                active_items.append(payload)
                current_leave_user_ids.add(record.user_id)
            elif payload['status'] == 'pending':
                pending_items.append(payload)
            elif payload['status'] == 'completed':
                completed_items.append(payload)

    candidates = []
    for user in sorted(users, key=lambda item: (item.number or '', item.name or '')):
        candidates.append({
            'user_id': user.id,
            'name': user.name,
            'number': user.number,
            'department': user.department,
            'group': user.group,
            'role': user.role,
            'has_current_leave': user.id in current_leave_user_ids,
        })

    return jsonify({
        'success': True,
        'current_week': current_week,
        'settings_error': settings_err,
        'required_submission': int((settings or {}).get('required_submission') or 0),
        'summary': {
            'active_count': len(active_items),
            'pending_makeup_count': len(pending_items),
            'completed_makeup_count': len(completed_items),
        },
        'candidates': candidates,
        'active': active_items,
        'pending': pending_items,
        'completed': completed_items,
    })


@admin_bp.route('/api/leave-management/current-week', methods=['POST'])
@login_required
def create_current_week_leave():
    if get_user_manage_permission(session['user_id']) != '超级管理员':
        return forbidden_json('成员请假设置')

    settings, settings_err = get_leave_teaching_settings()
    if settings_err:
        return jsonify({'success': False, 'message': settings_err}), 400
    current_week, week_err = get_leave_current_teaching_week(settings)
    if week_err or not current_week:
        return jsonify({'success': False, 'message': week_err or '当前教学周不可用'}), 400

    data = request.get_json() or {}
    user_id = data.get('user_id')
    target_users = _resolve_department_leave_users(session['user_id'], [str(user_id)])
    if not target_users:
        return jsonify({
            'success': False,
            'message': build_forbidden_message(
                '成员请假设置',
                '当前账号没有操作成员请假设置的权限。',
                action='操作',
            ),
        }), 403
    target_user = target_users[0]

    existing = AssessmentOverride.query.filter(
        AssessmentOverride.user_id == target_user.id,
        AssessmentOverride.override_type == LEAVE_OVERRIDE_TYPE,
        AssessmentOverride.start_week <= current_week,
        AssessmentOverride.end_week >= current_week,
    ).first()
    if existing:
        return jsonify({'success': False, 'message': f'{target_user.name} 当前教学周已处于请假中'}), 400

    reason = (data.get('reason') or '').strip() or '超级管理员发起当前教学周请假'
    record = AssessmentOverride(
        user_id=target_user.id,
        start_week=current_week,
        end_week=current_week,
        override_type=LEAVE_OVERRIDE_TYPE,
        reason=reason,
        created_by=session['user_id'],
    )
    db.session.add(record)
    db.session.commit()
    return jsonify({
        'success': True,
        'message': f'已为 {target_user.name} 设置第{current_week}周请假',
        'leave': build_leave_status_payload(record, current_week, user=target_user, settings=settings),
    })


def _get_super_admin_leave_record(override_id):
    if get_user_manage_permission(session['user_id']) != '超级管理员':
        return None, jsonify(build_forbidden_payload('成员请假设置')), 403
    record = AssessmentOverride.query.filter_by(
        id=override_id,
        override_type=LEAVE_OVERRIDE_TYPE,
    ).first()
    if not record:
        return None, jsonify({'success': False, 'message': '请假记录不存在'}), 404
    return record, None, None


def _serialize_leave_form_option(group_data, settings, selected_unique_ids):
    latest_form = group_data.get('latest_form')
    if not latest_form:
        return None
    unique_id = latest_form.unique_id or latest_form.id
    try:
        normalized_unique_id = int(unique_id)
    except (TypeError, ValueError):
        normalized_unique_id = unique_id
    effective_week = get_leave_form_effective_week_no(latest_form, settings) if settings else None
    return {
        'form_id': latest_form.id,
        'unique_id': normalized_unique_id,
        'course_title': latest_form.course_title or '',
        'teacher_name': latest_form.teacher_name or '',
        'lecture_date': latest_form.lecture_date or '',
        'status': latest_form.status or '',
        'effective_week': effective_week,
        'created_at': latest_form.created_at.strftime('%Y-%m-%d %H:%M:%S') if latest_form.created_at else '',
        'selected': normalized_unique_id in selected_unique_ids,
    }


@admin_bp.route('/api/leave-management/<int:override_id>/forms', methods=['GET'])
@login_required
def get_leave_makeup_form_options(override_id):
    record, error_response, status_code = _get_super_admin_leave_record(override_id)
    if error_response:
        return error_response, status_code
    user = User.query.get(record.user_id)
    if not user:
        return jsonify({'success': False, 'message': '请假人员不存在'}), 404

    settings, settings_err = get_leave_teaching_settings()
    selected_unique_ids = set()
    for item in get_leave_makeup_forms(record):
        value = item.get('unique_id') or item.get('form_id')
        try:
            value = int(value)
        except (TypeError, ValueError):
            pass
        selected_unique_ids.add(value)

    form_options = []
    for group_data in _latest_form_groups_for_users([user.number]):
        item = _serialize_leave_form_option(group_data, settings, selected_unique_ids)
        if item:
            form_options.append(item)
    form_options.sort(key=lambda item: item.get('created_at') or '', reverse=True)

    return jsonify({
        'success': True,
        'settings_error': settings_err,
        'leave': {
            'override_id': record.id,
            'user_id': record.user_id,
            'name': user.name,
            'number': user.number,
            'department': user.department,
            'group': user.group,
            'start_week': record.start_week,
            'end_week': record.end_week,
            'reason': record.reason or '',
        },
        'forms': form_options,
    })


@admin_bp.route('/api/leave-management/<int:override_id>/makeup-forms', methods=['PUT'])
@login_required
def update_leave_makeup_forms(override_id):
    record, error_response, status_code = _get_super_admin_leave_record(override_id)
    if error_response:
        return error_response, status_code
    user = User.query.get(record.user_id)
    if not user:
        return jsonify({'success': False, 'message': '请假人员不存在'}), 404

    data = request.get_json() or {}
    selected_unique_ids = set()
    for raw_value in data.get('unique_ids') or []:
        try:
            selected_unique_ids.add(int(raw_value))
        except (TypeError, ValueError):
            continue

    selected_forms = []
    for group_data in _latest_form_groups_for_users([user.number]):
        latest_form = group_data.get('latest_form')
        if not latest_form:
            continue
        unique_id = latest_form.unique_id or latest_form.id
        try:
            normalized_unique_id = int(unique_id)
        except (TypeError, ValueError):
            continue
        if normalized_unique_id in selected_unique_ids:
            selected_forms.append(latest_form)

    set_leave_makeup_forms(
        record,
        selected_forms,
        source='manual',
        operator_user_id=session['user_id'],
    )
    db.session.commit()

    settings, settings_err = get_leave_teaching_settings()
    current_week = None
    if not settings_err:
        current_week, _ = get_leave_current_teaching_week(settings)
    return jsonify({
        'success': True,
        'message': '补交表单已更新',
        'leave': build_leave_status_payload(record, current_week, user=user, settings=settings) if current_week else None,
    })


