# -*- coding: utf-8 -*-
# Phase 1 mechanical split from app/blueprints/admin.py
# Module: users

from flask import render_template, request, redirect, url_for, flash, session, jsonify
from app.models import User, Department, Group, LectureForm, Permission, RolePermission, Course, CourseRegistration, db, PersonnelMovementRecord
from datetime import datetime, timedelta
from app.security import role_required
from app.utils.review_permissions import get_user_review_permission
from app.utils.permission_feedback import build_forbidden_message, flash_forbidden
from app.utils.manage_permissions import get_user_manage_permission
from app.utils.submission_permissions import (
    set_submission_permission_revoked,
    submission_permission_revoked,
)
from app.utils.password_audit import record_password_audit
from app.utils.user_status import UNASSIGNED_DEPARTMENT_NAME, UNASSIGNED_GROUP_NAME, is_user_active
from app.services.form_bindings import get_registration_logical_form_counts
from app.services.profile_stats import build_user_profile_stats
from app.services.organization_membership import (
    assign_user_to_group,
    canonical_group_user_criteria,
    clear_user_group,
    is_user_in_canonical_group_scope,
)
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
        # 授权只认 canonical group_id；legacy group 文本不参与判定，无归属时 fail closed。
        return is_user_in_canonical_group_scope(current_user, target_user)
    return False


def _build_user_profile_stats(user):
    """Compatibility wrapper: delegate to the canonical profile-stats service.

    Existing tests patch ``app.blueprints.admin.users.datetime``; keep the
    ``datetime.now()`` call site in this module so frozen-time tests keep
    working without touching the whole test suite.
    """
    return build_user_profile_stats(user, now=datetime.now())


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
    logical_bind_counts = get_registration_logical_form_counts(
        [registration.id for registration in registrations]
    )

    for registration in registrations:
        course = Course.query.filter_by(
            course_code=registration.course_code,
            selection_code=registration.selection_code
        ).first()
        bind_count = logical_bind_counts.get(registration.id, 0)
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
            # 只能查看本小组：授权只认 canonical group_id，无归属时 fail closed 返回空。
            query = query.filter(canonical_group_user_criteria(current_user))
        
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
                # 授权只认 canonical group_id，legacy group 文本不参与判定。
                if not is_user_in_canonical_group_scope(current_user, user):
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
                    assign_user_to_group(user, group)
                else:
                    clear_user_group(user)

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
                    clear_user_group(user)
            
            # 小组更新
            if 'group_id' in data:
                group_id = data['group_id']
                if group_id:
                    group = Group.query.get(group_id)
                    if not group:
                        return jsonify({'success': False, 'message': '指定的小组不存在'}), 400
                    if group.department != user.department:
                        return jsonify({'success': False, 'message': '小组不属于用户所在部门'}), 400
                    assign_user_to_group(user, group)
                else:
                    clear_user_group(user)
            
            # 密码更新
            if 'new_password' in data and data['new_password']:
                user.password_hash = generate_password_hash(data['new_password'])
                password_changed = True
        
        if before_snapshot.get('role') != user.role:
            # Role transitions do not silently carry a personal submission grant.
            fill_permission = Permission.query.filter_by(name='填表').first()
            if fill_permission:
                RolePermission.query.filter_by(
                    role=f'特殊角色_{user.id}', permission_id=fill_permission.id,
                ).delete()
            set_submission_permission_revoked(user.id, True)

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

        # Count logical forms by newest physical version, never historical timestamps.
        latest_forms = {}
        for form in LectureForm.query.filter_by(listener_number=user_to_depart.number).order_by(LectureForm.id.desc()).all():
            latest_forms.setdefault(form.logical_id, form)
        outstanding_count = sum(
            form.status in ('待审核', '部门已审核')
            for form in latest_forms.values()
        )
        if outstanding_count:
            return jsonify({
                'success': False,
                'code': 'outstanding_reviews',
                'message': f'该用户还有{outstanding_count}份未结听课表，请先在审核队列完成审核或驳回，再办理离任。',
                'outstanding_count': outstanding_count,
                'action': {'label': '处理未结听课表', 'url': url_for('admin.review_forms')},
            }), 409

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
        set_submission_permission_revoked(user_to_delete.id, False)

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

        if submission_permission_revoked(user.id):
            fill_ids = {perm.id for perm in all_permissions if perm.name == '填表'}
            user_permissions = [perm_id for perm_id in user_permissions if perm_id not in fill_ids]
        
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
        has_submission_permission = False
        for perm_id in permission_ids:
            permission = Permission.query.get(perm_id)
            if permission:
                role_perm = RolePermission(role=f'特殊角色_{user.id}', permission_id=perm_id)
                db.session.add(role_perm)
                has_submission_permission = has_submission_permission or permission.name == '填表'

        # An empty personal set falls back to role defaults, so revocation is explicit.
        set_submission_permission_revoked(user.id, not has_submission_permission)
        
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


