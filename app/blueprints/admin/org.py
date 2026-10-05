# -*- coding: utf-8 -*-
# Phase 1 mechanical split from app/blueprints/admin.py
# Module: org

from flask import render_template, request, redirect, url_for, session, jsonify
from app.models import User, Department, Group, LectureForm, db, ScoreRecord
from sqlalchemy import func
from app.security import role_required
from app.utils.permission_feedback import build_forbidden_message, flash_forbidden
from app.utils.manage_permissions import get_user_manage_permission
from app.utils.user_status import UNASSIGNED_DEPARTMENT_NAME, UNASSIGNED_GROUP_NAME, is_user_active
from app.services.organization_membership import (
    assign_user_to_group,
    canonical_scope_group_criteria,
    clear_user_group,
    group_member_criteria,
    group_member_criteria_for_identity,
    is_group_in_canonical_scope,
)
from app.services.organization_policy import (
    can_assign_group_leader,
    can_batch_move_group_members,
    can_create_group,
    leader_assignment_may_migrate_department,
)
from werkzeug.security import check_password_hash
from . import admin_bp
from .shared import _active_user_query, _create_personnel_movement_record, _serialize_user_basic, _set_user_unassigned, _snapshot_user_for_movement


def _positive_group_capacity(value):
    """Accept the integer string emitted by the existing FormData UI."""
    if isinstance(value, str) and value.isascii() and value.isdigit():
        value = int(value)
    if type(value) is int and value > 0:
        return value
    return None


def _build_group_payload(group, users, *, name=None, department=None, description=None, max_members=None, is_virtual=False):
    group_name = name if name is not None else group.name
    group_department = department if department is not None else group.department
    payload = {
        'id': None if is_virtual else group.id,
        'name': group_name,
        'department': group_department,
        'leader': None if is_virtual else group.leader,
        'description': description if description is not None else (None if is_virtual else group.description),
        'max_members': max_members if max_members is not None else ('无限制' if is_virtual else group.max_members),
        'member_count': len(users),
        'current_members': len(users),
        'members': [],
        'users': [],
        'is_virtual': is_virtual,
    }
    for user in users:
        user_data = _serialize_user_basic(user)
        payload['members'].append(user_data)
        payload['users'].append(user_data)
    return payload


@admin_bp.route('/manage_departments')
@role_required('管理员')
def manage_departments():
    """部门管理页面"""
    user = User.query.get(session['user_id'])
    
    # 检查用户是否有管理权限
    manage_permission = get_user_manage_permission(user.id)
    
    if not manage_permission:
        flash_forbidden('人员与部门')
        if user.role == '管理员':
            return redirect(url_for('admin.admin_dashboard'))
        else:
            return redirect(url_for('admin.super_admin_dashboard'))
            
    return render_template('admin/manage_departments.html', 
                          manage_permission=manage_permission,
                          current_user_dept=user.department)


@admin_bp.route('/manage_groups')
@role_required('管理员')
def manage_groups():
    """小组管理"""
    user = User.query.get(session['user_id'])
    manage_permission = get_user_manage_permission(user.id)
    
    # 检查用户是否有管理权限（管理部门或管理部门小组）
    if not manage_permission and manage_permission != '超级管理员':
         flash_forbidden('部门与小组')
         return redirect(url_for('admin.admin_dashboard'))
    
    # 确定要管理的小组范围
    if manage_permission == '超级管理员':
        groups = Group.query.all()
    elif manage_permission == '管理部门':
        # 管理员只能管理本部门的小组
        groups = Group.query.filter_by(department=user.department).all()
        # 如果通过department name关联
        if not groups and user.department:
            dept = Department.query.filter_by(name=user.department).first()
            if dept:
                # Group表使用department名称关联，不是ID
                groups = Group.query.filter_by(department=dept.name).all()
    elif manage_permission == '管理部门小组':
        # 只能管理本小组：canonical group_id 判定，无归属时 fail closed。
        groups = Group.query.filter(canonical_scope_group_criteria(user)).all()
    else:
        groups = []

    return render_template('admin/manage_groups.html', groups=groups, user=user, manage_permission=manage_permission)


@admin_bp.route('/api/departments', methods=['GET'])
@role_required('管理员')
def get_departments():
    """获取所有部门及其小组和用户信息"""
    user = User.query.get(session['user_id'])
    manage_permission = get_user_manage_permission(user.id)
    
    if not manage_permission:
        return jsonify({
            'success': False,
            'message': build_forbidden_message(
                '人员与部门',
                '当前账号没有查看部门的权限。',
                action='查看',
            ),
        }), 403
        
    try:
        # 确定要查询的部门范围
        if manage_permission == '超级管理员':
            departments = Department.query.all()
        else:
            departments = Department.query.filter_by(name=user.department).all()
        has_real_unassigned_department = any(dept.name == UNASSIGNED_DEPARTMENT_NAME for dept in departments)
            
        result = []
        
        for dept in departments:
            groups = Group.query.filter_by(department=dept.name).all()
            
            # 如果是"管理部门小组"权限，只显示自己所在的小组
            # （授权只认 canonical group_id，legacy group 文本不参与判定）
            if manage_permission == '管理部门小组':
                groups = [g for g in groups if is_group_in_canonical_scope(user, g)]
            
            # 计算部门总用户数
            total_users = _active_user_query().filter_by(department=dept.name).count()
            if dept.name == UNASSIGNED_DEPARTMENT_NAME and total_users == 0:
                continue
            
            # 计算部门评价信息 (仅超级管理员和部门管理员可见，小组管理员不可见)
            total_deduction = 0.0
            member_deductions = []
            
            if manage_permission in ['超级管理员', '管理部门']:
                # 1. 获取部门内所有用户
                dept_users = _active_user_query().filter_by(department=dept.name).all()
                dept_user_numbers = [u.number for u in dept_users]
                
                if dept_user_numbers:
                    # 2. 获取这些用户的所有评分记录
                    forms = LectureForm.query.filter(LectureForm.listener_number.in_(dept_user_numbers)).with_entities(LectureForm.id).all()
                    form_ids = [f.id for f in forms]
                    
                    if form_ids:
                        # 部门累计扣分 (假设为 total_department_score 之和)
                        total_deduction = db.session.query(func.sum(ScoreRecord.total_department_score))\
                            .filter(ScoreRecord.form_id.in_(form_ids)).scalar() or 0.0
                        
                        # 部员扣分排行
                        member_stats = db.session.query(
                            LectureForm.listener_number,
                            func.sum(ScoreRecord.total_department_score).label('score')
                        ).join(ScoreRecord, LectureForm.id == ScoreRecord.form_id)\
                         .filter(LectureForm.listener_number.in_(dept_user_numbers))\
                         .group_by(LectureForm.listener_number)\
                         .order_by(func.sum(ScoreRecord.total_department_score).desc())\
                         .all()
                         
                        user_map = {u.number: u.name for u in dept_users}
                        
                        for ms in member_stats:
                            if ms.score > 0:
                                member_deductions.append({
                                    'name': user_map.get(ms.listener_number, ms.listener_number),
                                    'score': ms.score
                                })

            dept_data = {
                'id': dept.id,
                'name': dept.name,
                'description': dept.description,
                'head': dept.head,
                'manager_id': dept.manager_id,
                'total_users': total_users,
                'total_deduction': total_deduction,
                'member_deductions': member_deductions,
                'groups': [],
                'is_virtual': False,
            }
            
            for group in groups:
                users = _active_user_query().filter_by(group_id=group.id).all()
                if group.name == UNASSIGNED_GROUP_NAME and not users:
                    continue
                dept_data['groups'].append(_build_group_payload(group, users))
            
            # 添加没有小组的用户（仅当权限允许时）
            if manage_permission != '管理部门小组':
                no_group_users = _active_user_query().filter_by(department=dept.name, group_id=None).all()
                if no_group_users:
                    no_group_data = _build_group_payload(
                        None,
                        no_group_users,
                        name=UNASSIGNED_GROUP_NAME,
                        department=dept.name,
                        description='未分配到小组的用户',
                        max_members='无限制',
                        is_virtual=True
                    )
                    dept_data['groups'].append(no_group_data)
            
            result.append(dept_data)

        if manage_permission == '超级管理员' and not has_real_unassigned_department:
            unassigned_users = _active_user_query().filter_by(department=UNASSIGNED_DEPARTMENT_NAME).all()
            if unassigned_users:
                result.append({
                    'id': None,
                    'name': UNASSIGNED_DEPARTMENT_NAME,
                    'description': '未分配到部门的用户',
                    'head': None,
                    'manager_id': None,
                    'total_users': len(unassigned_users),
                    'total_deduction': 0.0,
                    'member_deductions': [],
                    'groups': [
                        _build_group_payload(
                            None,
                            unassigned_users,
                            name=UNASSIGNED_GROUP_NAME,
                            department=UNASSIGNED_DEPARTMENT_NAME,
                            description='未分配到小组的用户',
                            max_members='无限制',
                            is_virtual=True
                        )
                    ],
                    'is_virtual': True,
                })
        
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/departments', methods=['POST'])
@role_required('超级管理员')
def add_department():
    """添加新部门"""
    try:
        user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(user.id)
        
        if manage_permission != '超级管理员':
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '人员与部门',
                     '当前账号没有添加部门的权限。',
                     action='添加',
                 ),
             }), 403
             
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'success': False, 'message': '请求数据必须为JSON对象'}), 400
        name = data.get('name', '').strip()
        description = data.get('description', '').strip()
        manager_id_raw = data.get('manager_id')
        head = data.get('head', '').strip()
        
        if not name:
            return jsonify({'success': False, 'message': '部门名称不能为空'}), 400
        
        # 检查部门名称是否已存在
        existing_dept = Department.query.filter_by(name=name).first()
        if existing_dept:
            return jsonify({'success': False, 'message': '部门名称已存在'}), 400
        
        # 处理负责人ID（优先使用manager_id）
        manager_id = None
        head_name = head if head else None
        manager_user = None
        if manager_id_raw is not None and str(manager_id_raw).strip() != '':
            try:
                manager_id = int(manager_id_raw)
            except ValueError:
                return jsonify({'success': False, 'message': '负责人ID格式不正确'}), 400
            manager_user = User.query.get(manager_id)
            if not manager_user or not is_user_active(manager_user):
                return jsonify({'success': False, 'message': '指定的负责人不存在'}), 400
            head_name = manager_user.name
        
        new_dept = Department(name=name, description=description, manager_id=manager_id, head=head_name)
        db.session.add(new_dept)
        # flush 取得 Department.id，负责人迁移与建部门保持在同一事务内。
        db.session.flush()

        # 如果指定了负责人，更新负责人的department
        if manager_user and manager_user.department != name:
            manager_user.department = name
            # 清除旧的小组关联（因为部门变了）
            clear_user_group(manager_user)

        db.session.commit()
        
        return jsonify({'success': True, 'message': '部门添加成功', 'data': {
            'id': new_dept.id,
            'name': new_dept.name,
            'description': new_dept.description,
            'head': new_dept.head,
            'manager_id': new_dept.manager_id
        }})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/departments/<int:dept_id>', methods=['GET'])
@role_required('管理员')
def get_department(dept_id):
    """获取单个部门信息"""
    try:
        user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(user.id)
        
        if not manage_permission:
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '人员与部门',
                     '当前账号没有查看部门信息的权限。',
                     action='查看',
                 ),
             }), 403
             
        dept = Department.query.get_or_404(dept_id)
        
        # 权限检查
        if manage_permission != '超级管理员':
            if dept.name != user.department:
                 return jsonify({
                     'success': False,
                     'message': build_forbidden_message(
                         '人员与部门',
                         '当前账号没有查看该部门信息的权限。',
                         action='查看',
                     ),
                 }), 403
        
        return jsonify({'success': True, 'data': {
            'id': dept.id,
            'name': dept.name,
            'description': dept.description,
            'head': dept.head,
            'manager_id': dept.manager_id
        }})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/departments/<int:dept_id>', methods=['PUT'])
@role_required('管理员')
def update_department(dept_id):
    """更新部门信息"""
    try:
        user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(user.id)
        
        if not manage_permission:
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '人员与部门',
                     '当前账号没有修改部门信息的权限。',
                     action='修改',
                 ),
             }), 403
             
        dept = Department.query.get_or_404(dept_id)
        
        # 权限检查
        if manage_permission != '超级管理员':
            if dept.name != user.department:
                 return jsonify({
                     'success': False,
                     'message': build_forbidden_message(
                         '人员与部门',
                         '当前账号没有修改该部门信息的权限。',
                         action='修改',
                     ),
                 }), 403
            # 小组级管理权限不能修改部门级元数据/负责人。
            if manage_permission != '管理部门':
                return jsonify({
                    'success': False,
                    'message': build_forbidden_message(
                        '人员与部门',
                        '仅部门级及以上的管理权限可以修改部门信息。',
                        action='修改',
                    ),
                }), 403
        
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'success': False, 'message': '请求数据必须为JSON对象'}), 400
        
        old_name = dept.name
        new_name = data.get('name', '').strip()
        description = data.get('description', '').strip()
        manager_id_raw = data.get('manager_id')
        head = data.get('head', '').strip()
        
        if not new_name:
            return jsonify({'success': False, 'message': '部门名称不能为空'}), 400
        
        # 检查是否尝试修改部门名称
        if new_name != old_name:
            if manage_permission != '超级管理员':
                return jsonify({
                    'success': False,
                    'message': build_forbidden_message(
                        '人员与部门',
                        '当前账号没有修改部门名称的权限。',
                        action='修改',
                    ),
                }), 403
                
            existing_dept = Department.query.filter_by(name=new_name).first()
            if existing_dept:
                return jsonify({'success': False, 'message': '部门名称已存在'}), 400
            
            # 更新相关用户和小组的部门信息
            User.query.filter_by(department=old_name).update({'department': new_name})
            Group.query.filter_by(department=old_name).update({'department': new_name})
        
        dept.name = new_name
        dept.description = description
        
        # 更新负责人（优先使用manager_id）
        if manager_id_raw is not None:
            if str(manager_id_raw).strip() == '':
                dept.manager_id = None
                dept.head = None
            else:
                try:
                    manager_id = int(manager_id_raw)
                except ValueError:
                    return jsonify({'success': False, 'message': '负责人ID格式不正确'}), 400
                manager_user = User.query.get(manager_id)
                if not manager_user or not is_user_active(manager_user):
                    return jsonify({'success': False, 'message': '指定的负责人不存在'}), 400
                
                # 检查负责人是否属于该部门；非超管不得自动跨部门迁移人员。
                if manager_user.department != new_name: # new_name 是更新后的部门名
                     if manage_permission != '超级管理员':
                         return jsonify({
                             'success': False,
                             'message': build_forbidden_message(
                                 '人员与部门',
                                 '指定的负责人不属于该部门，不能自动跨部门迁移。',
                                 action='修改',
                             ),
                         }), 400
                     # 超级管理员保留既有自动分配兼容行为
                     manager_user.department = new_name
                     if manager_user.group_id:
                         group = Group.query.get(manager_user.group_id)
                         if group and group.department != new_name:
                             clear_user_group(manager_user)

                dept.manager_id = manager_id
                dept.head = manager_user.name
        else:
            # 兼容旧字段
            dept.head = head
        
        db.session.commit()
        
        return jsonify({'success': True, 'message': '部门更新成功'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/departments/<int:dept_id>/disband', methods=['POST'])
@admin_bp.route('/api/departments/<int:dept_id>', methods=['DELETE'])
@role_required('超级管理员')
def disband_department(dept_id):
    """解散部门：删除组织记录，成员转入未分配部门。"""
    try:
        user = User.query.get(session['user_id'])
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'success': False, 'message': '请求数据必须为JSON对象'}), 400
        password = data.get('password')
        
        # 验证密码
        if not password or not check_password_hash(user.password_hash, password):
            return jsonify({'success': False, 'message': '密码验证失败，无法解散部门'}), 403
            
        dept = Department.query.get_or_404(dept_id)
        if dept.name == UNASSIGNED_DEPARTMENT_NAME:
            return jsonify({'success': False, 'message': '未分配部门不能解散'}), 400
        
        dept_name = dept.name
        dept_users = User.query.filter_by(department=dept_name).all()
        moved_count = 0
        for target_user in dept_users:
            before_snapshot = _snapshot_user_for_movement(target_user)
            _set_user_unassigned(target_user)
            after_snapshot = _snapshot_user_for_movement(target_user)
            moved_count += 1
            _create_personnel_movement_record(
                user.id,
                {
                    **after_snapshot,
                    'department_before': before_snapshot.get('department'),
                    'department_after': after_snapshot.get('department'),
                    'group_before': before_snapshot.get('group'),
                    'group_after': after_snapshot.get('group'),
                },
                '部门解散',
                f"{after_snapshot.get('name')}（{after_snapshot.get('number')}）因部门解散转入未分配部门",
                [{
                    'field': 'department',
                    'label': '部门',
                    'before': before_snapshot.get('department') or '未设置',
                    'after': UNASSIGNED_DEPARTMENT_NAME
                }, {
                    'field': 'group',
                    'label': '小组',
                    'before': before_snapshot.get('group') or UNASSIGNED_GROUP_NAME,
                    'after': UNASSIGNED_GROUP_NAME
                }]
            )

        # 删除部门下的小组记录，用户已转入未分配部门，不再保留原小组。
        groups = Group.query.filter_by(department=dept.name).all()
        for group in groups:
            db.session.delete(group)
        
        db.session.delete(dept)
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'部门已解散，{moved_count} 名用户已转入未分配部门'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'解散失败: {str(e)}'}), 500


@admin_bp.route('/api/groups', methods=['GET'])
@role_required('管理员')
def get_groups_by_department():
    """按部门获取小组"""
    try:
        user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(user.id)
        
        if not manage_permission:
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '小组管理',
                     '当前账号没有查看小组的权限。',
                     action='查看',
                 ),
             }), 403
             
        department = request.args.get('department')
        
        # 权限过滤
        if manage_permission == '超级管理员':
            if department:
                groups = Group.query.filter_by(department=department).all()
            else:
                groups = Group.query.all()
        elif manage_permission == '管理部门':
            # 只能查看本部门的小组
            # 即使前端没传department参数，也只返回本部门的
            # 如果前端传了其他部门，则返回空或报错
            if department and department != user.department:
                return jsonify({
                    'success': False,
                    'message': build_forbidden_message(
                        '小组管理',
                        '当前账号没有查看该小组的权限。',
                        action='查看',
                    ),
                }), 403
            groups = Group.query.filter_by(department=user.department).all()
        elif manage_permission == '管理部门小组':
            # 只能查看本小组：canonical group_id 判定，无归属时 fail closed。
            groups = Group.query.filter(canonical_scope_group_criteria(user)).all()
        
        groups_data = []
        for group in groups:
            groups_data.append({
                'id': group.id,
                'name': group.name,
                'department': group.department,
                'leader': group.leader,
                'description': group.description,
                'max_members': group.max_members
            })
        
        return jsonify({'success': True, 'data': groups_data})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/groups', methods=['POST'])
@role_required('管理员')
def add_group():
    """添加新小组"""
    try:
        user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(user.id)
        
        if not manage_permission:
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '小组管理',
                    '当前账号没有添加小组的权限。',
                    action='添加',
                ),
            }), 403
            
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'success': False, 'message': '请求数据必须为JSON对象'}), 400
        name = data.get('name', '').strip()
        department = data.get('department', '').strip()
        leader = data.get('leader', '').strip()
        description = data.get('description', '').strip()
        max_members = _positive_group_capacity(data.get('max_members', 10))
        if max_members is None:
            return jsonify({'success': False, 'message': '小组容量必须是大于0的整数'}), 400
        
        if not name:
            return jsonify({'success': False, 'message': '小组名称不能为空'}), 400
        
        if not department:
            return jsonify({'success': False, 'message': '所属部门不能为空'}), 400
            
        # 权限检查：小组创建仅超级管理员/管理部门（小组级 capability 收口，与 UI 一致）
        if not can_create_group(user, manage_permission, department):
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '小组管理',
                    '当前账号没有在当前管理范围内添加小组的权限。',
                    action='添加',
                ),
            }), 403
        
        # 检查部门是否存在
        dept = Department.query.filter_by(name=department).first()
        if not dept and department != UNASSIGNED_DEPARTMENT_NAME:
            return jsonify({'success': False, 'message': '指定的部门不存在'}), 400
        
        # 检查小组名称在该部门内是否已存在
        existing_group = Group.query.filter_by(name=name, department=department).first()
        if existing_group:
            return jsonify({'success': False, 'message': '该部门内已存在同名小组'}), 400
        
        # 处理组长：leader_id 与 legacy leader 姓名解析到同一 candidate 流程
        # （active 校验 → scope policy → 归属 mutation），杜绝两套语义漂移。
        leader_id = data.get('leader_id')
        leader_name = leader
        leader_candidate = None

        if leader_id:
            leader_candidate = User.query.get(leader_id)
            if not leader_candidate or not is_user_active(leader_candidate):
                return jsonify({'success': False, 'message': '指定的小组长不存在或已离任'}), 400
            leader_name = leader_candidate.name
        elif leader:
            # 尝试根据名字查找
            leader_candidate = _active_user_query().filter_by(name=leader, department=department).first()
            if leader_candidate:
                leader_id = leader_candidate.id

        # 组长 scope policy：小组尚未创建，先按目标部门校验，拒绝任何部分创建。
        if leader_candidate is not None and not can_assign_group_leader(
            user, manage_permission, leader_candidate, group_department=department,
        ):
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '小组管理',
                    '当前账号没有指派该组长的权限。',
                    action='添加',
                ),
            }), 403

        new_group = Group(
            name=name,
            department=department,
            # department_id=dept.id, # Group表无此字段
            leader=leader_name,
            leader_id=leader_id,
            description=description,
            max_members=max_members
        )
        db.session.add(new_group)
        # flush 取得 Group.id，组长归属与建小组保持在同一事务内。
        db.session.flush()

        # 如果指定了组长，在同一事务内完成组长的 canonical membership 归属。
        if leader_candidate is not None:
            assign_user_to_group(leader_candidate, new_group)
            if (
                leader_candidate.department != department
                and leader_assignment_may_migrate_department(manage_permission)
            ):
                leader_candidate.department = department

        db.session.commit()
        
        return jsonify({'success': True, 'message': '小组添加成功', 'data': {
            'id': new_group.id,
            'name': new_group.name,
            'department': new_group.department,
            'leader': new_group.leader,
            'leader_id': new_group.leader_id,
            'description': new_group.description,
            'max_members': new_group.max_members
        }})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/groups/<int:group_id>', methods=['GET'])
@role_required('管理员')
def get_group(group_id):
    """获取单个小组信息"""
    try:
        user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(user.id)
        
        if not manage_permission:
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '小组管理',
                     '当前账号没有查看小组信息的权限。',
                     action='查看',
                 ),
             }), 403
             
        group = Group.query.get_or_404(group_id)
        
        # 权限检查
        if manage_permission != '超级管理员':
            if group.department != user.department:
                 return jsonify({
                     'success': False,
                     'message': build_forbidden_message(
                         '小组管理',
                         '当前账号没有查看该小组的权限。',
                         action='查看',
                     ),
                 }), 403
            
            if manage_permission == '管理部门小组':
                # 授权只认 canonical group_id，legacy group 文本不参与判定。
                if not is_group_in_canonical_scope(user, group):
                     return jsonify({
                         'success': False,
                         'message': build_forbidden_message(
                             '小组管理',
                             '当前账号没有查看该小组的权限。',
                             action='查看',
                         ),
                     }), 403
        
        return jsonify({'success': True, 'data': {
            'id': group.id,
            'name': group.name,
            'department': group.department,
            'leader': group.leader,
            'leader_id': group.leader_id,
            'description': group.description,
            'max_members': group.max_members
        }})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/groups/<int:group_id>', methods=['PUT'])
@role_required('管理员')
def update_group(group_id):
    """更新小组信息"""
    try:
        user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(user.id)
        
        if not manage_permission:
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '小组管理',
                     '当前账号没有修改小组的权限。',
                     action='修改',
                 ),
             }), 403
             
        group = Group.query.get_or_404(group_id)
        
        # 权限检查
        if manage_permission != '超级管理员':
            if manage_permission == '管理部门':
                if group.department != user.department:
                     return jsonify({
                         'success': False,
                         'message': build_forbidden_message(
                             '小组管理',
                             '当前账号没有修改该小组的权限。',
                             action='修改',
                         ),
                     }), 403
            elif manage_permission == '管理部门小组':
                # 授权只认 canonical group_id，legacy group 文本不参与判定。
                if not is_group_in_canonical_scope(user, group):
                     return jsonify({
                         'success': False,
                         'message': build_forbidden_message(
                             '小组管理',
                             '当前账号没有修改该小组的权限。',
                             action='修改',
                         ),
                     }), 403

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'success': False, 'message': '请求数据必须为JSON对象'}), 400
        
        name = data.get('name', '').strip()
        department = data.get('department', '').strip()
        leader = data.get('leader', '').strip()
        description = data.get('description', '').strip()
        max_members = _positive_group_capacity(data.get('max_members', 10))
        if max_members is None:
            return jsonify({'success': False, 'message': '小组容量必须是大于0的整数'}), 400
        
        if not name:
            return jsonify({'success': False, 'message': '小组名称不能为空'}), 400
        
        if not department:
            return jsonify({'success': False, 'message': '所属部门不能为空'}), 400

        old_group_name = group.name
        old_department = group.department
        old_group_id = group.id
            
        # 检查部门变更权限
        if manage_permission != '超级管理员':
            if department != group.department:
                return jsonify({
                    'success': False,
                    'message': build_forbidden_message(
                        '小组管理',
                        '当前账号没有修改小组归属的权限。',
                        action='修改',
                    ),
                }), 403
        
        # 检查部门是否存在
        dept = Department.query.filter_by(name=department).first()
        if not dept and department != UNASSIGNED_DEPARTMENT_NAME:
            return jsonify({'success': False, 'message': '指定的部门不存在'}), 400
        
        # 如果名称或部门发生变化，检查新组合是否已存在
        if name != old_group_name or department != old_department:
            existing_group = Group.query.filter_by(name=name, department=department).first()
            if existing_group and existing_group.id != group_id:
                return jsonify({'success': False, 'message': '该部门内已存在同名小组'}), 400
        
        # 处理组长：leader_id 与 legacy leader 姓名解析到同一 candidate 流程
        # （active 校验 → scope policy → 归属 mutation），杜绝两套语义漂移。
        leader_id = data.get('leader_id')
        leader_name = leader
        leader_candidate = None

        if leader_id:
            try:
                # 确保leader_id是整数
                leader_id = int(leader_id) if leader_id else None
            except ValueError:
                leader_id = None

            if leader_id:
                leader_candidate = User.query.get(leader_id)
                if not leader_candidate or not is_user_active(leader_candidate):
                    return jsonify({'success': False, 'message': '指定的小组长不存在或已离任'}), 400
                leader_name = leader_candidate.name
        elif leader:
            # 尝试根据名字查找
            leader_candidate = _active_user_query().filter_by(name=leader, department=department).first()
            if leader_candidate:
                leader_id = leader_candidate.id

        # 组长 scope policy：拒绝借组长指派跨部门搬人或从兄弟小组/未分组池拉人。
        if leader_candidate is not None and not can_assign_group_leader(
            user, manage_permission, leader_candidate,
            group_department=department, group_id=group.id,
        ):
            return jsonify({
                'success': False,
                'message': build_forbidden_message(
                    '小组管理',
                    '当前账号没有指派该组长的权限。',
                    action='修改',
                ),
            }), 403

        group.name = name
        group.department = department
        # group.department_id = dept.id # Group表无此字段
        group.leader = leader_name
        group.leader_id = leader_id
        group.description = description
        group.max_members = max_members

        # 用户表保留 group 文本/department 冗余字段做兼容：以小组旧身份对全部当前
        # 成员（canonical + legacy）同步，legacy 成员在改名/迁移后不掉组；
        # non-null group_id 的成员（含 stale 文本）只走 canonical 分支，不会被误改。
        member_updates = {}
        if name != old_group_name:
            member_updates['group'] = name
        if department != old_department:
            member_updates['department'] = department

        # 组长归属：leader_id 与姓名路径统一在此执行（candidate 已通过 scope policy）。
        if leader_candidate is not None and leader_candidate.group_id != group.id:
            assign_user_to_group(leader_candidate, group)
            if (
                leader_candidate.department != department
                and leader_assignment_may_migrate_department(manage_permission)
            ):
                leader_candidate.department = department

        if member_updates:
            User.query.filter(
                group_member_criteria_for_identity(old_group_id, old_department, old_group_name)
            ).update(member_updates)

        db.session.commit()
        
        return jsonify({'success': True, 'message': '小组更新成功'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/groups/<int:group_id>/disband', methods=['POST'])
@admin_bp.route('/api/groups/<int:group_id>', methods=['DELETE'])
@role_required('管理员')
def disband_group(group_id):
    """解散小组：删除小组记录，成员转入未分配小组。"""
    try:
        user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(user.id)
        
        if not manage_permission:
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '小组管理',
                     '当前账号没有解散小组的权限。',
                     action='解散',
                 ),
             }), 403
              
        group = Group.query.get_or_404(group_id)
        
        # 权限检查
        if manage_permission != '超级管理员':
            if manage_permission == '管理部门':
                if group.department != user.department:
                     return jsonify({
                         'success': False,
                         'message': build_forbidden_message(
                             '小组管理',
                             '当前账号没有解散该小组的权限。',
                             action='解散',
                         ),
                     }), 403
            else:
                return jsonify({
                    'success': False,
                    'message': build_forbidden_message(
                        '小组管理',
                        '当前账号没有解散小组的权限。',
                        action='解散',
                    ),
                }), 403
         
        # 验证密码
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'success': False, 'message': '请求数据必须为JSON对象'}), 400
        password = data.get('password')
        if not password or not check_password_hash(user.password_hash, password):
            return jsonify({'success': False, 'message': '密码验证失败，无法解散小组'}), 403

        group_users = User.query.filter(group_member_criteria(group)).all()
        moved_count = 0
        for target_user in group_users:
            before_snapshot = _snapshot_user_for_movement(target_user)
            clear_user_group(target_user)
            after_snapshot = _snapshot_user_for_movement(target_user)
            moved_count += 1
            _create_personnel_movement_record(
                user.id,
                {
                    **after_snapshot,
                    'department_before': before_snapshot.get('department'),
                    'department_after': after_snapshot.get('department'),
                    'group_before': before_snapshot.get('group'),
                    'group_after': after_snapshot.get('group'),
                },
                '小组解散',
                f"{after_snapshot.get('name')}（{after_snapshot.get('number')}）因小组解散转入未分配小组",
                [{
                    'field': 'group',
                    'label': '小组',
                    'before': before_snapshot.get('group') or UNASSIGNED_GROUP_NAME,
                    'after': UNASSIGNED_GROUP_NAME
                }]
            )
         
        db.session.delete(group)
        db.session.commit()
         
        return jsonify({'success': True, 'message': f'小组已解散，{moved_count} 名用户已转入未分配小组'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'解散失败: {str(e)}'}), 500


@admin_bp.route('/api/groups/<int:group_id>/move_members', methods=['POST'])
@role_required('管理员')
def move_members_to_group(group_id):
    """批量移动成员到小组"""
    try:
        user = User.query.get(session['user_id'])
        manage_permission = get_user_manage_permission(user.id)
        
        if not manage_permission:
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '小组成员管理',
                     '当前账号没有操作小组成员的权限。',
                     action='操作',
                 ),
             }), 403
             
        group = Group.query.get_or_404(group_id)

        # 权限检查：批量移动成员仅超级管理员/管理部门（小组级无此 capability，与 UI 一致）
        if not can_batch_move_group_members(manage_permission):
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '小组成员管理',
                     '当前账号没有操作该小组成员的权限。',
                     action='操作',
                 ),
             }), 403
        if manage_permission != '超级管理员' and group.department != user.department:
             return jsonify({
                 'success': False,
                 'message': build_forbidden_message(
                     '小组成员管理',
                     '当前账号没有操作该小组成员的权限。',
                     action='操作',
                 ),
             }), 403
        
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'success': False, 'message': '请求数据必须为JSON对象'}), 400
        user_ids = data.get('user_ids', [])
        
        if not user_ids:
            return jsonify({'success': False, 'message': '请选择要移动的用户'}), 400
            
        # 验证用户是否属于该部门（只能在同部门内移动到小组）
        # 除非是超级管理员，可能允许跨部门（但业务逻辑上小组属于部门，跨部门移动到小组意味着换部门）
        # 这里简化逻辑：移动到小组 = 更新 group_id 和 group
        # 同时也检查/更新 department
        
        users = _active_user_query().filter(User.id.in_(user_ids)).all()
        count = 0
        
        for u in users:
            # 权限检查：能否操作该用户
            if manage_permission != '超级管理员':
                if u.department != user.department:
                    continue # 跳过非本部门用户

            assign_user_to_group(u, group)
            
            # 如果部门不一致，更新部门（注意：这可能需要更高权限，这里假设移动到小组就隐含了部门变更）
            if u.department != group.department:
                if manage_permission == '超级管理员':
                    u.department = group.department
                else:
                    # 普通管理员不能跨部门拉人
                    continue 
            
            count += 1
            
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'成功移动 {count} 名用户到小组'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@admin_bp.route('/api/departments/<int:dept_id>/move_members', methods=['POST'])
@role_required('超级管理员')
def move_members_to_department(dept_id):
    """批量移动成员到部门"""
    try:
        dept = Department.query.get_or_404(dept_id)
        
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'success': False, 'message': '请求数据必须为JSON对象'}), 400
        user_ids = data.get('user_ids', [])
        
        if not user_ids:
            return jsonify({'success': False, 'message': '请选择要移动的用户'}), 400
            
        users = _active_user_query().filter(User.id.in_(user_ids)).all()
        
        for u in users:
            u.department = dept.name
            clear_user_group(u)
            
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'成功移动 {len(users)} 名用户到部门'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


