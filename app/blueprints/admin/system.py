# -*- coding: utf-8 -*-
# Phase 1 mechanical split from app/blueprints/admin.py
# Module: system

from flask import render_template, request, redirect, url_for, session, jsonify, send_file
from app.models import User, Department, Group, LectureForm, Teacher, Venue, Course, ListeningBan, db, SystemSetting
from datetime import datetime
from app.utils.auto_review import AutoReviewEngine, SETTING_KEY_SEMESTER_MONDAY, SETTING_KEY_SCHEDULE_PATH, SETTING_KEY_CONTACTS_PATH, SETTING_KEY_FEEDBACK_PATH
from app.blueprints.auth import role_required
from app.utils.profile_settings import PROFILE_EDITABLE_FIELD_OPTIONS, SETTING_KEY_PROFILE_EDITABLE_FIELDS, get_profile_editable_fields, normalize_profile_editable_fields
from app.utils.course_registration_limits import SETTING_KEY_COURSE_WEEKLY_LIMIT_COUNT, SETTING_KEY_COURSE_WEEKLY_LIMIT_ENABLED, get_course_weekly_limit_settings, normalize_course_weekly_limit_count
from app.utils.env_config import env_path
import os
from werkzeug.security import check_password_hash
import json
from . import admin_bp
from .shared import _get_accessible_department_users, allowed_file


DEFAULT_AUTO_REVIEW_REPORT_DIR = os.path.join('data', 'storage', 'exports', 'auto_review')


DEFAULT_AUTO_REVIEW_UPLOAD_DIR = os.path.join('data', 'storage', 'uploads', 'auto_review')


def _coerce_teaching_int(value):
    """Return int(value) for non-bool numeric-looking values, or None."""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_teaching_settings_int(setting, default, lower=None, upper=None):
    """Fail-safe parsing for legacy/invalid teaching settings rows."""
    if not setting or setting.value is None or setting.value == '':
        return default
    parsed = _coerce_teaching_int(setting.value)
    if parsed is None:
        return default
    if lower is not None and parsed < lower:
        return default
    if upper is not None and parsed > upper:
        return default
    return parsed


@admin_bp.route('/auto_review')
@role_required('超级管理员')
def auto_review_page():
    """自动审核旧入口，兼容书签并定位到设置中心。"""
    return redirect(url_for('admin.system_management', tab='automation'))


@admin_bp.route('/api/auto_review/settings', methods=['GET', 'POST'])
@role_required('超级管理员')
def auto_review_settings():
    """自动审核基础设置：获取/设置第一周星期一，并检查文件存在"""
    if request.method == 'GET':
        engine = AutoReviewEngine()
        return jsonify({'success': True, 'status': engine.files_status()})

    data = request.get_json() or {}
    monday = data.get('semester_monday')
    if not monday:
        return jsonify({'success': False, 'message': '缺少第一周星期一日期（YYYY-MM-DD）'}), 400
    try:
        datetime.strptime(monday, '%Y-%m-%d')
    except Exception:
        return jsonify({'success': False, 'message': '日期格式错误，应为YYYY-MM-DD'}), 400
    SystemSetting.set(SETTING_KEY_SEMESTER_MONDAY, monday)
    engine = AutoReviewEngine()
    return jsonify({'success': True, 'status': engine.files_status()})


@admin_bp.route('/api/auto_review/upload', methods=['POST'])
@role_required('超级管理员')
def auto_review_upload():
    """上传/替换课表、通讯录或反馈文件，并保存路径到系统设置"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '未选择文件'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': '未选择文件'}), 400
    file_type = request.form.get('file_type') or request.form.get('type')
    if file_type not in ('schedule', 'contacts', 'feedback'):
        return jsonify({'success': False, 'message': '缺少或错误的文件类型（schedule/contacts/feedback）'}), 400
    if not allowed_file(file.filename):
        return jsonify({'success': False, 'message': '文件格式不支持，请上传.xls或.xlsx文件'}), 400

    upload_dir = env_path('AUTO_REVIEW_UPLOAD_DIR', DEFAULT_AUTO_REVIEW_UPLOAD_DIR)
    os.makedirs(upload_dir, exist_ok=True)

    ext = file.filename.rsplit('.', 1)[1].lower()
    prefix = 'schedule' if file_type == 'schedule' else ('contacts' if file_type == 'contacts' else 'feedback')
    save_name = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
    save_path = os.path.join(upload_dir, save_name)
    file.save(save_path)

    if file_type == 'schedule':
        SystemSetting.set(SETTING_KEY_SCHEDULE_PATH, save_path)
    elif file_type == 'contacts':
        SystemSetting.set(SETTING_KEY_CONTACTS_PATH, save_path)
    else:
        SystemSetting.set(SETTING_KEY_FEEDBACK_PATH, save_path)

    engine = AutoReviewEngine()
    return jsonify({'success': True, 'message': '上传成功', 'path': save_path, 'status': engine.files_status()})


@admin_bp.route('/api/auto_review/feedback_run', methods=['POST'])
@role_required('超级管理员')
def auto_review_feedback_run():
    """执行反馈文件审核，并可导出报告"""
    data = request.get_json() or {}
    export = data.get('export', True)

    engine = AutoReviewEngine()
    result = engine.review_feedback()

    report_path = None
    if export:
        report_path = engine.export_report(result)

    download_url = None
    if report_path:
        basename = os.path.basename(report_path)
        download_url = url_for('admin.auto_review_download', filename=basename)

    return jsonify({'success': True, 'data': result, 'report_path': report_path, 'download_url': download_url})


@admin_bp.route('/auto_review/download')
@role_required('超级管理员')
def auto_review_download():
    """下载自动审核报告"""
    filename = request.args.get('filename')
    if not filename:
        return jsonify({'success': False, 'message': '缺少文件名'}), 400
    reports_dir = env_path('AUTO_REVIEW_REPORT_DIR', DEFAULT_AUTO_REVIEW_REPORT_DIR)
    real_path = os.path.join(reports_dir, os.path.basename(filename))
    if not os.path.exists(real_path):
        return jsonify({'success': False, 'message': '报告不存在'}), 404
    return send_file(real_path, as_attachment=True)


@admin_bp.route('/api/settings/teaching', methods=['GET'])
@role_required('超级管理员')
def get_teaching_settings():
    """获取听课制度设置"""
    try:
        settings = {
            'first_week_monday': SystemSetting.query.filter_by(key='teaching_first_week_monday').first(),
            'week_start_day': SystemSetting.query.filter_by(key='teaching_week_start_day').first(),
            'total_weeks': SystemSetting.query.filter_by(key='teaching_total_weeks').first(),
            'required_submission_count': SystemSetting.query.filter_by(key='teaching_required_submission').first(),
            'check_dept_review': SystemSetting.query.filter_by(key='teaching_check_dept_review').first(),
            'check_center_review': SystemSetting.query.filter_by(key='teaching_check_center_review').first(),
            'show_auto_review_details': SystemSetting.query.filter_by(
                key='teaching_show_auto_review_details'
            ).first(),
            'enable_typos_check': SystemSetting.query.filter_by(key='teaching_enable_typos_check').first(),
            'reviewer_display_mode': SystemSetting.query.filter_by(key='teaching_reviewer_display_mode').first(),
        }
        profile_editable_fields = get_profile_editable_fields()
        course_weekly_limit = get_course_weekly_limit_settings()

        first_week_raw = settings['first_week_monday'].value if settings['first_week_monday'] else None
        first_week_value = None
        if first_week_raw:
            try:
                datetime.strptime(str(first_week_raw).strip(), '%Y-%m-%d')
                first_week_value = first_week_raw
            except (TypeError, ValueError):
                first_week_value = None

        week_start_day = _safe_teaching_settings_int(
            settings['week_start_day'], default=0, lower=0, upper=6
        )
        total_weeks = _safe_teaching_settings_int(
            settings['total_weeks'], default=20, lower=1, upper=52
        )
        required_submission = _safe_teaching_settings_int(
            settings['required_submission_count'], default=1, lower=0
        )

        data = {
            'first_week_monday': first_week_value,
            'week_start_day': week_start_day,
            'total_weeks': total_weeks,
            'required_submission_count': required_submission,
            'required_listening_count': required_submission,
            'check_dept_review': settings['check_dept_review'].value == 'true'
            if settings['check_dept_review']
            else False,
            'check_center_review': settings['check_center_review'].value == 'true'
            if settings['check_center_review']
            else False,
            'show_auto_review_details': settings['show_auto_review_details'].value == 'true'
            if settings['show_auto_review_details']
            else True,
            'enable_typos_check': settings['enable_typos_check'].value == 'true'
            if settings['enable_typos_check']
            else True,
            'reviewer_display_mode': (
                settings['reviewer_display_mode'].value
                if settings['reviewer_display_mode']
                else 'name'
            ),
            'profile_editable_fields': profile_editable_fields,
            'profile_editable_field_options': PROFILE_EDITABLE_FIELD_OPTIONS,
            'course_weekly_limit_enabled': course_weekly_limit['enabled'],
            'course_weekly_limit_count': course_weekly_limit['limit_count'],
        }

        return jsonify({'success': True, 'data': data})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500


@admin_bp.route('/api/settings/teaching', methods=['POST'])
@role_required('超级管理员')
def update_teaching_settings():
    """更新听课制度设置"""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({
                'success': False,
                'message': '请求数据格式错误',
            }), 400

        required_fields = ['first_week_monday', 'total_weeks', 'required_submission_count']
        for field in required_fields:
            if field not in data:
                return jsonify({'success': False, 'message': f'缺少必填字段: {field}'}), 400

        first_week_raw = str(data['first_week_monday'] or '').strip()
        if not first_week_raw:
            return jsonify({'success': False, 'message': '第一周基准周一日期格式错误，应为YYYY-MM-DD'}), 400
        try:
            datetime.strptime(first_week_raw, '%Y-%m-%d')
        except (TypeError, ValueError):
            return jsonify({'success': False, 'message': '第一周基准周一日期格式错误，应为YYYY-MM-DD'}), 400

        week_start_raw = data.get('week_start_day', 0)
        week_start_day = _coerce_teaching_int(week_start_raw)
        if week_start_day is None or week_start_day < 0 or week_start_day > 6:
            return jsonify({
                'success': False,
                'message': '教学周起始日必须是0到6之间的整数',
            }), 400

        total_weeks_raw = data['total_weeks']
        total_weeks = _coerce_teaching_int(total_weeks_raw)
        if total_weeks is None or total_weeks < 1 or total_weeks > 52:
            return jsonify({
                'success': False,
                'message': '总教学周数必须是1到52之间的整数',
            }), 400

        required_submission_raw = data['required_submission_count']
        required_submission = _coerce_teaching_int(required_submission_raw)
        if required_submission is None or required_submission < 0:
            return jsonify({
                'success': False,
                'message': '需交表数必须是大于等于0的整数',
            }), 400

        settings_map = {
            'teaching_first_week_monday': first_week_raw,
            'teaching_week_start_day': str(week_start_day),
            'teaching_total_weeks': str(total_weeks),
            'teaching_required_submission': str(required_submission),
            'teaching_check_dept_review': 'true' if data.get('check_dept_review') else 'false',
            'teaching_check_center_review': 'true' if data.get('check_center_review') else 'false',
            'teaching_show_auto_review_details': 'true'
            if data.get('show_auto_review_details')
            else 'false',
            'teaching_enable_typos_check': 'true' if data.get('enable_typos_check') else 'false',
            'teaching_reviewer_display_mode': data.get('reviewer_display_mode', 'name')
            if data.get('reviewer_display_mode', 'name') in ['name', 'number']
            else 'name',
            SETTING_KEY_COURSE_WEEKLY_LIMIT_ENABLED: 'true'
            if data.get('course_weekly_limit_enabled')
            else 'false',
            SETTING_KEY_COURSE_WEEKLY_LIMIT_COUNT: str(
                normalize_course_weekly_limit_count(data.get('course_weekly_limit_count'))
            ),
            SETTING_KEY_PROFILE_EDITABLE_FIELDS: json.dumps(
                normalize_profile_editable_fields(data.get('profile_editable_fields')),
                ensure_ascii=False,
            ),
        }

        for key, value in settings_map.items():
            setting = SystemSetting.query.filter_by(key=key).first()
            if not setting:
                setting = SystemSetting(key=key, value=value)
                db.session.add(setting)
            else:
                setting.value = value

        db.session.commit()
        return jsonify({'success': True, 'message': '设置已更新'})
    except Exception as exc:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(exc)}), 500


@admin_bp.route('/super_admin_dashboard')
@role_required('超级管理员')
def super_admin_dashboard():
    """超级管理员仪表板"""
    return redirect(url_for('main.index'))


@admin_bp.route('/admin_dashboard')
@role_required('管理员')
def admin_dashboard():
    """管理员仪表板"""
    return redirect(url_for('main.index'))


@admin_bp.route('/api/clear_table', methods=['POST'])
@role_required('超级管理员')
def clear_table():
    """清空指定数据表"""
    try:
        table_name = request.form.get('table_name')
        password = request.form.get('password')
        
        # 验证参数
        if not table_name or not password:
            return jsonify({'success': False, 'message': '参数不完整'})
        
        # 验证密码（验证当前用户密码）
        current_user = User.query.get(session['user_id'])
        if not current_user or not check_password_hash(current_user.password_hash, password):
            return jsonify({'success': False, 'message': '密码错误，无法执行清空操作'})
        
        # 定义允许清空的表及其对应的模型
        allowed_tables = {
            'courses': Course,
            'teachers': Teacher,
            'venues': Venue,
            'lecture_forms': LectureForm,
            'listening_bans': ListeningBan,
            'users': User,
            'departments': Department,
            'groups': Group
        }
        
        if table_name not in allowed_tables:
            return jsonify({'success': False, 'message': f'不允许清空表：{table_name}'})
        
        # 获取对应的模型类
        model_class = allowed_tables[table_name]
        
        # 统计删除前的记录数
        count_before = model_class.query.count()
        
        # 执行清空操作
        if table_name == 'users':
            # 用户表特殊处理：保留超级管理员账户
            deleted_count = model_class.query.filter(model_class.role != '超级管理员').delete()
        else:
            # 其他表直接清空
            deleted_count = model_class.query.delete()
        
        # 提交更改
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'成功清空 {table_name} 表',
            'deleted_count': deleted_count,
            'table_name': table_name
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'清空失败：{str(e)}'})


@admin_bp.route('/system_management')
@role_required('超级管理员')
def system_management():
    """统一系统设置中心。"""
    active_tab = request.args.get('tab', 'teaching')
    if active_tab not in {'teaching', 'assessment', 'automation', 'imports'}:
        active_tab = 'teaching'
    user = User.query.get(session['user_id'])
    return render_template(
        'admin/system_management.html',
        active_tab=active_tab,
        status=AutoReviewEngine().files_status(),
        available_departments=list(_get_accessible_department_users(user.id).keys()),
        account_username=user.student_id,
        is_super_admin=True,
    )


