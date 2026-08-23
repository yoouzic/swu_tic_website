# -*- coding: utf-8 -*-
"""Assessment/statistics user and department scope services.

These helpers are pure application/domain logic.  They depend only on models
and utility modules; they never import blueprints or Flask HTTP objects.
"""
from app.models import User
from app.utils.manage_permissions import get_user_manage_permission
from app.utils.review_permissions import get_reviewable_users
from app.utils.user_status import active_user_filter


def active_user_query():
    return User.query.filter(active_user_filter())


def resolve_assessment_users(current_user_id, user_ids):
    current_user = User.query.get(current_user_id)
    if not current_user:
        return []
    manage_permission = get_user_manage_permission(current_user_id)
    if manage_permission == '管理部门':
        scoped_users = active_user_query().filter(
            User.role.in_(['信息员', '管理员']),
            User.department == current_user.department,
            User.id != current_user_id
        ).all()
        reviewable_ids = set(u.id for u in scoped_users)
    else:
        reviewable_ids = set(get_reviewable_users(current_user_id))
    if user_ids:
        selected_ids = set()
        for uid in user_ids:
            try:
                selected_ids.add(int(uid))
            except Exception:
                continue
        target_ids = reviewable_ids.intersection(selected_ids)
    else:
        target_ids = reviewable_ids
    if not target_ids:
        return []
    users = active_user_query().filter(User.id.in_(list(target_ids))).all()
    return users


def get_accessible_department_users(current_user_id):
    users = resolve_assessment_users(current_user_id, [])
    department_map = {}
    for user in users:
        department_name = (user.department or '').strip()
        if not department_name:
            continue
        department_map.setdefault(department_name, []).append(user)
    return dict(sorted(department_map.items(), key=lambda item: item[0]))


def resolve_selected_departments(current_user_id, department_names):
    department_map = get_accessible_department_users(current_user_id)
    if department_names:
        normalized = []
        for name in department_names:
            value = (name or '').strip()
            if value and value in department_map and value not in normalized:
                normalized.append(value)
        selected_names = normalized
    else:
        selected_names = list(department_map.keys())
    return selected_names, {name: department_map[name] for name in selected_names if name in department_map}


def has_assessment_stats_access(user_id):
    manage_permission = get_user_manage_permission(user_id)
    return manage_permission in ['超级管理员', '管理部门']
