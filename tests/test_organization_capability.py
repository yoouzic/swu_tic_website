# -*- coding: utf-8 -*-
"""Round 7A-R1 organization capability regressions.

Closes the remaining capability drift after Round 7A:

- group-level managers cannot create groups nor batch-move members;
- leader assignment (``leader_id`` AND legacy leader-name path) follows one
  scope policy per manage-permission tier;
- legacy (``group_id`` NULL) members survive group rename / department move;
- UI capability visibility matches backend enforcement and never derives from
  legacy ``User.group`` text.
"""
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app.app import app
from app.models import Department, Group, Permission, RolePermission, User, db
from app.utils.user_status import UNASSIGNED_GROUP_NAME
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

PERM_MANAGE_DEPARTMENT = '管理部门'
PERM_MANAGE_GROUP = '管理部门小组'
ROLE_INFO = '信息员'
ROLE_ADMIN = '管理员'
ROLE_SUPER = '超级管理员'
PASSWORD = 'password'

MANAGE_GROUPS_TEMPLATE = Path('app/templates/admin/manage_groups.html')
MANAGE_DEPARTMENTS_TEMPLATE = Path('app/templates/admin/manage_departments.html')


class OrganizationCapabilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix='org-capability-')

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.db_path = Path(self.temp_dir.name) / f'org-capability-{id(self)}.sqlite'
        configure_sqlite_database(app, db, self.db_path)
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.app_context = app.app_context()
        self.app_context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self._create_organization()

    def tearDown(self):
        cleanup_sqlite_database(db, drop_all=True)
        self.app_context.pop()

    def _create_organization(self):
        self.permission_objects = {}
        for name in (PERM_MANAGE_DEPARTMENT, PERM_MANAGE_GROUP):
            permission = Permission(name=name, description=f'test {name}')
            db.session.add(permission)
            self.permission_objects[name] = permission
        db.session.flush()

        self.dept_a = Department(name='办公部', description='test')
        self.dept_b = Department(name='策划部', description='test')
        db.session.add_all([self.dept_a, self.dept_b])
        db.session.flush()

        self.group_a1 = Group(name='甲组', department=self.dept_a.name, max_members=100)
        self.group_a2 = Group(name='乙组', department=self.dept_a.name, max_members=100)
        db.session.add_all([self.group_a1, self.group_a2])
        db.session.flush()

        self.super_admin = self._make_user(
            'S100', 'admin-super', ROLE_SUPER, self.dept_a.name, self.group_a1.name, self.group_a1.id,
        )
        self.department_manager = self._make_user(
            'D100', 'admin-d100', ROLE_ADMIN, self.dept_a.name, self.group_a1.name, self.group_a1.id,
            permissions={PERM_MANAGE_DEPARTMENT},
        )
        self.group_manager = self._make_user(
            'G100', 'admin-g100', ROLE_ADMIN, self.dept_a.name, self.group_a1.name, self.group_a1.id,
            permissions={PERM_MANAGE_GROUP},
        )
        db.session.commit()

    def _make_user(self, number, student_id, role, department, group, group_id, permissions=None):
        user = User(
            number=number,
            department=department,
            name=f'User {number}',
            gender='-',
            grade='-',
            college='Test College',
            major='-',
            dormitory='-',
            phone='-',
            qq='-',
            student_id=student_id,
            password_hash=generate_password_hash(PASSWORD),
            role=role,
            group=group,
            group_id=group_id,
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        for name in (permissions or set()):
            db.session.add(RolePermission(
                role=f'特殊角色_{user.id}',
                permission_id=self.permission_objects[name].id,
            ))
        db.session.flush()
        # 场景用户必须落库：fault/rollback 断言不得连带回滚测试夹具。
        db.session.commit()
        return user

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    def _membership(self, user):
        return (user.department, user.group_id, user.group)

    # ---- create group capability (§5/§6) ----

    def test_group_manager_cannot_create_group_even_as_self_leader(self):
        before = self._membership(self.group_manager)
        self._login(self.group_manager)
        response = self.client.post('/admin/api/groups', json={
            'name': '越权新组', 'department': self.dept_a.name,
            'leader': '', 'description': '', 'leader_id': self.group_manager.id,
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json().get('success', True))

        db.session.expire_all()
        self.assertIsNone(
            Group.query.filter_by(name='越权新组', department=self.dept_a.name).first()
        )
        self.assertEqual(self._membership(db.session.get(User, self.group_manager.id)), before)

    def test_department_manager_can_still_create_group_in_own_department(self):
        self._login(self.department_manager)
        response = self.client.post('/admin/api/groups', json={
            'name': '部门新组', 'department': self.dept_a.name,
            'leader': '', 'description': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(
            Group.query.filter_by(name='部门新组', department=self.dept_a.name).first()
        )

    # ---- batch move members capability (§9) ----

    def test_group_manager_cannot_batch_move_members(self):
        sibling_member = self._make_user(
            'T910', 's-t910', ROLE_INFO, self.dept_a.name, self.group_a2.name, self.group_a2.id,
        )
        unassigned = self._make_user(
            'T911', 's-t911', ROLE_INFO, self.dept_a.name, UNASSIGNED_GROUP_NAME, None,
        )

        self._login(self.group_manager)
        for candidate in (sibling_member, unassigned):
            before = self._membership(candidate)
            response = self.client.post(
                f'/admin/api/groups/{self.group_a1.id}/move_members',
                json={'user_ids': [candidate.id]},
            )
            self.assertEqual(response.status_code, 403)
            self.assertFalse(response.get_json().get('success', True))
            db.session.expire_all()
            self.assertEqual(
                self._membership(db.session.get(User, candidate.id)), before,
                msg=f'candidate {candidate.number} must stay unchanged',
            )

    def test_department_manager_can_still_batch_move_members(self):
        candidate = self._make_user(
            'T920', 's-t920', ROLE_INFO, self.dept_a.name, UNASSIGNED_GROUP_NAME, None,
        )
        self._login(self.department_manager)
        response = self.client.post(
            f'/admin/api/groups/{self.group_a1.id}/move_members',
            json={'user_ids': [candidate.id]},
        )
        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        candidate_after = db.session.get(User, candidate.id)
        self.assertEqual(candidate_after.group_id, self.group_a1.id)
        self.assertEqual(candidate_after.group, self.group_a1.name)

    # ---- leader scope matrix: update_group (§7/§16) ----

    def _update_group_payload(self, **overrides):
        payload = {
            'name': self.group_a1.name, 'department': self.dept_a.name,
            'leader': '', 'description': '', 'max_members': 10,
        }
        payload.update(overrides)
        return payload

    def test_super_admin_can_assign_cross_department_leader_via_update(self):
        candidate = self._make_user('L100', 's-l100', ROLE_INFO, self.dept_b.name, '旧组', None)
        self._login(self.super_admin)
        response = self.client.put(
            f'/admin/api/groups/{self.group_a1.id}',
            json=self._update_group_payload(leader_id=candidate.id),
        )
        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        candidate_after = db.session.get(User, candidate.id)
        self.assertEqual(candidate_after.department, self.dept_a.name)
        self.assertEqual(candidate_after.group_id, self.group_a1.id)
        self.assertEqual(candidate_after.group, self.group_a1.name)
        self.assertEqual(db.session.get(Group, self.group_a1.id).leader_id, candidate.id)

    def test_department_manager_can_assign_same_department_leader(self):
        candidate = self._make_user('L110', 's-l110', ROLE_INFO, self.dept_a.name, UNASSIGNED_GROUP_NAME, None)
        self._login(self.department_manager)
        response = self.client.put(
            f'/admin/api/groups/{self.group_a1.id}',
            json=self._update_group_payload(leader_id=candidate.id),
        )
        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        candidate_after = db.session.get(User, candidate.id)
        self.assertEqual(candidate_after.group_id, self.group_a1.id)
        self.assertEqual(candidate_after.department, self.dept_a.name)

    def test_department_manager_cannot_assign_cross_department_leader(self):
        candidate = self._make_user('L120', 's-l120', ROLE_INFO, self.dept_b.name, '旧组', None)
        before = self._membership(candidate)
        group_before = (self.group_a1.leader, self.group_a1.leader_id)

        self._login(self.department_manager)
        response = self.client.put(
            f'/admin/api/groups/{self.group_a1.id}',
            json=self._update_group_payload(leader_id=candidate.id),
        )
        self.assertEqual(response.status_code, 403)

        db.session.expire_all()
        self.assertEqual(self._membership(db.session.get(User, candidate.id)), before)
        group_after = db.session.get(Group, self.group_a1.id)
        self.assertEqual((group_after.leader, group_after.leader_id), group_before)

    def test_group_manager_can_promote_current_own_group_member(self):
        member = self._make_user(
            'L130', 's-l130', ROLE_INFO, self.dept_a.name, self.group_a1.name, self.group_a1.id,
        )
        self._login(self.group_manager)
        response = self.client.put(
            f'/admin/api/groups/{self.group_a1.id}',
            json=self._update_group_payload(leader_id=member.id),
        )
        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        self.assertEqual(db.session.get(Group, self.group_a1.id).leader_id, member.id)
        member_after = db.session.get(User, member.id)
        self.assertEqual(member_after.group_id, self.group_a1.id)
        self.assertEqual(member_after.department, self.dept_a.name)

    def test_group_manager_cannot_pull_sibling_group_member_as_leader(self):
        sibling_member = self._make_user(
            'L140', 's-l140', ROLE_INFO, self.dept_a.name, self.group_a2.name, self.group_a2.id,
        )
        self._login(self.group_manager)
        response = self.client.put(
            f'/admin/api/groups/{self.group_a1.id}',
            json=self._update_group_payload(leader_id=sibling_member.id),
        )
        self.assertEqual(response.status_code, 403)
        db.session.expire_all()
        self.assertEqual(
            self._membership(db.session.get(User, sibling_member.id)),
            (self.dept_a.name, self.group_a2.id, self.group_a2.name),
        )

    def test_group_manager_cannot_pull_unassigned_user_as_leader(self):
        unassigned = self._make_user('L150', 's-l150', ROLE_INFO, self.dept_a.name, UNASSIGNED_GROUP_NAME, None)
        self._login(self.group_manager)
        response = self.client.put(
            f'/admin/api/groups/{self.group_a1.id}',
            json=self._update_group_payload(leader_id=unassigned.id),
        )
        self.assertEqual(response.status_code, 403)
        db.session.expire_all()
        self.assertIsNone(db.session.get(User, unassigned.id).group_id)

    def test_group_manager_cannot_pull_cross_department_user_as_leader(self):
        outsider = self._make_user('L160', 's-l160', ROLE_INFO, self.dept_b.name, '旧组', None)
        before = self._membership(outsider)
        self._login(self.group_manager)
        response = self.client.put(
            f'/admin/api/groups/{self.group_a1.id}',
            json=self._update_group_payload(leader_id=outsider.id),
        )
        self.assertEqual(response.status_code, 403)
        db.session.expire_all()
        self.assertEqual(self._membership(db.session.get(User, outsider.id)), before)

    # ---- leader scope matrix: add_group (§7/§16) ----

    def _add_group_payload(self, **overrides):
        payload = {
            'name': '政策新组', 'department': self.dept_a.name,
            'leader': '', 'description': '',
        }
        payload.update(overrides)
        return payload

    def test_super_admin_add_group_with_cross_department_leader_migrates(self):
        candidate = self._make_user('L200', 's-l200', ROLE_INFO, self.dept_b.name, '旧组', None)
        self._login(self.super_admin)
        response = self.client.post('/admin/api/groups', json=self._add_group_payload(leader_id=candidate.id))
        self.assertEqual(response.status_code, 200)
        group = Group.query.filter_by(name='政策新组', department=self.dept_a.name).first()
        self.assertIsNotNone(group)
        db.session.expire_all()
        candidate_after = db.session.get(User, candidate.id)
        self.assertEqual(candidate_after.department, self.dept_a.name)
        self.assertEqual(candidate_after.group_id, group.id)

    def test_department_manager_add_group_with_cross_department_leader_denied(self):
        candidate = self._make_user('L210', 's-l210', ROLE_INFO, self.dept_b.name, '旧组', None)
        before = self._membership(candidate)

        self._login(self.department_manager)
        response = self.client.post('/admin/api/groups', json=self._add_group_payload(leader_id=candidate.id))
        self.assertEqual(response.status_code, 403)

        db.session.expire_all()
        self.assertIsNone(Group.query.filter_by(name='政策新组', department=self.dept_a.name).first())
        self.assertEqual(self._membership(db.session.get(User, candidate.id)), before)

    def test_department_manager_add_group_with_same_department_leader_allowed(self):
        candidate = self._make_user('L220', 's-l220', ROLE_INFO, self.dept_a.name, UNASSIGNED_GROUP_NAME, None)
        self._login(self.department_manager)
        response = self.client.post('/admin/api/groups', json=self._add_group_payload(leader_id=candidate.id))
        self.assertEqual(response.status_code, 200)
        group = Group.query.filter_by(name='政策新组', department=self.dept_a.name).first()
        self.assertIsNotNone(group)
        db.session.expire_all()
        self.assertEqual(db.session.get(User, candidate.id).group_id, group.id)

    # ---- unified leader_id / leader-name policy (§8) ----

    def test_update_group_leader_name_path_assigns_membership_like_leader_id(self):
        candidate = self._make_user('L300', 's-l300', ROLE_INFO, self.dept_a.name, UNASSIGNED_GROUP_NAME, None)
        self._login(self.department_manager)
        response = self.client.put(
            f'/admin/api/groups/{self.group_a1.id}',
            json=self._update_group_payload(leader=candidate.name),
        )
        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        group_after = db.session.get(Group, self.group_a1.id)
        self.assertEqual(group_after.leader_id, candidate.id)
        candidate_after = db.session.get(User, candidate.id)
        self.assertEqual(candidate_after.group_id, self.group_a1.id)
        self.assertEqual(candidate_after.group, self.group_a1.name)

    def test_group_manager_leader_name_path_enforces_same_policy(self):
        sibling_member = self._make_user(
            'L310', 's-l310', ROLE_INFO, self.dept_a.name, self.group_a2.name, self.group_a2.id,
        )
        self._login(self.group_manager)
        response = self.client.put(
            f'/admin/api/groups/{self.group_a1.id}',
            json=self._update_group_payload(leader=sibling_member.name),
        )
        self.assertEqual(response.status_code, 403)
        db.session.expire_all()
        self.assertIsNone(db.session.get(Group, self.group_a1.id).leader_id)
        self.assertEqual(
            self._membership(db.session.get(User, sibling_member.id)),
            (self.dept_a.name, self.group_a2.id, self.group_a2.name),
        )

    # ---- legacy membership continuity across rename / department move (§13/§15) ----

    def _group_update_payload(self, group, **overrides):
        payload = {
            'name': group.name, 'department': group.department,
            'leader': '', 'description': '', 'max_members': 10,
        }
        payload.update(overrides)
        return payload

    def test_rename_group_syncs_legacy_members(self):
        legacy = self._make_user(
            'T400', 's-t400', ROLE_INFO, self.group_a1.department, self.group_a1.name, None,
        )
        canonical = self._make_user(
            'T401', 's-t401', ROLE_INFO, self.group_a1.department, self.group_a1.name, self.group_a1.id,
        )

        self._login(self.super_admin)
        response = self.client.put(
            f'/admin/api/groups/{self.group_a1.id}',
            json=self._group_update_payload(self.group_a1, name='甲组二'),
        )
        self.assertEqual(response.status_code, 200)

        db.session.expire_all()
        legacy_after = db.session.get(User, legacy.id)
        self.assertEqual(legacy_after.group, '甲组二')
        self.assertIsNone(legacy_after.group_id)

        renamed_group = db.session.get(Group, self.group_a1.id)
        member_ids = [m.id for m in renamed_group.get_members()]
        self.assertIn(legacy.id, member_ids)
        self.assertIn(canonical.id, member_ids)
        self.assertEqual(db.session.get(User, canonical.id).group, '甲组二')

    def test_rename_group_does_not_touch_stale_canonical_user(self):
        stale = self._make_user(
            'T410', 's-t410', ROLE_INFO, self.dept_a.name, self.group_a1.name, self.group_a2.id,
        )
        # 期望值必须在 PUT 之前捕获：group_a1 是同一 ORM 对象，改名后其 .name 已变。
        expected_group_id = self.group_a2.id
        expected_group_text = self.group_a1.name

        self._login(self.super_admin)
        response = self.client.put(
            f'/admin/api/groups/{self.group_a1.id}',
            json=self._group_update_payload(self.group_a1, name='甲组二'),
        )
        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        stale_after = db.session.get(User, stale.id)
        self.assertEqual(stale_after.group_id, expected_group_id)
        self.assertEqual(stale_after.group, expected_group_text)

    def test_department_move_keeps_legacy_member_continuity(self):
        move_group = Group(name='丙组', department=self.dept_a.name, max_members=100)
        db.session.add(move_group)
        db.session.commit()

        legacy = self._make_user(
            'T420', 's-t420', ROLE_INFO, move_group.department, move_group.name, None,
        )

        self._login(self.super_admin)
        response = self.client.put(
            f'/admin/api/groups/{move_group.id}',
            json=self._group_update_payload(move_group, department=self.dept_b.name),
        )
        self.assertEqual(response.status_code, 200)

        db.session.expire_all()
        legacy_after = db.session.get(User, legacy.id)
        self.assertEqual(legacy_after.department, self.dept_b.name)
        self.assertEqual(legacy_after.group, '丙组')
        self.assertIsNone(legacy_after.group_id)
        moved_group = db.session.get(Group, move_group.id)
        self.assertEqual(moved_group.department, self.dept_b.name)
        self.assertIn(legacy.id, [m.id for m in moved_group.get_members()])

    # ---- UI / backend parity (§10/§11/§12/§17) ----

    def test_manage_departments_page_hides_create_and_edit_for_group_manager(self):
        self._login(self.group_manager)
        response = self.client.get('/admin/manage_departments')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn('data-bs-target="#addGroupModal"', html)
        self.assertNotIn('data-department-action="edit"', html)
        self.assertNotIn('data-bs-target="#addDepartmentModal"', html)

    def test_manage_departments_page_shows_edit_for_department_manager(self):
        self._login(self.department_manager)
        response = self.client.get('/admin/manage_departments')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('data-bs-target="#addGroupModal"', html)
        self.assertIn('data-department-action="edit"', html)
        self.assertNotIn('data-bs-target="#addDepartmentModal"', html)

    def test_manage_departments_page_shows_add_department_for_super_admin(self):
        self._login(self.super_admin)
        response = self.client.get('/admin/manage_departments')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('data-bs-target="#addDepartmentModal"', html)

    def test_manage_groups_page_edit_visible_for_stale_text_canonical_owner(self):
        stale_actor = self._make_user(
            'G200', 'admin-g200', ROLE_ADMIN, self.dept_a.name, '早已过期的旧名', self.group_a1.id,
            permissions={PERM_MANAGE_GROUP},
        )
        self._login(stale_actor)
        response = self.client.get('/admin/manage_groups')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(f'editGroup({self.group_a1.id})', html)
        self.assertNotIn(f'editGroup({self.group_a2.id})', html)
        self.assertNotIn('data-bs-target="#addGroupModal"', html)

    def test_manage_groups_template_never_uses_legacy_text_for_capability(self):
        source = MANAGE_GROUPS_TEMPLATE.read_text(encoding='utf-8')
        self.assertNotIn("group.name == user.group", source)
        self.assertIn('group.id == user.group_id', source)
        self.assertIn('user.group_id is not none', source)

    def test_manage_departments_template_gates_department_edit_and_create(self):
        source = MANAGE_DEPARTMENTS_TEMPLATE.read_text(encoding='utf-8')
        # 部门编辑操作必须收口到 部门级及以上。
        self.assertIn(
            "{% if manage_permission == '超级管理员' or manage_permission == '管理部门' %}",
            source,
        )
        # 头部添加小组按钮不再对小组级管理员开放（else 分支已移除）。
        add_department_index = source.index('#addDepartmentModal')
        add_group_button_index = source.index('#addGroupModal')
        header_fragment = source[add_department_index - 400:add_group_button_index]
        self.assertIn("{% elif manage_permission == '管理部门' %}", header_fragment)
        self.assertNotIn('{% else %}', header_fragment)


if __name__ == '__main__':
    unittest.main()
