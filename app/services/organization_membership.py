# -*- coding: utf-8 -*-
"""Organization membership and group-scope primitives.

Canonical rule: a user's group membership is identified by ``User.group_id``.
The legacy ``User.group`` text column stays synchronized for display and
historical compatibility only and must never drive authorization decisions.

This module is an application/data boundary: it may depend on models and
SQLAlchemy expressions, but never on Flask HTTP objects or blueprints.
"""
from sqlalchemy import and_, false, or_

from app.models import Group, User
from app.utils.user_status import UNASSIGNED_GROUP_NAME


def is_user_in_canonical_group_scope(actor, target):
    """User-level 管理部门小组 scope check.

    Contract: same department AND the target carries the actor's canonical
    ``group_id``.  An actor without a canonical group assignment fails closed,
    even when legacy group texts happen to match.
    """
    if actor is None or target is None:
        return False
    if actor.group_id is None:
        return False
    return (
        actor.department == target.department
        and target.group_id == actor.group_id
    )


def is_group_in_canonical_scope(actor, group):
    """Group-level 管理部门小组 scope check.

    Contract: same department AND the group is the actor's canonical group.
    Fails closed when the actor has no canonical group assignment.
    """
    if actor is None or group is None:
        return False
    if actor.group_id is None:
        return False
    return (
        actor.department == group.department
        and group.id == actor.group_id
    )


def canonical_group_user_criteria(actor):
    """SQLAlchemy criteria for users inside the actor's canonical group scope.

    Returns an always-false criteria when the actor has no canonical group, so
    list queries fail closed instead of leaking the unassigned-user pool.
    """
    if actor is None or actor.group_id is None:
        return false()
    return and_(
        User.department == actor.department,
        User.group_id == actor.group_id,
    )


def canonical_scope_group_criteria(actor):
    """SQLAlchemy criteria for groups inside the actor's canonical scope."""
    if actor is None or actor.group_id is None:
        return false()
    return and_(
        Group.department == actor.department,
        Group.id == actor.group_id,
    )


def group_member_criteria(group):
    """SQLAlchemy criteria for members of ``group``.

    Delegates to ``Group.member_criteria`` so model queries and organization
    routes share one authoritative predicate: canonical ``group_id`` wins, and
    only rows whose ``group_id`` is NULL may fall back to the legacy text
    columns.  A non-NULL ``group_id`` therefore always beats a stale legacy
    group name.
    """
    return Group.member_criteria(group)


def group_member_criteria_for_identity(group_id, department, group_name):
    """Identity-parameterized member predicate (shared with update use-case).

    Lets the group-update use-case match members by the group's OLD identity
    before its name/department are rewritten, without duplicating the
    membership semantics defined in ``Group.member_criteria_for_identity``.
    """
    return Group.member_criteria_for_identity(group_id, department, group_name)


def assign_user_to_group(user, group):
    """Assign canonical group membership and sync the legacy text column.

    Department changes are deliberately NOT part of this primitive; a use-case
    that is authorized to move users across departments must update
    ``user.department`` itself.
    """
    user.group_id = group.id
    user.group = group.name


def clear_user_group(user):
    """Clear canonical group membership and sync the legacy text column."""
    user.group_id = None
    user.group = UNASSIGNED_GROUP_NAME
