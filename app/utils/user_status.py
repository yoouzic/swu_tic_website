# -*- coding: utf-8 -*-
"""User active-status helpers shared by views and permission utilities."""

from sqlalchemy import or_

from ..models import User

UNASSIGNED_DEPARTMENT_NAME = '未分配部门'
UNASSIGNED_GROUP_NAME = '未分配小组'


def active_user_filter():
    """Treat legacy NULL values as active; only explicit False means inactive."""
    return or_(User.is_active.is_(True), User.is_active.is_(None))


def is_user_active(user):
    return bool(user) and user.is_active is not False
