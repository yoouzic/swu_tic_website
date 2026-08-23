# -*- coding: utf-8 -*-
# Phase 1 mechanical split from app/blueprints/admin.py
# Module: assessment_stats

from flask import render_template, request, redirect, url_for, session, jsonify, send_file
from app.models import User, LectureForm, db, SystemSetting, ScoreRecord, ScoreItem, StatisticsSnapshot, AssessmentOverride
from datetime import datetime, timedelta
from app.blueprints.auth import login_required, role_required
from app.utils.permission_feedback import build_forbidden_message, forbidden_json, flash_forbidden
from app.utils.manage_permissions import get_user_manage_permission
from app.utils.audit_tags import LATE_TAG_LATE, parse_audit_tag
from app.utils.leave_management import ASSESSMENT_EXEMPT_OVERRIDE_TYPES, LEAVE_OVERRIDE_TYPE
from app.utils.user_status import UNASSIGNED_GROUP_NAME
import pandas as pd
import openpyxl
from openpyxl.styles import Font
from io import BytesIO
import json
from . import admin_bp
from .shared import _build_review_form_filter_datetime, _get_accessible_department_users, _latest_form_groups_for_users, _normalize_review_form_time_filter, _parse_lecture_date_value, _resolve_assessment_users, _to_int_or_none, allowed_file, get_reviewer_display_mode


SNAPSHOT_TYPE_DEPARTMENT_MONTHLY = 'department_monthly_assessment'


SNAPSHOT_TYPE_SUBMISSION_REWARD = 'submission_reward'


def _has_assessment_stats_access(user_id):
    manage_permission = get_user_manage_permission(user_id)
    return manage_permission in ['超级管理员', '管理部门']


def _snapshot_base_query(snapshot_type, current_user_id):
    query = StatisticsSnapshot.query.filter_by(snapshot_type=snapshot_type)
    if get_user_manage_permission(current_user_id) != '超级管理员':
        query = query.filter_by(created_by=current_user_id)
    return query.order_by(StatisticsSnapshot.created_at.desc(), StatisticsSnapshot.id.desc())


def _build_snapshot_list_items(snapshot_type, current_user_id):
    records = _snapshot_base_query(snapshot_type, current_user_id).all()
    creator_ids = {record.created_by for record in records if record.created_by}
    creators = {
        user.id: user
        for user in User.query.filter(User.id.in_(list(creator_ids))).all()
    } if creator_ids else {}
    items = []
    for record in records:
        try:
            filters_data = json.loads(record.filters_json or '{}')
        except Exception:
            filters_data = {}
        creator = creators.get(record.created_by)
        items.append({
            'id': record.id,
            'title': record.title,
            'created_at': record.created_at.strftime('%Y-%m-%d %H:%M:%S') if record.created_at else '',
            'creator_name': creator.name if creator else '',
            'range_start': filters_data.get('start_date', ''),
            'range_end': filters_data.get('end_date', ''),
            'period_label': filters_data.get('period_label', ''),
            'scope_label': filters_data.get('scope_label', ''),
        })
    return items


def _get_snapshot_record_or_404(snapshot_id, snapshot_type, current_user_id):
    record = StatisticsSnapshot.query.filter_by(id=snapshot_id, snapshot_type=snapshot_type).first()
    if not record:
        return None, jsonify({'success': False, 'message': '历史记录不存在'}), 404
    if get_user_manage_permission(current_user_id) != '超级管理员' and record.created_by != current_user_id:
        return None, jsonify({
            'success': False,
            'message': build_forbidden_message(
                '统计历史记录',
                '当前账号没有访问该历史记录的权限。',
                action='查看',
            ),
        }), 403
    return record, None, None


def _create_statistics_snapshot(snapshot_type, title, filters_data, payload_data, current_user_id):
    record = StatisticsSnapshot(
        snapshot_type=snapshot_type,
        title=title,
        filters_json=json.dumps(filters_data, ensure_ascii=False),
        payload_json=json.dumps(payload_data, ensure_ascii=False),
        created_by=current_user_id
    )
    db.session.add(record)
    db.session.commit()
    return record


def _load_snapshot_payload(record):
    try:
        filters_data = json.loads(record.filters_json or '{}')
    except Exception:
        filters_data = {}
    try:
        payload_data = json.loads(record.payload_json or '{}')
    except Exception:
        payload_data = {}
    return filters_data, payload_data


@admin_bp.route('/review-assessment-stats')
@role_required('管理员')
def review_assessment_stats():
    if not _has_assessment_stats_access(session['user_id']):
        flash_forbidden('审表考评统计')
        return redirect(url_for('main.index'))
    manage_permission = get_user_manage_permission(session['user_id'])
    can_manual_assessment_import = manage_permission == '超级管理员'
    return render_template('admin/review_assessment_stats.html', can_manual_assessment_import=can_manual_assessment_import)


@admin_bp.route('/submission-count-stats')
@role_required('管理员')
def submission_count_stats():
    if not _has_assessment_stats_access(session['user_id']):
        flash_forbidden('交表数量统计')
        return redirect(url_for('main.index'))
    return render_template('admin/submission_count_stats.html')


@admin_bp.route('/department-monthly-assessment-stats')
@role_required('管理员')
def department_monthly_assessment_stats():
    if not _has_assessment_stats_access(session['user_id']):
        flash_forbidden('部门月度考评')
        return redirect(url_for('main.index'))
    available_departments = list(_get_accessible_department_users(session['user_id']).keys())
    return render_template('admin/department_monthly_assessment_stats.html', available_departments=available_departments)


def _parse_assessment_range(start_date_str, end_date_str):
    if not start_date_str or not end_date_str:
        return None, None, '请先选择开始和结束时间'
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1)
        return start_date, end_date, None
    except Exception:
        return None, None, '时间格式错误，请使用YYYY-MM-DD'


def _resolve_selected_departments(current_user_id, department_names):
    department_map = _get_accessible_department_users(current_user_id)
    if department_names:
        normalized = []
        for name in department_names:
            value = (name or '').strip()
            if value and value in department_map and value not in normalized:
                normalized.append(value)
        selected_names = normalized
    else:
        selected_names = list(department_map.keys())
    return selected_names, {name: department_map[name] for name in selected_names if name in department_map}


def _latest_forms_in_range(listener_numbers, start_date, end_date):
    if not listener_numbers:
        return []
    forms = LectureForm.query.filter(
        LectureForm.listener_number.in_(listener_numbers),
        LectureForm.created_at >= start_date,
        LectureForm.created_at < end_date
    ).order_by(LectureForm.created_at.asc(), LectureForm.id.asc()).all()
    latest_map = {}
    for form in forms:
        uid = form.unique_id or form.id
        latest_map[uid] = form
    return list(latest_map.values())


def _assessment_form_latest_timestamp(form):
    if not form:
        return None
    return form.updated_at or form.created_at


def _assessment_latest_form_groups_for_users(listener_numbers):
    if not listener_numbers:
        return []
    forms = LectureForm.query.filter(
        LectureForm.listener_number.in_(listener_numbers)
    ).order_by(LectureForm.unique_id.asc(), LectureForm.id.asc()).all()
    group_map = {}
    for form in forms:
        uid = form.unique_id or form.id
        group_map.setdefault(uid, []).append(form)

    groups = []
    for uid, form_list in group_map.items():
        sorted_forms = sorted(
            form_list,
            key=lambda f: (_assessment_form_latest_timestamp(f) or datetime.min, f.id),
            reverse=True
        )
        latest_form = sorted_forms[0]
        groups.append({
            'unique_id': uid,
            'latest_form': latest_form,
            'latest_timestamp': _assessment_form_latest_timestamp(latest_form),
            'forms': sorted_forms
        })

    groups.sort(
        key=lambda group: (
            group['latest_timestamp'] or datetime.min,
            group['latest_form'].id if group['latest_form'] else 0
        ),
        reverse=True
    )
    return groups


def _assessment_latest_forms_in_range(listener_numbers, start_date, end_date):
    if not listener_numbers:
        return []
    latest_forms = []
    for group_data in _assessment_latest_form_groups_for_users(listener_numbers):
        latest_form = group_data['latest_form']
        latest_timestamp = group_data['latest_timestamp']
        if not latest_form or not latest_timestamp:
            continue
        if start_date <= latest_timestamp < end_date:
            latest_forms.append(latest_form)
    return latest_forms


def _get_teaching_reward_settings():
    first_week_raw = SystemSetting.get('teaching_first_week_monday')
    if not first_week_raw:
        return None, '请先在制度设置中配置第一周起始日期'
    try:
        first_week_date = datetime.strptime(first_week_raw, '%Y-%m-%d').date()
    except Exception:
        return None, '制度设置中的第一周起始日期格式错误'
    try:
        week_start_day = int(SystemSetting.get('teaching_week_start_day', '0') or 0)
    except Exception:
        week_start_day = 0
    if week_start_day < 0 or week_start_day > 6:
        week_start_day = 0
    try:
        required_submission = int(SystemSetting.get('teaching_required_submission', '1') or 1)
    except Exception:
        required_submission = 1
    if required_submission < 0:
        required_submission = 0
    try:
        total_weeks = int(SystemSetting.get('teaching_total_weeks', '20') or 20)
    except Exception:
        total_weeks = 20
    if total_weeks < 1 or total_weeks > 52:
        total_weeks = 20
    return {
        'first_week_date': first_week_date,
        'week_start_day': week_start_day,
        'required_submission': required_submission,
        'total_weeks': total_weeks
    }, None


def _get_teaching_week_no(date_obj, first_week_date, week_start_day, total_weeks=None):
    if not date_obj or not first_week_date:
        return None
    from app.services.teaching_calendar import TeachingCalendarConfig, teaching_week_number
    config = TeachingCalendarConfig(
        first_week_date=first_week_date,
        week_start_day=week_start_day,
        total_weeks=total_weeks if total_weeks is not None else 52,
    )
    return teaching_week_number(date_obj, config)


def _compute_teaching_week_window(start_date, end_date, first_week_date, week_start_day):
    range_start = start_date.date()
    range_end = (end_date - timedelta(days=1)).date()
    if range_end < range_start:
        return {
            'has_full_weeks': False,
            'start_week': None,
            'end_week': None,
            'week_count': 0,
            'label': '无完整教学周',
            'window_start': None,
            'window_end': None
        }
    first_full_start = range_start + timedelta(days=(week_start_day - range_start.weekday()) % 7)
    last_candidate_start = range_end - timedelta(days=6)
    if last_candidate_start < first_full_start:
        return {
            'has_full_weeks': False,
            'start_week': None,
            'end_week': None,
            'week_count': 0,
            'label': '无完整教学周',
            'window_start': None,
            'window_end': None
        }
    last_full_start = last_candidate_start - timedelta(days=(last_candidate_start.weekday() - week_start_day) % 7)
    teaching_start = first_week_date - timedelta(days=(first_week_date.weekday() - week_start_day) % 7)
    if first_full_start < teaching_start:
        first_full_start = teaching_start
    if last_full_start < first_full_start:
        return {
            'has_full_weeks': False,
            'start_week': None,
            'end_week': None,
            'week_count': 0,
            'label': '无完整教学周',
            'window_start': None,
            'window_end': None
        }
    start_week = ((first_full_start - teaching_start).days // 7) + 1
    end_week = ((last_full_start - teaching_start).days // 7) + 1
    if end_week < 1:
        return {
            'has_full_weeks': False,
            'start_week': None,
            'end_week': None,
            'week_count': 0,
            'label': '无完整教学周',
            'window_start': None,
            'window_end': None
        }
    if start_week < 1:
        start_week = 1
    week_count = ((last_full_start - first_full_start).days // 7) + 1
    label = str(start_week) if start_week == end_week else f'{start_week}-{end_week}'
    window_start = datetime.combine(first_full_start, datetime.min.time())
    window_end = datetime.combine(last_full_start + timedelta(days=7), datetime.min.time())
    return {
        'has_full_weeks': True,
        'start_week': start_week,
        'end_week': end_week,
        'week_count': week_count,
        'label': label,
        'window_start': window_start,
        'window_end': window_end
    }


def _get_teaching_term_start(first_week_date, week_start_day):
    from app.services.teaching_calendar import TeachingCalendarConfig, teaching_term_start
    return teaching_term_start(TeachingCalendarConfig(
        first_week_date=first_week_date,
        week_start_day=week_start_day,
        total_weeks=52,
    ))


def _get_form_effective_week_no(form, reward_settings):
    if not form:
        return None
    parsed_tag = parse_audit_tag(form.audit_tag)
    if parsed_tag.get('week_correction_week_no') is not None:
        return parsed_tag['week_correction_week_no']
    lecture_date = _parse_lecture_date_value(form.lecture_date)
    week_no = _get_teaching_week_no(
        lecture_date,
        reward_settings['first_week_date'],
        reward_settings['week_start_day'],
        reward_settings.get('total_weeks'),
    )
    if week_no is None:
        return None
    if parsed_tag.get('legacy_late_tag') == LATE_TAG_LATE:
        week_no -= 1
    if week_no < 1:
        return None
    return week_no


def _build_teaching_month_templates(reward_window, reward_settings):
    if not reward_window.get('has_full_weeks'):
        return []
    start_week = reward_window.get('start_week')
    end_week = reward_window.get('end_week')
    if start_week is None or end_week is None:
        return []
    teaching_start = _get_teaching_term_start(
        reward_settings['first_week_date'],
        reward_settings['week_start_day']
    )

    # 尝试加载自定义教学月设置
    custom_months = _load_custom_month_definitions()

    if custom_months:
        # 使用自定义教学月设置
        month_templates = []
        for idx, m_def in enumerate(custom_months):
            m_start = m_def['start_week']
            m_end = m_def['end_week']
            # 仅包含完整落在选择范围内的教学月
            if m_start < start_week or m_end > end_week:
                continue
            month_no = idx + 1
            month_start_date = teaching_start + timedelta(days=(m_start - 1) * 7)
            month_end_date = teaching_start + timedelta(days=(m_end - 1) * 7 + 6)
            weeks = []
            for week_no in range(m_start, m_end + 1):
                week_start_date = teaching_start + timedelta(days=(week_no - 1) * 7)
                week_end_date = week_start_date + timedelta(days=6)
                weeks.append({
                    'week_no': week_no,
                    'week_label': f'第{week_no}周',
                    'start_date': week_start_date.strftime('%Y-%m-%d'),
                    'end_date': week_end_date.strftime('%Y-%m-%d'),
                })
            month_label = m_def.get('label') or f'第{month_no}教学月'
            month_templates.append({
                'month_no': month_no,
                'month_label': month_label,
                'week_range_label': f'第{m_start}-{m_end}周',
                'start_week': m_start,
                'end_week': m_end,
                'start_date': month_start_date.strftime('%Y-%m-%d'),
                'end_date': month_end_date.strftime('%Y-%m-%d'),
                'weeks': weeks,
            })
        return month_templates

    # 默认逻辑：每4周为一个教学月
    month_templates = []
    start_month = ((start_week - 1) // 4) + 1
    end_month = ((end_week - 1) // 4) + 1
    for month_no in range(start_month, end_month + 1):
        month_start_week = (month_no - 1) * 4 + 1
        month_end_week = month_start_week + 3
        if month_start_week < start_week or month_end_week > end_week:
            continue
        month_start_date = teaching_start + timedelta(days=(month_start_week - 1) * 7)
        month_end_date = month_start_date + timedelta(days=27)
        weeks = []
        for week_no in range(month_start_week, month_end_week + 1):
            week_start_date = teaching_start + timedelta(days=(week_no - 1) * 7)
            week_end_date = week_start_date + timedelta(days=6)
            weeks.append({
                'week_no': week_no,
                'week_label': f'第{week_no}周',
                'start_date': week_start_date.strftime('%Y-%m-%d'),
                'end_date': week_end_date.strftime('%Y-%m-%d'),
            })
        month_templates.append({
            'month_no': month_no,
            'month_label': f'第{month_no}教学月',
            'week_range_label': f'第{month_start_week}-{month_end_week}周',
            'start_week': month_start_week,
            'end_week': month_end_week,
            'start_date': month_start_date.strftime('%Y-%m-%d'),
            'end_date': month_end_date.strftime('%Y-%m-%d'),
            'weeks': weeks,
        })
    return month_templates


def _load_custom_month_definitions():
    """加载自定义教学月设置，返回列表或 None"""
    raw = SystemSetting.get('teaching_month_definitions')
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, list) or len(data) == 0:
            return None
        result = []
        for item in data:
            s = int(item.get('start_week'))
            e = int(item.get('end_week'))
            if s < 1 or e < 1 or s > e:
                continue
            result.append({
                'start_week': s,
                'end_week': e,
                'label': item.get('label', '').strip() or None
            })
        return result if result else None
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        return None


def _resolve_error_unique_ids(candidate_groups):
    form_to_unique = {}
    for group_data in candidate_groups:
        unique_id = group_data['unique_id']
        for form in group_data['forms']:
            form_to_unique[form.id] = unique_id
    if not form_to_unique:
        return set()
    error_form_ids = db.session.query(ScoreRecord.form_id).join(
        ScoreItem, ScoreItem.score_record_id == ScoreRecord.id
    ).filter(
        ScoreRecord.form_id.in_(list(form_to_unique.keys())),
        (ScoreItem.department_score > 0) | (ScoreItem.personal_score > 0)
    ).distinct().all()
    error_unique_ids = set()
    for row in error_form_ids:
        unique_id = form_to_unique.get(row.form_id)
        if unique_id is not None:
            error_unique_ids.add(unique_id)
    return error_unique_ids


def _form_group_created_at(group_data):
    if not group_data:
        return None
    created_candidates = [
        form.created_at for form in (group_data.get('forms') or [])
        if getattr(form, 'created_at', None)
    ]
    if created_candidates:
        return min(created_candidates)
    latest_form = group_data.get('latest_form')
    return latest_form.created_at if latest_form and latest_form.created_at else None


def _sort_submission_count_rows(rows, sort_by):
    if sort_by == 'reward':
        rows.sort(key=lambda x: (-x['reward_form_count'], -x['submission_group_count'], x['number'] or '', x['name'] or ''))
    else:
        rows.sort(key=lambda x: (-x['submission_group_count'], -x['reward_form_count'], x['number'] or '', x['name'] or ''))
    for idx, row in enumerate(rows, start=1):
        row['rank'] = idx
    return rows


def _build_review_assessment_rows(users, start_date, end_date):
    by_number = {u.number: u for u in users if u.number}
    user_stats = {
        u.id: {
            'user_id': u.id,
            'name': u.name,
            'number': u.number,
            'department': u.department,
            'group': u.group,
            'linked_department_score': 0.0,
            'linked_personal_score': 0.0
        } for u in users
    }

    latest_forms = _assessment_latest_forms_in_range(list(by_number.keys()), start_date, end_date)
    for form in latest_forms:
        user = by_number.get(form.listener_number)
        if not user or not form.score_record:
            continue
        user_stats[user.id]['linked_department_score'] += float(form.score_record.total_department_score or 0.0)
        user_stats[user.id]['linked_personal_score'] += float(form.score_record.total_personal_score or 0.0)

    rows = []
    for row in user_stats.values():
        row['total_department_score'] = row['linked_department_score']
        row['total_personal_score'] = row['linked_personal_score']
        row['total_score'] = row['total_personal_score']
        rows.append(row)

    rows.sort(key=lambda x: x['total_personal_score'], reverse=True)
    for idx, row in enumerate(rows, start=1):
        row['rank'] = idx
    return rows


def _build_export_filename(filename_title, default_title):
    title = (filename_title or '').strip() or default_title
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        title = title.replace(char, '_')
    title = title.strip().strip('.')
    if not title:
        title = default_title
    if not title.lower().endswith('.xlsx'):
        title = f'{title}.xlsx'
    return title


def _autosize_worksheet(ws, min_width=12, max_width=40):
    for column_cells in ws.columns:
        max_length = 0
        column_letter = column_cells[0].column_letter
        for cell in column_cells:
            value = '' if cell.value is None else str(cell.value)
            if len(value) > max_length:
                max_length = len(value)
        ws.column_dimensions[column_letter].width = max(min_width, min(max_length + 2, max_width))


def _workbook_response(wb, filename):
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


def _build_reward_week_templates(reward_window, required_submission):
    templates = []
    if not reward_window.get('has_full_weeks') or not reward_window.get('window_start'):
        return templates
    window_start = reward_window['window_start']
    start_week = reward_window['start_week'] or 1
    for index in range(reward_window.get('week_count') or 0):
        week_start = window_start + timedelta(days=7 * index)
        week_end = week_start + timedelta(days=6)
        week_no = start_week + index
        templates.append({
            'week_index': index,
            'week_no': week_no,
            'week_label': f'第{week_no}周',
            'start_date': week_start.strftime('%Y-%m-%d'),
            'end_date': week_end.strftime('%Y-%m-%d'),
            'required_submission': int(required_submission or 0),
            'effective_count': 0,
            'error_count': 0,
            'reward_form_count': 0
        })
    return templates


def _get_reward_week_index(group_data, reward_window, reward_settings):
    if not reward_window.get('has_full_weeks'):
        return None
    latest_form = group_data.get('latest_form') if group_data else None
    if not latest_form:
        return None
    week_no = _get_form_effective_week_no(latest_form, reward_settings)
    if week_no is None:
        return None
    start_week = reward_window.get('start_week')
    end_week = reward_window.get('end_week')
    if start_week is None or end_week is None or week_no < start_week or week_no > end_week:
        return None
    return week_no - start_week


def _build_submission_count_rows(users, start_date, end_date, reward_settings, reward_window, time_filter_type='created'):
    by_number = {u.number: u for u in users if u.number}
    week_templates = _build_reward_week_templates(reward_window, reward_settings['required_submission'])
    user_stats = {
        u.id: {
            'user_id': u.id,
            'name': u.name,
            'number': u.number,
            'department': u.department,
            'group': u.group,
            'submission_group_count': 0,
            'reward_effective_count': 0,
            'reward_error_count': 0,
            'reward_form_count': 0,
            'reward_week_details': [dict(template) for template in week_templates]
        } for u in users
    }

    groups = _latest_form_groups_for_users(list(by_number.keys()))
    reward_candidates = []
    for group_data in groups:
        latest_form = group_data['latest_form']
        if not latest_form or not latest_form.created_at:
            continue
        user = by_number.get(latest_form.listener_number)
        if not user:
            continue
        filter_dt = _build_review_form_filter_datetime(group_data, time_filter_type)
        if filter_dt and start_date <= filter_dt < end_date:
            user_stats[user.id]['submission_group_count'] += 1
        week_index = _get_reward_week_index(group_data, reward_window, reward_settings)
        if week_index is None or latest_form.status != '中心已审核':
            continue
        reward_candidates.append({
            'group_data': group_data,
            'user_id': user.id,
            'week_index': week_index
        })

    error_unique_ids = _resolve_error_unique_ids([item['group_data'] for item in reward_candidates])
    for item in reward_candidates:
        row = user_stats[item['user_id']]
        week_detail = row['reward_week_details'][item['week_index']]
        row['reward_effective_count'] += 1
        week_detail['effective_count'] += 1
        if item['group_data']['unique_id'] in error_unique_ids:
            row['reward_error_count'] += 1
            week_detail['error_count'] += 1

    for row in user_stats.values():
        total_reward_forms = 0
        for week_detail in row['reward_week_details']:
            week_reward = max(
                int(week_detail['effective_count']) - int(week_detail['error_count']) - int(week_detail['required_submission']),
                0
            )
            week_detail['reward_form_count'] = week_reward
            total_reward_forms += week_reward
        row['reward_form_count'] = total_reward_forms

    return list(user_stats.values())


def _format_score_value_label(value):
    try:
        numeric = float(value or 0)
    except Exception:
        numeric = 0.0
    if numeric.is_integer():
        return str(int(numeric))
    return f'{numeric:.2f}'.rstrip('0').rstrip('.')


def _build_department_monthly_assessment_payload(current_user_id, start_date, end_date, department_names):
    reward_settings, reward_err = _get_teaching_reward_settings()
    if reward_err:
        return None, reward_err

    reward_window = _compute_teaching_week_window(
        start_date,
        end_date,
        reward_settings['first_week_date'],
        reward_settings['week_start_day']
    )
    month_templates = _build_teaching_month_templates(reward_window, reward_settings)
    selected_departments, department_user_map = _resolve_selected_departments(current_user_id, department_names)
    payload = {
        'departments': [],
        'meta': {
            'range_start': start_date.strftime('%Y-%m-%d'),
            'range_end': (end_date - timedelta(days=1)).strftime('%Y-%m-%d'),
            'week_label': reward_window.get('label'),
            'has_full_weeks': reward_window.get('has_full_weeks', False),
            'has_full_months': len(month_templates) > 0,
            'month_count': len(month_templates),
            'month_labels': [item['month_label'] for item in month_templates],
            'months': month_templates,
            'required_submission_per_member': reward_settings['required_submission'],
            'scope_label': '、'.join(selected_departments) if selected_departments else '未选择部门',
            'period_label': '、'.join(item['month_label'] for item in month_templates) if month_templates else '无完整教学月',
            'disclaimer': '考评情况为系统根据当前考评规则和数据自动给出，不代表真实考评情况，请认真核实，建议核实无误后保存或导出数据。'
        }
    }

    if not selected_departments:
        return payload, None
    if not month_templates:
        for department_name in selected_departments:
            payload['departments'].append({
                'department': department_name,
                'member_count': len(department_user_map.get(department_name, [])),
                'months': []
            })
        return payload, None

    selected_week_nos = {
        week['week_no']
        for month in month_templates
        for week in month.get('weeks', [])
    }

    # 查询所有相关用户的考核豁免规则
    all_user_ids_for_exemption = []
    for dept_users in department_user_map.values():
        all_user_ids_for_exemption.extend([u.id for u in dept_users])
    exempt_map = {}  # {user_id: [(start_week, end_week), ...]}
    if all_user_ids_for_exemption:
        exemption_records = AssessmentOverride.query.filter(
            AssessmentOverride.user_id.in_(all_user_ids_for_exemption),
            AssessmentOverride.override_type.in_(ASSESSMENT_EXEMPT_OVERRIDE_TYPES)
        ).all()
        for ov in exemption_records:
            exempt_map.setdefault(ov.user_id, []).append((ov.start_week, ov.end_week))

    def _is_user_exempted(user_id, week_no):
        for s, e in exempt_map.get(user_id, []):
            if s <= week_no <= e:
                return True
        return False

    user_by_number = {}
    department_rows = {}
    required_submission_per_member = int(reward_settings['required_submission'] or 0)
    for department_name in selected_departments:
        users = department_user_map.get(department_name, [])
        user_by_number.update({user.number: user for user in users if user.number})
        month_rows = []
        week_map = {}
        member_count = len(users)
        for month in month_templates:
            weeks = []
            for week in month.get('weeks', []):
                wno = week['week_no']
                active_count = sum(1 for u in users if not _is_user_exempted(u.id, wno))
                week_required = active_count * required_submission_per_member
                week_row = {
                    'week_no': wno,
                    'week_label': week['week_label'],
                    'start_date': week['start_date'],
                    'end_date': week['end_date'],
                    'required_submission': week_required,
                    'effective_count': 0,
                    'error_count': 0,
                    'extra_count': 0,
                    'shortage_count': 0,
                    'missing_count': 0,
                    'missing_penalty': 0,
                    'assessment_total_score': 0.0,
                    'assessment_actual_score': 0.0,
                    'raw_score': 0.0,
                    'final_score': 0.0,
                    'assessment_tiers': [],
                    '_assessment_tier_map': {},
                    '_active_member_count': active_count
                }
                weeks.append(week_row)
                week_map[wno] = week_row
            month_summary_required = sum(w['required_submission'] for w in weeks)
            month_rows.append({
                'month_no': month['month_no'],
                'month_label': month['month_label'],
                'week_range_label': month['week_range_label'],
                'start_date': month['start_date'],
                'end_date': month['end_date'],
                'weeks': weeks,
                'summary': {
                    'required_submission': month_summary_required,
                    'effective_count': 0,
                    'error_count': 0,
                    'extra_count': 0,
                    'shortage_count': 0,
                    'missing_count': 0,
                    'missing_penalty': 0,
                    'assessment_total_score': 0.0,
                    'assessment_actual_score': 0.0,
                    'raw_score': 0.0,
                    'final_score': 0.0
                }
            })
        department_rows[department_name] = {
            'department': department_name,
            'member_count': member_count,
            'required_submission_per_member': required_submission_per_member,
            'required_submission_per_week': member_count * required_submission_per_member,
            'users': users,
            'months': month_rows,
            '_week_map': week_map
        }

    groups = _latest_form_groups_for_users(list(user_by_number.keys()))
    effective_form_candidates = []
    user_week_effective = {}
    for group_data in groups:
        latest_form = group_data.get('latest_form')
        if not latest_form:
            continue
        user = user_by_number.get(latest_form.listener_number)
        if not user:
            continue
        department_name = user.department
        department_row = department_rows.get(department_name)
        if not department_row:
            continue
        week_no = _get_form_effective_week_no(latest_form, reward_settings)
        if week_no not in selected_week_nos:
            continue
        week_row = department_row['_week_map'].get(week_no)
        if not week_row:
            continue

        # 跳过被豁免用户的表单
        if _is_user_exempted(user.id, week_no):
            continue

        if latest_form.status == '中心已审核':
            week_row['effective_count'] += 1
            user_week_effective[(user.id, week_no)] = user_week_effective.get((user.id, week_no), 0) + 1
            effective_form_candidates.append({
                'group_data': group_data,
                'department': department_name,
                'week_no': week_no,
            })

        if latest_form.score_record:
            for item in latest_form.score_record.items:
                score_value = float(item.personal_score or 0.0)
                if score_value <= 0:
                    continue
                week_row['assessment_total_score'] += score_value
                score_key = _format_score_value_label(score_value)
                tier_entry = week_row['_assessment_tier_map'].setdefault(score_key, {
                    'score_value': score_value,
                    'score_label': score_key,
                    'item_count': 0,
                    'total_score': 0.0
                })
                tier_entry['item_count'] += 1
                tier_entry['total_score'] += score_value

    error_unique_ids = _resolve_error_unique_ids([item['group_data'] for item in effective_form_candidates])
    for item in effective_form_candidates:
        if item['group_data']['unique_id'] in error_unique_ids:
            department_rows[item['department']]['_week_map'][item['week_no']]['error_count'] += 1

    for department_name, department_row in department_rows.items():
        users = department_row['users']
        for week_no, week_row in department_row['_week_map'].items():
            for user in users:
                # 跳过被豁免的用户
                if _is_user_exempted(user.id, week_no):
                    continue
                effective_count = user_week_effective.get((user.id, week_no), 0)
                diff = effective_count - department_row['required_submission_per_member']
                if diff > 0:
                    week_row['extra_count'] += diff
                elif diff < 0:
                    week_row['shortage_count'] += abs(diff)

            week_row['missing_count'] = max(
                int(week_row['required_submission']) - int(week_row['effective_count']),
                0
            )
            week_row['missing_penalty'] = week_row['missing_count'] * 3
            week_row['raw_score'] = float(week_row['extra_count']) - float(week_row['shortage_count']) * 2
            week_row['assessment_actual_score'] = max(float(week_row['assessment_total_score']) - 5, 0.0)
            week_row['final_score'] = (
                float(week_row['raw_score'])
                - float(week_row['missing_penalty'])
                - float(week_row['assessment_actual_score'])
            )
            week_row['assessment_tiers'] = sorted(
                week_row['_assessment_tier_map'].values(),
                key=lambda item: (float(item['score_value']), item['score_label'])
            )
            week_row.pop('_assessment_tier_map', None)

        for month_row in department_row['months']:
            for week_row in month_row['weeks']:
                for key in ['required_submission', 'effective_count', 'error_count', 'extra_count', 'shortage_count', 'missing_count', 'missing_penalty', 'assessment_total_score', 'assessment_actual_score', 'raw_score', 'final_score']:
                    month_row['summary'][key] += float(week_row[key])
            for int_key in ['required_submission', 'effective_count', 'error_count', 'extra_count', 'shortage_count', 'missing_count', 'missing_penalty']:
                month_row['summary'][int_key] = int(month_row['summary'][int_key])

        department_row.pop('users', None)
        department_row.pop('_week_map', None)
        payload['departments'].append({
            'department': department_row['department'],
            'member_count': department_row['member_count'],
            'required_submission_per_member': department_row['required_submission_per_member'],
            'required_submission_per_week': department_row['required_submission_per_week'],
            'months': department_row['months'],
        })

    return payload, None


def _build_submission_snapshot_payload(users, start_date, end_date, sort_by, time_filter_type='created'):
    reward_settings, reward_err = _get_teaching_reward_settings()
    if reward_err:
        return None, reward_err
    reward_window = _compute_teaching_week_window(
        start_date,
        end_date,
        reward_settings['first_week_date'],
        reward_settings['week_start_day']
    )
    rows = _build_submission_count_rows(
        users,
        start_date,
        end_date,
        reward_settings,
        reward_window,
        time_filter_type=time_filter_type
    )
    _sort_submission_count_rows(rows, sort_by)
    return {
        'users': rows,
        'sort_by': sort_by,
        'time_filter_type': time_filter_type,
        'reward_meta': {
            'range_start': start_date.strftime('%Y-%m-%d'),
            'range_end': (end_date - timedelta(days=1)).strftime('%Y-%m-%d'),
            'week_label': reward_window['label'],
            'start_week': reward_window['start_week'],
            'end_week': reward_window['end_week'],
            'week_count': reward_window['week_count'],
            'has_full_weeks': reward_window['has_full_weeks'],
            'required_submission_per_week': reward_settings['required_submission'],
            'disclaimer': '奖励表情况为系统根据当前奖励表规则和数据自动给出，不代表真实考评情况，请认真核实，建议核实无误后保存或导出数据。'
        }
    }, None


def _build_submission_snapshot_workbook(payload):
    rows = payload.get('users', [])
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '交表统计'
    headers = ['排名', '姓名', '编号', '部门', '小组', '交表数量', '奖励表数量', '有效表单数', '错误表单数']
    ws.append(headers)
    for row in rows:
        ws.append([
            row.get('rank'),
            row.get('name'),
            row.get('number'),
            row.get('department'),
            row.get('group'),
            row.get('submission_group_count'),
            row.get('reward_form_count'),
            row.get('reward_effective_count'),
            row.get('reward_error_count')
        ])
    for cell in ws[1]:
        cell.font = Font(bold=True)

    detail_ws = wb.create_sheet('奖励表详情')
    detail_headers = ['姓名', '编号', '周次', '开始日期', '结束日期', '有效表单数', '错误表单数', '每周需交表数', '奖励表数']
    detail_ws.append(detail_headers)
    for row in rows:
        for week in row.get('reward_week_details', []):
            detail_ws.append([
                row.get('name'),
                row.get('number'),
                week.get('week_label'),
                week.get('start_date'),
                week.get('end_date'),
                week.get('effective_count'),
                week.get('error_count'),
                week.get('required_submission'),
                week.get('reward_form_count')
            ])
    for cell in detail_ws[1]:
        cell.font = Font(bold=True)

    _autosize_worksheet(ws)
    _autosize_worksheet(detail_ws)
    return wb


def _build_department_monthly_workbook(payload):
    wb = openpyxl.Workbook()
    summary_ws = wb.active
    summary_ws.title = '月度考评汇总'
    summary_headers = ['部门', '教学月', '周次范围', '成员数', '月多交表', '月少交表', '月错误表单扣分（原始分）', '月错误表单扣分（实际扣分）', '月总考评分']
    summary_ws.append(summary_headers)
    for department_row in payload.get('departments', []):
        for month_row in department_row.get('months', []):
            summary = month_row.get('summary', {})
            summary_ws.append([
                department_row.get('department'),
                month_row.get('month_label'),
                month_row.get('week_range_label'),
                department_row.get('member_count'),
                summary.get('extra_count'),
                summary.get('shortage_count'),
                summary.get('assessment_total_score'),
                summary.get('assessment_actual_score'),
                summary.get('final_score'),
            ])
    for cell in summary_ws[1]:
        cell.font = Font(bold=True)

    detail_ws = wb.create_sheet('周度考评明细')
    detail_headers = ['部门', '教学月', '周次', '时间范围', '需交表数', '有效表单数', '错误表单数', '多交表（+1分/张）', '少交表（-2分/张）', '实际交表少于应交表数（-3/张）', '错误表单扣分（原始分）', '错误表单扣分（实际扣分）', '周考评分', '扣分档位汇总']
    detail_ws.append(detail_headers)
    for department_row in payload.get('departments', []):
        for month_row in department_row.get('months', []):
            for week_row in month_row.get('weeks', []):
                tier_text = '；'.join(
                    f"{tier.get('score_label')}分 x {tier.get('item_count')}项 = {tier.get('total_score')}"
                    for tier in week_row.get('assessment_tiers', [])
                )
                detail_ws.append([
                    department_row.get('department'),
                    month_row.get('month_label'),
                    week_row.get('week_label'),
                    f"{week_row.get('start_date')} ~ {week_row.get('end_date')}",
                    week_row.get('required_submission'),
                    week_row.get('effective_count'),
                    week_row.get('error_count'),
                    week_row.get('extra_count'),
                    week_row.get('shortage_count'),
                    week_row.get('missing_count'),
                    week_row.get('assessment_total_score'),
                    week_row.get('assessment_actual_score'),
                    week_row.get('final_score'),
                    tier_text
                ])
    for cell in detail_ws[1]:
        cell.font = Font(bold=True)

    _autosize_worksheet(summary_ws)
    _autosize_worksheet(detail_ws, max_width=60)
    return wb


@admin_bp.route('/api/review/submission-count/stats', methods=['GET'])
@login_required
def get_submission_count_stats():
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('交表数量统计')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    start_date, end_date, err = _parse_assessment_range(start_date_str, end_date_str)
    if err:
        return jsonify({'success': False, 'message': err}), 400

    user_ids = request.args.getlist('user_ids')
    users = _resolve_assessment_users(session['user_id'], user_ids)
    if not users:
        return jsonify({'success': True, 'users': []})
    reward_settings, reward_err = _get_teaching_reward_settings()
    if reward_err:
        return jsonify({'success': False, 'message': reward_err}), 400
    reward_window = _compute_teaching_week_window(
        start_date,
        end_date,
        reward_settings['first_week_date'],
        reward_settings['week_start_day']
    )
    sort_by = request.args.get('sort_by', 'submission')
    if sort_by not in ['submission', 'reward']:
        sort_by = 'submission'
    time_filter_type = _normalize_review_form_time_filter(request.args.get('time_filter_type'))
    payload, payload_err = _build_submission_snapshot_payload(users, start_date, end_date, sort_by, time_filter_type)
    if payload_err:
        return jsonify({'success': False, 'message': payload_err}), 400
    rows = []
    for row in payload.get('users', []):
        copied_row = dict(row)
        copied_row.pop('reward_week_details', None)
        rows.append(copied_row)
    _sort_submission_count_rows(rows, sort_by)
    return jsonify({
        'success': True,
        'users': rows,
        'sort_by': sort_by,
        'time_filter_type': time_filter_type,
        'reward_meta': payload.get('reward_meta', {})
    })


@admin_bp.route('/api/review/submission-count/export', methods=['GET'])
@login_required
def export_submission_count_stats():
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('交表数量统计')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    start_date, end_date, err = _parse_assessment_range(start_date_str, end_date_str)
    if err:
        return jsonify({'success': False, 'message': err}), 400

    user_ids = request.args.getlist('user_ids')
    users = _resolve_assessment_users(session['user_id'], user_ids)
    if not users:
        return jsonify({'success': False, 'message': '无可导出用户'}), 400
    sort_by = request.args.get('sort_by', 'submission')
    if sort_by not in ['submission', 'reward']:
        sort_by = 'submission'
    time_filter_type = _normalize_review_form_time_filter(request.args.get('time_filter_type'))
    payload, payload_err = _build_submission_snapshot_payload(users, start_date, end_date, sort_by, time_filter_type)
    if payload_err:
        return jsonify({'success': False, 'message': payload_err}), 400
    wb = _build_submission_snapshot_workbook(payload)

    default_title = f'{start_date_str}至{end_date_str}交表统计'
    filename = _build_export_filename(request.args.get('filename_title'), default_title)
    return _workbook_response(wb, filename)


@admin_bp.route('/api/review/submission-count/reward-detail/<int:user_id>', methods=['GET'])
@login_required
def get_submission_reward_detail(user_id):
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('交表数量统计')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    start_date, end_date, err = _parse_assessment_range(start_date_str, end_date_str)
    if err:
        return jsonify({'success': False, 'message': err}), 400

    users = _resolve_assessment_users(session['user_id'], [str(user_id)])
    if not users:
        return jsonify({
            'success': False,
            'message': build_forbidden_message(
                '审表考评统计',
                '当前账号没有查看统计成员明细的权限。',
                action='查看',
            ),
        }), 403
    reward_settings, reward_err = _get_teaching_reward_settings()
    if reward_err:
        return jsonify({'success': False, 'message': reward_err}), 400
    reward_window = _compute_teaching_week_window(
        start_date,
        end_date,
        reward_settings['first_week_date'],
        reward_settings['week_start_day']
    )
    row = _build_submission_count_rows(users, start_date, end_date, reward_settings, reward_window)[0]
    return jsonify({
        'success': True,
        'user': {
            'id': users[0].id,
            'name': users[0].name,
            'number': users[0].number,
            'department': users[0].department,
            'group': users[0].group
        },
        'summary': {
            'submission_group_count': row['submission_group_count'],
            'reward_effective_count': row['reward_effective_count'],
            'reward_error_count': row['reward_error_count'],
            'reward_form_count': row['reward_form_count']
        },
        'weeks': row['reward_week_details'],
        'reward_meta': {
            'week_label': reward_window['label'],
            'start_week': reward_window['start_week'],
            'end_week': reward_window['end_week'],
            'week_count': reward_window['week_count'],
            'has_full_weeks': reward_window['has_full_weeks'],
            'required_submission_per_week': reward_settings['required_submission']
        }
    })


@admin_bp.route('/api/review/submission-count/detail/<int:user_id>', methods=['GET'])
@login_required
def get_submission_count_detail(user_id):
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('交表数量统计')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    start_date, end_date, err = _parse_assessment_range(start_date_str, end_date_str)
    if err:
        return jsonify({'success': False, 'message': err}), 400

    users = _resolve_assessment_users(session['user_id'], [str(user_id)])
    if not users:
        return jsonify({
            'success': False,
            'message': build_forbidden_message(
                '交表数量统计',
                '当前账号没有查看统计成员明细的权限。',
                action='查看',
            ),
        }), 403
    time_filter_type = _normalize_review_form_time_filter(request.args.get('time_filter_type'))
    user = users[0]
    if not user.number:
        return jsonify({'success': True, 'user': {'id': user.id, 'name': user.name, 'number': user.number, 'department': user.department, 'group': user.group}, 'groups': []})

    reviewer_display_mode = get_reviewer_display_mode()
    groups = _latest_form_groups_for_users([user.number])
    filtered_groups = []
    reviewer_ids = set()
    for group_data in groups:
        latest_form = group_data['latest_form']
        if not latest_form:
            continue
        filter_dt = _build_review_form_filter_datetime(group_data, time_filter_type)
        if not filter_dt or filter_dt < start_date or filter_dt >= end_date:
            continue
        for form in group_data['forms']:
            if form.reviewer_id:
                reviewer_ids.add(form.reviewer_id)
    reviewer_map = {}
    if reviewer_ids:
        reviewers = User.query.filter(User.id.in_(list(reviewer_ids))).all()
        reviewer_map = {r.id: r for r in reviewers}

    for group_data in groups:
        latest_form = group_data['latest_form']
        if not latest_form or not latest_form.created_at:
            continue
        if latest_form.created_at < start_date or latest_form.created_at >= end_date:
            continue
        forms_data = []
        for form in group_data['forms']:
            reviewer = reviewer_map.get(form.reviewer_id) if form.reviewer_id else None
            if reviewer_display_mode == 'number':
                reviewer_display = reviewer.number if reviewer else '-'
            else:
                reviewer_display = reviewer.name if reviewer else '-'
            forms_data.append({
                'id': form.id,
                'status': form.status,
                'created_at': form.created_at.strftime('%Y-%m-%d %H:%M:%S') if form.created_at else '-',
                'updated_at': form.updated_at.strftime('%Y-%m-%d %H:%M:%S') if form.updated_at else '-',
                'review_comment': form.review_comment or '',
                'reviewer_display': reviewer_display,
                'teacher_name': form.teacher_name or '',
                'course_title': form.course_title or '',
                'lecture_date': form.lecture_date or ''
            })
        filtered_groups.append({
            'unique_id': group_data['unique_id'],
            'latest_created_at': latest_form.created_at.strftime('%Y-%m-%d %H:%M:%S') if latest_form.created_at else '-',
            'latest_updated_at': latest_form.updated_at.strftime('%Y-%m-%d %H:%M:%S') if latest_form.updated_at else '-',
            'filter_datetime': filter_dt.strftime('%Y-%m-%d %H:%M:%S') if filter_dt else '-',
            'teacher_name': latest_form.teacher_name or '',
            'course_title': latest_form.course_title or '',
            'lecture_date': latest_form.lecture_date or '',
            'forms': forms_data
        })

    filtered_groups.sort(key=lambda x: x['latest_created_at'], reverse=True)
    return jsonify({
        'success': True,
        'user': {
            'id': user.id,
            'name': user.name,
            'number': user.number,
            'department': user.department,
            'group': user.group
        },
        'groups': filtered_groups,
        'time_filter_type': time_filter_type
    })


@admin_bp.route('/api/review/submission-count/snapshots', methods=['GET'])
@login_required
def list_submission_count_snapshots():
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('交表统计快照')
    return jsonify({
        'success': True,
        'snapshots': _build_snapshot_list_items(SNAPSHOT_TYPE_SUBMISSION_REWARD, session['user_id'])
    })


@admin_bp.route('/api/review/submission-count/snapshots', methods=['POST'])
@login_required
def save_submission_count_snapshot():
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('交表统计快照')
    data = request.get_json() or {}
    start_date, end_date, err = _parse_assessment_range(data.get('start_date'), data.get('end_date'))
    if err:
        return jsonify({'success': False, 'message': err}), 400
    users = _resolve_assessment_users(session['user_id'], data.get('user_ids') or [])
    if not users:
        return jsonify({'success': False, 'message': '无可保存用户'}), 400
    sort_by = data.get('sort_by', 'submission')
    if sort_by not in ['submission', 'reward']:
        sort_by = 'submission'
    time_filter_type = _normalize_review_form_time_filter(data.get('time_filter_type'))
    payload, payload_err = _build_submission_snapshot_payload(users, start_date, end_date, sort_by, time_filter_type)
    if payload_err:
        return jsonify({'success': False, 'message': payload_err}), 400
    if not payload.get('reward_meta', {}).get('has_full_weeks'):
        return jsonify({'success': False, 'message': '当前筛选范围内无完整教学周，无法保存奖励表历史'}), 400

    filters_data = {
        'start_date': data.get('start_date'),
        'end_date': data.get('end_date'),
        'sort_by': sort_by,
        'time_filter_type': time_filter_type,
        'user_ids': [user.id for user in users],
        'scope_label': f'共 {len(users)} 人',
        'period_label': payload.get('reward_meta', {}).get('week_label', '')
    }
    title = (data.get('title') or '').strip() or f"{data.get('start_date')}至{data.get('end_date')}交表统计"
    record = _create_statistics_snapshot(
        SNAPSHOT_TYPE_SUBMISSION_REWARD,
        title,
        filters_data,
        payload,
        session['user_id']
    )
    return jsonify({
        'success': True,
        'message': '奖励表历史已保存',
        'snapshot': {
            'id': record.id,
            'title': record.title
        }
    })


@admin_bp.route('/api/review/submission-count/snapshots/<int:snapshot_id>', methods=['GET'])
@login_required
def get_submission_count_snapshot(snapshot_id):
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('交表统计快照')
    record, error_response, status_code = _get_snapshot_record_or_404(snapshot_id, SNAPSHOT_TYPE_SUBMISSION_REWARD, session['user_id'])
    if error_response:
        return error_response, status_code
    filters_data, payload_data = _load_snapshot_payload(record)
    return jsonify({
        'success': True,
        'snapshot': {
            'id': record.id,
            'title': record.title,
            'created_at': record.created_at.strftime('%Y-%m-%d %H:%M:%S') if record.created_at else '',
            'filters': filters_data,
            'payload': payload_data
        }
    })


@admin_bp.route('/api/review/submission-count/snapshots/<int:snapshot_id>/export', methods=['GET'])
@login_required
def export_submission_count_snapshot(snapshot_id):
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('交表统计快照')
    record, error_response, status_code = _get_snapshot_record_or_404(snapshot_id, SNAPSHOT_TYPE_SUBMISSION_REWARD, session['user_id'])
    if error_response:
        return error_response, status_code
    _, payload_data = _load_snapshot_payload(record)
    wb = _build_submission_snapshot_workbook(payload_data)
    filename = _build_export_filename(request.args.get('filename_title'), record.title)
    return _workbook_response(wb, filename)


@admin_bp.route('/api/review/department-monthly-assessment/stats', methods=['GET'])
@login_required
def get_department_monthly_assessment_stats():
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('部门月度考评')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    start_date, end_date, err = _parse_assessment_range(start_date_str, end_date_str)
    if err:
        return jsonify({'success': False, 'message': err}), 400
    payload, payload_err = _build_department_monthly_assessment_payload(
        session['user_id'],
        start_date,
        end_date,
        request.args.getlist('departments')
    )
    if payload_err:
        return jsonify({'success': False, 'message': payload_err}), 400
    return jsonify({'success': True, **payload})


@admin_bp.route('/api/review/department-monthly-assessment/export', methods=['GET'])
@login_required
def export_department_monthly_assessment_stats():
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('部门月度考评')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    start_date, end_date, err = _parse_assessment_range(start_date_str, end_date_str)
    if err:
        return jsonify({'success': False, 'message': err}), 400
    payload, payload_err = _build_department_monthly_assessment_payload(
        session['user_id'],
        start_date,
        end_date,
        request.args.getlist('departments')
    )
    if payload_err:
        return jsonify({'success': False, 'message': payload_err}), 400
    if not payload.get('meta', {}).get('has_full_months'):
        return jsonify({'success': False, 'message': '当前筛选范围内无完整教学月，无法导出月度考评'}), 400
    wb = _build_department_monthly_workbook(payload)
    default_title = f'{start_date_str}至{end_date_str}部门月度考评统计'
    filename = _build_export_filename(request.args.get('filename_title'), default_title)
    return _workbook_response(wb, filename)


@admin_bp.route('/api/review/department-monthly-assessment/snapshots', methods=['GET'])
@login_required
def list_department_monthly_assessment_snapshots():
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('部门月度考评快照')
    return jsonify({
        'success': True,
        'snapshots': _build_snapshot_list_items(SNAPSHOT_TYPE_DEPARTMENT_MONTHLY, session['user_id'])
    })


@admin_bp.route('/api/review/department-monthly-assessment/snapshots', methods=['POST'])
@login_required
def save_department_monthly_assessment_snapshot():
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('部门月度考评快照')
    data = request.get_json() or {}
    start_date, end_date, err = _parse_assessment_range(data.get('start_date'), data.get('end_date'))
    if err:
        return jsonify({'success': False, 'message': err}), 400
    departments = data.get('departments') or []
    payload, payload_err = _build_department_monthly_assessment_payload(
        session['user_id'],
        start_date,
        end_date,
        departments
    )
    if payload_err:
        return jsonify({'success': False, 'message': payload_err}), 400
    if not payload.get('meta', {}).get('has_full_months'):
        return jsonify({'success': False, 'message': '当前筛选范围内无完整教学月，无法保存月度考评历史'}), 400
    filters_data = {
        'start_date': data.get('start_date'),
        'end_date': data.get('end_date'),
        'departments': departments,
        'scope_label': payload.get('meta', {}).get('scope_label', ''),
        'period_label': payload.get('meta', {}).get('period_label', '')
    }
    title = (data.get('title') or '').strip() or f"{data.get('start_date')}至{data.get('end_date')}部门月度考评统计"
    record = _create_statistics_snapshot(
        SNAPSHOT_TYPE_DEPARTMENT_MONTHLY,
        title,
        filters_data,
        payload,
        session['user_id']
    )
    return jsonify({
        'success': True,
        'message': '部门月度考评历史已保存',
        'snapshot': {
            'id': record.id,
            'title': record.title
        }
    })


@admin_bp.route('/api/review/department-monthly-assessment/snapshots/<int:snapshot_id>', methods=['GET'])
@login_required
def get_department_monthly_assessment_snapshot(snapshot_id):
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('部门月度考评快照')
    record, error_response, status_code = _get_snapshot_record_or_404(snapshot_id, SNAPSHOT_TYPE_DEPARTMENT_MONTHLY, session['user_id'])
    if error_response:
        return error_response, status_code
    filters_data, payload_data = _load_snapshot_payload(record)
    return jsonify({
        'success': True,
        'snapshot': {
            'id': record.id,
            'title': record.title,
            'created_at': record.created_at.strftime('%Y-%m-%d %H:%M:%S') if record.created_at else '',
            'filters': filters_data,
            'payload': payload_data
        }
    })


@admin_bp.route('/api/review/department-monthly-assessment/snapshots/<int:snapshot_id>/export', methods=['GET'])
@login_required
def export_department_monthly_assessment_snapshot(snapshot_id):
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('部门月度考评快照')
    record, error_response, status_code = _get_snapshot_record_or_404(snapshot_id, SNAPSHOT_TYPE_DEPARTMENT_MONTHLY, session['user_id'])
    if error_response:
        return error_response, status_code
    _, payload_data = _load_snapshot_payload(record)
    wb = _build_department_monthly_workbook(payload_data)
    filename = _build_export_filename(request.args.get('filename_title'), record.title)
    return _workbook_response(wb, filename)


@admin_bp.route('/api/review/assessment/stats', methods=['GET'])
@login_required
def get_review_assessment_stats():
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('审表考评统计')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    start_date, end_date, err = _parse_assessment_range(start_date_str, end_date_str)
    if err:
        return jsonify({'success': False, 'message': err}), 400

    user_ids = request.args.getlist('user_ids')
    users = _resolve_assessment_users(session['user_id'], user_ids)
    if not users:
        return jsonify({'success': True, 'users': []})

    rows = _build_review_assessment_rows(users, start_date, end_date)
    return jsonify({'success': True, 'users': rows})


@admin_bp.route('/api/review/assessment/export', methods=['GET'])
@login_required
def export_review_assessment_stats():
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('审表考评统计')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    start_date, end_date, err = _parse_assessment_range(start_date_str, end_date_str)
    if err:
        return jsonify({'success': False, 'message': err}), 400

    user_ids = request.args.getlist('user_ids')
    users = _resolve_assessment_users(session['user_id'], user_ids)
    if not users:
        return jsonify({'success': False, 'message': '无可导出用户'}), 400

    rows = _build_review_assessment_rows(users, start_date, end_date)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '考评统计'
    headers = ['排名', '姓名', '编号', '部门', '小组', '部门扣分', '个人扣分', '总个人扣分']
    ws.append(headers)
    for row in rows:
        ws.append([
            row['rank'],
            row['name'],
            row['number'],
            row['department'],
            row['group'],
            float(row['linked_department_score'] or 0.0),
            float(row['linked_personal_score'] or 0.0),
            float(row['total_personal_score'] or 0.0)
        ])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    _autosize_worksheet(ws)

    default_title = f'{start_date_str}至{end_date_str}考评统计'
    filename = _build_export_filename(request.args.get('filename_title'), default_title)
    return _workbook_response(wb, filename)


@admin_bp.route('/api/review/assessment/detail/<int:user_id>', methods=['GET'])
@login_required
def get_review_assessment_detail(user_id):
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('审表考评统计')
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    start_date, end_date, err = _parse_assessment_range(start_date_str, end_date_str)
    if err:
        return jsonify({'success': False, 'message': err}), 400

    users = _resolve_assessment_users(session['user_id'], [str(user_id)])
    if not users:
        return jsonify({
            'success': False,
            'message': build_forbidden_message(
                '审表考评统计',
                '当前账号没有查看统计成员明细的权限。',
                action='查看',
            ),
        }), 403
    user = users[0]

    entries = []
    groups = []
    linked_department_total = 0.0
    linked_personal_total = 0.0

    latest_forms = _assessment_latest_forms_in_range([user.number], start_date, end_date)
    for form in latest_forms:
        if not form.score_record:
            continue
        display_time = _assessment_form_latest_timestamp(form)
        group_items = []
        group_department_total = 0.0
        group_personal_total = 0.0
        for item in form.score_record.items:
            dept_score = float(item.department_score or 0.0)
            pers_score = float(item.personal_score or 0.0)
            linked_department_total += dept_score
            linked_personal_total += pers_score
            group_department_total += dept_score
            group_personal_total += pers_score
            item_data = {
                'issue_detail': item.reason or '',
                'department_score': dept_score,
                'personal_score': pers_score
            }
            group_items.append(item_data)
            entries.append({
                'source': '反馈表单',
                'date': display_time.strftime('%Y-%m-%d %H:%M') if display_time else '-',
                'teacher_name': form.teacher_name or '',
                'course_title': form.course_title or '',
                **item_data,
                'sort_time': display_time.isoformat() if display_time else ''
            })
        groups.append({
            'form_id': form.id,
            'unique_id': form.unique_id or '',
            'date': display_time.strftime('%Y-%m-%d %H:%M') if display_time else '-',
            'teacher_name': form.teacher_name or '',
            'course_title': form.course_title or '',
            'total_department_score': group_department_total,
            'total_personal_score': group_personal_total,
            'item_count': len(group_items),
            'items': group_items,
            'sort_time': display_time.isoformat() if display_time else ''
        })

    groups.sort(key=lambda x: x['sort_time'], reverse=True)
    entries.sort(key=lambda x: x['sort_time'], reverse=True)
    for group in groups:
        group.pop('sort_time', None)
    for entry in entries:
        entry.pop('sort_time', None)

    return jsonify({
        'success': True,
        'user': {
            'id': user.id,
            'name': user.name,
            'number': user.number,
            'department': user.department,
            'group': user.group
        },
        'summary': {
            'linked_department_score': linked_department_total,
            'linked_personal_score': linked_personal_total,
            'total_department_score': linked_department_total,
            'total_personal_score': linked_personal_total,
            'total_score': linked_personal_total
        },
        'groups': groups,
        'entries': entries
    })


def _read_manual_assessment_file(file_storage):
    if not file_storage or not file_storage.filename:
        return None, '请先选择导入文件'
    if not allowed_file(file_storage.filename):
        return None, '仅支持xls或xlsx文件'
    try:
        return pd.read_excel(file_storage), None
    except Exception as e:
        return None, f'读取文件失败：{str(e)}'


def _normalize_manual_assessment_items(items):
    normalized_items = []
    for item in items or []:
        if isinstance(item, dict):
            reason = item.get('reason')
            department_score = item.get('department_score')
            personal_score = item.get('personal_score')
        else:
            reason = getattr(item, 'reason', '')
            department_score = getattr(item, 'department_score', 0.0)
            personal_score = getattr(item, 'personal_score', 0.0)
        normalized_items.append({
            'reason': str(reason or '').strip() or '手动导入考评项',
            'department_score': float(department_score or 0.0),
            'personal_score': float(personal_score or 0.0)
        })
    normalized_items.sort(key=lambda item: (item['reason'], item['department_score'], item['personal_score']))
    return normalized_items


def _build_score_snapshot(items, score_record=None):
    normalized_items = _normalize_manual_assessment_items(items)
    if score_record:
        total_department_score = float(score_record.total_department_score or 0.0)
        total_personal_score = float(score_record.total_personal_score or 0.0)
    else:
        total_department_score = sum(item['department_score'] for item in normalized_items)
        total_personal_score = sum(item['personal_score'] for item in normalized_items)
    return {
        'total_department_score': total_department_score,
        'total_personal_score': total_personal_score,
        'items': normalized_items
    }


def _build_existing_score_snapshot(form):
    if not form or not form.score_record:
        return _build_score_snapshot([])
    return _build_score_snapshot(form.score_record.items, score_record=form.score_record)


def _manual_assessment_snapshots_equal(before_snapshot, after_snapshot):
    return (
        float(before_snapshot.get('total_department_score') or 0.0) == float(after_snapshot.get('total_department_score') or 0.0)
        and float(before_snapshot.get('total_personal_score') or 0.0) == float(after_snapshot.get('total_personal_score') or 0.0)
        and before_snapshot.get('items', []) == after_snapshot.get('items', [])
    )


def _parse_manual_assessment_rows(df):
    required_columns = ['表单ID', '考评项', '部门扣分', '个人扣分']
    for col in required_columns:
        if col not in df.columns:
            return None, f'缺少字段：{col}'

    valid_row_count = 0
    skipped_count = 0
    skipped_details = []
    grouped_items = {}
    forms_by_id = {}

    for idx, row in df.iterrows():
        form_id_value = row.get('表单ID')
        reason_value = row.get('考评项')
        dept_value = row.get('部门扣分')
        pers_value = row.get('个人扣分')

        if pd.isna(form_id_value) and pd.isna(reason_value) and pd.isna(dept_value) and pd.isna(pers_value):
            continue

        form_id = _to_int_or_none(form_id_value)
        if not form_id:
            skipped_count += 1
            if len(skipped_details) < 15:
                skipped_details.append(f'第{idx + 2}行：表单ID无效')
            continue

        form = LectureForm.query.get(form_id)
        if not form:
            skipped_count += 1
            if len(skipped_details) < 15:
                skipped_details.append(f'第{idx + 2}行：表单ID {form_id} 不存在')
            continue

        reason = str(reason_value).strip() if not pd.isna(reason_value) else ''
        try:
            department_score = float(dept_value) if not pd.isna(dept_value) else 0.0
            personal_score = float(pers_value) if not pd.isna(pers_value) else 0.0
        except Exception:
            skipped_count += 1
            if len(skipped_details) < 15:
                skipped_details.append(f'第{idx + 2}行：扣分字段格式错误')
            continue

        if not reason and department_score == 0 and personal_score == 0:
            skipped_count += 1
            if len(skipped_details) < 15:
                skipped_details.append(f'第{idx + 2}行：考评项和扣分不能同时为空/0')
            continue

        normalized_item = {
            'reason': reason or '手动导入考评项',
            'department_score': department_score,
            'personal_score': personal_score
        }
        valid_row_count += 1
        forms_by_id[form_id] = form
        grouped_items.setdefault(form_id, []).append(normalized_item)

    return {
        'valid_row_count': valid_row_count,
        'skipped_count': skipped_count,
        'skipped_details': skipped_details,
        'grouped_items': grouped_items,
        'forms_by_id': forms_by_id
    }, None


def _build_manual_assessment_preview(parsed_result):
    changed_forms = []
    changed_grouped_items = {}
    unchanged_form_ids = []
    changed_row_count = 0

    for form_id, items in parsed_result['grouped_items'].items():
        form = parsed_result['forms_by_id'][form_id]
        before_snapshot = _build_existing_score_snapshot(form)
        after_snapshot = _build_score_snapshot(items)
        if _manual_assessment_snapshots_equal(before_snapshot, after_snapshot):
            unchanged_form_ids.append(form_id)
            continue

        changed_grouped_items[form_id] = _normalize_manual_assessment_items(items)
        changed_row_count += len(items)
        changed_forms.append({
            'form_id': form.id,
            'listener_number': form.listener_number or '',
            'teacher_name': form.teacher_name or '',
            'course_title': form.course_title or '',
            'change_type': 'replace' if form.score_record else 'create',
            'before': before_snapshot,
            'after': after_snapshot
        })

    changed_forms.sort(key=lambda item: item['form_id'])
    unchanged_form_ids.sort()
    return {
        'summary': {
            'valid_row_count': parsed_result['valid_row_count'],
            'changed_form_count': len(changed_forms),
            'changed_row_count': changed_row_count,
            'unchanged_form_count': len(unchanged_form_ids),
            'skipped_count': parsed_result['skipped_count']
        },
        'changed_forms': changed_forms,
        'changed_grouped_items': changed_grouped_items,
        'unchanged_form_ids': unchanged_form_ids,
        'skipped_details': parsed_result['skipped_details']
    }


def _apply_manual_assessment_changes(changed_grouped_items, operator_id, import_time):
    imported_row_count = 0
    imported_form_count = 0
    for form_id, items in changed_grouped_items.items():
        old_record = ScoreRecord.query.filter_by(form_id=form_id).first()
        if old_record:
            db.session.delete(old_record)
            db.session.flush()

        score_snapshot = _build_score_snapshot(items)
        score_record = ScoreRecord(
            form_id=form_id,
            reviewer_id=operator_id,
            total_department_score=score_snapshot['total_department_score'],
            total_personal_score=score_snapshot['total_personal_score'],
            created_at=import_time,
            updated_at=import_time
        )
        db.session.add(score_record)
        db.session.flush()

        for item in score_snapshot['items']:
            db.session.add(ScoreItem(
                score_record_id=score_record.id,
                reason=item['reason'],
                department_score=item['department_score'],
                personal_score=item['personal_score'],
                is_auto_generated=False
            ))

        form = LectureForm.query.get(form_id)
        if form:
            form.reviewer_id = operator_id
            form.review_time = import_time
            db.session.add(form)

        imported_row_count += len(score_snapshot['items'])
        imported_form_count += 1

    return imported_row_count, imported_form_count


@admin_bp.route('/api/review/assessment/import/template', methods=['GET'])
@role_required('超级管理员')
def download_manual_assessment_template():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '手动导入考评模板'
    headers = ['表单ID', '考评项', '部门扣分', '个人扣分']
    ws.append(headers)
    ws.append([1001, '课堂秩序管理欠佳', 1, 2])
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name='手动导入考评模板.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@admin_bp.route('/api/review/assessment/import/preview', methods=['POST'])
@role_required('超级管理员')
def preview_manual_assessment_import():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '请先选择导入文件'}), 400

    df, read_error = _read_manual_assessment_file(request.files['file'])
    if read_error:
        return jsonify({'success': False, 'message': read_error}), 400

    parsed_result, parse_error = _parse_manual_assessment_rows(df)
    if parse_error:
        return jsonify({'success': False, 'message': parse_error}), 400

    preview_result = _build_manual_assessment_preview(parsed_result)
    return jsonify({
        'success': True,
        'summary': preview_result['summary'],
        'changed_forms': preview_result['changed_forms'],
        'unchanged_form_ids': preview_result['unchanged_form_ids'],
        'skipped_details': preview_result['skipped_details']
    })


@admin_bp.route('/api/review/assessment/import', methods=['POST'])
@role_required('超级管理员')
def import_manual_assessment():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '请先选择导入文件'}), 400

    df, read_error = _read_manual_assessment_file(request.files['file'])
    if read_error:
        return jsonify({'success': False, 'message': read_error}), 400

    parsed_result, parse_error = _parse_manual_assessment_rows(df)
    if parse_error:
        return jsonify({'success': False, 'message': parse_error}), 400

    preview_result = _build_manual_assessment_preview(parsed_result)
    operator_id = session.get('user_id')
    import_time = datetime.now()
    imported_count, imported_form_count = _apply_manual_assessment_changes(
        preview_result['changed_grouped_items'],
        operator_id,
        import_time
    )

    db.session.commit()
    return jsonify({
        'success': True,
        'imported_count': imported_count,
        'imported_form_count': imported_form_count,
        'valid_row_count': parsed_result['valid_row_count'],
        'unchanged_form_count': len(preview_result['unchanged_form_ids']),
        'unchanged_form_ids': preview_result['unchanged_form_ids'],
        'skipped_count': parsed_result['skipped_count'],
        'skipped_details': parsed_result['skipped_details']
    })


@admin_bp.route('/assessment-exemption-settings')
@role_required('管理员')
def assessment_exemption_settings():
    user = User.query.get(session['user_id'])
    if user.role == '超级管理员':
        return redirect(url_for('admin.system_management', tab='assessment'))
    if not _has_assessment_stats_access(session['user_id']):
        flash_forbidden('考评规则设置')
        return redirect(url_for('main.index'))
    available_departments = list(_get_accessible_department_users(session['user_id']).keys())
    is_super_admin = get_user_manage_permission(session['user_id']) == '超级管理员'
    return render_template(
        'admin/assessment_exemption_settings.html',
        available_departments=available_departments,
        is_super_admin=is_super_admin,
    )


@admin_bp.route('/api/assessment-overrides/list', methods=['GET'])
@login_required
def list_assessment_overrides():
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('考评规则设置')
    department_names = request.args.getlist('departments')
    selected_departments, department_user_map = _resolve_selected_departments(
        session['user_id'],
        department_names,
    )
    if not selected_departments:
        return jsonify({'success': True, 'departments': []})

    all_user_ids = []
    for users in department_user_map.values():
        all_user_ids.extend([u.id for u in users])

    overrides_by_user = {}
    if all_user_ids:
        overrides = AssessmentOverride.query.filter(
            AssessmentOverride.user_id.in_(all_user_ids)
        ).order_by(AssessmentOverride.start_week.asc(), AssessmentOverride.end_week.asc()).all()
        for ov in overrides:
            overrides_by_user.setdefault(ov.user_id, []).append({
                'id': ov.id,
                'override_type': ov.override_type,
                'override_value': ov.override_value,
                'start_week': ov.start_week,
                'end_week': ov.end_week,
                'reason': ov.reason,
                'created_at': ov.created_at.strftime('%Y-%m-%d %H:%M:%S') if ov.created_at else '',
            })

    result_departments = []
    for dept_name in selected_departments:
        users = department_user_map.get(dept_name, [])
        group_map = {}
        for user in users:
            group_name = user.group or UNASSIGNED_GROUP_NAME
            group_map.setdefault(group_name, []).append(user)
        groups = []
        for group_name in sorted(group_map.keys()):
            members = []
            for user in sorted(group_map[group_name], key=lambda u: (u.number or '', u.name or '')):
                members.append({
                    'user_id': user.id,
                    'name': user.name,
                    'number': user.number,
                    'overrides': overrides_by_user.get(user.id, []),
                })
            groups.append({
                'group_name': group_name,
                'members': members,
            })
        result_departments.append({
            'department': dept_name,
            'groups': groups,
        })

    return jsonify({'success': True, 'departments': result_departments})


@admin_bp.route('/api/assessment-overrides', methods=['POST'])
@login_required
def create_assessment_override():
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('考评规则设置')
    data = request.get_json() or {}
    user_ids = data.get('user_ids') or []
    if not user_ids:
        return jsonify({'success': False, 'message': '请选择至少一个成员'}), 400
    try:
        start_week = int(data.get('start_week'))
        end_week = int(data.get('end_week'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': '起止周次必须为整数'}), 400
    if start_week < 1 or end_week < 1:
        return jsonify({'success': False, 'message': '周次必须大于0'}), 400
    if start_week > end_week:
        return jsonify({'success': False, 'message': '起始周不能大于结束周'}), 400
    override_type = (data.get('override_type') or 'exempt').strip()
    if override_type not in ('exempt', 'custom_requirement', 'partial_exempt', LEAVE_OVERRIDE_TYPE):
        return jsonify({'success': False, 'message': '不支持的规则类型'}), 400
    if override_type == LEAVE_OVERRIDE_TYPE and get_user_manage_permission(session['user_id']) != '超级管理员':
        return jsonify({'success': False, 'message': '请假规则请在统计分析页为当前教学周发起'}), 403
    reason = (data.get('reason') or '').strip()
    if not reason:
        return jsonify({'success': False, 'message': '请填写理由'}), 400
    override_value = data.get('override_value')

    accessible_users = _resolve_assessment_users(session['user_id'], [str(uid) for uid in user_ids])
    accessible_ids = {u.id for u in accessible_users}
    if not accessible_ids:
        return jsonify({
            'success': False,
            'message': build_forbidden_message(
                '考评规则设置',
                '当前账号没有操作所选成员规则的权限。',
                action='操作',
            ),
        }), 403

    created_count = 0
    skipped_count = 0
    for uid in user_ids:
        try:
            uid_int = int(uid)
        except (TypeError, ValueError):
            continue
        if uid_int not in accessible_ids:
            continue
        existing = AssessmentOverride.query.filter_by(
            user_id=uid_int,
            start_week=start_week,
            end_week=end_week,
            override_type=override_type,
        ).first()
        if existing:
            skipped_count += 1
            continue
        record = AssessmentOverride(
            user_id=uid_int,
            start_week=start_week,
            end_week=end_week,
            override_type=override_type,
            override_value=override_value,
            reason=reason,
            created_by=session['user_id'],
        )
        db.session.add(record)
        created_count += 1

    db.session.commit()
    message = f'成功添加 {created_count} 条规则'
    if skipped_count > 0:
        message += f'，跳过 {skipped_count} 条已存在的规则'
    return jsonify({
        'success': True,
        'message': message,
        'created_count': created_count,
        'skipped_count': skipped_count,
    })


@admin_bp.route('/api/assessment-overrides/<int:override_id>', methods=['DELETE'])
@login_required
def delete_assessment_override(override_id):
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('考评规则设置')
    record = db.session.get(AssessmentOverride, override_id)
    if not record:
        return jsonify({'success': False, 'message': '规则不存在'}), 404
    manage_permission = get_user_manage_permission(session['user_id'])
    if manage_permission != '超级管理员':
        accessible_users = _resolve_assessment_users(session['user_id'], [str(record.user_id)])
        if not accessible_users:
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '考评规则设置',
                    '当前账号没有删除该规则的权限。',
                    action='删除',
                ),
            }), 403
    db.session.delete(record)
    db.session.commit()
    return jsonify({'success': True, 'message': '规则已删除'})


@admin_bp.route('/api/teaching-month-definitions', methods=['GET'])
@login_required
def get_teaching_month_definitions():
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('教学月设置')
    custom = _load_custom_month_definitions()
    if custom:
        return jsonify({'success': True, 'is_custom': True, 'months': custom})
    defaults = []
    for i in range(5):
        start_week = i * 4 + 1
        end_week = start_week + 3
        defaults.append({
            'start_week': start_week,
            'end_week': end_week,
            'label': f'第{i + 1}教学月',
        })
    return jsonify({'success': True, 'is_custom': False, 'months': defaults})


@admin_bp.route('/api/teaching-month-definitions', methods=['POST'])
@login_required
def save_teaching_month_definitions():
    if not _has_assessment_stats_access(session['user_id']):
        return forbidden_json('教学月设置')
    data = request.get_json() or {}
    months = data.get('months')
    if not months:
        SystemSetting.set('teaching_month_definitions', '')
        return jsonify({'success': True, 'message': '已恢复为默认教学月设置（每4周一个教学月）'})
    if not isinstance(months, list):
        return jsonify({'success': False, 'message': 'months 必须是数组'}), 400

    validated = []
    for idx, item in enumerate(months):
        try:
            start_week = int(item.get('start_week'))
            end_week = int(item.get('end_week'))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'message': f'第{idx + 1}项的周次必须为整数'}), 400
        if start_week < 1 or end_week < 1:
            return jsonify({'success': False, 'message': f'第{idx + 1}项的周次必须大于0'}), 400
        if start_week > end_week:
            return jsonify({
                'success': False,
                'message': f'第{idx + 1}项的起始周({start_week})不能大于结束周({end_week})',
            }), 400
        label = str(item.get('label') or '').strip()
        validated.append({'start_week': start_week, 'end_week': end_week, 'label': label})

    SystemSetting.set('teaching_month_definitions', json.dumps(validated, ensure_ascii=False))
    return jsonify({'success': True, 'message': f'已保存自定义教学月设置（{len(validated)}个教学月）'})


