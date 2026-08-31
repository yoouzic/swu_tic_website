# -*- coding: utf-8 -*-
"""Organization capability policy predicates.

决策只依赖 ``actor`` / ``candidate`` / ``group`` 字段 / ``manage_permission``，
是纯 capability predicate：不感知 HTTP、session 或 blueprint。legacy
``User.group`` 文本永远不参与任何判定。

依赖方向：Blueprint → organization policy / membership → models。
"""
from app.utils.user_status import is_user_active

MANAGE_PERMISSION_SUPER = '超级管理员'
MANAGE_PERMISSION_DEPARTMENT = '管理部门'
MANAGE_PERMISSION_GROUP = '管理部门小组'


def can_create_group(actor, manage_permission, department):
    """小组创建 capability。

    - 超级管理员：任意合法部门；
    - 管理部门：仅 actor.department；
    - 管理部门小组：拒绝 —— 小组级 scope 被定义为 actor.group_id 所指的
      canonical own group，创建尚不属于该 scope 的新小组与之矛盾。
    """
    if actor is None:
        return False
    if manage_permission == MANAGE_PERMISSION_SUPER:
        return True
    if manage_permission == MANAGE_PERMISSION_DEPARTMENT:
        return actor.department == department
    return False


def can_batch_move_group_members(manage_permission):
    """批量移动成员进小组 capability：仅超级管理员/管理部门。

    与 manage_departments/manage_groups UI 仅向这两级显示“批量移入成员/
    添加小组”的 capability 对齐；小组级管理员在后端独立拒绝。
    """
    return manage_permission in (MANAGE_PERMISSION_SUPER, MANAGE_PERMISSION_DEPARTMENT)


def can_assign_group_leader(actor, manage_permission, candidate, group_department, group_id=None):
    """组长候选人 scope policy（leader_id 与 legacy 姓名路径共用）。

    - 超级管理员：任意 active 用户；允许跨部门候选（保持既有自动迁移兼容行为）；
    - 管理部门：candidate 必须与 actor 同部门，且与目标小组同部门 —— 不得借
      leader assignment 跨部门搬人；
    - 管理部门小组：candidate 必须已经是该 canonical 小组成员
      （candidate.group_id == group_id 且部门一致）—— 不得从兄弟小组、
      未分组池或其他部门拉人。

    ``group_id`` 传 None 表示小组尚未创建（add_group）；此时小组级 actor
    本就无创建 capability，由调用方先行拒绝。
    """
    if candidate is None or not is_user_active(candidate):
        return False
    if manage_permission == MANAGE_PERMISSION_SUPER:
        return True
    if manage_permission == MANAGE_PERMISSION_DEPARTMENT:
        return (
            actor is not None
            and actor.department == group_department
            and candidate.department == group_department
        )
    if manage_permission == MANAGE_PERMISSION_GROUP:
        return (
            group_id is not None
            and candidate.group_id == group_id
            and candidate.department == group_department
        )
    return False


def leader_assignment_may_migrate_department(manage_permission):
    """leader assignment 是否允许自动迁移候选人 department。

    仅超级管理员保留该兼容行为；管理部门/小组级 policy 已保证候选人与
    目标小组同部门，迁移分支不应触发。
    """
    return manage_permission == MANAGE_PERMISSION_SUPER
