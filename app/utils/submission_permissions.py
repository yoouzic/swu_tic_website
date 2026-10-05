"""Personal submission rights are independent of administrative authority."""
from flask import has_app_context

from app.models import Permission, RolePermission, SystemSetting, db


def _revocation_key(user_id):
    return f'submission_permission_revoked_{user_id}'


def submission_permission_revoked(user_id):
    return SystemSetting.get(_revocation_key(user_id)) == '1'


def set_submission_permission_revoked(user_id, revoked):
    """Change explicit denial within the caller's transaction; never commit here."""
    setting = SystemSetting.query.filter_by(key=_revocation_key(user_id)).first()
    if revoked:
        if setting is None:
            db.session.add(SystemSetting(key=_revocation_key(user_id), value='1'))
        else:
            setting.value = '1'
    elif setting is not None:
        db.session.delete(setting)


def can_submit_lecture_form(user, *, permission_names=None):
    """Resolve the active user's fill capability, without a super-admin bypass.

    ``permission_names`` supplies trusted permissions for pure presentation tests;
    request guards always resolve current database permissions themselves.
    """
    if user is None or getattr(user, 'is_active', None) is False:
        return False
    role = getattr(user, 'role', None)
    if role == '信息员':
        return True
    if role != '管理员':
        return False
    user_id = getattr(user, 'id', None)
    if has_app_context() and user_id is not None:
        if submission_permission_revoked(user_id):
            return False
    if permission_names is None:
        if not has_app_context() or user_id is None:
            return False
        permissions = db.session.query(Permission).join(RolePermission).filter(
            RolePermission.role == f'特殊角色_{user_id}',
        ).all()
        if not permissions:
            permissions = db.session.query(Permission).join(RolePermission).filter(
                RolePermission.role == role,
            ).all()
        permission_names = (permission.name for permission in permissions)
    return '填表' in permission_names
