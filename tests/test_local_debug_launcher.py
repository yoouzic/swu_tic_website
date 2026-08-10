import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash


ROOT = Path(__file__).resolve().parents[1]


class LocalDebugDataTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name, 'lecture_forms-debug.db')

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_prepare(self, *, extra_env=None):
        env = os.environ.copy()
        env.update({
            'LOCAL_DEBUG_MODE': '1',
            'INSTANCE_DIR': self.temp_dir.name,
            'SQLITE_DB_PATH': str(self.db_path),
            'UPLOAD_FOLDER': str(Path(self.temp_dir.name, 'uploads')),
            'DATABASE_URL': '',
            'SECRET_KEY': 'local-debug-test',
            'PYTHONUTF8': '1',
        })
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, 'tools/prepare_local_debug.py', '--password', '1234564'],
            cwd=ROOT,
            env=env,
            text=True,
            encoding='utf-8',
            capture_output=True,
            check=False,
        )

    def rows(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            return con.execute(
                'select student_id, role, password_hash from users order by student_id'
            ).fetchall()
        finally:
            con.close()

    def test_prepare_creates_three_roles_with_shared_password(self):
        result = self.run_prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        users = {row['student_id']: row for row in self.rows()}
        self.assertEqual(users['super']['role'], '超级管理员')
        self.assertEqual(users['manager']['role'], '管理员')
        self.assertEqual(users['user001']['role'], '信息员')
        for student_id in ('super', 'manager', 'user001'):
            self.assertTrue(check_password_hash(users[student_id]['password_hash'], '1234564'))

    def test_prepare_resets_all_admin_roles_but_not_unrelated_information_officer(self):
        first = self.run_prepare()
        self.assertEqual(first.returncode, 0, first.stderr)
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                'insert into users '
                '(student_id, password_hash, number, name, department, "group", gender, grade, '
                'college, major, dormitory, phone, qq, role, is_active) '
                'values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                ('extra-admin', generate_password_hash('old-admin'), 'M900', '额外管理员',
                 '办公部', '未分配小组', '-', '-', '-', '-', '-', '-', '-', '管理员', 1),
            )
            con.execute(
                'insert into users '
                '(student_id, password_hash, number, name, department, "group", gender, grade, '
                'college, major, dormitory, phone, qq, role, is_active) '
                'values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                ('extra-user', generate_password_hash('keep-me'), 'U900', '额外信息员',
                 '办公部', '未分配小组', '-', '-', '-', '-', '-', '-', '-', '信息员', 1),
            )
            con.commit()
        finally:
            con.close()

        second = self.run_prepare()
        self.assertEqual(second.returncode, 0, second.stderr)
        users = {row['student_id']: row for row in self.rows()}
        self.assertTrue(check_password_hash(users['extra-admin']['password_hash'], '1234564'))
        self.assertTrue(check_password_hash(users['extra-user']['password_hash'], 'keep-me'))

    def test_prepare_refuses_to_run_without_explicit_debug_guard(self):
        result = self.run_prepare(extra_env={'LOCAL_DEBUG_MODE': '0'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('LOCAL_DEBUG_MODE=1', result.stderr)

    def test_prepare_refuses_default_business_database(self):
        result = self.run_prepare(extra_env={
            'SQLITE_DB_PATH': str(ROOT / 'data' / 'instance' / 'lecture_forms.db'),
        })
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('default business database', result.stderr)


class LocalDebugServerContractTest(unittest.TestCase):
    def test_server_requires_debug_guard_and_uses_loopback(self):
        source_path = ROOT / 'tools' / 'local_debug_server.py'
        self.assertTrue(source_path.exists(), 'local debug server wrapper is missing')
        source = source_path.read_text(encoding='utf-8')
        self.assertIn("LOCAL_DEBUG_MODE') != '1'", source)
        self.assertIn("host='127.0.0.1'", source)
        self.assertIn('use_reloader=False', source)
        self.assertIn('LOCAL_DEBUG_LOG_PATH', source)

    def test_server_does_not_reference_default_database(self):
        source_path = ROOT / 'tools' / 'local_debug_server.py'
        self.assertTrue(source_path.exists(), 'local debug server wrapper is missing')
        source = source_path.read_text(encoding='utf-8')
        self.assertNotIn('data/instance/lecture_forms.db', source.replace('\\', '/'))


if __name__ == '__main__':
    unittest.main()
