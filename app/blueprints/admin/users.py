# -*- coding: utf-8 -*-
# Phase 1 mechanical split from app/blueprints/admin.py
# Module: users

from flask import render_template, request, redirect, url_for, flash, session, jsonify
from app.models import User, Department, Group, LectureForm, Permission, RolePermission, Course, CourseRegistration, db, SystemSetting, ScoreRecord, ScoreItem, PersonnelMovementRecord
from sqlalchemy import func
from datetime import datetime, timedelta
from app.blueprints.auth import role_required
from app.utils.review_permissions import get_user_review_permission
from app.utils.permission_feedback import build_forbidden_message, flash_forbidden
from app.utils.manage_permissions import get_user_manage_permission
from app.utils.password_audit import record_password_audit
from app.utils.audit_tags import parse_audit_tag
from app.utils.leave_management import parse_lecture_date_value
from app.utils.user_status import UNASSIGNED_DEPARTMENT_NAME, UNASSIGNED_GROUP_NAME, is_user_active
from werkzeug.security import generate_password_hash, check_password_hash
import json
from . import admin_bp
from .shared import _active_user_query, _create_personnel_movement_record, _serialize_user_basic, _snapshot_user_for_movement, generate_random_password


def _can_view_managed_user(current_user, target_user, manage_permission):
    if not current_user or not target_user or not manage_permission:
        return False
    if manage_permission == '超级管理员':
        return True
    if target_user.department != current_user.department:
        return False
    if manage_permission == '管理部门':
        return target_user.role != '超级管理员'
    if manage_permission == '管理部门小组':
        current_group = current_user.group_id or current_user.group
        target_group = target_user.group_id or target_user.group
        return current_group == target_group
    return False


def _build_user_profile_stats(user):
    stats = None
    if not user or user.role not in ['信息员', '管理员', '超级管理员']:
        return stats

    first_week_setting = SystemSetting.query.filter_by(key='teaching_first_week_monday').first()
    if first_week_setting and first_week_setting.value:
        try:
            first_week_date = datetime.strptime(first_week_setting.value, '%Y-%m-%d').date()
        except ValueError:
            first_week_date = None
    else:
        first_week_date = None

    week_start_day_setting = SystemSetting.query.filter_by(key='teaching_week_start_day').first()
    week_start_day = int(week_start_day_setting.value) if week_start_day_setting and week_start_day_setting.value else 0

    required_submission_setting = SystemSetting.query.filter_by(key='teaching_required_submission').first()
    required_submission = int(required_submission_setting.value) if required_submission_setting and required_submission_setting.value else 1
    required_listening = required_submission

    check_dept_setting = SystemSetting.query.filter_by(key='teaching_check_dept_review').first()
    check_dept = check_dept_setting.value == 'true' if check_dept_setting else False

    check_center_setting = SystemSetting.query.filter_by(key='teaching_check_center_review').first()
    check_center = check_center_setting.value == 'true' if check_center_setting else False

    def get_week_num(date_obj):
        if not first_week_date or not date_obj:
            return -1
        current_date = date_obj.date() if isinstance(date_obj, datetime) else date_obj
        days_to_subtract = (first_week_date.weekday() - week_start_day) % 7
        actual_start_date = first_week_date - timedelta(days=days_to_subtract)
        diff = (current_date - actual_start_date).days
        if diff < 0:
            return -1
        return (diff // 7) + 1

    def get_form_group_week_num(versions):
        if not versions:
            return -1
        latest_version = versions[-1]
        parsed_tag = parse_audit_tag(latest_version.audit_tag)
        if parsed_tag.get('week_correction_week_no') is not None:
            return parsed_tag['week_correction_week_no']
        return get_week_num(parse_lecture_date_value(latest_version.lecture_date))

    def count_feedback_chars(form):
        if not form:
            return 0
        text = f"{form.course_feedback or ''}{form.suggestions or ''}"
        return len(''.join(str(text).split()))

    current_week_num = None
    current_week_label = '当前不在教学周内'
    if first_week_date:
        resolved_week_num = get_week_num(datetime.now().date())
        if resolved_week_num > 0:
            current_week_num = resolved_week_num
            current_week_label = f'当前教学周（第 {resolved_week_num} 周）'
        else:
            current_week_label = '本学期教学周尚未开始'

    all_forms = LectureForm.query.filter_by(listener_number=user.number).order_by(LectureForm.created_at.asc()).all()
    form_groups = {}
    for form in all_forms:
        uid = form.unique_id or form.id
        form_groups.setdefault(uid, []).append(form)

    total_submitted_count = len(form_groups)
    total_approved_count = 0
    total_reward_count = 0
    total_feedback_chars = 0
    week_submitted_count = 0
    week_approved_count = 0
    weekly_error_free_counts = {}

    for versions in form_groups.values():
        versions.sort(key=lambda item: item.id)
        submit_week = get_form_group_week_num(versions)

        if submit_week == current_week_num:
            week_submitted_count += 1

        approved_versions = [version for version in versions if version.status == '中心已审核']
        approved_form = approved_versions[-1] if approved_versions else None
        if approved_form:
            total_approved_count += 1
            total_feedback_chars += count_feedback_chars(approved_form)
            if submit_week == current_week_num:
                week_approved_count += 1

        has_dept_review = False
        dept_review_ok = True
        has_center_review = False
        center_review_ok = True

        for version in versions:
            if version.status == '部门已审核':
                has_dept_review = True
                score_record = ScoreRecord.query.filter_by(form_id=version.id).first()
                if score_record:
                    auto_items = ScoreItem.query.filter_by(score_record_id=score_record.id, is_auto_generated=True).count()
                    if auto_items > 0:
                        dept_review_ok = False

            if version.status == '中心已审核':
                has_center_review = True
                score_record = ScoreRecord.query.filter_by(form_id=version.id).first()
                if score_record:
                    auto_items = ScoreItem.query.filter_by(score_record_id=score_record.id, is_auto_generated=True).count()
                    if auto_items > 0:
                        center_review_ok = False

        criteria_met = True
        if check_dept and (not has_dept_review or not dept_review_ok):
            criteria_met = False
        if check_center and (not has_center_review or not center_review_ok):
            criteria_met = False
        if not check_dept and not check_center:
            criteria_met = False

        if criteria_met and submit_week > 0:
            weekly_error_free_counts[submit_week] = weekly_error_free_counts.get(submit_week, 0) + 1

    week_error_free_count = weekly_error_free_counts.get(current_week_num, 0)
    week_reward_count = max(0, week_error_free_count - required_listening)
    for count in weekly_error_free_counts.values():
        total_reward_count += max(0, count - required_listening)

    days_since_last = '无'
    last_form_obj = LectureForm.query.filter_by(listener_number=user.number).order_by(LectureForm.created_at.desc()).first()
    if last_form_obj and last_form_obj.created_at:
        days_since_last = (datetime.now() - last_form_obj.created_at).days

    this_month_start = datetime(datetime.now().year, datetime.now().month, 1)
    this_month = 0
    for versions in form_groups.values():
        if versions and versions[0].created_at and versions[0].created_at >= this_month_start:
            this_month += 1

    last_submit = last_form_obj.created_at.strftime('%Y-%m-%d') if last_form_obj and last_form_obj.created_at else '无'
    average_feedback_chars = total_feedback_chars / total_approved_count if total_approved_count else 0

    user_form_ids = [form.id for form in all_forms]
    if user_form_ids:
        total_deduction = db.session.query(func.sum(ScoreRecord.total_personal_score)).filter(
            ScoreRecord.form_id.in_(user_form_ids)
        ).scalar() or 0.0
    else:
        total_deduction = 0.0

    assessment_items = []
    if user_form_ids:
        item_rows = db.session.query(ScoreItem, ScoreRecord, LectureForm)\
            .join(ScoreRecord, ScoreItem.score_record_id == ScoreRecord.id)\
            .join(LectureForm, ScoreRecord.form_id == LectureForm.id)\
            .filter(
                LectureForm.listener_number == user.number,
                db.or_(ScoreItem.personal_score > 0, ScoreItem.department_score > 0)
            ).all()

        for score_item, score_record, form in item_rows:
            assessment_time = (
                score_record.updated_at
                or score_record.created_at
                or score_item.created_at
                or form.review_time
                or form.updated_at
                or form.created_at
            )
            personal_score = float(score_item.personal_score or 0.0)
            department_score = float(score_item.department_score or 0.0)
            assessment_items.append({
                'reason': score_item.reason or '未填写考评原因',
                'personal_score': personal_score,
                'department_score': department_score,
                'score_sort': personal_score,
                'assessment_time': assessment_time,
                'assessment_timestamp': assessment_time.timestamp() if assessment_time else 0,
                'assessment_time_text': assessment_time.strftime('%Y-%m-%d %H:%M') if assessment_time else '-',
                'course_title': form.course_title or '-',
                'teacher_name': form.teacher_name or '-',
                'form_id': form.id
            })

    assessment_items.sort(
        key=lambda item: (
            item['score_sort'],
            item['department_score'],
            item['assessment_timestamp'],
            item['form_id']
        ),
        reverse=True
    )

    results = db.session.query(
        LectureForm.listener_number,
        func.sum(ScoreRecord.total_personal_score).label('total')
    ).join(ScoreRecord, LectureForm.id == ScoreRecord.form_id).group_by(LectureForm.listener_number).all()

    scores_map = {row[0]: float(row[1] or 0.0) for row in results}
    all_scores = list(scores_map.values())
    all_scores.sort(reverse=True)

    has_evaluation_sample = user.number in scores_map
    user_score = scores_map.get(user.number)
    percentile = None
    if has_evaluation_sample and all_scores:
        try:
            rank_index = all_scores.index(user_score)
            percentile = (rank_index + 1) / len(all_scores)
        except ValueError:
            percentile = None

    if percentile is None:
        rating_label = '暂无评级'
    elif percentile <= 0.2:
        rating_label = '需要继续努力'
    elif percentile <= 0.7:
        rating_label = '表现良好'
    else:
        rating_label = '行为很好'

    numeric_deduction = float(total_deduction or 0.0)
    deduction_display = '0.00' if abs(numeric_deduction) < 0.005 else f'-{abs(numeric_deduction):.2f}'

    stats = {
        'total_forms': total_submitted_count,
        'this_month': this_month,
        'last_submit': last_submit,
        'total_deduction': total_deduction,
        'deduction_display': deduction_display,
        'has_evaluation_sample': has_evaluation_sample,
        'rating_label': rating_label,
        'percentile': percentile,
        'current_week': current_week_num,
        'current_week_label': current_week_label,
        'week_submitted': week_submitted_count,
        'week_approved': week_approved_count,
        'week_reward': week_reward_count,
        'total_submitted': total_submitted_count,
        'total_approved': total_approved_count,
        'total_reward': total_reward_count,
        'average_feedback_chars': average_feedback_chars,
        'total_feedback_chars': total_feedback_chars,
        'days_since_last': days_since_last,
        'assessment_items': assessment_items
    }
    return stats


def _build_user_form_groups(user, search='', date_from='', date_to=''):
    query = LectureForm.query.filter_by(listener_number=user.number)

    if search:
        query = query.filter(
            db.or_(
                LectureForm.course_title.contains(search),
                LectureForm.teacher_name.contains(search),
                LectureForm.lecture_location.contains(search)
            )
        )

    if date_from:
        query = query.filter(LectureForm.lecture_date >= date_from)
    if date_to:
        query = query.filter(LectureForm.lecture_date <= date_to)

    all_forms = query.order_by(LectureForm.updated_at.desc(), LectureForm.created_at.desc(), LectureForm.id.desc()).all()
    grouped_forms = {}
    for form in all_forms:
        unique_key = form.unique_id if form.unique_id else f"single_{form.id}"
        grouped_forms.setdefault(unique_key, []).append(form)

    form_groups = []
    for unique_id, versions in grouped_forms.items():
        versions.sort(key=lambda item: item.updated_at or item.created_at, reverse=True)
        form_groups.append({
            'unique_id': unique_id,
            'latest_form': versions[0],
            'versions': versions,
            'version_count': len(versions)
        })

    form_groups.sort(
        key=lambda item: item['latest_form'].updated_at or item['latest_form'].created_at,
        reverse=True
    )
    return form_groups


def _build_user_reservations(user, search='', date_from='', date_to=''):
    query = CourseRegistration.query.filter_by(user_id=user.id)

    if date_from:
        start_dt = datetime.strptime(date_from, '%Y-%m-%d')
        query = query.filter(CourseRegistration.created_at >= start_dt)
    if date_to:
        end_dt = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
        query = query.filter(CourseRegistration.created_at < end_dt)

    registrations = query.order_by(CourseRegistration.created_at.desc()).all()
    reservations = []
    normalized_search = (search or '').strip().lower()

    for registration in registrations:
        course = Course.query.filter_by(
            course_code=registration.course_code,
            selection_code=registration.selection_code
        ).first()
        bind_count = LectureForm.query.filter_by(registration_id=registration.id).count()
        item = {
            'id': registration.id,
            'course_code': registration.course_code,
            'selection_code': registration.selection_code,
            'course_name': course.course_name if course else '课程已删除',
            'teacher_name': course.teacher.name if course and course.teacher else '未知',
            'class_time': course.class_time if course else '',
            'class_location': course.class_location if course else '',
            'listening_info': registration.listening_info,
            'created_at': registration.created_at,
            'is_bound': bind_count > 0,
            'bind_count': bind_count,
            'can_edit': False,
            'can_delete': False
        }

        if normalized_search:
            haystack = ' '.join([
                item['course_name'] or '',
                item['teacher_name'] or '',
                item['course_code'] or '',
                item['selection_code'] or '',
                item['listening_info'] or ''
            ]).lower()
            if normalized_search not in haystack:
                continue

        reservations.append(item)

    return reservations


@admin_bp.route('/users/<int:user_id>/view')
@role_required('管理员')
def view_managed_user(user_id):
    current_user = User.query.get(session['user_id'])
    manage_permission = get_user_manage_permission(current_user.id)
    if not manage_permission:
        flash_forbidden('成员资料', '该成员不在当前管理范围内。', action='查看')
        return redirect(url_for('admin.manage_departments'))

    target_user = User.query.get_or_404(user_id)
    if not is_user_active(target_user):
        flash('该用户已离任', 'error')
        return redirect(url_for('admin.manage_departments'))
    if not _can_view_managed_user(current_user, target_user, manage_permission):
        flash_forbidden('成员资料', '该成员不在当前管理范围内。', action='查看')
        return redirect(url_for('admin.manage_departments'))

    search = request.args.get('search', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()

    detail_context = {
        'stats': _build_user_profile_stats(target_user),
        'forms': _build_user_form_groups(target_user, search=search, date_from=date_from, date_to=date_to),
        'my_reservations': _build_user_reservations(target_user, search=search, date_from=date_from, date_to=date_to),
        'search': search,
        'date_from': date_from,
        'date_to': date_to,
    }
    if request.args.get('format') == 'fragment':
        return render_template(
            'admin/_user_detail_panel.html',
            user=target_user,
            target_user=target_user,
            current_user=current_user,
            is_fragment=True,
            **detail_context
        )
    return render_template(
        'admin/user_detail.html',
        user=target_user,
        target_user=target_user,
        current_user=current_user,
        is_fragment=False,
        **detail_context
    )


@admin_bp.route('/api/users', methods=['GET'])
@role_required('管理员')
def get_users():
    """获取用户（支持按部门筛选）"""
    try:
        current_user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(current_user.id)
        
        if not manage_permission:
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '人员与部门',
                    '当前账号没有查看人员的权限。',
                    action='查看',
                ),
            }), 403
            
        department = request.args.get('department')
        
        query = _active_user_query()
        
        # 权限过滤
        if manage_permission == '超级管理员':
            if department:
                query = query.filter_by(department=department)
        elif manage_permission == '管理部门':
            # 只能查看本部门
            query = query.filter_by(department=current_user.department)
        elif manage_permission == '管理部门小组':
            # 只能查看本小组
            if current_user.group:
                # 假设user.group存储的是小组名称
                query = query.filter_by(department=current_user.department, group=current_user.group)
            else:
                # 未分配小组的用户无法看到任何用户，或者只能看到自己？这里暂且返回空
                return jsonify({'success': True, 'data': []})
        
        users = query.all()
        
        users_data = [_serialize_user_basic(user) for user in users]
        
        return jsonify({'success': True, 'data': users_data})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/users', methods=['POST'])
@role_required('管理员')
def add_user():
    """添加新用户"""
    try:
        current_user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(current_user.id)
        
        # 只有超级管理员和具有"管理部门"权限的管理员可以添加用户
        if manage_permission != '超级管理员' and manage_permission != '管理部门':
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '人员与部门',
                     '当前账号没有添加人员的权限。',
                     action='添加',
                 ),
             }), 403
        
        data = request.get_json()
        
        # 必填字段验证
        name = data.get('name', '').strip()
        department = data.get('department', '').strip()
        gender = data.get('gender', '').strip()
        grade = data.get('grade', '').strip()
        college = data.get('college', '').strip()
        major = data.get('major', '').strip()
        dormitory = data.get('dormitory', '').strip()
        phone = data.get('phone', '').strip()
        qq = data.get('qq', '').strip()
        student_id = data.get('student_id', '').strip()
        role = data.get('role', '').strip()
        
        # 权限检查：非超级管理员只能添加本部门用户
        if manage_permission != '超级管理员':
            if department != current_user.department:
                return jsonify({
                    'success': False,
                    'message': build_forbidden_message(
                        '人员与部门',
                        '当前账号没有在当前管理范围内添加人员的权限。',
                        action='添加',
                    ),
                }), 403
            # 非超级管理员不能添加超级管理员
            if role == '超级管理员':
                return jsonify({
                    'success': False,
                    'message': build_forbidden_message(
                        '人员与部门',
                        '当前账号没有创建该人员的权限。',
                        action='添加',
                    ),
                }), 403
        
        # 检查所有必填字段
        required_fields = {
            'name': name,
            'department': department,
            'gender': gender,
            'grade': grade,
            'college': college,
            'major': major,
            'dormitory': dormitory,
            'phone': phone,
            'qq': qq,
            'student_id': student_id,
            'role': role
        }
        
        missing_fields = []
        for field_name, field_value in required_fields.items():
            if not field_value:
                field_labels = {
                    'name': '姓名',
                    'department': '部门',
                    'gender': '性别',
                    'grade': '年级',
                    'college': '学院',
                    'major': '专业',
                    'dormitory': '宿舍',
                    'phone': '手机号码',
                    'qq': 'QQ号码',
                    'student_id': '学号',
                    'role': '角色'
                }
                missing_fields.append(field_labels.get(field_name, field_name))
        
        if missing_fields:
            return jsonify({'success': False, 'message': f'以下字段为必填项：{", ".join(missing_fields)}'}), 400
        
        # 检查学号是否存在
        if User.query.filter_by(student_id=student_id).first():
            return jsonify({'success': False, 'message': '该学号已存在'}), 400
            
        # 自动生成编号：获取当前最大编号并加1
        max_user = User.query.order_by(User.number.desc()).first()
        if max_user and max_user.number.isdigit():
            number = str(int(max_user.number) + 1).zfill(4)  # 4位数字，不足补0
        else:
            number = "0001"  # 如果没有用户或编号不是数字，从0001开始
        
        # 检查部门是否存在
        dept = Department.query.filter_by(name=department).first()
        if not dept and department != UNASSIGNED_DEPARTMENT_NAME:
            return jsonify({'success': False, 'message': '指定的部门不存在'}), 400
        
        # 获取可选字段
        password_field = data.get('password', '').strip()  # 用户可能设置自定义密码
        group_id = data.get('group_id')
        group_name = None
        
        # 如果指定了小组，检查小组是否存在且属于指定部门
        if group_id:
            try:
                group_id = int(group_id)
            except ValueError:
                return jsonify({'success': False, 'message': '小组ID格式不正确'}), 400
            
            if group_id:
                group = Group.query.get(group_id)
                if not group:
                    return jsonify({'success': False, 'message': '指定的小组不存在'}), 400
                if group.department != department:
                    return jsonify({'success': False, 'message': '小组不属于指定的部门'}), 400
                group_name = group.name
        else:
            # 如果没有指定小组，设置为未分配小组
            group_id = None
            group_name = UNASSIGNED_GROUP_NAME
        
        # 生成密码：如果用户提供了密码就使用用户的密码，否则生成随机密码
        if password_field:
            password = password_field
        else:
            password = generate_random_password()
        password_hash = generate_password_hash(password)
        
        new_user = User(
            number=number,
            name=name,
            gender=gender,
            grade=grade,
            college=college,
            major=major,
            dormitory=dormitory,
            phone=phone,
            qq=qq,
            student_id=student_id,
            password_hash=password_hash,
            role=role,
            department=department,
            group_id=group_id,
            group=group_name # 设置 group 字段
        )
        
        db.session.add(new_user)
        db.session.flush()
        record_password_audit(
            actor_user_id=session.get('user_id'),
            target_user_id=new_user.id,
            action='admin_create_user_password_init',
            details={'custom_password': bool(password_field)}
        )
        db.session.commit()
        
        return jsonify({'success': True, 'message': '用户添加成功', 'data': {
            'id': new_user.id,
            'number': new_user.number,
            'name': new_user.name,
            'password': password  # 返回明文密码供管理员记录
        }})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/users/<int:user_id>', methods=['GET'])
@role_required('管理员')
def get_user(user_id):
    """获取单个用户信息"""
    try:
        current_user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(current_user.id)
        
        # 检查权限
        if not manage_permission:
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '人员与部门',
                    '当前账号没有查看人员的权限。',
                    action='查看',
                ),
            }), 403
            
        user = User.query.get_or_404(user_id)
        if not is_user_active(user):
            return jsonify({'success': False, 'message': '用户已离任'}), 404
        
        # 权限范围检查
        if manage_permission != '超级管理员':
            if user.department != current_user.department:
                return jsonify({
                    'success': False,
                    'message': build_forbidden_message(
                        '人员与部门',
                        '当前账号没有查看该人员的权限。',
                        action='查看',
                    ),
                }), 403
            if manage_permission == '管理部门小组':
                # 假设user.group存储的是小组名称
                if user.group != current_user.group:
                    return jsonify({
                        'success': False,
                        'message': build_forbidden_message(
                            '人员与部门',
                            '当前账号没有查看该人员的权限。',
                            action='查看',
                        ),
                    }), 403
        
        return jsonify({'success': True, 'data': _serialize_user_basic(user)})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/users/<int:user_id>', methods=['PUT'])
@role_required('管理员')
def update_user(user_id):
    """更新用户信息"""
    try:
        current_user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(current_user.id)
        
        # 只有超级管理员和具有"管理部门"权限的管理员可以修改用户信息
        # "管理部门小组"权限不能修改用户信息
        if manage_permission != '超级管理员' and manage_permission != '管理部门':
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '人员与部门',
                     '当前账号没有修改人员信息的权限。',
                     action='修改',
                 ),
             }), 403
             
        user = User.query.get_or_404(user_id)
        if not is_user_active(user):
            return jsonify({'success': False, 'message': '用户已离任'}), 404
        
        # 如果不是超级管理员，只能修改本部门用户
        if manage_permission != '超级管理员' and user.department != current_user.department:
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '人员与部门',
                     '当前账号没有修改该人员的权限。',
                     action='修改',
                 ),
             }), 403
             
        # "管理部门"权限不能修改超级管理员的信息
        if manage_permission != '超级管理员' and user.role == '超级管理员':
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '人员与部门',
                     '当前账号没有修改该人员信息的权限。',
                     action='修改',
                 ),
             }), 403
             
        data = request.get_json()
        before_snapshot = _snapshot_user_for_movement(user)
        password_changed = False
        if 'new_password' not in data and 'password' in data:
            data['new_password'] = data.get('password')
        
        # "管理部门"权限的限制：只能修改小组和密码，不能修改角色、部门等敏感信息
        if manage_permission == '管理部门':
            # 允许修改的字段：小组
            if 'group_id' in data:
                group_id = data['group_id']
                if group_id:
                    group = Group.query.get(group_id)
                    if not group:
                        return jsonify({'success': False, 'message': '指定的小组不存在'}), 400
                    if group.department != user.department:
                        return jsonify({'success': False, 'message': '小组不属于用户所在部门'}), 400
                    user.group_id = group_id
                    user.group = group.name # 同步更新名称
                else:
                    user.group_id = None
                    user.group = UNASSIGNED_GROUP_NAME # 同步更新名称

            if 'new_password' in data and data['new_password']:
                user.password_hash = generate_password_hash(data['new_password'])
                password_changed = True
            
            # 检查是否尝试修改受限字段
            restricted_fields = ['name', 'department', 'role', 'student_id']
            for field in restricted_fields:
                if field in data and data[field]:
                    # 如果尝试修改且值确实改变了
                    if field == 'department' and data[field] != user.department:
                         return jsonify({
                             'success': False,
                             'message': build_forbidden_message(
                                 '人员与部门',
                                 '当前账号没有修改人员部门的权限。',
                                 action='修改',
                             ),
                         }), 403
                    elif field == 'role' and data[field] != user.role:
                         return jsonify({
                             'success': False,
                             'message': build_forbidden_message(
                                 '人员与部门',
                                 '当前账号没有修改人员角色的权限。',
                                 action='修改',
                             ),
                         }), 403
                    # 其他字段暂不严格限制报错，只是忽略
        else:
            # 超级管理员可以修改所有字段
            
            # 基本信息更新
            if 'name' in data: user.name = data['name'].strip()
            if 'gender' in data: user.gender = data['gender'].strip()
            if 'grade' in data: user.grade = data['grade'].strip()
            if 'college' in data: user.college = data['college'].strip()
            if 'major' in data: user.major = data['major'].strip()
            if 'dormitory' in data: user.dormitory = data['dormitory'].strip()
            if 'phone' in data: user.phone = data['phone'].strip()
            if 'qq' in data: user.qq = data['qq'].strip()
            if 'student_id' in data: user.student_id = data['student_id'].strip()
            if 'role' in data: user.role = data['role'].strip()
            
            # 部门更新
            if 'department' in data:
                new_department = data['department'].strip()
                if new_department != user.department:
                    # 检查新部门是否存在
                    dept = Department.query.filter_by(name=new_department).first()
                    if not dept and new_department != UNASSIGNED_DEPARTMENT_NAME:
                        return jsonify({'success': False, 'message': '指定的部门不存在'}), 400
                    user.department = new_department
                    # 如果更换部门，清除小组关联
                    user.group_id = None
                    user.group = UNASSIGNED_GROUP_NAME
            
            # 小组更新
            if 'group_id' in data:
                group_id = data['group_id']
                if group_id:
                    group = Group.query.get(group_id)
                    if not group:
                        return jsonify({'success': False, 'message': '指定的小组不存在'}), 400
                    if group.department != user.department:
                        return jsonify({'success': False, 'message': '小组不属于用户所在部门'}), 400
                    user.group_id = group_id
                    user.group = group.name
                else:
                    user.group_id = None
                    user.group = UNASSIGNED_GROUP_NAME
            
            # 密码更新
            if 'new_password' in data and data['new_password']:
                user.password_hash = generate_password_hash(data['new_password'])
                password_changed = True
        
        after_snapshot = _snapshot_user_for_movement(user)
        movement_changes = []
        if before_snapshot.get('department') != after_snapshot.get('department'):
            movement_changes.append({
                'field': 'department',
                'label': '部门',
                'before': before_snapshot.get('department') or '未设置',
                'after': after_snapshot.get('department') or '未设置'
            })
        if before_snapshot.get('group') != after_snapshot.get('group'):
            movement_changes.append({
                'field': 'group',
                'label': '小组',
                'before': before_snapshot.get('group') or UNASSIGNED_GROUP_NAME,
                'after': after_snapshot.get('group') or UNASSIGNED_GROUP_NAME
            })

        profile_field_labels = {
            'name': '姓名',
            'gender': '性别',
            'grade': '年级',
            'college': '学院',
            'major': '专业',
            'dormitory': '宿舍',
            'phone': '电话',
            'qq': 'QQ',
            'student_id': '学号',
            'role': '角色'
        }
        profile_changes = []
        for field, label in profile_field_labels.items():
            if before_snapshot.get(field) != after_snapshot.get(field):
                profile_changes.append({
                    'field': field,
                    'label': label,
                    'before': before_snapshot.get(field) or '',
                    'after': after_snapshot.get(field) or ''
                })

        if movement_changes:
            movement_snapshot = dict(after_snapshot)
            movement_snapshot['department_before'] = before_snapshot.get('department')
            movement_snapshot['department_after'] = after_snapshot.get('department')
            movement_snapshot['group_before'] = before_snapshot.get('group')
            movement_snapshot['group_after'] = after_snapshot.get('group')
            _create_personnel_movement_record(
                current_user.id,
                movement_snapshot,
                '部门/小组变动',
                f"{after_snapshot.get('name')}（{after_snapshot.get('number')}）发生部门/小组变动",
                movement_changes
            )

        if profile_changes:
            _create_personnel_movement_record(
                current_user.id,
                after_snapshot,
                '个人资料修改',
                f"{after_snapshot.get('name')}（{after_snapshot.get('number')}）修改了个人资料",
                profile_changes
            )

        if password_changed:
            record_password_audit(
                actor_user_id=current_user.id,
                target_user_id=user.id,
                action='admin_reset_user_password',
                details={'manage_permission': manage_permission}
            )
        
        db.session.commit()
        
        return jsonify({'success': True, 'message': '用户信息更新成功'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/users/<int:user_id>/depart', methods=['POST'])
@role_required('管理员')
def depart_user(user_id):
    """用户离任：软删除账号，保留历史记录。"""
    try:
        current_user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(current_user.id)

        if manage_permission != '超级管理员' and manage_permission != '管理部门':
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '人员与部门',
                    '当前账号没有办理人员离任的权限。',
                    action='办理离任',
                ),
            }), 403

        user_to_depart = User.query.get_or_404(user_id)
        if user_to_depart.id == current_user.id:
            return jsonify({'success': False, 'message': '不能将当前登录账号设为离任'}), 400
        if user_to_depart.role == '超级管理员':
            return jsonify({'success': False, 'message': '超级管理员不能办理离任，请先调整角色或使用超级管理员删除'}), 400
        if manage_permission != '超级管理员' and user_to_depart.department != current_user.department:
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '人员与部门',
                    '当前账号没有办理该人员离任的权限。',
                    action='办理离任',
                ),
            }), 403

        data = request.get_json() or {}
        password = data.get('password')
        if not password or not check_password_hash(current_user.password_hash, password):
            return jsonify({'success': False, 'message': '密码验证失败，无法办理离任'}), 403

        if not is_user_active(user_to_depart):
            return jsonify({'success': True, 'message': '该用户已处于离任状态'})

        before_snapshot = _snapshot_user_for_movement(user_to_depart)
        user_to_depart.is_active = False
        user_to_depart.department = '离任'
        user_to_depart.group_id = None
        user_to_depart.group = '离任'
        RolePermission.query.filter_by(role=f'特殊角色_{user_to_depart.id}').delete()
        after_snapshot = _snapshot_user_for_movement(user_to_depart)

        _create_personnel_movement_record(
            current_user.id,
            {
                **after_snapshot,
                'department_before': before_snapshot.get('department'),
                'department_after': after_snapshot.get('department'),
                'group_before': before_snapshot.get('group'),
                'group_after': after_snapshot.get('group'),
            },
            '用户离任',
            f"{before_snapshot.get('name')}（{before_snapshot.get('number')}）已离任",
            [{
                'field': 'is_active',
                'label': '状态',
                'before': '在任',
                'after': '离任'
            }, {
                'field': 'department',
                'label': '部门',
                'before': before_snapshot.get('department') or '未设置',
                'after': '离任'
            }, {
                'field': 'group',
                'label': '小组',
                'before': before_snapshot.get('group') or UNASSIGNED_GROUP_NAME,
                'after': '离任'
            }]
        )

        db.session.commit()
        return jsonify({'success': True, 'message': '用户已离任，后续不再参与活动或页面展示'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/users/<int:user_id>', methods=['DELETE'])
@role_required('管理员')
def delete_user(user_id):
    """删除用户（及相关权限）"""
    try:
        current_user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(current_user.id)
        
        # 只有超级管理员保留物理删除用户的能力
        if manage_permission != '超级管理员':
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '人员与部门',
                     '当前账号没有直接删除人员的权限，请使用离任流程。',
                     action='删除',
                 ),
             }), 403
             
        user_to_delete = User.query.get_or_404(user_id)
        
        if user_to_delete.id == current_user.id:
             return jsonify({'success': False, 'message': '不能删除当前登录账号'}), 400
        
        # 验证密码
        data = request.get_json() or {}
        password = data.get('password')
        if not password or not check_password_hash(current_user.password_hash, password):
            return jsonify({'success': False, 'message': '密码验证失败，无法删除用户'}), 403
        
        # 检查是否有相关的听课表单
        forms_count = LectureForm.query.filter_by(listener_number=user_to_delete.number).count()
        if forms_count > 0:
            return jsonify({'success': False, 'message': f'无法删除用户，该用户有 {forms_count} 条听课记录'}), 400
        
        delete_snapshot = _snapshot_user_for_movement(user_to_delete)

        # 删除相关的特殊角色权限
        RolePermission.query.filter_by(role=f'特殊角色_{user_to_delete.id}').delete()

        _create_personnel_movement_record(
            current_user.id,
            delete_snapshot,
            '删除用户',
            f"删除用户 {delete_snapshot.get('name')}（{delete_snapshot.get('number')}）",
            [{
                'field': 'delete',
                'label': '删除',
                'before': f"{delete_snapshot.get('department') or '未设置'} / {delete_snapshot.get('group') or UNASSIGNED_GROUP_NAME}",
                'after': '用户已删除'
            }]
        )
        
        db.session.delete(user_to_delete)
        db.session.commit()
        
        return jsonify({'success': True, 'message': '用户及关联权限已删除'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/personnel-movement-records', methods=['GET'])
@role_required('管理员')
def list_personnel_movement_records():
    try:
        current_user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(current_user.id)
        if not manage_permission:
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '人员流动记录',
                    '当前账号没有查看人员流动记录的权限。',
                    action='查看',
                ),
            }), 403

        query = PersonnelMovementRecord.query
        if manage_permission != '超级管理员':
            query = query.filter(PersonnelMovementRecord.department_name == current_user.department)

        limit = request.args.get('limit', 100)
        try:
            limit = max(1, min(int(limit), 300))
        except Exception:
            limit = 100

        records = query.order_by(
            PersonnelMovementRecord.created_at.desc(),
            PersonnelMovementRecord.id.desc()
        ).limit(limit).all()

        operator_ids = {record.operator_user_id for record in records if record.operator_user_id}
        operator_map = {
            user.id: user
            for user in User.query.filter(User.id.in_(list(operator_ids))).all()
        } if operator_ids else {}

        result = []
        for record in records:
            try:
                details = json.loads(record.details_json) if record.details_json else {}
            except Exception:
                details = {}
            operator = operator_map.get(record.operator_user_id)
            result.append({
                'id': record.id,
                'target_user_id': record.target_user_id,
                'target_user_number': record.target_user_number,
                'target_user_name': record.target_user_name,
                'department_name': record.department_name,
                'group_name': record.group_name,
                'action_type': record.action_type,
                'summary': record.summary,
                'details': details.get('changes', []),
                'operator_name': operator.name if operator else '',
                'operator_number': operator.number if operator else '',
                'created_at': record.created_at.strftime('%Y-%m-%d %H:%M:%S') if record.created_at else ''
            })
        return jsonify({'success': True, 'records': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/permissions', methods=['GET'])
@role_required('超级管理员')
def get_permissions():
    """获取所有权限列表"""
    try:
        permissions = Permission.query.all()
        result = []
        for perm in permissions:
            result.append({
                'id': perm.id,
                'name': perm.name,
                'description': perm.description
            })
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/users/<int:user_id>/permissions', methods=['GET'])
@role_required('管理员')
def get_user_permissions(user_id):
    """获取用户的权限列表"""
    try:
        current_user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(current_user.id)
        
        # 只有超级管理员和具有"管理部门"权限的管理员可以查看权限
        # "管理部门小组"权限不能管理用户权限
        if manage_permission != '超级管理员' and manage_permission != '管理部门':
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '权限管理',
                     '当前账号没有查看权限配置的权限。',
                     action='查看',
                 ),
             }), 403
             
        user = User.query.get_or_404(user_id)
        if not is_user_active(user):
            return jsonify({'success': False, 'message': '用户已离任'}), 404
        
        # 如果不是超级管理员，只能查看本部门用户的权限
        if manage_permission != '超级管理员' and user.department != current_user.department:
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '权限管理',
                     '当前账号没有查看该人员权限配置的权限。',
                     action='查看',
                 ),
             }), 403
        
        # 获取所有权限
        all_permissions = Permission.query.all()
        permissions = []
        for perm in all_permissions:
            permissions.append({
                'id': perm.id,
                'name': perm.name,
                'description': perm.description
            })
        
        # 获取用户当前拥有的权限ID列表
        user_permissions = []
        # 首先检查是否有自定义权限（使用特殊角色标识符存储）
        custom_role_permissions = db.session.query(Permission).join(RolePermission).filter(
            RolePermission.role == f'特殊角色_{user.id}'
        ).all()
        
        if custom_role_permissions:
            # 有自定义权限，使用自定义权限
            user_permissions = [perm.id for perm in custom_role_permissions]
        else:
            # 没有自定义权限，查询角色默认权限
            role_permissions = db.session.query(Permission).join(RolePermission).filter(
                RolePermission.role == user.role
            ).all()
            user_permissions = [perm.id for perm in role_permissions]
        
        return jsonify({
            'success': True, 
            'permissions': permissions,
            'user_permissions': user_permissions
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/users/<int:user_id>/permissions', methods=['PUT'])
@role_required('管理员')
def update_user_permissions(user_id):
    """更新用户权限（自动设置为管理员）"""
    try:
        current_user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(current_user.id)
        
        # 只有超级管理员和具有"管理部门"权限的管理员可以修改权限
        if manage_permission != '超级管理员' and manage_permission != '管理部门':
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '权限管理',
                     '当前账号没有修改权限配置的权限。',
                     action='修改',
                 ),
             }), 403
             
        user = User.query.get_or_404(user_id)
        if not is_user_active(user):
            return jsonify({'success': False, 'message': '用户已离任'}), 404
        
        # 如果不是超级管理员，只能修改本部门用户的权限
        if manage_permission != '超级管理员' and user.department != current_user.department:
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '权限管理',
                     '当前账号没有修改该人员权限配置的权限。',
                     action='修改',
                 ),
             }), 403
             
        # "管理部门"权限不能修改超级管理员的权限
        if manage_permission != '超级管理员' and user.role == '超级管理员':
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '权限管理',
                     '当前账号没有修改该人员权限配置的权限。',
                     action='修改',
                 ),
             }), 403
        
        data = request.get_json()
        permission_ids = data.get('permission_ids', [])
        
        # 非超级管理员只能授予自身拥有的、且不高于自身级别的权限。
        if manage_permission != '超级管理员':
            current_review = get_user_review_permission(current_user.id)
            review_rank = {'审表_小组': 1, '审表_部门': 2, '审表_中心': 3}
            current_review_rank = review_rank.get(current_review, 0)
            for perm_id in permission_ids:
                permission = Permission.query.get(perm_id)
                if permission is None:
                    continue
                name = permission.name
                if name in ('管理部门', '超级管理员'):
                    return jsonify({
                        'success': False,
                        'message': build_forbidden_message(
                            '权限管理',
                            '不能授予高于自身级别或系统保留的管理权限。',
                            action='授予',
                        ),
                    }), 403
                if name == '管理部门小组':
                    # 管理部门级管理员可以下放小组管理权；已通过上方管理权限门槛。
                    continue
                if name in review_rank:
                    if current_review_rank < review_rank[name]:
                        return jsonify({
                            'success': False,
                            'message': build_forbidden_message(
                                '权限管理',
                                '不能授予高于自身的审核权限。',
                                action='授予',
                            ),
                        }), 403
                    continue
                if name != '填表':
                    # 拒绝未知/自定义权限名绕过的可能性。
                    return jsonify({
                        'success': False,
                        'message': build_forbidden_message(
                            '权限管理',
                            '无权授予该权限。',
                            action='授予',
                        ),
                    }), 403

        # 删除用户现有的自定义权限（如果有特殊角色权限）
        RolePermission.query.filter_by(role=f'特殊角色_{user.id}').delete()
        
        # 添加新的权限
        for perm_id in permission_ids:
            permission = Permission.query.get(perm_id)
            if permission:
                role_perm = RolePermission(role=f'特殊角色_{user.id}', permission_id=perm_id)
                db.session.add(role_perm)
        
        # 更新用户角色为管理员
        if user.role != '超级管理员':
            user.role = '管理员'
        
        db.session.commit()
        
        return jsonify({'success': True, 'message': '用户权限更新成功，角色已设置为管理员'})
    except Exception as e:
        print(f"DEBUG: 发生错误: {str(e)}")
        print(f"DEBUG: 错误类型: {type(e)}")
        import traceback
        print(f"DEBUG: 错误堆栈: {traceback.format_exc()}")
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


