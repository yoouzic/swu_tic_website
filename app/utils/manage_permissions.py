# -*- coding: utf-8 -*-
"""
管理权限验证工具模块
"""

from ..models import User, Permission, RolePermission, db
from .user_status import is_user_active

def get_user_manage_permission(user_id):
    """
    获取用户的管理权限
    返回: None, '管理部门小组', '管理部门', '超级管理员'
    """
    user = User.query.get(user_id)
    if not user or not is_user_active(user):
        return None
    
    # 超级管理员默认具有最高管理权限
    if user.role == '超级管理员':
        return '超级管理员'
    
    # 信息员无管理权限
    if user.role == '信息员':
        return None
    
    # 管理员需要根据role_permissions表确定管理权限
    if user.role == '管理员':
        # 首先检查是否有自定义权限
        custom_permissions = db.session.query(Permission).join(RolePermission).filter(
            RolePermission.role == f'特殊角色_{user.id}'
        ).all()
        
        if custom_permissions:
            permission_names = [perm.name for perm in custom_permissions]
        else:
            role_permissions = db.session.query(Permission).join(RolePermission).filter(
                RolePermission.role == user.role
            ).all()
            permission_names = [perm.name for perm in role_permissions]
        
        if '管理部门' in permission_names:
            return '管理部门'
        elif '管理部门小组' in permission_names:
            return '管理部门小组'
            
    return None

def check_manage_permission(user_id, required_permission):
    """
    检查用户是否具有指定的管理权限
    """
    user_permission = get_user_manage_permission(user_id)
    if not user_permission:
        return False
        
    permission_levels = {
        '管理部门小组': 1,
        '管理部门': 2,
        '超级管理员': 3
    }
    
    user_level = permission_levels.get(user_permission, 0)
    required_level = permission_levels.get(required_permission, 0)
    
    return user_level >= required_level
