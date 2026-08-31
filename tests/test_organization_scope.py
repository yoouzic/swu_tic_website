# -*- coding: utf-8 -*-
"""Round 7A organization scope regressions.

Canonical rule under test: ``User.group_id`` is the only authoritative group
identity for 管理部门小组 authorization and for group membership queries; the
legacy ``User.group`` text is display/compatibility only.  Also covers the
single-transaction contracts of ``add_department`` / ``add_group`` and the
disband member-selection invariants.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from werkzeug.security import generate_password_hash

from app.app import app
from app.models import Department, Group, Permission, RolePermission, User, db
from app.services.organization_membership import (
    group_member_criteria,
    is_group_in_canonical_scope,
    is_user_in_canonical_group_scope,
)
from app.utils.user_status import UNASSIGNED_GROUP_NAME
from tests.app_test_utils import cleanup_sqlite_database, configure_sqlite_database

PERM_MANAGE_GROUP = '管理部门小组'
ROLE_INFO = '信息员'
ROLE_ADMIN = '管理员'
ROLE_SUPER = '超级管理员'
PASSWORD = 'password'


class OrganizationScopeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix='org-scope-')

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            cleanup_sqlite_database(db)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.db_path = Path(self.temp_dir.name) / f'org-scope-{id(self)}.sqlite'
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
        self.manage_group_permission = Permission(
            name=PERM_MANAGE_GROUP, description='test 管理部门小组'
        )
        db.session.add(self.manage_group_permission)
        db.session.flush()

        self.dept_a = Department(name='办公部', description='test')
        self.dept_b = Department(name='策划部', description='test')
        db.session.add_all([self.dept_a, self.dept_b])
        db.session.flush()

        self.group_a1 = Group(name='甲组', department=self.dept_a.name, max_members=100)
        self.group_a2 = Group(name='乙组', department=self.dept_a.name, max_members=100)
        self.group_b1 = Group(name='甲组', department=self.dept_b.name, max_members=100)
        db.session.add_all([self.group_a1, self.group_a2, self.group_b1])
        db.session.flush()

        self.group_manager = self._make_user(
            'G100', 'admin-g100', ROLE_ADMIN, self.dept_a.name, self.group_a1.name, self.group_a1.id,
            permissions={PERM_MANAGE_GROUP},
        )
        self.null_group_manager = self._make_user(
            'GNULL', 'admin-gnull', ROLE_ADMIN, self.dept_a.name, UNASSIGNED_GROUP_NAME, None,
            permissions={PERM_MANAGE_GROUP},
        )
        self.super_admin = self._make_user(
            'S100', 'admin-super', ROLE_SUPER, self.dept_a.name, self.group_a1.name, self.group_a1.id,
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
            permission = Permission.query.filter_by(name=name).first()
            db.session.add(RolePermission(
                role=f'特殊角色_{user.id}',
                permission_id=permission.id,
            ))
        db.session.flush()
        # 场景用户必须落库：fault-injection 用例中路由的 rollback 不得连带回滚测试夹具。
        db.session.commit()
        return user

    def _login(self, user):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user.id
            sess['user_role'] = user.role
            sess['user_name'] = user.name

    # ---- A. NULL group actor fail closed ----

    def test_a_null_group_manager_cannot_list_unassigned_pool(self):
        target = self._make_user(
            'T100', 's-t100', ROLE_INFO, self.dept_a.name, UNASSIGNED_GROUP_NAME, None,
        )
        self._login(self.null_group_manager)
        response = self.client.get('/admin/api/users')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['success'])
        # Both actor and target share the legacy text 未分配小组; text equality must not leak the pool.
        self.assertEqual(data['data'], [])
        self.assertNotIn(target.id, [u['id'] for u in data['data']])

    def test_a_null_group_manager_cannot_view_user_detail_api(self):
        target = self._make_user(
            'T101', 's-t101', ROLE_INFO, self.dept_a.name, UNASSIGNED_GROUP_NAME, None,
        )
        self._login(self.null_group_manager)
        response = self.client.get(f'/admin/api/users/{target.id}')
        self.assertEqual(response.status_code, 403)

    def test_a_null_group_manager_cannot_open_managed_user_page(self):
        target = self._make_user(
            'T102', 's-t102', ROLE_INFO, self.dept_a.name, UNASSIGNED_GROUP_NAME, None,
        )
        self._login(self.null_group_manager)
        response = self.client.get(f'/admin/users/{target.id}/view')
        self.assertEqual(response.status_code, 302)

    def test_a_null_group_manager_has_no_group_scope_in_group_apis(self):
        self._login(self.null_group_manager)
        response = self.client.get('/admin/api/groups')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json().get('data'), [])

        # Even the actor's own-department group is out of scope without a canonical assignment.
        response = self.client.get(f'/admin/api/groups/{self.group_a1.id}')
        self.assertEqual(response.status_code, 403)

    def test_a_null_group_manager_tree_has_no_groups_and_no_unassigned_bucket(self):
        self._login(self.null_group_manager)
        response = self.client.get('/admin/api/departments')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['success'])
        for dept in data['data']:
            self.assertEqual(dept['groups'], [])

    def test_a_scope_predicates_fail_closed_for_null_group_actor(self):
        self.assertFalse(
            is_user_in_canonical_group_scope(self.null_group_manager, self.group_manager)
        )
        self.assertFalse(is_group_in_canonical_scope(self.null_group_manager, self.group_a1))

    # ---- B. canonical ID wins over stale group text ----

    def test_b_canonical_id_decides_user_scope_over_stale_text(self):
        actor = self._make_user(
            'A200', 'admin-a200', ROLE_ADMIN, self.dept_a.name, 'B组', self.group_a1.id,
            permissions={PERM_MANAGE_GROUP},
        )
        target_a = self._make_user('T200', 's-t200', ROLE_INFO, self.dept_a.name, '旧名称', self.group_a1.id)
        target_b = self._make_user('T201', 's-t201', ROLE_INFO, self.dept_a.name, self.group_a1.name, self.group_a2.id)

        self._login(actor)

        self.assertEqual(self.client.get(f'/admin/api/users/{target_a.id}').status_code, 200)
        self.assertEqual(self.client.get(f'/admin/api/users/{target_b.id}').status_code, 403)

        self.assertEqual(self.client.get(f'/admin/users/{target_a.id}/view').status_code, 200)
        self.assertEqual(self.client.get(f'/admin/users/{target_b.id}/view').status_code, 302)

        response = self.client.get('/admin/api/users')
        self.assertEqual(response.status_code, 200)
        ids = [u['id'] for u in response.get_json()['data']]
        self.assertIn(target_a.id, ids)
        self.assertNotIn(target_b.id, ids)

    def test_b_canonical_id_decides_group_scope_over_stale_text(self):
        actor = self._make_user(
            'A201', 'admin-a201', ROLE_ADMIN, self.dept_a.name, 'B组', self.group_a1.id,
            permissions={PERM_MANAGE_GROUP},
        )
        self._login(actor)
        self.assertEqual(self.client.get(f'/admin/api/groups/{self.group_a1.id}').status_code, 200)
        self.assertEqual(self.client.get(f'/admin/api/groups/{self.group_a2.id}').status_code, 403)

        # Same-name group in another department stays out of scope (department coupling).
        self.assertEqual(self.client.get(f'/admin/api/groups/{self.group_b1.id}').status_code, 403)

    # ---- C. legacy membership fallback ----

    def test_c_legacy_member_without_group_id_is_still_recognized(self):
        legacy = self._make_user(
            'T300', 's-t300', ROLE_INFO, self.group_a1.department, self.group_a1.name, None,
        )
        canonical = self._make_user(
            'T301', 's-t301', ROLE_INFO, self.group_a1.department, self.group_a1.name, self.group_a1.id,
        )

        member_ids = [m.id for m in self.group_a1.get_members()]
        self.assertIn(legacy.id, member_ids)
        self.assertIn(canonical.id, member_ids)
        self.assertEqual(self.group_a1.get_member_count(), len(member_ids))

        criteria_ids = [u.id for u in db.session.query(User).filter(group_member_criteria(self.group_a1)).all()]
        self.assertIn(legacy.id, criteria_ids)

    # ---- D. non-null canonical ID defeats legacy fallback ----

    def test_d_canonical_id_beats_stale_legacy_text_in_membership(self):
        stale = self._make_user(
            'T400', 's-t400', ROLE_INFO, self.dept_a.name, self.group_a1.name, self.group_a2.id,
        )

        self.assertNotIn(stale.id, [m.id for m in self.group_a1.get_members()])
        self.assertIn(stale.id, [m.id for m in self.group_a2.get_members()])
        self.assertNotIn(
            stale.id,
            [u.id for u in db.session.query(User).filter(group_member_criteria(self.group_a1)).all()],
        )

    # ---- E. disband regression ----

    def test_e_disband_group_keeps_member_of_other_group_with_stale_text(self):
        stale = self._make_user(
            'T500', 's-t500', ROLE_INFO, self.dept_a.name, self.group_a1.name, self.group_a2.id,
        )
        legacy = self._make_user(
            'T501', 's-t501', ROLE_INFO, self.dept_a.name, self.group_a1.name, None,
        )

        self._login(self.super_admin)
        response = self.client.post(
            f'/admin/api/groups/{self.group_a1.id}/disband',
            json={'password': PASSWORD},
        )
        self.assertEqual(response.status_code, 200)

        db.session.expire_all()
        stale_after = db.session.get(User, stale.id)
        self.assertEqual(stale_after.group_id, self.group_a2.id)
        self.assertIsNotNone(stale_after.group_id)

        legacy_after = db.session.get(User, legacy.id)
        self.assertIsNone(legacy_after.group_id)
        self.assertEqual(legacy_after.group, UNASSIGNED_GROUP_NAME)

        self.assertIsNone(db.session.get(Group, self.group_a1.id))

    # ---- F. add_department transaction atomicity ----

    def _department_payload(self, manager):
        return {
            'name': '全新部门',
            'description': 'atomicity probe',
            'manager_id': manager.id,
        }

    def _assert_department_rolled_back(self, manager, before):
        db.session.expire_all()
        self.assertIsNone(Department.query.filter_by(name='全新部门').first())
        manager_after = db.session.get(User, manager.id)
        self.assertEqual(
            (manager_after.department, manager_after.group_id, manager_after.group),
            before,
        )

    def test_f_add_department_rolls_back_when_final_commit_fails(self):
        manager = self._make_user('M600', 's-m600', ROLE_ADMIN, self.dept_b.name, '旧组', None)
        before = (manager.department, manager.group_id, manager.group)

        self._login(self.super_admin)
        with mock.patch.object(db.session, 'commit', side_effect=RuntimeError('commit fault')):
            response = self.client.post('/admin/api/departments', json=self._department_payload(manager))

        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.get_json().get('success', True))
        self._assert_department_rolled_back(manager, before)

    def test_f_add_department_rolls_back_when_department_flush_fails(self):
        # 单事务 proof：建部门阶段（add+flush）失败时，负责人迁移尚未发生且部门不落库。
        # 修复前该路由没有显式 flush、部门会先独立提交，因此本用例可区分新旧实现。
        manager = self._make_user('M603', 's-m603', ROLE_ADMIN, self.dept_b.name, '旧组', None)
        before = (manager.department, manager.group_id, manager.group)

        self._login(self.super_admin)
        with mock.patch.object(db.session, 'flush', side_effect=RuntimeError('flush fault')):
            response = self.client.post('/admin/api/departments', json=self._department_payload(manager))

        self.assertEqual(response.status_code, 500)
        self._assert_department_rolled_back(manager, before)

    def test_f_add_department_rolls_back_when_manager_mutation_fails(self):
        manager = self._make_user('M601', 's-m601', ROLE_ADMIN, self.dept_b.name, '旧组', None)
        before = (manager.department, manager.group_id, manager.group)

        self._login(self.super_admin)
        with mock.patch(
            'app.blueprints.admin.org.clear_user_group',
            side_effect=RuntimeError('manager mutation fault'),
        ):
            response = self.client.post('/admin/api/departments', json=self._department_payload(manager))

        self.assertEqual(response.status_code, 500)
        self._assert_department_rolled_back(manager, before)

    def test_f_add_department_success_keeps_single_transaction_semantics(self):
        manager = self._make_user('M602', 's-m602', ROLE_ADMIN, self.dept_b.name, '旧组', None)

        self._login(self.super_admin)
        response = self.client.post('/admin/api/departments', json=self._department_payload(manager))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])

        db.session.expire_all()
        dept = Department.query.filter_by(name='全新部门').first()
        self.assertIsNotNone(dept)
        self.assertEqual(dept.manager_id, manager.id)
        manager_after = db.session.get(User, manager.id)
        self.assertEqual(manager_after.department, '全新部门')
        self.assertIsNone(manager_after.group_id)
        self.assertEqual(manager_after.group, UNASSIGNED_GROUP_NAME)

    # ---- G. add_group transaction atomicity ----

    def _group_payload(self, leader):
        return {
            'name': '全新小组',
            'department': self.dept_a.name,
            'leader': '',
            'description': 'atomicity probe',
            'leader_id': leader.id,
        }

    def _assert_group_rolled_back(self, leader, before):
        db.session.expire_all()
        self.assertIsNone(
            Group.query.filter_by(name='全新小组', department=self.dept_a.name).first()
        )
        leader_after = db.session.get(User, leader.id)
        self.assertEqual(
            (leader_after.department, leader_after.group_id, leader_after.group),
            before,
        )

    def test_g_add_group_rolls_back_when_final_commit_fails(self):
        leader = self._make_user('L700', 's-l700', ROLE_INFO, self.dept_a.name, '旧组', None)
        before = (leader.department, leader.group_id, leader.group)

        self._login(self.super_admin)
        with mock.patch.object(db.session, 'commit', side_effect=RuntimeError('commit fault')):
            response = self.client.post('/admin/api/groups', json=self._group_payload(leader))

        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.get_json().get('success', True))
        self._assert_group_rolled_back(leader, before)

    def test_g_add_group_rolls_back_when_group_flush_fails(self):
        # 单事务 proof：建小组阶段（add+flush）失败时，组长归属尚未发生且小组不落库。
        leader = self._make_user('L703', 's-l703', ROLE_INFO, self.dept_a.name, '旧组', None)
        before = (leader.department, leader.group_id, leader.group)

        self._login(self.super_admin)
        with mock.patch.object(db.session, 'flush', side_effect=RuntimeError('flush fault')):
            response = self.client.post('/admin/api/groups', json=self._group_payload(leader))

        self.assertEqual(response.status_code, 500)
        self._assert_group_rolled_back(leader, before)

    def test_g_add_group_rolls_back_when_leader_mutation_fails(self):
        leader = self._make_user('L701', 's-l701', ROLE_INFO, self.dept_a.name, '旧组', None)
        before = (leader.department, leader.group_id, leader.group)

        self._login(self.super_admin)
        with mock.patch(
            'app.blueprints.admin.org.assign_user_to_group',
            side_effect=RuntimeError('leader mutation fault'),
        ):
            response = self.client.post('/admin/api/groups', json=self._group_payload(leader))

        self.assertEqual(response.status_code, 500)
        self._assert_group_rolled_back(leader, before)

    def test_g_add_group_success_assigns_leader_membership(self):
        leader = self._make_user('L702', 's-l702', ROLE_INFO, self.dept_b.name, '旧组', None)

        self._login(self.super_admin)
        response = self.client.post('/admin/api/groups', json=self._group_payload(leader))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])

        db.session.expire_all()
        group = Group.query.filter_by(name='全新小组', department=self.dept_a.name).first()
        self.assertIsNotNone(group)
        leader_after = db.session.get(User, leader.id)
        self.assertEqual(leader_after.group_id, group.id)
        self.assertEqual(leader_after.group, '全新小组')
        self.assertEqual(leader_after.department, self.dept_a.name)

    # ---- scope regression for update/move entry points ----

    def test_update_and_move_group_apis_use_canonical_scope(self):
        # Round 7A-R1 收口后：小组级管理员 update own group 仍按 canonical 判定，
        # 但批量 move_members capability 整体关闭（UI 同步隐藏，见 test_organization_capability）。
        actor = self._make_user(
            'A800', 'admin-a800', ROLE_ADMIN, self.dept_a.name, 'B组', self.group_a1.id,
            permissions={PERM_MANAGE_GROUP},
        )
        target = self._make_user('T800', 's-t800', ROLE_INFO, self.dept_a.name, '旧名称', self.group_a1.id)

        self._login(actor)

        # Own canonical group: update allowed.
        response = self.client.put(f'/admin/api/groups/{self.group_a1.id}', json={
            'name': '甲组改名', 'department': self.dept_a.name,
            'leader': '', 'description': '', 'max_members': 10,
        })
        self.assertEqual(response.status_code, 200)

        # Batch move capability closed for group-level managers (own group included).
        response = self.client.post(
            f'/admin/api/groups/{self.group_a1.id}/move_members',
            json={'user_ids': [target.id]},
        )
        self.assertEqual(response.status_code, 403)

        # Sibling group stays out of scope despite any legacy text.
        response = self.client.put(f'/admin/api/groups/{self.group_a2.id}', json={
            'name': '乙组改名', 'department': self.dept_a.name,
            'leader': '', 'description': '', 'max_members': 10,
        })
        self.assertEqual(response.status_code, 403)

        response = self.client.post(
            f'/admin/api/groups/{self.group_a2.id}/move_members',
            json={'user_ids': [target.id]},
        )
        self.assertEqual(response.status_code, 403)

        db.session.expire_all()
        target_after = db.session.get(User, target.id)
        self.assertEqual(target_after.group_id, self.group_a1.id)
        self.assertEqual(target_after.group, '甲组改名')


if __name__ == '__main__':
    unittest.main()
