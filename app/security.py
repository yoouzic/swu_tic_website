# -*- coding: utf-8 -*-
"""Authentication and role-guard decorators, independent of the auth Blueprint."""
from functools import wraps

from flask import flash, jsonify, redirect, request, session, url_for

from app.models import User
from app.utils.permission_feedback import (
    flash_forbidden,
    forbidden_json,
    resolve_permission_resource,
)
from app.utils.user_status import is_user_active


def login_required(f):
    """登录验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('请先登录', 'warning')
            return redirect(url_for('auth.login'))
        user = User.query.get(session['user_id'])
        if not user or not is_user_active(user):
            session.clear()
            flash('账号已离任或不可用，请联系管理员', 'warning')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


def role_required(role):
    """角色权限验证装饰器"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 检查是否是API请求
            is_api_request = request.path.startswith('/admin/api/')

            if 'user_id' not in session:
                if is_api_request:
                    return jsonify({'success': False, 'message': '请先登录'}), 401
                flash('请先登录', 'warning')
                return redirect(url_for('auth.login'))

            user = User.query.get(session['user_id'])
            if not user:
                if is_api_request:
                    return jsonify({'success': False, 'message': '用户不存在'}), 403
                flash('用户不存在', 'error')
                return redirect(url_for('main.index'))
            if not is_user_active(user):
                session.clear()
                if is_api_request:
                    return jsonify({'success': False, 'message': '账号已离任或不可用'}), 403
                flash('账号已离任或不可用，请联系管理员', 'warning')
                return redirect(url_for('auth.login'))

            # 超级管理员可以访问所有功能
            if user.role == '超级管理员':
                return f(*args, **kwargs)

            # 其他用户需要匹配指定角色
            if user.role != role:
                resource = resolve_permission_resource()
                if is_api_request:
                    return forbidden_json(resource)
                flash_forbidden(resource)
                return redirect(url_for('main.index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator
