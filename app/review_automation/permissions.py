"""Permission guards for the additive automated-review API."""

from functools import wraps

from flask import g, jsonify, session

from app.models import User, db
from app.utils.permission_feedback import build_forbidden_message
from app.utils.review_permissions import get_reviewable_users, get_user_review_permission
from app.utils.user_status import is_user_active


def _error(message, code, status):
    return jsonify({'success': False, 'code': code, 'message': message}), status


def _authenticated_user():
    user_id = session.get('user_id')
    if user_id is None:
        return None, _error('请先登录', 'authentication_required', 401)
    user = db.session.get(User, user_id)
    if user is None or not is_user_active(user):
        session.clear()
        return None, _error('账号不可用', 'authentication_required', 401)
    return user, None


def api_login_required(view):
    """Require an active session and always return JSON API errors."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        user, error = _authenticated_user()
        if error is not None:
            return error
        g.automation_user = user
        return view(*args, **kwargs)

    return wrapped


def require_super_admin(view):
    """Restrict dataset, rule, and service configuration to super admins."""

    @wraps(view)
    @api_login_required
    def wrapped(*args, **kwargs):
        user = g.automation_user
        if user.role != '超级管理员':
            return _error(
                build_forbidden_message(
                    '自动审核配置',
                    '当前账号没有管理自动审核配置的权限。',
                    action='管理',
                ),
                'forbidden',
                403,
            )
        return view(*args, **kwargs)

    return wrapped


def require_automation_staff(view):
    """Allow health reads only to center reviewers and super administrators."""

    @wraps(view)
    @api_login_required
    def wrapped(*args, **kwargs):
        user = g.automation_user
        if user.role != '超级管理员' and not is_center_reviewer(user.id):
            return _error(
                build_forbidden_message(
                    '自动审核',
                    '当前账号没有自动审核工作人员权限。',
                    action='操作',
                ),
                'forbidden',
                403,
            )
        return view(*args, **kwargs)

    return wrapped


def require_center_reviewer(view):
    """Restrict batches and evidence to the existing center-review scope."""

    @wraps(view)
    @api_login_required
    def wrapped(*args, **kwargs):
        user = g.automation_user
        if get_user_review_permission(user.id) != '审表_中心':
            return _error(
                build_forbidden_message(
                    '自动审核',
                    '当前账号没有中心级审核权限。',
                    action='操作',
                ),
                'forbidden',
                403,
            )
        return view(*args, **kwargs)

    return wrapped


def is_center_reviewer(user_id):
    return get_user_review_permission(user_id) == '审表_中心'


def reviewable_user_ids(user_id):
    """Use the existing review permission boundary as the automation boundary."""

    return set(get_reviewable_users(user_id))


__all__ = [
    'api_login_required',
    'is_center_reviewer',
    'require_automation_staff',
    'require_center_reviewer',
    'require_super_admin',
    'reviewable_user_ids',
]
