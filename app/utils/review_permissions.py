# -*- coding: utf-8 -*-
"""
审核权限验证工具模块
"""

from flask import session
from ..models import User, Permission, RolePermission, db, Group, Department
from .user_status import active_user_filter, is_user_active, UNASSIGNED_DEPARTMENT_NAME, UNASSIGNED_GROUP_NAME


_REVIEW_PERMISSION_PRESENTATION = {
    '审表_小组': {
        'permission_label': '小组级审核',
        'scope_label': '审核范围：本小组',
    },
    '审表_部门': {
        'permission_label': '部门级审核',
        'scope_label': '审核范围：本部门',
    },
    '审表_中心': {
        'permission_label': '中心级审核',
        'scope_label': '审核范围：全中心',
    },
}


def get_review_permission_presentation(permission):
    """Return user-facing copy without exposing internal permission keys."""
    presentation = _REVIEW_PERMISSION_PRESENTATION.get(permission, {
        'permission_label': '无审核权限',
        'scope_label': '审核范围：不可用',
    })
    return dict(presentation)

def get_user_review_permission(user_id):
    """
    获取用户的审核权限
    返回: None, '审表_小组', '审表_部门', '审表_中心'
    """
    user = User.query.get(user_id)
    if not user or not is_user_active(user):
        return None
    
    # 超级管理员默认具有最高审表权限
    if user.role == '超级管理员':
        return '审表_中心'
    
    # 信息员无审表权限
    if user.role == '信息员':
        return None
    
    # 管理员需要根据role_permissions表确定审表权限
    if user.role == '管理员':
        # 首先检查是否有自定义权限
        custom_permissions = db.session.query(Permission).join(RolePermission).filter(
            RolePermission.role == f'特殊角色_{user.id}'
        ).all()
        
        if custom_permissions:
            # 有自定义权限，检查审表权限
            permission_names = [perm.name for perm in custom_permissions]
        else:
            # 没有自定义权限，查询角色默认权限
            role_permissions = db.session.query(Permission).join(RolePermission).filter(
                RolePermission.role == user.role
            ).all()
            permission_names = [perm.name for perm in role_permissions]
        
        # 按优先级返回最高权限
        if '审表_中心' in permission_names:
            return '审表_中心'
        elif '审表_部门' in permission_names:
            return '审表_部门'
        elif '审表_小组' in permission_names:
            return '审表_小组'
    
    return None

def check_review_permission(user_id, required_permission):
    """
    检查用户是否具有指定的审核权限
    """
    user_permission = get_user_review_permission(user_id)
    if not user_permission:
        return False
    
    # 权限等级：审表_中心 > 审表_部门 > 审表_小组
    permission_levels = {
        '审表_小组': 1,
        '审表_部门': 2,
        '审表_中心': 3
    }
    
    user_level = permission_levels.get(user_permission, 0)
    required_level = permission_levels.get(required_permission, 0)
    
    return user_level >= required_level

def get_reviewable_users(user_id):
    """
    根据用户的审核权限，获取可审核的用户列表
    返回用户ID列表
    """
    user = User.query.get(user_id)
    if not user or not is_user_active(user):
        return []
    
    permission = get_user_review_permission(user_id)
    if not permission:
        return []
    
    if permission == '审表_中心':
        # 可以审核所有信息员和管理员的表单，但不能审核自己
        users = User.query.filter(User.role.in_(['信息员', '管理员']), active_user_filter()).all()
        return [u.id for u in users if u.id != user_id]
    
    elif permission == '审表_部门':
        # 可以审核同部门信息员和管理员的表单，但不能审核自己
        users = User.query.filter(User.role.in_(['信息员', '管理员']), User.department == user.department, active_user_filter()).all()
        return [u.id for u in users if u.id != user_id]
    
    elif permission == '审表_小组':
        # 可以审核同小组信息员和管理员的表单，但不能审核自己
        users = User.query.filter(User.role.in_(['信息员', '管理员']), User.group_id == user.group_id, active_user_filter()).all()
        return [u.id for u in users if u.id != user_id]
    
    return []

def get_reviewable_status_list(user_id):
    """
    根据用户的审核权限，获取可审核的表单状态列表
    """
    permission = get_user_review_permission(user_id)
    if not permission:
        return []
    
    if permission == '审表_中心':
        # 可以查看和审核所有状态的表单
        return ['待审核', '部门已审核', '中心已审核', '已驳回']
    
    elif permission in ['审表_部门', '审表_小组']:
        # 可以查看所有状态，但只能审核待审核和部门已审核状态
        return ['待审核', '部门已审核', '中心已审核', '已驳回']
    
    return []

def can_review_status(user_id, form_status, permission=None):
    """
    检查用户是否可以审核指定状态的表单
    """
    permission = permission or get_user_review_permission(user_id)
    if not permission:
        return False
    
    if permission == '审表_中心':
        # 最终审核必须接收已由首阶段管理员推进的表单。
        return form_status == '部门已审核'

    elif permission in ['审表_部门', '审表_小组']:
        # 首阶段管理员只能审核待审核版本，避免重复推进同一阶段。
        return form_status == '待审核'
    
    return False

def get_next_status_after_review(user_id):
    """
    获取用户审核后表单应该变更的状态
    """
    permission = get_user_review_permission(user_id)
    if not permission:
        return None
    
    if permission == '审表_中心':
        return '中心已审核'
    elif permission in ['审表_部门', '审表_小组']:
        return '部门已审核'
    
    return None

def get_user_structure_for_review(user_id):
    """
    根据用户的审核权限，获取用户结构数据
    返回格式根据权限不同：
    - 审表_小组：用户数组
    - 审表_部门：按小组分组的对象
    - 审表_中心：按部门-小组分组的对象
    """
    permission = get_user_review_permission(user_id)
    if not permission:
        return []
    
    if permission == '审表_中心':
        # 中心权限：返回按部门-小组-用户三级结构组织的数据
        users = User.query.filter(User.role.in_(['信息员', '管理员']), active_user_filter()).all()
        departments = {}
        
        for user in users:
            # 排除自己
            if user.id == user_id:
                continue
                
            dept_name = user.department or UNASSIGNED_DEPARTMENT_NAME
            group = Group.query.get(user.group_id) if user.group_id else None
            group_name = group.name if group else UNASSIGNED_GROUP_NAME
            
            # 初始化部门
            if dept_name not in departments:
                departments[dept_name] = {}
            
            # 初始化小组
            if group_name not in departments[dept_name]:
                departments[dept_name][group_name] = []
            
            # 添加用户
            departments[dept_name][group_name].append({
                'id': user.id,
                'name': user.name,
                'number': user.number,
                'role': user.role
            })
        
        return departments
    
    elif permission == '审表_部门':
        # 部门权限：返回按小组分组的数据
        current_user = User.query.get(user_id)
        users = User.query.filter(User.role.in_(['信息员', '管理员']), User.department == current_user.department, active_user_filter()).all()
        groups = {}
        
        for user in users:
            # 排除自己
            if user.id == user_id:
                continue
                
            group = Group.query.get(user.group_id) if user.group_id else None
            group_name = group.name if group else UNASSIGNED_GROUP_NAME
            
            # 初始化小组
            if group_name not in groups:
                groups[group_name] = []
            
            # 添加用户
            groups[group_name].append({
                'id': user.id,
                'name': user.name,
                'number': user.number,
                'role': user.role
            })
        
        return groups
    
    elif permission == '审表_小组':
        # 小组权限：返回用户数组
        current_user = User.query.get(user_id)
        users = User.query.filter(User.role.in_(['信息员', '管理员']), User.group_id == current_user.group_id, active_user_filter()).all()
        result = []
        
        for user in users:
            # 排除自己
            if user.id == user_id:
                continue
                
            result.append({
                'id': user.id,
                'name': user.name,
                'number': user.number,
                'role': user.role
            })
        
        return result
    
    return []
