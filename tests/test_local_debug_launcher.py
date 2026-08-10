import os
import socket
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


class LocalDebugPowerShellContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_path = ROOT / 'tools' / 'local_debug.ps1'
        cls.source = source_path.read_text(encoding='utf-8') if source_path.exists() else ''

    def test_manager_exposes_required_actions_and_loopback_url(self):
        for action in ('start', 'stop', 'restart', 'status', 'open'):
            self.assertIn(f"'{action}'", self.source)
        self.assertIn('http://127.0.0.1:', self.source)

    def test_manager_uses_isolated_paths_and_clears_database_url(self):
        self.assertIn("data\\instance\\debug", self.source)
        self.assertIn('lecture_forms-debug.db', self.source)
        self.assertIn("$env:DATABASE_URL = ''", self.source)
        self.assertIn("$env:LOCAL_DEBUG_MODE = '1'", self.source)

    def test_manager_records_pid_and_never_kills_python_by_name(self):
        self.assertIn('local-debug.pid', self.source)
        self.assertIn('/T', self.source)
        self.assertIn('/PID', self.source)
        self.assertNotIn('/im python', self.source.lower())
        self.assertNotIn('Stop-Process -Name', self.source)

    def test_manager_refuses_foreign_port_occupants(self):
        self.assertIn('Test-TcpPort', self.source)
        self.assertIn('occupied by another process', self.source)

    def test_manager_owns_compact_menu_and_uses_5087_by_default(self):
        self.assertIn("'menu'", self.source)
        self.assertIn('$DefaultPort = 5087', self.source)
        for label in ('SWU TIC 本地调试', '1  启动', '2  关闭', '3  重启',
                      '4  打开网页', '0  退出', '请选择'):
            self.assertIn(label, self.source)
        self.assertNotIn('$DefaultPort = 5000', self.source)

    def test_manager_hides_server_console_and_avoids_write_error_stack_dump(self):
        self.assertIn('-WindowStyle Hidden', self.source)
        self.assertIn('[Console]::Error.WriteLine', self.source)
        self.assertNotIn('Write-Error $_.Exception.Message', self.source)


class LocalDebugPortContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'tools' / 'local_debug.ps1').read_text(encoding='utf-8-sig')

    def test_manager_has_explicit_validated_port_override_and_shared_port_state(self):
        self.assertIn('$DefaultPort = 5087', self.source)
        self.assertIn('$env:LOCAL_DEBUG_PORT', self.source)
        self.assertIn('[int]::TryParse', self.source)
        self.assertIn('1 and 65535', self.source)
        self.assertIn("$env:FLASK_RUN_PORT = [string]$Port", self.source)
        self.assertIn('http://127.0.0.1:$Port', self.source)

    def test_manager_rejects_invalid_port_without_starting(self):
        env = os.environ.copy()
        env['LOCAL_DEBUG_PORT'] = 'not-a-port'
        result = subprocess.run(
            [
                'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', str(ROOT / 'tools' / 'local_debug.ps1'), '-Action', 'status',
            ],
            cwd=ROOT,
            env=env,
            text=True,
            encoding='utf-8',
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('LOCAL_DEBUG_PORT must be an integer between 1 and 65535', result.stderr)


class LocalDebugErrorOutputTest(unittest.TestCase):
    def test_foreign_port_error_is_concise_and_does_not_kill_listener(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            env = os.environ.copy()
            env['LOCAL_DEBUG_PORT'] = str(port)
            result = subprocess.run(
                [
                    'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                    '-File', str(ROOT / 'tools' / 'local_debug.ps1'),
                    '-Action', 'start',
                ],
                cwd=ROOT,
                env=env,
                text=True,
                encoding='utf-8',
                errors='replace',
                capture_output=True,
                check=False,
            )
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(str(port), combined)
            self.assertNotIn('CategoryInfo', combined)
            self.assertNotIn('FullyQualifiedErrorId', combined)
            self.assertNotIn('Write-Error', combined)
            self.assertEqual(listener.getsockname()[1], port)
        finally:
            listener.close()


class LocalDebugCmdContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ROOT / '本地调试.cmd'
        cls.raw = cls.path.read_bytes() if cls.path.exists() else b''
        cls.source = cls.raw.decode('ascii', errors='replace') if cls.raw else ''

    def test_wrapper_is_bom_free_ascii_crlf_and_starts_with_echo_off(self):
        self.assertTrue(self.raw.startswith(b'@echo off\r\n'))
        self.assertFalse(self.raw.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\n', self.raw.replace(b'\r\n', b''))
        self.assertTrue(all(byte < 128 for byte in self.raw))

    def test_wrapper_delegates_menu_and_noninteractive_actions(self):
        self.assertIn('tools\\local_debug.ps1', self.source)
        self.assertIn('-Action menu', self.source)
        self.assertIn('-Action "%~1"', self.source)
        self.assertNotIn('set /p', self.source.lower())
        self.assertNotIn('echo [1]', self.source.lower())


if __name__ == '__main__':
    unittest.main()
