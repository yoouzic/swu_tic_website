# -*- coding: utf-8 -*-
"""Authentication and role-guard decorators, independent of the auth Blueprint."""
from functools import wraps

from flask import flash, jsonify, redirect, request, session, url_for

from app.models import User, db
from app.utils.permission_feedback import (
    flash_forbidden,
    forbidden_json,
    resolve_permission_resource,
)
from app.utils.user_status import is_user_active
from app.utils.submission_permissions import can_submit_lecture_form


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


def submission_required(*, api=False, methods=None):
    """Guard personal fill actions, including mixed read/write API endpoints."""
    guarded_methods = frozenset(method.upper() for method in methods) if methods is not None else None

    def decorator(view):
        @wraps(view)
        def decorated(*args, **kwargs):
            user_id = session.get('user_id')
            user = db.session.get(User, user_id) if user_id is not None else None
            if user is None or not is_user_active(user):
                if user_id is not None:
                    session.clear()
                message = '请先登录' if user_id is None else '账号已离任或不可用，请联系管理员'
                if api:
                    return jsonify(success=False, data=None, message=message), 401
                flash(message, 'warning')
                return redirect(url_for('auth.login'))
            if guarded_methods is not None and request.method not in guarded_methods:
                return view(*args, **kwargs)
            if not can_submit_lecture_form(user):
                message = '当前账号没有个人填报权限，请返回工作台或联系管理员。'
                if api:
                    return jsonify(success=False, data=None, message=message), 403
                flash(message, 'error')
                return redirect(url_for('main.index'))
            return view(*args, **kwargs)
        return decorated
    return decorator
