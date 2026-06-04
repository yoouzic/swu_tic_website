import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from werkzeug.security import check_password_hash


class InitUserScriptTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, 'init_user_test.db')

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run_script(self, *args):
        env = os.environ.copy()
        env.update({
            'INSTANCE_DIR': self.temp_dir.name,
            'SQLITE_DB_PATH': self.db_path,
            'SECRET_KEY': 'test-secret-key',
            'PYTHONUTF8': '1',
        })
        return subprocess.run(
            [sys.executable, 'tools/init_user.py', *args],
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), '..')),
            env=env,
            text=True,
            encoding='utf-8',
            capture_output=True,
            check=False,
        )

    def _get_user(self, student_id):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            return con.execute(
                'select id, student_id, number, name, role, department, "group", group_id, password_hash, is_active '
                'from users where student_id = ?',
                (student_id,),
            ).fetchone()
        finally:
            con.close()

    def _get_group(self, department, name):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            return con.execute(
                'select id, department, name from groups where department = ? and name = ?',
                (department, name),
            ).fetchone()
        finally:
            con.close()

    def _get_special_permissions(self, user_id):
        con = sqlite3.connect(self.db_path)
        try:
            rows = con.execute(
                'select p.name '
                'from role_permissions rp '
                'join permissions p on p.id = rp.permission_id '
                'where rp.role = ? '
                'order by p.name',
                (f'特殊角色_{user_id}',),
            ).fetchall()
            return [row[0] for row in rows]
        finally:
            con.close()

    def test_create_super_admin_with_defaults(self):
        result = self._run_script(
            '--student-id', 'admin',
            '--password', 'Admin@123456',
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        row = self._get_user('admin')
        self.assertIsNotNone(row)
        self.assertEqual(row['role'], '超级管理员')
        self.assertEqual(row['number'], 'SA001')
        self.assertEqual(row['name'], '超级管理员')
        self.assertEqual(row['department'], '未分配部门')
        self.assertEqual(row['group'], '未分配小组')
        self.assertEqual(row['is_active'], 1)
        self.assertTrue(check_password_hash(row['password_hash'], 'Admin@123456'))

    def test_update_existing_user(self):
        first = self._run_script(
            '--student-id', 'manager',
            '--password', 'OldPass123',
            '--role', '管理员',
            '--number', 'M001',
            '--name', 'Old Manager',
            '--department', '办公部',
        )
        self.assertEqual(first.returncode, 0, first.stderr)

        second = self._run_script(
            '--student-id', 'manager',
            '--password', 'NewPass123',
            '--role', '信息员',
            '--number', 'U001',
            '--name', 'New User',
            '--department', '技术部',
            '--group', '一组',
        )

        self.assertEqual(second.returncode, 0, second.stderr)
        row = self._get_user('manager')
        self.assertEqual(row['role'], '信息员')
        self.assertEqual(row['number'], 'U001')
        self.assertEqual(row['name'], 'New User')
        self.assertEqual(row['department'], '技术部')
        self.assertEqual(row['group'], '一组')
        self.assertTrue(check_password_hash(row['password_hash'], 'NewPass123'))

    def test_create_three_role_demo_users(self):
        result = self._run_script(
            '--create-demo-users',
            '--super-password', 'Super@123456',
            '--manager-password', 'Manager@123456',
            '--user-password', 'User@123456',
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('created: student_id=super', result.stdout)
        self.assertIn('created: student_id=manager', result.stdout)
        self.assertIn('created: student_id=user001', result.stdout)

        super_user = self._get_user('super')
        manager = self._get_user('manager')
        normal_user = self._get_user('user001')

        self.assertEqual(super_user['role'], '超级管理员')
        self.assertEqual(super_user['number'], 'SA002')
        self.assertTrue(check_password_hash(super_user['password_hash'], 'Super@123456'))

        self.assertEqual(manager['role'], '管理员')
        self.assertEqual(manager['department'], '办公部')
        self.assertTrue(check_password_hash(manager['password_hash'], 'Manager@123456'))
        self.assertEqual(
            self._get_special_permissions(manager['id']),
            ['审表_部门', '管理部门'],
        )

        self.assertEqual(normal_user['role'], '信息员')
        self.assertEqual(normal_user['group'], '一组')
        group = self._get_group('办公部', '一组')
        self.assertIsNotNone(group)
        self.assertEqual(normal_user['group_id'], group['id'])
        self.assertTrue(check_password_hash(normal_user['password_hash'], 'User@123456'))


if __name__ == '__main__':
    unittest.main()
